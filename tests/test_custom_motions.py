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
from PIL import Image, ImageDraw  # noqa: E402


def brief(**extra):
    return {"action": "subscribe", "style": "Playful purple, bold letters", "background": "#241035",
            "ink": "#ffffff", "accent": "#ffcf3f", "image_action": "wave", "label": "INSCREVA-SE",
            "note": "Brincalhão", "has_image": True, "language": "pt-BR", **extra}


def fake_frame(path):
    Image.new("RGBA", (80, 24), (255, 0, 0, 128)).save(path, "PNG")


def cutout_bytes():
    image = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((8, 6, 50, 36), fill=(220, 30, 30, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def pose_bytes(size=(120, 80)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((8, 6, size[0] - 8, size[1] - 6), fill=(220, 30, 30, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


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
        electric = cm.clean_brief(brief(extras="lightning"))
        self.assertEqual(electric["electric"], "lightning")   # older briefs still ask for a lightning effect
        self.assertEqual(electric["extras"], "none")
        with self.assertRaises(ValueError):
            cm.clean_brief(brief(action="dance"))

    def test_a_chosen_palette_overrides_the_ai_colors(self):
        palette = {"background": "#010203", "ink": "#fefefe", "accent": "#ffcc00", "detail": "#00aaff"}
        answer = {"style": "Bold", "background": "#999999", "ink": "#111111", "accent": "#ff0000",
                  "image_action": "wave", "label": "INSCREVA-SE", "note": "Teste"}
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.brain.ask_ai", return_value=(answer, "model", 20)) as ask:
            got = cm.ask_brief(brief(palette_colors=palette))
        for key, value in palette.items():
            self.assertEqual(got[key], value)
        self.assertIn(palette["accent"], ask.call_args.args[0])   # the model was told which colors to use

    def test_generated_pictures_become_six_new_frames(self):
        clean = cm.clean_brief(brief())
        names = [f"pose-{i}.png" for i in range(6)]
        doc = cm.build_lottie(clean, "character", names)
        self.assertEqual(len(doc["assets"]), 6)
        self.assertEqual([a["p"] for a in doc["assets"]], names)
        self.assertEqual(sum(1 for l in doc["layers"] if l["ty"] == 2), 6)
        self.assertEqual([l["ip"] for l in sorted(doc["layers"], key=lambda l: l["ip"])], [0, 16, 36, 56, 76, 96])
        self.assertEqual(max(l["op"] for l in doc["layers"] if l["ty"] == 2), 120)

    def _ready_checkout(self):
        checkout = Path(TEMP.name) / "fake-checkout"
        (checkout / "node_modules" / "canvaskit-wasm").mkdir(parents=True, exist_ok=True)
        (checkout / "package.json").write_text("{}")
        status = {"ready": True, "checkout": str(checkout), "node": "node", "message": ""}
        return checkout, status

    def test_six_incoming_pngs_are_saved_and_become_six_visible_poses(self):
        checkout, status = self._ready_checkout()
        calls = []
        def fake_run(checkout_, scene, out, images=None, webm=False):
            calls.append(list(images or []))
            out.mkdir(parents=True, exist_ok=True)
            names = []
            for i in range(6):
                name = f"frame-{i * 20:04d}.png"
                fake_frame(out / name)
                names.append(name)
            return {"frames": names}
        with patch.object(cm, "checkout_status", return_value=status), patch.object(cm, "_run", side_effect=fake_run):
            got = cm.draft("test", brief(), cutout_bytes(), str(checkout), [pose_bytes() for _ in range(6)])
        folder = cm._draft_dir("test", got["id"])
        saved = sorted(p.name for p in folder.glob("pose-*.png"))
        self.assertEqual(saved, [f"pose-{i}.png" for i in range(6)])
        for name in saved:
            with Image.open(folder / name) as picture:
                self.assertEqual(picture.size, (220, 220))   # assets declare 220x220, so poses must match
                self.assertEqual(picture.mode, "RGBA")
        scene = json.loads((folder / "character.json").read_text(encoding="utf-8"))
        self.assertEqual(len(scene["assets"]), 6)
        self.assertEqual([a["p"] for a in scene["assets"]], saved)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[1]), 6)               # the renderer received the six saved pictures
        cm.discard("test", got["id"])

    def test_an_incomplete_sequence_is_a_failure_not_a_generic_character(self):
        checkout, status = self._ready_checkout()
        for generated in (None, [pose_bytes()], [pose_bytes()] * 5, [b"not a picture"] * 6):
            with self.subTest(count=len(generated or [])), \
                 patch.object(cm, "checkout_status", return_value=status), \
                 patch.object(cm, "_run", return_value={"frames": []}):
                with self.assertRaisesRegex(ValueError, "six usable pictures"):
                    cm.draft("test", brief(), cutout_bytes(), str(checkout), generated)
        with self.assertRaisesRegex(ValueError, "needs six new poses"):
            cm.build_lottie(cm.clean_brief(brief()), "character")
        with self.assertRaisesRegex(ValueError, "needs six new poses"):
            cm.build_lottie(cm.clean_brief(brief()), "character", ["pose-0.png"])
        generic = cm.build_lottie(cm.clean_brief(brief()), "character", generic=True)
        self.assertTrue(all(l["nm"].startswith("Generic drawing") for l in generic["layers"] if l["ty"] == 4))
        self.assertTrue(all(s["ty"] == "gr" for s in generic["layers"][0]["shapes"]))  # parts do not bleed

    def test_action_draws_the_requested_cursor_click_lightning_and_confirmation(self):
        chosen = brief(extras="none", electric="lightning", cursor="pointer", click_timing="early", language="pt-BR")
        doc = cm.build_lottie(cm.clean_brief(chosen), "action")
        names = [l["nm"] for l in doc["layers"]]
        for expected in ("Electric reply", "Click response", "Pointer press and release",
                         "Confirmation tick", "Call to action", "Confirmed action"):
            self.assertIn(expected, names)
        pointer = next(l for l in doc["layers"] if l["nm"] == "Pointer press and release")
        response = next(l for l in doc["layers"] if l["nm"] == "Click response")
        self.assertEqual(response["ip"], 34)                          # quick means an early click
        self.assertIn(26, [k["t"] for k in pointer["ks"]["p"]["k"]])  # the pointer arrives before it
        self.assertIn(82, [k["s"][0] for k in pointer["ks"]["s"]["k"]])  # the pointer presses down
        confirmed = next(l for l in doc["layers"] if l["nm"] == "Confirmed action")
        self.assertEqual(confirmed["t"]["d"]["k"][0]["s"]["t"], "INSCRITO")
        slow = cm.build_lottie(cm.clean_brief(brief(click_timing="late")), "action")
        slow_response = next(l for l in slow["layers"] if l["nm"] == "Click response")
        self.assertEqual(slow_response["ip"], 72)

        plain = cm.build_lottie(cm.clean_brief(brief(electric="none", cursor="none")), "action")
        plain_names = [l["nm"] for l in plain["layers"]]
        self.assertNotIn("Electric reply", plain_names)
        self.assertNotIn("Pointer press and release", plain_names)

    def test_the_ai_detail_color_survives_when_no_palette_is_chosen(self):
        answer = {"style": "Bold", "background": "#111111", "ink": "#ffffff", "accent": "#ff0000",
                  "detail": "#123456", "image_action": "wave", "label": "INSCREVA-SE", "note": "Teste"}
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.brain.ask_ai", return_value=(answer, "model", 20)):
            got = cm.ask_brief(brief())
        self.assertEqual(got["detail"], "#123456")

    def test_drawing_uses_the_image_provider_and_asks_for_wave_poses(self):
        answer = {"style": "Bold", "background": "#111111", "ink": "#ffffff", "accent": "#ff0000",
                  "image_action": "wave", "label": "INSCREVA-SE", "note": "Teste"}
        made = [pose_bytes() for _ in range(6)]
        pictures = []
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.ai.provider", return_value="codex"), \
                patch("studio.ai.draw", return_value=(answer, 10, 2, made)) as draw:
            got = cm.ask_brief(brief(image_prompt="tchau"), pictures)
        self.assertEqual(pictures, made)
        self.assertIn("hand high", draw.call_args.args[0].lower())
        self.assertEqual(got["image_action"], "wave")
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.ai.provider", return_value="codex"), \
                patch("studio.ai.draw", return_value=(answer, 10, 2, made[:4])):
            with self.assertRaisesRegex(ValueError, "did not return the six poses"):
                cm.ask_brief(brief(image_prompt="tchau"), [])

    def test_action_uses_the_chosen_palette_and_the_requested_decoration(self):
        chosen = brief(background="#123456", ink="#ffffff", accent="#00ff00", detail="#ff00ff", extras="stars")
        doc = cm.build_lottie(cm.clean_brief(chosen), "action")
        self.assertTrue(any(l["nm"] == "Requested detail" for l in doc["layers"]))
        colors = set()
        for layer in doc["layers"]:
            for shape in layer.get("shapes", []):
                color = shape.get("c", {}).get("k")
                if isinstance(color, list) and len(color) == 4:
                    colors.add(tuple(round(float(v), 3) for v in color))
        self.assertIn((0.071, 0.204, 0.337, 1.0), colors)   # #123456 card
        self.assertIn((0.0, 1.0, 0.0, 1.0), colors)         # #00ff00 accent
        self.assertIn((1.0, 0.0, 1.0, 1.0), colors)         # #ff00ff detail

    def test_two_lottie_scenes_share_the_contract_but_not_the_artwork(self):
        clean = cm.clean_brief(brief())
        action = cm.build_lottie(clean, "action")
        character = cm.build_lottie(clean, "character", [f"pose-{i}.png" for i in range(6)])
        for doc in (action, character):
            self.assertEqual((doc["w"], doc["h"], doc["fr"], doc["op"]), (800, 240, 30, 120))
            self.assertEqual(len(doc["fonts"]["list"]), 1)
            def expression(node):
                if isinstance(node, dict):
                    return any((k == "x" and isinstance(v, str)) or expression(v) for k, v in node.items())
                if isinstance(node, list):
                    return any(expression(v) for v in node)
                return False
            self.assertFalse(any(expression(layer) for layer in doc["layers"]))
        self.assertEqual(action["assets"], [])
        self.assertIn(5, [l["ty"] for l in action["layers"]])       # the action's text
        self.assertEqual(len(character["assets"]), 6)               # six generated poses, not the source picture
        self.assertNotIn("source-image.png", [a["p"] for a in character["assets"]])
        self.assertEqual(sum(1 for l in character["layers"] if l["nm"].startswith("New picture")), 6)
        self.assertNotIn(5, [l["ty"] for l in character["layers"]]) # no card, no label on the character

    def test_ai_brief_is_asked_once_and_merged_with_the_form(self):
        answer = {"style": "Purple and bold", "background": "#241035", "ink": "#ffffff",
                  "accent": "#ffcf3f", "image_action": "laugh", "label": "INSCREVA-SE", "note": "Alegre"}
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.brain.ask_ai", return_value=(answer, "model", 20)) as ask:
            got = cm.ask_brief(brief(action_description="um personagem roxo", image_prompt="acenar tchau"))
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(got["image_action"], "laugh")
        self.assertIn("purple", ask.call_args.args[0].lower())
        # Providers that wrap the schema answer in "items" must work too.
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.brain.ask_ai", return_value=({"items": answer}, "model", 20)):
            self.assertEqual(cm.ask_brief(brief())["accent"], answer["accent"])
        # Plain text answers from a model are still accepted.
        with patch("studio.ai.ready", return_value=True), \
                patch("studio.brain.ask_ai", return_value=(f"Sure!\n```json\n{json.dumps(answer)}\n```", "model", 20)):
            self.assertEqual(cm.ask_brief(brief())["style"], answer["style"])
        with patch("studio.ai.ready", return_value=False):
            with self.assertRaises(ValueError):
                cm.ask_brief(brief())

    def test_a_cutout_picture_needs_real_transparency(self):
        solid = io.BytesIO()
        Image.new("RGB", (40, 40), (255, 0, 0)).save(solid, "PNG")
        with self.assertRaises(ValueError):
            cm.check_cutout(solid.getvalue())
        cut = io.BytesIO()
        image = Image.new("RGBA", (40, 40), (255, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((8, 8, 32, 32), fill=(255, 0, 0, 255))
        image.save(cut, "PNG")
        self.assertEqual(Image.open(io.BytesIO(cm.check_cutout(cut.getvalue()))).mode, "RGBA")

    def test_each_motion_has_its_own_filmstrip(self):
        folder = cm._draft_dir("test", "a" * 16)
        for kind in cm.KINDS:
            sub = folder / kind
            sub.mkdir(parents=True, exist_ok=True)
            for i in range(11):
                fake_frame(sub / f"frame-{i * 10:04d}.png")
        for kind in cm.KINDS:
            strip = cm.filmstrip_data("test", "a" * 16, kind)
            self.assertEqual(strip[:2], b"\xff\xd8")
            self.assertGreater(Image.open(io.BytesIO(strip)).width, 400)
            self.assertEqual(cm.frame_jpeg("test", "a" * 16, kind, "frame-0020.png")[:2], b"\xff\xd8")
        with self.assertRaises(ValueError):
            cm.filmstrip_data("test", "a" * 16, "other")
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

    def test_custom_motion_click_sound_follows_the_drawn_click(self):
        asset = Path(TEMP.name) / "motion-click1234.webm"
        scene = cm.build_lottie(cm.clean_brief(brief(click_timing="early")), "action")
        asset.with_suffix(".json").write_text(json.dumps({"action": scene}), encoding="utf-8")
        planned = interactions.plan(["subscribe"], 8, 300, 1380, "pt-BR",
                                    custom_motions={"subscribe": asset})[0]
        plain = interactions.plan(["subscribe"], 8, 300, 1380, "pt-BR")[0]
        click = next(s for s in planned["sounds"] if s["name"] == "click")
        original = next(s for s in plain["sounds"] if s["name"] == "click")
        self.assertLess(click["start"], original["start"])
        asset.with_suffix(".json").unlink()


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
