from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import math
import json
import shutil
import unittest
from tests.export_case import ExportTestCase
from goprovbox.reports import export_report_path
import xml.etree.ElementTree as ET

from goprovbox.lapdata import Session,Projection
from goprovbox.fullscene import parse_full_scene,fit_layout,MapTransform,dial_frame,scene_source
from goprovbox.overlay import Renderer,parse_scene
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


def dial_members(rotation='clockwise',arc='(-60,60)'):
    """Tiny delivered needle frames make position and range errors visible."""
    members=scene_members();root=ET.fromstring(members['SCENE.XML'])
    graphics=root.find('.//graphics');graphics.clear()
    layout=root.find('.//layout');layout.clear();layout.set('format','(320,180)')
    ET.SubElement(root.find('.//images'),'image',name='dial',path='dial.png')
    members['dial.png']=png((40,40),(10,10,10,255))
    needles=ET.SubElement(root.find('.//scene'),'needles')
    needle=ET.SubElement(needles,'needle',name='hand',images='needles/hand')
    needle.text='(-60,-12,0),(0,0,-12),(60,12,0)'
    for angle,colour in ((-60,(255,0,0,255)),(0,(0,255,0,255)),(60,(0,0,255,255))):
        members[f'needles/hand/{angle}.png']=png((3,3),colour)
    ET.SubElement(graphics,'dialgauge',name='speed dial',datasource='vbox.speed_gnd_mps scaling=3.6',
                  background='dial',range='(0,100)',arc=arc,rotation=rotation,rotate_point='(20,20)',
                  number_of_rotations='1',needle='hand')
    ET.SubElement(layout,'graphic',name='speed dial',screen_pos='(20,20)')
    members['SCENE.XML']=ET.tostring(root)
    return members


def resultant_formula(first='vbox.lat_acc_smth_mps2 scaling=0.1',second='vbox.lng_acc_smth_mps2 scaling=0.1'):
    return 'maths.eqn{Sqrt((a**2)+(b**2));ds:a{'+first+'};ds:b{'+second+'}}'


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


class FullSceneTests(ExportTestCase):
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

    def test_track_is_gold_without_dark_border_and_keeps_scene_marker(self):
        # A track-only scene isolates map pixels from white text and gauge artwork.
        self.scene.elements=[e for e in self.scene.elements if e['kind']=='map']
        for size in ((1920,1080),(1920,1920),(1080,1920),(640,360)):
            with self.subTest(size=size):
                renderer=Renderer(self.clip,[self.match],*size,self.scene)
                frame=renderer.frame(30)
                colours={colour for count,colour in frame.getcolors(frame.width*frame.height) if colour[3]}
                self.assertIn((255,170,0,255),colours)
                self.assertIn((255,0,0,255),colours)
                self.assertFalse(any(r==240 and g==249 and b==255 for r,g,b,a in colours))
                self.assertFalse(any(r<30 and g<30 and b<40 for r,g,b,a in colours))

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

    def test_dial_scene_uses_only_referenced_channels_and_interpolates_needle_position(self):
        members=dial_members();scene=parse_full_scene(members)
        self.assertEqual(scene.bindings,{})
        with self.assertRaisesRegex(TelemetryError,'dialgauge widget is not supported'):
            parse_scene(members)
        rows=[]
        for utc,speed in ((BASE,40),(BASE+.1,60)):
            values=self.vbo.rows[0].values[:12].copy();values[4]=str(speed)
            rows.append(Row(utc,values))
        # No RPM, throttle or brake columns exist in this speed-only recording.
        vbo=replace(self.vbo,columns=self.vbo.columns[:12],rows=rows)
        renderer=Renderer(self.clip,[replace(self.match,vbo=vbo,rows=rows)],320,180,scene)
        frame=renderer.frame(.05)
        self.assertIn((0,255,0,255),{colour for count,colour in frame.getcolors(frame.width*frame.height)})
        needle=renderer.elements[0]
        angle,image,offset=renderer.needle_cache['speed dial']
        self.assertEqual(angle,0)
        self.assertEqual(offset,tuple(round(v*renderer.scale) for v in (20,8)))
        self.assertEqual(frame.getpixel((needle['pos'][0]+offset[0]+1,needle['pos'][1]+offset[1]+1)),(0,255,0,255))
        self.assertIsNone(dial_frame(needle,None))
        self.assertEqual(dial_frame(needle,-50),-60)
        self.assertEqual(dial_frame(needle,150),60)

    def test_dial_clockwise_anticlockwise_and_wrapped_arcs(self):
        clockwise=parse_full_scene(dial_members()).elements[0]
        anticlockwise=parse_full_scene(dial_members('anticlockwise','(60,-60)')).elements[0]
        self.assertEqual([dial_frame(clockwise,v) for v in (0,50,100)],[-60,0,60])
        self.assertEqual([dial_frame(anticlockwise,v) for v in (0,50,100)],[60,0,-60])
        wrapped=dict(clockwise,arc_start=150,sweep=60,frames={150:None,-180:None,-150:None})
        self.assertEqual([dial_frame(wrapped,v) for v in (0,50,100)],[150,-180,-150])

    def test_dial_invalid_geometry_missing_frames_and_unbounded_frame_sets_are_rejected(self):
        for attributes in ({'range':'(1,1)'},{'arc':'(0,0)'},{'number_of_rotations':'2'},
                           {'rotate_point':'(100,100)'},{'rotation':'sideways'}):
            members=dial_members();root=ET.fromstring(members['SCENE.XML'])
            root.find('.//dialgauge').attrib.update(attributes);members['SCENE.XML']=ET.tostring(root)
            with self.subTest(attributes=attributes),self.assertRaises(TelemetryError):
                parse_full_scene(members)
        members=dial_members();del members['needles/hand/0.png']
        with self.assertRaisesRegex(TelemetryError,'0.png'):
            parse_full_scene(members)
        for frames in ('(0,0,-12),(0,0,-12)',','.join('(0,0,0)' for _ in range(362)),
                       '(0,-200,0)','not frame metadata'):
            members=dial_members();root=ET.fromstring(members['SCENE.XML'])
            root.find('.//needle').text=frames;members['SCENE.XML']=ET.tostring(root)
            with self.subTest(frames=frames[:40]),self.assertRaises(TelemetryError):
                parse_full_scene(members)

    def test_old_lap_sources_keep_current_previous_and_past_best_distinct(self):
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        session=next(iter(renderer.sessions.values()))
        for seconds,current,previous,best in ((0,None,None,None),(10,1,None,None),(30,2,1,20000)):
            utc=BASE+seconds;lap=session.lap_values(utc)
            with self.subTest(seconds=seconds):
                self.assertEqual(renderer.source_value(('laptiming.lap_number',1),session,utc,{},lap),current)
                self.assertEqual(renderer.source_value(('lapresults.lap.previous.lap_number',1),session,utc,{},lap),previous)
                value=renderer.source_value(('lapresults.lap.best.lap_time_ms',1),session,utc,{},lap)
                if best is None:self.assertIsNone(value)
                else:self.assertAlmostEqual(value,best,places=4)

    def test_generic_bar_reads_recorded_satellites_without_four_channel_requirement(self):
        members=dial_members();root=ET.fromstring(members['SCENE.XML'])
        gauge=root.find('.//dialgauge');gauge.tag='multibar';gauge.set('datasource','vbox.sats_used')
        ET.SubElement(gauge,'graphics_bar',range='(0,24)',size='(10,40)',offset='(0,0)',
                      colour='(255,255,0,255)',barmode='bottom_top_min')
        members['SCENE.XML']=ET.tostring(root);scene=parse_full_scene(members)
        self.assertEqual(scene.bindings,{})
        renderer=Renderer(self.clip,[self.match],320,180,scene)
        frame=renderer.frame(30)
        self.assertIn((255,0,255,255),{colour for count,colour in frame.getcolors(frame.width*frame.height)})

    def test_braking_g_bar_fills_from_right_at_maximum_toward_negative_minimum(self):
        members=dial_members();root=ET.fromstring(members['SCENE.XML'])
        gauge=root.find('.//dialgauge');gauge.tag='multibar';gauge.set('datasource','can.bin[0].channel(Acceleration)')
        ET.SubElement(gauge,'graphics_bar',range='(-1.5,0)',size='(20,10)',offset='(5,5)',
                      colour='(255,255,0,0)',barmode='left_right_max')
        members['SCENE.XML']=ET.tostring(root);scene=parse_full_scene(members)
        self.vbo.columns.append('acceleration')
        for row in self.vbo.rows:row.values.append('0')
        for value,left_filled,right_filled in ((1,False,False),(0,False,False),(-.75,False,True),(-1.5,True,True),(-2,True,True)):
            for row in self.vbo.rows:row.values[-1]=str(value)
            renderer=Renderer(self.clip,[self.match],320,180,scene)
            frame=renderer.frame(30);element=renderer.elements[0]
            bx,by,w,h=(round(v*renderer.scale) for v in (5,5,20,10));x,y=element['pos']
            with self.subTest(acceleration=value):
                self.assertEqual(frame.getpixel((x+bx+1,y+by+h//2))==(255,0,0,255),left_filled)
                self.assertEqual(frame.getpixel((x+bx+w-2,y+by+h//2))==(255,0,0,255),right_filled)

    def test_resultant_formula_uses_only_whitelisted_scaled_recorded_acceleration(self):
        parsed=scene_source(resultant_formula())
        self.assertEqual(parsed,('_resultant_acceleration',.1))
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        known=SimpleNamespace(acceleration=lambda utc,lateral=False:3 if lateral else 4)
        self.assertAlmostEqual(renderer.source_value(parsed,known,BASE,{},{}),.5)
        gap=SimpleNamespace(acceleration=lambda utc,lateral=False:None if lateral else 4)
        self.assertIsNone(renderer.source_value(parsed,gap,BASE,{},{}))
        # Different declarations, executable text and other formula shapes are not interpreted.
        for expression in (resultant_formula(second='vbox.speed_gnd_mps scaling=0.1'),
                           resultant_formula(second='vbox.lng_acc_smth_mps2 scaling=2'),
                           resultant_formula().replace('Sqrt','__import__'),
                           resultant_formula().replace('a**2','a**3'),
                           resultant_formula().replace(';ds:b',';ds:a')):
            with self.subTest(expression=expression),self.assertRaises(TelemetryError):
                scene_source(expression)

    def test_resultant_formula_scene_parses_and_propagates_telemetry_gaps(self):
        members=scene_members();root=ET.fromstring(members['SCENE.XML'])
        root.find(".//text[@name='speed']").set('datasource',resultant_formula())
        root.find('.//graphics').remove(root.find(".//text[@name='laps']"))
        root.find('.//layout').remove(root.find(".//graphic[@name='laps']"))
        members['SCENE.XML']=ET.tostring(root)
        scene=parse_full_scene(members)
        element=next(e for e in scene.elements if e['name']=='speed')
        self.assertEqual(element['source'],('_resultant_acceleration',.1))
        renderer=Renderer(self.clip,[self.match],320,180,scene)
        session=next(iter(renderer.sessions.values()))
        self.assertIsNotNone(renderer.source_value(element['source'],session,BASE+30,{},{}))
        session.gaps.append(BASE+30)
        self.assertIsNone(renderer.source_value(element['source'],session,BASE+30,{},{}))

    def test_scene_clock_time_hms_formats_centiseconds_and_midnight(self):
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        element=next(e for e in renderer.elements if e['name']=='rpm')
        element=dict(element,fmtfn='time_hms',size=(200,20))
        for value,expected in ((3723450,'01:02:03.45'),(86399999,'00:00:00.00'),(None,'--:--:--.--')):
            with self.subTest(value=value):
                renderer.text_image(element,value)
                self.assertEqual(renderer.text_cache[element['name']][0],expected)

    def test_integer_readouts_round_like_recorder_without_changing_timeline(self):
        renderer=Renderer(self.clip,[self.match],320,180,self.scene)
        before=renderer.timeline.at(BASE+30).copy()
        element=next(e for e in renderer.elements if e['name']=='rpm')
        for value,expected in ((6079.75,'6080'),(6151.5,'6152'),(6103.49,'6103')):
            with self.subTest(value=value):
                renderer.text_image(element,value)
                self.assertEqual(renderer.text_cache['rpm'][0],expected)
        element=next(e for e in renderer.elements if e['name']=='speed')
        for value,expected in ((110.899,'111'),(111.66,'112'),(110.43,'110'),(None,'--')):
            with self.subTest(value=value):
                renderer.text_image(element,value)
                self.assertEqual(renderer.text_cache['speed'][0],expected)
        self.assertEqual(renderer.timeline.at(BASE+30),before)

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
        for row,position in zip(vbo.rows,self.vbo.rows):
            for column in ('lat','long'):
                row.values[vbo.index(column)]=position.values[self.vbo.index(column)]
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
        report=json.loads(export_report_path(result, "report.json").read_text())
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
            if present:
                # Check the actual encoded/decoded video, allowing H.264 colour loss.
                gold=sum(r>210 and 125<g<205 and b<55 for r,g,b in zip(pixels[0::3],pixels[1::3],pixels[2::3]))
                self.assertGreater(gold,20)


if __name__=='__main__':unittest.main()
