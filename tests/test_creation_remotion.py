"""Local render boundaries: private files, credentials, cancellation and audio."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import wave
import numpy as np
from PIL import Image
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class RenderBoundary(unittest.TestCase):
    def setUp(self):
        self.render = importlib.import_module('studio.creation_remotion')

    def test_worker_does_not_inherit_credentials_or_node_injection(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'do-not-inherit', 'DATABASE_URL': 'secret',
                                     'NODE_OPTIONS': '--require=evil.js', 'PATH': 'system-path'}):
            env = self.render._worker_env()
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('DATABASE_URL', env)
        self.assertNotIn('NODE_OPTIONS', env)
        self.assertEqual(env['PATH'], 'system-path')

    def test_asset_server_serves_only_exact_job_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'allowed.png').write_bytes(b'private-media')
            (root / 'secret.txt').write_text('secret')
            with self.render._AssetServer({'clip': root / 'allowed.png'}) as server:
                allowed = server.urls['clip']
                self.assertEqual(urlopen(allowed).read(), b'private-media')
                origin = allowed.rsplit('/', 1)[0]
                for suffix in ('/', '/secret.txt', '/../secret.txt', '/%2e%2e/secret.txt'):
                    with self.subTest(suffix=suffix), self.assertRaises(HTTPError):
                        urlopen(origin + suffix)

    def test_cancelled_worker_stops_before_finishing(self):
        started = time.monotonic()
        def cancelled():
            if time.monotonic() - started > .3:
                raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            self.render._run([sys.executable, '-c', 'import time; time.sleep(30)'],
                             check_cancel=cancelled, timeout=10)
        self.assertLess(time.monotonic() - started, 5)

    def test_worker_timeout_and_failed_exit_are_actionable(self):
        with self.assertRaisesRegex(RuntimeError, 'tempo|time'):
            self.render._run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=.2)
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            self.render._run([sys.executable, '-c', 'raise RuntimeError("boom")'], timeout=5)

    def test_progress_protocol_is_bounded_and_streamed(self):
        events = []
        result = self.render._run([sys.executable, '-c',
            'import json; print(json.dumps({"type":"progress","progress":0.5})); print("done")'],
            on_event=events.append, timeout=5)
        self.assertIn('done', result)
        self.assertEqual(events, [{'type': 'progress', 'progress': .5}])

    def test_private_media_supports_browser_byte_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'video.mp4'
            path.write_bytes(b'0123456789')
            from urllib.request import Request
            with self.render._AssetServer({'clip': path}) as server:
                response = urlopen(Request(server.urls['clip'], headers={'Range': 'bytes=2-5'}))
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b'2345')
                self.assertEqual(response.headers['Content-Range'], 'bytes 2-5/10')
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(server.urls['clip'], headers={'Range': 'bytes=99-'}))
                self.assertEqual(error.exception.code, 416)

    def test_source_change_invalidates_approval_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.png'
            Image.new('RGB', (32, 32), 'red').save(path)
            timeline = {'aspect': '9:16', 'scenes': [{'duration': 1., 'media': 'image.png'}]}
            first = self.render.identity(timeline, temp)
            Image.new('RGB', (32, 32), 'blue').save(path)
            second = self.render.identity(timeline, temp)
            self.assertEqual(first['timeline_sha256'], second['timeline_sha256'])
            self.assertNotEqual(first['assets_sha256'], second['assets_sha256'])
            self.assertNotIn(str(path), json.dumps(second))

    def test_ffmpeg_ducks_music_during_absolute_narration_interval(self):
        from studio import tools
        if tools.setup():
            self.skipTest('FFmpeg is not installed')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, frequency in [('voice', 800), ('music', 200)]:
                samples = (np.sin(np.arange(96000) * (2 * np.pi * frequency / 48000)) * 6500).astype('<i2')
                with wave.open(str(root / (name + '.wav')), 'wb') as audio:
                    audio.setparams((1, 2, 48000, 0, 'NONE', ''))
                    audio.writeframes(samples.tobytes())
            timeline = {'format': {'fps': 30, 'durationInFrames': 60}, 'assets': [
                {'id': 'voice', 'file': 'voice.wav'}, {'id': 'music', 'file': 'music.wav'}], 'tracks': [
                {'id': 'voice', 'type': 'audio', 'role': 'narration', 'clips': [
                    {'id': 'speech', 'assetId': 'voice', 'startFrame': 15, 'durationInFrames': 30}]},
                {'id': 'music', 'type': 'audio', 'role': 'music', 'duckUnder': 'voice', 'clips': [
                    {'id': 'bed', 'assetId': 'music', 'startFrame': 0, 'durationInFrames': 60, 'gainDb': -6}]}]}
            output = self.render._mix(timeline, root, root, None)
            with wave.open(str(output), 'rb') as audio:
                self.assertEqual(audio.getframerate(), 48000)
                data = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').reshape((-1, 2))[:, 0]
            def amplitude(start, frequency):
                chunk = data[round(start * 48000):round((start + .2) * 48000)]
                return abs(np.sum(chunk * np.exp(-2j * np.pi * frequency * np.arange(len(chunk)) / 48000))) / len(chunk)
            self.assertEqual(len(data), 96000)
            self.assertGreater(amplitude(.1, 200), amplitude(.9, 200) * 2)
            self.assertGreater(amplitude(.9, 800), amplitude(.1, 800) * 10)

    def test_real_remotion_visual_is_muxed_once_with_audio_and_manifest(self):
        from studio import tools
        if tools.setup():
            self.skipTest('FFmpeg is not installed')
        with tempfile.TemporaryDirectory(prefix='pc-render-test-') as temp:
            root = Path(temp)
            Image.new('RGB', (120, 240), '#527a38').save(root / 'photo.png')
            samples = (np.sin(np.arange(48000) * (2 * np.pi * 500 / 48000)) * 6500).astype('<i2')
            with wave.open(str(root / 'voice.wav'), 'wb') as audio:
                audio.setparams((1, 2, 48000, 0, 'NONE', ''))
                audio.writeframes(samples.tobytes())
            timeline = {'aspect': '9:16', 'scenes': [{'id': 'CENA-1', 'duration': 1.,
                'media': 'photo.png', 'audio': 'voice.wav', 'caption': 'Uma explicação.',
                'text': 'Texto vertical', 'motion': 'zoom_in'}]}
            output = root / 'pilot.mp4'
            events = []
            result = self.render.render(timeline, root, output, height=320, on_progress=events.append)
            self.assertEqual((result['width'], result['height']), (180, 320))
            self.assertEqual(result['renderer'], 'ffmpeg_remotion')
            manifest = json.loads(output.with_suffix('.manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(manifest['verification']['fullDecode'])
            self.assertEqual(manifest['profile']['sampleRate'], 48000)
            self.assertNotIn(str(root), json.dumps(manifest))
            self.assertIn('Uma explicação.', output.with_suffix('.srt').read_text(encoding='utf-8'))
            self.assertEqual(events[-1]['progress'], 1)
            self.assertIsNotNone(manifest['verification']['audioMeasurements']['integratedLufs'])
            final = root / 'final.mp4'
            exported = self.render.render(timeline, root, final, height=1920)
            self.assertEqual((exported['width'], exported['height']), (1080, 1920))
            for field in ('timeline_sha256', 'assets_sha256', 'templates_sha256'):
                self.assertEqual(manifest[field], exported['manifest'][field])
            for movie, name in [(output, 'pilot-frame.png'), (final, 'final-frame.png')]:
                self.render._ffmpeg(['-ss', '0.5', '-i', movie, '-vf', 'scale=180:320',
                                     '-frames:v', '1', root / name], None)
            pilot_frame = np.asarray(Image.open(root / 'pilot-frame.png').convert('RGB'), dtype=float)
            final_frame = np.asarray(Image.open(root / 'final-frame.png').convert('RGB'), dtype=float)
            self.assertLess(np.mean(np.abs(pilot_frame - final_frame)), 12)

    def test_audio_loop_stays_inside_explicit_source_segment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            samples = np.concatenate([np.sin(np.arange(12000) * (2 * np.pi * hz / 48000))
                                      for hz in (300, 900)])
            with wave.open(str(root / 'source.wav'), 'wb') as audio:
                audio.setparams((1, 2, 48000, 0, 'NONE', ''))
                audio.writeframes((samples * 6500).astype('<i2').tobytes())
            timeline = {'format': {'fps': 30, 'durationInFrames': 30},
                'assets': [{'id': 'source', 'file': 'source.wav', 'kind': 'audio'}], 'tracks': [
                {'id': 'music', 'type': 'audio', 'role': 'music', 'clips': [
                    {'id': 'bed', 'assetId': 'source', 'startFrame': 0, 'durationInFrames': 30,
                     'sourceInSeconds': 0., 'sourceOutSeconds': .25, 'loop': True}]}]}
            path = self.render._mix(timeline, root, root, None)
            with wave.open(str(path), 'rb') as audio:
                data = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').reshape((-1, 2))[:, 0]
            chunk = data[30000:39600]
            amplitude = lambda hz: abs(np.sum(chunk * np.exp(-2j * np.pi * hz * np.arange(len(chunk)) / 48000)))
            self.assertGreater(amplitude(300), amplitude(900) * 10)

    def test_narration_uses_sample_precise_seconds_without_limiter_delay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with wave.open(str(root / 'voice.wav'), 'wb') as audio:
                audio.setparams((1, 2, 48000, 0, 'NONE', ''))
                samples = np.sin(np.arange(1008) * (2 * np.pi * 700 / 48000))
                audio.writeframes((samples * 6500).astype('<i2').tobytes())
            timeline = {'format': {'fps': 30, 'durationInFrames': 30},
                'assets': [{'id': 'voice', 'file': 'voice.wav', 'kind': 'audio'}], 'tracks': [
                {'id': 'voice', 'type': 'audio', 'role': 'narration', 'clips': [
                    {'id': 'speech', 'assetId': 'voice', 'startFrame': 0, 'durationInFrames': 1,
                     'audioStartSeconds': .011, 'audioDurationSeconds': .021}]}]}
            path = self.render._mix(timeline, root, root, None)
            with wave.open(str(path), 'rb') as audio:
                data = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').reshape((-1, 2))[:, 0]
            audible = np.flatnonzero(np.abs(data) > 300)
            self.assertGreaterEqual(len(audible), 900)
            self.assertLess(abs(int(audible[0]) - 528), 32)
            self.assertLess(abs(int(audible[-1]) - 1535), 32)

    def test_height_must_preserve_exact_even_9_to_16_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new('RGB', (32, 32), 'red').save(root / 'image.png')
            timeline = {'aspect': '9:16', 'scenes': [{'duration': 1., 'media': 'image.png'}]}
            with self.assertRaises(ValueError):
                self.render.render(timeline, root, root / 'bad.mp4', height=322)


if __name__ == '__main__':
    unittest.main()
