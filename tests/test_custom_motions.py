"""Custom audience motions: brief, Lottie scene, filmstrip and the copy a new video keeps."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
TEMP = tempfile.TemporaryDirectory(prefix="pc-custom-motions-")
os.environ.update(PEAKCUT_DATA=TEMP.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
from studio import auth, creators, ctx, custom_motions as cm, db, interactions, jobs  # noqa: E402
from PIL import Image  # noqa: E402


def brief(**extra):
    return {"action": "subscribe", "style": "Playful purple, bold letters", "background": "#241035",
            "ink": "#ffffff", "accent": "#ffcf3f", "image_action": "wave", "label": "INSCREVA-SE",
            "note": "Brincalhão", "has_image": True, "language": "pt-BR", **extra}


def fake_frame(path):
    Image.new("RGBA", (80, 24), (255, 0, 0, 128)).save(path, "PNG")


class MotionRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def setUp(self):
        user = ctx.as_user(auth.local_user().id)
        user.__enter__()
        self.addCleanup(user.__exit__, None, None, None)
    def test_brief_only_keeps_what_can_be_drawn(self):
        got = cm.clean_brief(brief(label="x" * 60, image_action="unknown"))
        self.assertEqual(got["image_action"], "pop")
        self.assertLessEqual(len(got["label"]), 22)
        self.assertEqual(cm.clean_brief(brief(has_image=False, image_action="cry"))["image_action"], "pop")
        with self.assertRaises(ValueError):
            cm.clean_brief(brief(action="dance"))

    def test_lottie_scene_uses_the_shared_contract_and_the_uploaded_picture(self):
        doc = cm.build_lottie(cm.clean_brief(brief()), cm.SOURCE_IMAGE)
        self.assertEqual((doc["w"], doc["h"], doc["fr"], doc["op"]), (800, 240, 30, 120))
        self.assertEqual(doc["assets"][0]["p"], cm.SOURCE_IMAGE)
        self.assertEqual(len(doc["fonts"]["list"]), 1)
        kinds = [l["ty"] for l in doc["layers"]]
        self.assertIn(2, kinds)      # the picture
        self.assertIn(5, kinds)      # native text
        self.assertGreaterEqual(sum(1 for l in doc["layers"] if l["ty"] == 4), 3)
        def expression(node):
            if isinstance(node, dict):
                return any((k == "x" and isinstance(v, str)) or expression(v) for k, v in node.items())
            if isinstance(node, list):
                return any(expression(v) for v in node)
            return False
        for layer in doc["layers"]:
            self.assertFalse(expression(layer))   # no renderer-specific expressions
        plain = cm.build_lottie(cm.clean_brief(brief(has_image=False)))
        self.assertEqual(plain["assets"], [])
        self.assertNotIn(2, [l["ty"] for l in plain["layers"]])

    def test_ai_brief_is_asked_once_and_merged_with_the_form(self):
        answer = {"style": "Purple and bold", "background": "#241035", "ink": "#ffffff",
                  "accent": "#ffcf3f", "image_action": "laugh", "label": "INSCREVA-SE", "note": "Alegre"}
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.brain.ask_ai", return_value=(json.dumps(answer), "model", 20)) as ask:
            got = cm.ask_brief(brief(description="um personagem roxo", image_prompt="acenar tchau"))
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(got["image_action"], "laugh")
        self.assertIn("purple", ask.call_args.args[0].lower())
        with patch("studio.ai.ready", return_value=False):
            with self.assertRaises(ValueError):
                cm.ask_brief(brief())

    def test_filmstrip_shows_at_least_six_real_frames(self):
        folder = cm._draft_dir("test", "a" * 16)
        folder.mkdir(parents=True, exist_ok=True)
        names = []
        for i in range(11):
            name = f"frame-{i * 10:04d}.png"
            fake_frame(folder / name)
            names.append(name)
        strip = cm.filmstrip_data("test", "a" * 16)
        self.assertEqual(strip[:2], b"\xff\xd8")
        self.assertGreater(Image.open(io.BytesIO(strip)).width, 400)
        self.assertEqual(cm.frame_jpeg("test", "a" * 16, names[2])[:2], b"\xff\xd8")
        cm.discard("test", "a" * 16)
        self.assertFalse(folder.exists())

    def test_checkout_needs_node_and_canvaskit(self):
        folder = Path(TEMP.name) / "checkout"
        (folder / "node_modules" / "canvaskit-wasm").mkdir(parents=True)
        (folder / "package.json").write_text("{}")
        with patch("studio.custom_motions.shutil.which", return_value="C:/node.exe"):
            got = cm.checkout_status(str(folder))
        self.assertTrue(got["ready"])
        self.assertEqual(got["checkout"], str(folder))
        with patch("studio.custom_motions.shutil.which", return_value=""):
            self.assertFalse(cm.checkout_status(str(folder))["ready"])

    def test_plan_uses_an_approved_asset_without_changing_timing(self):
        custom = {"subscribe": Path(TEMP.name) / "motion-abcdefghij.webm"}
        plain = interactions.plan(["subscribe"], 8, 300, 1380, "pt-BR")
        changed = interactions.plan(["subscribe"], 8, 300, 1380, "pt-BR", custom_motions=custom)
        self.assertEqual(changed[0]["asset"], custom["subscribe"])
        self.assertEqual(changed[0]["start"], plain[0]["start"])
        self.assertEqual(changed[0]["end"], plain[0]["end"])
        self.assertEqual(changed[0]["sounds"], plain[0]["sounds"])


class MotionStorage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.user = auth.local_user().id

    def test_styles_and_custom_motions_are_separate_and_per_creator(self):
        with ctx.as_user(self.user):
            c = creators.create({"name": "Motions", "channel": "https://www.twitch.tv/motions"})
            record = {"id": "motion-abcdefghij", "action": "like", "name": "Hand wave",
                      "file": "motion-abcdefghij.webm", "created": 1.0}
            c = creators.add_custom_motion(c["id"], record)
            self.assertEqual(c["custom_motion_selected"], {"like": "motion-abcdefghij"})
            self.assertEqual(creators.custom_motion_assets(c), {"like": "motion-abcdefghij.webm"})
            self.assertEqual(c["interaction_styles"], [])     # colors were not changed
            with self.assertRaises(ValueError):
                creators.clean_custom_motions([{**record, "file": "../secret.webm"}])
            other = creators.create({"name": "Other", "channel": "https://www.twitch.tv/other"})
            self.assertEqual(other["custom_motions"], [])
            c = creators.select_custom_motion(c["id"], "like", "")
            self.assertEqual(c["custom_motion_selected"], {})
            c = creators.delete_custom_motion(c["id"], "motion-abcdefghij")
            self.assertEqual(c["custom_motions"], [])

    def test_new_jobs_copy_the_selected_motions_and_keep_them(self):
        with ctx.as_user(self.user):
            c = creators.create({"name": "Snapshots", "channel": "https://www.twitch.tv/snapshots"})
            c = creators.add_custom_motion(c["id"], {"id": "motion-1234567890", "action": "share",
                                                     "name": "Share", "file": "motion-1234567890.webm"})
            job = jobs.create({"type": "file", "name": "Test"}, {}, enqueue=False, creator=c["id"])
            self.assertEqual(job["options"]["custom_motions"], {"share": "motion-1234567890.webm"})
            creators.delete_custom_motion(c["id"], "motion-1234567890")
            self.assertEqual(jobs.get(job["id"])["options"]["custom_motions"], {"share": "motion-1234567890.webm"})
            with self.assertRaises(ValueError):
                jobs.clean_options({"custom_motions": "like"})
            plain = jobs.create({"type": "file", "name": "Plain"}, {}, enqueue=False)
            self.assertIn(plain["options"].get("custom_motions"), ({}, None))


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        db.engine.dispose()
        TEMP.cleanup()
