from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import math
import json
import shutil
import unittest
import xml.etree.ElementTree as ET

from goprovbox.lapdata import Session,Projection
from goprovbox.fullscene import parse_full_scene,fit_layout,MapTransform
from goprovbox.overlay import Renderer
from goprovbox.engine import Match,Scan,export,fingerprint
from goprovbox.media import run,executable,probe
from goprovbox.gpmf import TelemetryError
from goprovbox.vbo import Row
from tests.test_core import video,BASE
from tests.test_overlay import four_channel_vbo,scene_members,png


def complete_scene():
    members=scene_members();root=ET.fromstring(members['SCENE.XML']);g=root.find('.//graphics');layout=root.find('.//layout')
    laps=next(e for e in g if e.get('name')=='laps')
    laps.set('font','digits');laps.set('offset','(0,0,70,12)');laps.set('fmtfn','valid_time_ms');laps.set('fmtstr','%02u:%05.2f')
    layout.find("graphic[@name='laps']").set('screen_pos','(200,140)')
    ET.SubElement(root.find('.//images'),'image',name='ball',path='ball.png');members['ball.png']=png((64,64),(0,0,0,0))
    ET.SubElement(root.find('.//images'),'image',name='dot',path='dot.png');members['dot.png']=png((8,8),(255,0,0,255))
    ET.SubElement(g,'xyplot',name='g',background='ball',foreground='dot',x_range='(-2,2)',y_range='(-2,2)',
                  x_datasource='vbox.lat_acc_smth_mps2 scaling=0.1019716213',y_datasource='vbox.lng_acc_smth_mps2 scaling=0.1019716213')
    ET.SubElement(layout,'graphic',name='g',screen_pos='(0,100)')
    m=ET.SubElement(g,'map',name='track',reserved_size='(100,80)');ET.SubElement(m,'dynamic_point',image='dot')
    ET.SubElement(layout,'graphic',name='track',screen_pos='(10,10)')
    # Add punctuation to the synthetic bitmap font.
    f=root.find('.//font');f.text+=' ,(0x3a,55),(0x2e,60)';members['font.png']=png((65,12),(255,255,255,255))
    members['SCENE.XML']=ET.tostring(root)
    return parse_full_scene(members)


def circle_vbo(folder):
    vbo=four_channel_vbo(folder);base=vbo.rows[0].values.copy();p=Projection(51,.2);rows=[]
    for i in range(601):
        t=i/10;angle=2*math.pi*(t-5)/20;x=100*math.cos(angle);y=100*math.sin(angle)
        values=base.copy();values[2]=str((51+y/111195.0802)*60);values[3]=str(-(.2+x/p.xscale)*60)
        values[4]=str(2*math.pi*100/20*3.6);values[5]=str((-math.degrees(angle))%360)
        rows.append(Row(BASE+t,values))
    vbo.rows=rows
    old=next(i for i,line in enumerate(vbo.preamble) if line.startswith('Start '))
    lon=-(.2+100/p.xscale)*60
    vbo.preamble[old]=f'Start {lon} {(51-2/111195.0802)*60} {lon} {(51+2/111195.0802)*60} Start / Finish'
    return vbo


class FullSceneTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.folder=Path(temp.name)
        self.vbo=circle_vbo(self.folder);self.scene=complete_scene()
        self.clip=replace(video(self.folder/'video.mp4',duration=60),width=320,height=180)
        self.match=Match(self.vbo,self.clip,self.vbo.rows,0,0)

    def test_laps_use_direction_gate_and_interpolate_crossing_times(self):
        session=Session(self.vbo)
        self.assertEqual(len(session.crossings),3)
        for t,expected in zip(session.crossings,(5,25,45)):self.assertAlmostEqual(t-BASE,expected,places=4)
        self.assertEqual([round(l.duration,3) for l in session.laps],[20,20])

    def test_first_lap_has_no_future_best_or_delta(self):
        session=Session(self.vbo)
        first=session.lap_values(BASE+10)
        self.assertIsNone(first['best_lap']);self.assertIsNone(first['delta']);self.assertEqual(first['lap_time'],5)
        later=session.lap_values(BASE+30)
        self.assertEqual(later['best_lap'],20);self.assertAlmostEqual(later['delta'],0,places=4)

    def test_gaps_invalidate_laps_and_acceleration(self):
        self.vbo.rows=[r for r in self.vbo.rows if not BASE+14<r.utc<BASE+16]
        session=Session(self.vbo)
        self.assertEqual(len(session.laps),1)
        self.assertIsNone(session.acceleration(BASE+15))
        self.assertIsNone(session.lap_values(BASE+20)['lap_time'])

    def test_partial_video_uses_full_session_for_prior_laps(self):
        match=replace(self.match,rows=self.vbo.rows[400:501])
        renderer=Renderer(self.clip,[match],320,320,self.scene)
        session=next(iter(renderer.sessions.values()))
        self.assertEqual(session.lap_values(BASE+42)['best_lap'],20)
        self.assertIsNone(renderer.frame(39).getbbox());self.assertIsNotNone(renderer.frame(42).getbbox())

    def test_missing_gate_does_not_invent_lap_times(self):
        self.vbo.preamble=[line for line in self.vbo.preamble if not line.startswith('Start ')]
        session=Session(self.vbo)
        self.assertEqual(session.crossings,[]);self.assertIsNone(session.lap_values(BASE+30)['lap_time'])

    def test_map_preserves_ground_distance_aspect(self):
        projection=Projection(60,0)
        east=projection.point(60,100/projection.xscale)
        north=projection.point(60+100/111195.0802,0)
        transform=MapTransform([(0,0),east,north],(500,300))
        origin=transform.point((0,0));a=transform.point(east);b=transform.point(north)
        self.assertAlmostEqual(math.dist(origin,a),math.dist(origin,b),places=6)

    def test_widgets_fit_landscape_square_portrait_without_stretching(self):
        for size in ((1920,1080),(1920,1920),(1080,1920),(640,360)):
            elements,scale=fit_layout(self.scene.elements,self.scene.size,*size)
            for original,e in zip(self.scene.elements,elements):
                x,y=e['pos'];w,h=e['size']
                self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
                self.assertLessEqual(x+w,size[0]);self.assertLessEqual(y+h,size[1])
                self.assertLessEqual(abs(w-original['size'][0]*scale),.5)
                self.assertLessEqual(abs(h-original['size'][1]*scale),.5)

    def test_full_scene_includes_map_ball_and_lap_fields_but_no_pip(self):
        kinds={e['kind'] for e in self.scene.elements}
        self.assertTrue({'map','xyplot','text','bar'}<=kinds)
        self.assertEqual(set(self.scene.omitted),{'pipborder','Cam2'})
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        self.assertEqual(renderer.frame(30).size,(320,180))

    def test_full_mode_requires_a_scene(self):
        scan=Scan(self.folder,[self.clip],[self.vbo],[self.match],[],[],{})
        with self.assertRaisesRegex(TelemetryError,'Choose a VBOX scene'):
            export(scan,self.folder/'out',overlay_mode='full')
        self.assertFalse((self.folder/'out').exists())

    def test_unknown_source_and_missing_channel_are_reported_before_encoding(self):
        text=next(e for e in self.scene.elements if e['kind']=='text')
        for source,error in [('unsupported.value','Unsupported scene data source'),('can.bin[1].channel(Missing)','is missing')]:
            text['source']=(source,1)
            with self.assertRaisesRegex(TelemetryError,error):Renderer(self.clip,[self.match],320,180,self.scene)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_full_scene_export_preserves_data_audio_and_coverage(self):
        from goprovbox.vbo import write_vbo,read_vbo
        vbo=four_channel_vbo(self.folder)
        write_vbo(vbo,vbo.rows,list(range(len(vbo.rows))),'test_',vbo.path)
        vbo=read_vbo(vbo.path)
        clip=replace(self.clip,duration=3)
        run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=black:s=320x180:r=30',
             '-f','lavfi','-i','sine=frequency=500','-t','3','-c:v','libx264','-c:a','aac',str(clip.path)])
        match=Match(vbo,clip,vbo.rows[10:21],0,0)
        scan=Scan(self.folder,[clip],[vbo],[match],[],[],{p.name:fingerprint(p) for p in (clip.path,vbo.path)})
        with patch('goprovbox.overlay.load_scene',return_value=self.scene) as load:
            result=export(scan,self.folder/'out',overlay_scene=self.folder/'scene.vvhsn',overlay_mode='full',encoder='software',overlap_only=False)
            load.assert_called_once_with(self.folder/'scene.vvhsn',mode='full')
        report=json.loads((result/'report.json').read_text())
        self.assertEqual(report['settings']['overlay']['mode'],'full')
        self.assertTrue(report['settings']['overlay']['notes'])
        self.assertTrue(report['outputs'][0]['telemetry_preserved'])
        movie=result/report['media'][0]['file'];streams=probe(movie)['streams']
        self.assertEqual(next(s for s in streams if s['codec_type']=='video')['nb_frames'],'90')
        self.assertTrue(any(s['codec_type']=='audio' for s in streams))
        for time,present in [(.5,False),(1.5,True),(2.5,False)]:
            pixels=run([executable('ffmpeg'),'-v','error','-ss',str(time),'-i',str(movie),'-frames:v','1',
                        '-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
            self.assertEqual(max(pixels)>100,present)


if __name__=='__main__':unittest.main()
