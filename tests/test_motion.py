"""YouTube motions share a safe timeline with their quiet sound effects."""
import os
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, ".")
TEMP = tempfile.TemporaryDirectory(prefix="pc-motion-test-")
os.environ.update(PEAKCUT_DATA=TEMP.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
from studio import interactions, render, tools  # noqa: E402

ACTIONS = ["subscribe", "like", "comment", "share", "save"]


class MotionTests(unittest.TestCase):
    def test_empty_selection_has_no_motion_or_sound(self):
        self.assertEqual(interactions.plan([], 30, 300, 1380, "pt-BR"), [])

    def test_short_motions_keep_every_sound_inside_their_own_slot(self):
        for duration in (3, 5, 15, 60, 180):
            motions = interactions.plan(ACTIONS, duration, 300, 1380, "pt-BR")
            self.assertEqual([m["action"] for m in motions], ACTIONS)
            for i, m in enumerate(motions):
                self.assertTrue(0 <= m["start"] < m["end"] <= duration)
                if i:
                    self.assertLessEqual(motions[i - 1]["end"], m["start"])
                self.assertEqual(m["sounds"][0]["name"], "woosh")
                self.assertAlmostEqual(m["sounds"][0]["start"], m["start"])
                for sound in m["sounds"]:
                    self.assertEqual(sound["gain"], .6)
                    self.assertTrue(m["start"] <= sound["start"] < sound["end"] <= m["end"])

    def test_only_comment_uses_keyboard_and_every_action_has_a_click(self):
        motions = interactions.plan(ACTIONS, 30, 300, 1380, "en")
        for m in motions:
            sounds = [s["name"] for s in m["sounds"]]
            self.assertIn("click", sounds)
            self.assertEqual("typing" in sounds, m["action"] == "comment")

    def test_all_assets_ship_and_language_falls_back_without_using_a_path(self):
        for language in ("en", "pt-BR", "../../outside"):
            motions = interactions.plan(ACTIONS, 30, 300, 1380, language)
            for m in motions:
                self.assertTrue(m["asset"].is_file(), str(m["asset"]))
                self.assertEqual(m["asset"].parent.name, "pt-BR" if language == "pt-BR" else "en")
                self.assertTrue(m["asset"].with_suffix(".json").is_file())

    def test_each_layout_reserves_the_entire_artwork_height(self):
        for layout in render.LAYOUTS:
            for width, height in ((1920, 1080), (1080, 1920), (1000, 1000)):
                _, _, hook_y, cap_y = render.geometry(width, height, layout)
                m = interactions.plan(["like"], 8, hook_y, cap_y, "en")[0]
                center = m["y"] + interactions.HEIGHT / 2
                self.assertGreaterEqual(m["y"], m.get("caption_y", cap_y) + 124)
                self.assertGreaterEqual(m["y"], 1600)
                self.assertGreaterEqual(abs(center - hook_y), 300)
                self.assertTrue(0 <= m["y"] < m["y"] + interactions.HEIGHT <= 1880)

    def test_motion_and_sound_are_one_and_a_half_times_faster(self):
        m = interactions.plan(["like"], 8, 300, 1380, "pt-BR")[0]
        self.assertAlmostEqual(m["start"], 2.)
        self.assertAlmostEqual(m["end"] - m["start"], 4 / 1.5)
        self.assertAlmostEqual(m["rate"], 1 / 1.5)
        click = m["sounds"][-1]
        self.assertAlmostEqual(click["start"], 2 + 1.6 / 1.5)
        self.assertEqual(click["gain"], .6)

    def test_low_captions_reserve_space_only_when_a_motion_is_selected(self):
        positions = []
        for actions in ([], ["like"]):
            with patch.object(render, "_render_in", return_value={}) as encode:
                render.render_clip(Path(TEMP.name) / "unused.mp4", Path(TEMP.name) / "caption-check", "test",
                                   {"start": 0., "end": 8., "layout": "zoom", "style": "hype", "interactions": actions},
                                   [{"w": "caption", "s": 1., "e": 2.}], 1920, 1080)
            ass = encode.call_args.args[8]
            events = [line for line in ass.splitlines() if line.startswith("Dialogue:") and ",Cap," in line]
            y = max(float(re.search(r"\\pos\([^,]+,([^\)]+)\)", line)[1]) for line in events)
            positions.append(y)
            if actions:
                motion = encode.call_args.kwargs["motions"][0]
                self.assertLessEqual(y + 100, motion["y"] - 24)
        self.assertGreater(positions[0], 1600)
        self.assertLess(positions[1], 1520)


class MotionRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if tools.setup():
            raise unittest.SkipTest("ffmpeg is required for the actual picture and sound checks")
        cls.source = Path(TEMP.name) / "source.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x24324A:s=320x180:r=30:d=8", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", "8", "-c:v", "libx264", "-c:a", "aac", str(cls.source)], check=True)
        render.install_fonts()

    def make_clip(self, actions, name, joined=False, duration=4., source=None):
        clip = {"start": 0., "end": duration, "layout": "crop", "captions": False,
                "caption_language": "pt-BR", "interactions": actions}
        sequence = [(self.source, 2., 2.), (self.source, 0., 2.)] if joined else None
        with patch.object(render, "_qsv_ok", False):
            render.render_clip(source or self.source, Path(TEMP.name), name, clip, [], 320, 180, segments=sequence)
        return Path(TEMP.name) / f"{name}.mp4"

    def frame(self, video, when):
        data = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(when), "-i", str(video),
                                        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
        return np.frombuffer(data, dtype=np.uint8).reshape(1920, 1080, 3)

    def audio(self, source, filters=None):
        data = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(source), *(["-af", filters] if filters else []), "-ar", "48000", "-ac", "2",
                                        "-f", "f32le", "pipe:1"])
        return np.frombuffer(data, dtype="<f4").reshape(-1, 2)

    def test_export_has_youtube_artwork_and_preserves_transparency(self):
        video = self.make_clip(["like"], "art")
        frame = self.frame(video, 2.3)
        motion = interactions.plan(["like"], 4, 300, 1380, "pt-BR")[0]
        y = int(motion["y"])
        art = frame[y:y + 240, 140:940]
        self.assertGreater(np.sum(np.all(art > 235, axis=2)), 40000)
        self.assertGreater(np.sum((art[:, :, 0] > 200) & (art[:, :, 1] < 50)), 500)
        self.assertTrue(np.all(np.abs(frame[y + 5, 155].astype(int) - [36, 50, 74]) < 6))
        before = self.frame(video, .1)
        self.assertLess(before.max(), 90)

    def test_woosh_uses_sixty_percent_without_raising_the_source(self):
        video = self.make_clip(["like"], "sound", duration=8.)
        output = self.audio(video)
        source = self.audio(interactions.ASSETS / "sounds" / interactions.SOUNDS["woosh"],
                            "aresample=48000,atrim=start=0.32:duration=0.829375,asetpts=PTS-STARTPTS,atempo=1.5")
        start = interactions.plan(["like"], 8, 300, 1380, "pt-BR")[0]["start"]
        offset = round(start * 48000)
        low, high = 2400, 16000
        # Compare with the same audible source segment at the requested 1.5x speed.
        ratio = np.sqrt(np.mean(output[offset + low:offset + high] ** 2)) / np.sqrt(np.mean(source[low:high] ** 2))
        self.assertAlmostEqual(float(ratio), .6, delta=.04)
        self.assertLess(np.max(np.abs(output[:max(1, offset - 1000)])), .003)
        self.assertLessEqual(float(np.max(np.abs(output))), .98)

    def test_edited_sequence_keeps_effects_and_duration_and_no_selection_stays_silent(self):
        with_effect = self.make_clip(["comment"], "joined", joined=True)
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(with_effect)]))
        self.assertAlmostEqual(float(info["format"]["duration"]), 4., delta=.08)
        self.assertGreater(float(np.max(np.abs(self.audio(with_effect)))), .01)
        plain = self.make_clip([], "plain")
        self.assertLess(float(np.max(np.abs(self.audio(plain)))), .001)
        self.assertLess(self.frame(plain, 2.).max(), 90)

    def test_compressed_motion_keeps_clicks_and_typing_audible(self):
        m = interactions.plan(ACTIONS, 3., 300, 1380, "pt-BR")[2]
        for sound in m["sounds"]:
            with self.subTest(sound=sound["name"]), tempfile.TemporaryDirectory(prefix="pk-isolated-fx-") as directory:
                work = Path(directory)
                inputs, filters = interactions.compose(work, [{**m, "sounds": [sound]}], 1, 3.)
                output = work / "sound.wav"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(self.source), *inputs,
                                "-filter_complex", "[0:v]scale=1080:1920[v];[0:a]anull[normalized];" + filters,
                                "-map", "[motion_a]", "-t", "3", str(output),
                                "-map", "[motion_v]", "-t", "3", "-f", "null", "-"], cwd=work, check=True,
                               stdout=subprocess.DEVNULL)
                samples = self.audio(output)
                start, end = round(sound["start"] * 48000), round(sound["end"] * 48000)
                self.assertGreater(float(np.max(np.abs(samples[start:end]))), .001)

    def test_source_with_short_audio_does_not_silence_later_effects(self):
        source = Path(TEMP.name) / "short-audio.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(self.source), "-c:v", "copy",
                        "-af", "atrim=duration=1", "-c:a", "aac", str(source)], check=True)
        video = self.make_clip(["like"], "late-effect", source=source)
        samples = self.audio(video)
        self.assertGreaterEqual(len(samples), 4 * 48000 - 1000)
        click = interactions.plan(["like"], 4., 300, 1380, "pt-BR")[0]["sounds"][-1]
        start, end = round(click["start"] * 48000), round(click["end"] * 48000)
        self.assertGreater(float(np.max(np.abs(samples[start:end]))), .005)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        TEMP.cleanup()
