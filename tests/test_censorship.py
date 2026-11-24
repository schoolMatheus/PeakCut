"""Censorship preferences, matching and the exported picture and sound."""
import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, ".")
TEMP = tempfile.TemporaryDirectory(prefix="pc-censor-")
os.environ.update(PEAKCUT_DATA=TEMP.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
from studio import accounts, auth, ctx, db, jobs, render, server  # noqa: E402


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.user = auth.local_user().id

    def test_settings_are_validated_persisted_and_private(self):
        with ctx.as_user(self.user):
            self.assertEqual(server.get_settings().get("censorship_mode"), "off")
            version = ctx.version()
            saved = server.put_settings({"censorship_mode": "both", "censorship_words": "Droga, MERDA\ndroga"})
            self.assertEqual(saved["censorship_words"], "Droga\nMERDA")
            self.assertGreater(ctx.version(), version)
            accounts._settings_mem.clear()
            self.assertEqual(server.get_settings()["censorship_mode"], "both")
            for bad in ({"censorship_mode": "unknown"}, {"censorship_words": ["bad"]},
                        {"censorship_mode": "audio", "censorship_words": "x" * 61}):
                with self.assertRaises(server.HTTPException) as caught:
                    server.put_settings(bad)
                self.assertEqual(caught.exception.status_code, 400)
                self.assertEqual(server.get_settings()["censorship_mode"], "both")
            with ctx.as_user("abcdef0123456789"):
                self.assertEqual(server.get_settings()["censorship_mode"], "off")


class MatchingTests(unittest.TestCase):
    def test_whole_words_accents_and_punctuation_without_changing_original(self):
        from studio import censorship
        text = "DROGA! Drogaria, palavrão; PALAVRAO."
        self.assertEqual(censorship.mask_text(text, "droga\npalavrão"),
                         "DR*G@! Drogaria, p@l@vr@*; P@L@VR@*.")
        self.assertEqual(censorship.mask_text(text, ""), text)
        self.assertEqual(censorship.mask_text("psst", "psst"), "****")

    def test_mute_ranges_are_relative_clamped_and_merged(self):
        from studio import censorship
        words = [{"w": "droga", "s": 9.95, "e": 10.2}, {"w": "droga!", "s": 10.25, "e": 10.4},
                 {"w": "fine", "s": 10.5, "e": 10.8}, {"w": "droga", "s": 11.9, "e": 12.5}]
        before = copy.deepcopy(words)
        spans = censorship.mute_ranges(words, "droga", 10, 12)
        self.assertEqual(len(spans), 2)
        np.testing.assert_allclose(spans, [(0, .48), (1.82, 2)])
        self.assertEqual(words, before)

    def test_accents_work_when_the_transcript_uses_combining_characters(self):
        from studio import censorship
        text = "palavra\u0303o"
        self.assertEqual(censorship.mask_text(text, "palavrão"), "p@l@vr@*")
        self.assertEqual(len(censorship.mute_ranges([{"w": text, "s": .5, "e": .8}], "palavrão", 0, 1)), 1)

    def test_list_limits_and_literal_words(self):
        from studio import censorship
        self.assertEqual(censorship.normalize_words(" Palavrão, PALAVRAO\nDroga "), "Palavrão\nDroga")
        for invalid in (".*", "two words", "word," * 2501, ",".join(f"word{i}" for i in range(501))):
            with self.assertRaises(ValueError):
                censorship.normalize_words(invalid)

    def test_filter_actually_silences_only_the_requested_audio(self):
        from studio import censorship
        filt = censorship.audio_filter([(0.5, 1.0)])
        result = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
                                 "-af", filt, "-ar", "16000", "-f", "f32le", "pipe:1"],
                                capture_output=True, check=True)
        samples = np.frombuffer(result.stdout, dtype="<f4")
        self.assertGreater(np.max(np.abs(samples[1600:6400])), .01)
        self.assertLess(np.max(np.abs(samples[9600:14400])), .00001)
        self.assertGreater(np.max(np.abs(samples[17600:20800])), .01)


class RenderTests(unittest.TestCase):
    def test_render_masks_captions_and_uses_original_speech_when_translated_or_hidden(self):
        words = [{"w": "droga!", "s": 10.5, "e": 10.8}]
        original = [{"w": "bad", "s": 11, "e": 11.3}]
        before = copy.deepcopy(words)
        clip = {"start": 10, "end": 12, "hook": "Droga!", "captions": True}
        with ctx.as_user("0123456789abcdef"), patch.object(render, "_render_in", return_value={}) as encode:
            for mode, masked, muted in (("off", False, False), ("captions", True, False),
                                         ("audio", False, True), ("both", True, True)):
                accounts.save_settings({"censorship_mode": mode, "censorship_words": "droga\nbad"})
                render.render_clip(Path("unused"), Path(TEMP.name), "test", clip, words, 1920, 1080,
                                   speech_words=original)
                ass = encode.call_args.args[-1]
                self.assertEqual("dr*g@!" in ass.lower(), masked)
                self.assertEqual(bool(encode.call_args.kwargs.get("mute")), muted)
            render.render_clip(Path("unused"), Path(TEMP.name), "test", dict(clip, captions=False), words,
                               1920, 1080, speech_words=original)
            self.assertTrue(encode.call_args.kwargs["mute"])
            self.assertNotIn(",Cap,", encode.call_args.args[-1])
        self.assertEqual(words, before)

    def test_original_speech_keeps_its_timing_in_local_sources_and_reordered_cuts(self):
        with ctx.as_user("fedcba9876543210"):
            accounts.save_settings({"censorship_mode": "both", "censorship_words": "bad"})
            job = jobs.create({"type": "file", "name": "Censorship test"}, {}, enqueue=False)
            source = Path(job["dir"]) / "source.mp4"
            source.touch()
            clip = {"id": "clip1", "start": 100., "end": 104., "src": str(source), "src_offset": 100.,
                    "status": "done", "title": "Test", "description": "", "hashtags": [], "hook": "", "score": 90,
                    "captions": False}
            job.update(source_path=str(source), duration=110., width=180, height=320, clips=[clip])
            speech = [{"w": "bad", "s": 101., "e": 101.3}]
            with patch.object(render, "_render_in", return_value={"file": "clip1.mp4"}) as encode:
                jobs.render_one(job, clip, speech)
                self.assertEqual(clip["status"], "done", clip.get("error"))
                np.testing.assert_allclose(encode.call_args.kwargs["mute"], [(.92, 1.38)])
                clip.update(segments=[[101., 102.], [100., 101.5]], start=100., end=102.)
                job["source_path"] = str(source)
                jobs.render_one(job, clip, speech)
                self.assertEqual(clip["status"], "done", clip.get("error"))
                np.testing.assert_allclose(encode.call_args.kwargs["mute"], [(0., .38), (1.92, 2.38)])

    def test_exported_single_and_joined_video_have_silent_audio(self):
        from studio import media
        with ctx.as_user("1111222233334444"), tempfile.TemporaryDirectory(prefix="pc-censor-video-") as data:
            accounts.save_settings({"censorship_mode": "both", "censorship_words": "bad"})
            source, output = Path(data) / "source.mp4", Path(data) / "out"
            media.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=180x320:d=2",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac",
                       "-t", "2", str(source)])
            clip = {"start": 0., "end": 2., "hook": "bad", "style": "clean"}
            speech = [{"w": "bad", "s": .5, "e": 1.0}]
            with patch.multiple(render, W=180, H=320, _qsv_ok=False):
                render.install_fonts()
                for name, segments in (("single", None), ("joined", [(source, 0, 1), (source, 1, 1)])):
                    result = render.render_clip(source, output, name, clip, speech, 180, 320, segments=segments,
                                                speech_words=speech)
                    self.assertIn("b@d", (output / f"{name}.ass").read_text(encoding="utf-8"))
                    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output / result["file"]),
                                              "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
                                             capture_output=True, check=True)
                    samples = np.frombuffer(decoded.stdout, dtype="<i2")
                    self.assertGreater(np.max(np.abs(samples[1600:4800])), 10)
                    self.assertLess(np.max(np.abs(samples[10400:13600])), 5)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        db.engine.dispose()
        TEMP.cleanup()
