"""Build an actual short vertical movie without external services."""
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave
from PIL import Image

os.environ['PEAKCUT_DATA'] = tempfile.mkdtemp(prefix='pc-crender-')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import tools, creation_render as render


class Timeline(unittest.TestCase):
    def test_video_source_trim_transition_and_full_speech_are_preserved(self):
        tools.setup()
        from studio import media
        import json
        with tempfile.TemporaryDirectory(prefix='pc-hybrid-') as tmp:
            folder = Path(tmp)
            media.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=180x320:d=1:r=30',
                       '-f', 'lavfi', '-i', 'color=blue:s=180x320:d=1:r=30', '-filter_complex',
                       '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264',
                       '-pix_fmt', 'yuv420p', str(folder / 'source.mp4')])
            Image.new('RGB', (180, 320), '#00aa00').save(folder / 'second.png')
            for name in ('one', 'two'):
                with wave.open(str(folder / (name + '.wav')), 'wb') as audio:
                    audio.setparams((1, 2, 48000, 0, 'NONE', ''))
                    audio.writeframes(b''.join(struct.pack('<h', int(6000 * math.sin(i * .065))) for i in range(48000)))
            timeline = {'aspect': '9:16', 'scenes': [
                {'id': 'CENA-001', 'duration': 1., 'media': 'source.mp4', 'start': 1., 'audio': 'one.wav'},
                {'id': 'CENA-002', 'duration': 1., 'media': 'second.png', 'audio': 'two.wav',
                 'transition': 'crossfade', 'transition_duration': .2,
                 'overlays': [{'text': 'Duas cenas', 'template_id': 'cta_v1', 'at': .3, 'duration': .5, 'x': .5, 'y': .5}]}]}
            out = folder / 'hybrid.mp4'
            result = render.render(timeline, folder, out, height=320)
            self.assertAlmostEqual(result['duration'], 2., delta=.08)
            canonical = json.loads(out.with_suffix('.timeline.json').read_text(encoding='utf-8'))
            self.assertEqual(canonical['format']['durationInFrames'], 60)
            self.assertEqual(canonical['transitions'][0]['startFrame'], 30)
            media.run(['ffmpeg', '-y', '-v', 'error', '-ss', '0.4', '-i', str(out),
                       '-frames:v', '1', str(folder / 'blue.png')])
            pixel = Image.open(folder / 'blue.png').convert('RGB').getpixel((90, 160))
            self.assertGreater(pixel[2], pixel[0] * 3, 'Video must use the chosen source time, not the beginning')
            media.run(['ffmpeg', '-y', '-v', 'error', '-ss', '1.85', '-i', str(out), '-t', '0.1',
                       '-vn', '-ac', '1', '-ar', '48000', str(folder / 'tail.wav')])
            with wave.open(str(folder / 'tail.wav'), 'rb') as audio:
                raw = audio.readframes(audio.getnframes())
            values = struct.unpack('<' + 'h'*(len(raw)//2), raw)
            self.assertGreater(sum(v*v for v in values)/len(values), 10000,
                               'Transition overlap must not cut the end of the narration')

    def test_render_actual_vertical_file_and_subtitles(self):
        tools.setup()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            Image.new('RGB', (240, 120), '#527a38').save(folder / 'photo.png')
            with wave.open(str(folder / 'voice.wav'), 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', ''))
                audio.writeframes(b''.join(struct.pack('<h', int(3000 * math.sin(i * 0.1))) for i in range(16000)))
            timeline = {'aspect': '9:16', 'scenes': [
                {'id': 'CENA-001', 'duration': 1.0, 'media': 'photo.png', 'audio': 'voice.wav',
                 'text': 'Texto vertical', 'caption': 'Uma explicação.', 'motion': 'zoom_in', 'transition': 'fade'}],
                'tracks': [{'file':'voice.wav','at':0,'volume':.1,'duration':1,'kind':'music','loop':True}]}
            out = folder / 'pilot.mp4'
            render.render(timeline, folder, out, height=320)
            info = render.inspect(out)
            self.assertEqual(info['width'] * 16, info['height'] * 9)
            self.assertTrue(info['audio'])
            self.assertGreater(info['duration'], 0.9)
            self.assertTrue(out.with_suffix('.srt').is_file())

    def test_invalid_timelines_and_paths_fail_before_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            for scene in [{'duration': -1, 'media': 'x.png'}, {'duration': 1, 'media': '../private.png'},
                          {'duration': float('nan'), 'media': 'x.png'}, {'duration': 1, 'media': 'x.png', 'motion': 'shell'}]:
                with self.subTest(scene=scene), self.assertRaises(ValueError):
                    render.validate({'aspect': '9:16', 'scenes': [scene]}, Path(tmp))
            with self.assertRaises(ValueError):
                render.validate({'aspect': '16:9', 'scenes': []}, Path(tmp))

    def test_playlists_disguised_as_media_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'pretend.mp4'
            path.write_text('#EXTM3U\nhttp://127.0.0.1/private\n')
            with self.assertRaises(ValueError):
                render.validate_asset(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
