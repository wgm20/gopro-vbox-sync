"""Audio timing is checked against decoded sound, including ends and seams."""
from array import array
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import json
import math
import sys
import time
import unittest
import wave

from goprovbox.engine import Scan, encode, export, fingerprint, intersections
from goprovbox.gpmf import TelemetryError
from goprovbox.media import executable, probe, run
from goprovbox.sound import RATE, AudioPlan, AudioSpan, attach_audio, fit_audio_clock, plan_audio, render_audio, _process
from goprovbox.trimming import video_parts
from goprovbox.vbo import read_vbo, write_vbo
from tests.test_core import BASE, fixture, video
from tests.test_trimming import timestamps


def samples(path, start, duration):
    return array('f', run([executable('ffmpeg'), '-v', 'error', '-i', str(path), '-ss', str(start),
                          '-t', str(duration), '-map', '0:a:0', '-ac', '1', '-ar', str(RATE),
                          '-f', 'f32le', 'pipe:1']))


def correlation(a, b):
    n = min(len(a), len(b)); a, b = a[:n], b[:n]
    return sum(x*y for x, y in zip(a, b)) / math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))


class SoundTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)
        self.gopro = self.folder/'GX010001.mp4'
        self.recording(self.gopro, 8, 'sin(2*PI*900*t)')
        self.clip = replace(video(self.gopro, duration=8), width=160, height=160)

    def recording(self, path, duration, expression=None, offset=0, audio_delay=0, audio_clock=1):
        args = [executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=160x160:r=30']
        if expression:
            args += ['-itsoffset', str(audio_delay), '-f', 'lavfi', '-i', f'aevalsrc={expression}:s=48000', '-c:a', 'alac']
            if audio_clock != 1: args += ['-af', f'asetpts=PTS*{audio_clock}']
        args += ['-t', str(duration), '-c:v', 'libx264', '-output_ts_offset', str(offset), str(path)]
        run(args)

    def vbo(self, start=0, stop=7000, *, offset=2, drift=1, prefix='VBOX0001_', chapter=None):
        vbo = fixture(self.folder/(prefix.rstrip('_')+'.vbo'), timestamps(start, stop))
        indices = [1 if chapter is None or r.utc-BASE < chapter else 2 for r in vbo.rows]
        times = [offset+drift*(r.utc-BASE) if i == 1 else .5+drift*(r.utc-BASE-chapter)
                 for r, i in zip(vbo.rows, indices)]
        write_vbo(vbo, vbo.rows, times, prefix, vbo.path, indices)
        return read_vbo(vbo.path)

    def scan(self, vbo, clip=None):
        clip = clip or self.clip
        return Scan(self.folder, [clip], [vbo], intersections(vbo, clip), [], [],
                    {p.name:fingerprint(p) for p in (clip.path, vbo.path)})

    def test_default_prefers_vbox_with_prebuffer_and_exact_trimmed_audio_tail(self):
        original = self.folder/'VBOX0001_0001.mp4'
        self.recording(original, 12, 'sin(2*PI*(200*t+20*t*t))')
        vbo = self.vbo(2017, 4117)
        scan = self.scan(vbo)
        result = export(scan, self.folder/'out', encoder='software')
        report = json.loads((result/'report.json').read_text()); media = report['media'][0]
        self.assertEqual(report['settings']['audio_source'], 'vbox')
        self.assertEqual({s['kind'] for s in media['audio']['plan']['segments']}, {'vbox'})
        self.assertAlmostEqual(media['audio']['plan']['segments'][0]['source_start_seconds'], 4, places=4)
        movie = result/media['file']
        for time, length in ((.3, .3), (1.75, .3)):
            self.assertGreater(correlation(samples(original, 4+time, length), samples(movie, time, length)), .98)
        self.assertLess(abs(media['audio']['duration_seconds']-media['range']['duration_seconds']), .002)
        self.assertTrue(report['outputs'][0]['telemetry_preserved'])
        self.assertFalse(list(result.glob('*.wav')))
        self.assertEqual(export(scan, result, encoder='software'), result)
        with self.assertRaisesRegex(TelemetryError, 'already exists'):
            export(scan, result, encoder='software', audio_source='gopro')

    def test_nonzero_video_timestamp_and_audio_start_are_retained(self):
        original = self.folder/'VBOX0001_0001.mp4'
        self.recording(original, 12, 'sin(2*PI*(250*t+25*t*t))', offset=5, audio_delay=.123)
        vbo = self.vbo(2017, 4117); scan = self.scan(vbo)
        part = video_parts(self.clip, scan.matches)[0]
        plan = plan_audio(part.video, scan.matches)
        self.assertAlmostEqual(plan.spans[0].source_start, 9, places=4)
        wav = self.folder/'aligned.wav'; render_audio(plan, wav, Event())
        # ffmpeg -ss after -i is relative to format start (5 seconds).
        self.assertGreater(correlation(samples(original, 4.4, .4), samples(wav, .4, .4)), .999)

    def test_full_video_falls_back_for_gaps_and_missing_vbox_chapter(self):
        original = self.folder/'VBOX0001_0001.mp4'
        self.recording(original, 8, 'sin(2*PI*500*t)')
        vbo = self.vbo(1000, 6000, offset=1, chapter=4); scan = self.scan(vbo)
        plan = plan_audio(self.clip, scan.matches)
        self.assertEqual([s.kind for s in plan.spans], ['gopro', 'vbox', 'gopro'])
        self.assertTrue(any('0002.mp4 is missing' in n for n in plan.notes))
        wav = self.folder/'gaps.wav'; render_audio(plan, wav, Event())
        for start in (.1, 4.5, 7):
            # Chapter boundaries can fall between PCM samples; allow one sample
            # of rounding while still rejecting a shifted/replaced waveform.
            a, b = samples(self.gopro, start, .25), samples(wav, start, .25)
            self.assertGreater(max(correlation(a, b), correlation(a[1:], b), correlation(a, b[1:])), .995)
        a, b = samples(original, 3, .25), samples(wav, 2, .25)
        self.assertGreater(max(correlation(a, b), correlation(a[1:], b), correlation(a, b[1:])), .995)

    def test_multiple_original_chapters_keep_their_own_offsets(self):
        first, second = [self.folder/f'VBOX0001_{i:04d}.mp4' for i in (1,2)]
        self.recording(first, 8, 'sin(2*PI*(210*t+17*t*t))')
        self.recording(second, 8, 'sin(2*PI*(310*t+23*t*t))')
        vbo = self.vbo(offset=1, chapter=4); scan = self.scan(vbo)
        plan = plan_audio(self.clip, scan.matches)
        wav = self.folder/'chapters.wav'; render_audio(plan, wav, Event())
        for source, reference, output in ((first, 4.8, 3.8), (second, .55, 4.05), (second, 2.5, 6)):
            self.assertGreater(correlation(samples(source, reference, .1), samples(wav, output, .1)), .995)
        self.assertEqual(len(plan.clocks), 2)

    def test_unreliable_timestamps_use_gopro_instead_of_guessing(self):
        original = self.folder/'VBOX0001_0001.mp4'
        self.recording(original, 15, 'sin(2*PI*500*t)')
        vbo = self.vbo(drift=1.1); scan = self.scan(vbo)
        plan = plan_audio(self.clip, scan.matches)
        self.assertEqual({s.kind for s in plan.spans}, {'gopro'})
        self.assertTrue(any('unreliable' in n for n in plan.notes))

    def test_missing_audio_is_silent_only_where_both_sources_lack_sound(self):
        self.recording(self.gopro, 8)
        vbo = self.vbo(); scan = self.scan(vbo)
        plan = plan_audio(self.clip, scan.matches)
        self.assertEqual({s.kind for s in plan.spans}, {'silence'})
        wav = self.folder/'silence.wav'; render_audio(plan, wav, Event())
        with wave.open(str(wav)) as reader:
            self.assertEqual(reader.getnframes(), 8*RATE)
            self.assertFalse(any(reader.readframes(8*RATE)))

    def test_changed_sources_and_cancellation_stop_sound_preparation(self):
        vbo = self.vbo(); scan = self.scan(vbo)
        plan = plan_audio(self.clip, scan.matches)
        event = Event(); event.set()
        with self.assertRaises(InterruptedError): render_audio(plan, self.folder/'cancelled.wav', event)
        self.gopro.touch()
        with self.assertRaisesRegex(TelemetryError, 'changed'):
            render_audio(plan, self.folder/'changed.wav', Event())

    def test_clock_fit_handles_quantisation_and_rejects_jumps(self):
        vbo = self.vbo(stop=59000, drift=1.0018)
        ti = vbo.index('avitime')
        for row in vbo.rows:
            row.values[ti] = str(round(float(row.values[ti])/33.333)*33.333)
        clock = fit_audio_clock(vbo.rows, ti)
        self.assertAlmostEqual(clock.rate, 1.0018, delta=.0001)
        self.assertLess(clock.uncertainty, .02)
        vbo.rows[100].values[ti] = '900000'
        with self.assertRaises(TelemetryError): fit_audio_clock(vbo.rows, ti)

    def test_overlapping_vbox_sound_sources_are_not_mixed(self):
        a, b = self.vbo(prefix='VBOX0001_'), self.vbo(prefix='VBOX0002_')
        for name in ('VBOX0001_0001.mp4','VBOX0002_0001.mp4'):
            self.recording(self.folder/name, 12, 'sin(2*PI*500*t)')
        with self.assertRaisesRegex(TelemetryError, 'More than one'):
            plan_audio(self.clip, intersections(a, self.clip)+intersections(b, self.clip))

    def test_attaching_sound_preserves_all_compressed_video_packets(self):
        movie = self.folder/'video.mp4'
        clip = replace(self.clip, duration=2)
        encode(clip, movie, 0, 160, 'software', lambda _:None, lambda _:None, Event(), source_start=1, mute=True)
        def packets():
            return json.loads(run([executable('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                '-show_packets', '-show_data_hash', 'sha256', '-show_entries', 'packet=pts,dts,duration,data_hash',
                '-of', 'json', str(movie)]))['packets']
        before = packets()
        wav = self.folder/'sound.wav'
        plan = AudioPlan(2*RATE, [AudioSpan('gopro',self.gopro,0,2*RATE,1)], {})
        render_audio(plan, wav, Event()); checked = attach_audio(movie, wav, 2, Event())
        self.assertEqual(packets(), before)
        self.assertEqual(checked['duration_seconds'], 2)
        self.assertGreater(correlation(samples(self.gopro, 2.65, .3), samples(movie, 1.65, .3)), .98)

    def test_drift_correction_keeps_distinct_pulses_aligned_at_both_ends(self):
        # Over 40 seconds these clocks would accumulate 80ms of error if either
        # slope were ignored. Distinct pulses provide an unambiguous time check.
        original = self.folder/'VBOX0001_0001.mp4'
        points = (5.123, 21.287, 43.579)
        expression = '+'.join(f'.7*exp(-((t-{p})/.001)^2)' for p in points)
        self.recording(original, 48, expression)
        self.recording(self.gopro, 48, 'sin(2*PI*900*t)')
        clip = replace(self.clip, duration=48, clock=replace(self.clip.clock, rate=1.0007))
        vbo = self.vbo(1000, 45000, offset=2, drift=1.0013)
        scan = self.scan(vbo, clip); part = video_parts(clip, scan.matches)[0]
        plan = plan_audio(part.video, scan.matches)
        self.assertEqual({s.kind for s in plan.spans}, {'vbox'})
        wav = self.folder/'drift.wav'; render_audio(plan, wav, Event())
        for point in points:
            expected = ((point-2)/1.0013)/1.0007 - part.start
            block = samples(wav, expected-.04, .08)
            peak = max(range(len(block)), key=lambda n: abs(block[n]))
            self.assertLess(abs(peak/RATE-.04), .001, (point, peak/RATE))

    def test_source_change_during_encode_prevents_publication(self):
        original = self.folder/'VBOX0001_0001.mp4'
        self.recording(original, 12, 'sin(2*PI*500*t)')
        scan = self.scan(self.vbo(2017, 4117))
        def changed_encode(*args, **kwargs):
            result = encode(*args, **kwargs)
            original.touch()
            return result
        with patch('goprovbox.engine.encode', side_effect=changed_encode):
            with self.assertRaisesRegex(TelemetryError, 'Sound source changed during export'):
                export(scan, self.folder/'out', encoder='software')
        self.assertFalse((self.folder/'out').exists())

    def test_audio_packet_clock_drift_is_corrected_as_well_as_video_clock(self):
        original = self.folder/'VBOX0001_0001.mp4'
        points = (5.123, 21.287, 43.579)
        expression = '+'.join(f'.7*exp(-((t-{p})/.001)^2)' for p in points)
        self.recording(original, 48, expression, audio_clock=.9996)
        wav = self.folder/'packet-drift.wav'
        plan = AudioPlan(42*RATE, [AudioSpan('vbox',original,0,42*RATE,3)], {})
        render_audio(plan, wav, Event())
        for point in points:
            expected = point*.9996 - 3
            block = samples(wav, expected-.04, .08)
            peak = max(range(len(block)), key=lambda n: abs(block[n]))
            self.assertLess(abs(peak/RATE-.04), .001, (point, peak/RATE))

    def test_cancellation_interrupts_a_running_audio_process(self):
        event = Event(); began = time.monotonic()
        command = [sys.executable, '-u', '-c',
                   'import sys,time; sys.stdout.buffer.write(bytes(65536)); sys.stdout.flush(); time.sleep(60)']
        with self.assertRaises(InterruptedError):
            _process(command, self.folder/'cancel.log', event, lambda _: event.set())
        self.assertLess(time.monotonic()-began, 5)

    def test_explicit_gopro_choice_retains_camera_sound(self):
        self.recording(self.folder/'VBOX0001_0001.mp4', 12, 'sin(2*PI*500*t)')
        result = export(self.scan(self.vbo(2017, 4117)), self.folder/'out',
                        encoder='software', audio_source='gopro')
        report = json.loads((result/'report.json').read_text())
        self.assertEqual(report['media'][0]['audio']['source'], 'gopro')
        self.assertGreater(correlation(samples(self.gopro, 2.4, .3),
                                       samples(result/report['media'][0]['file'], .4, .3)), .98)


if __name__ == '__main__': unittest.main()
