"""Custom interaction styles: colors, seal, AI reading, and the copy each new video keeps."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
TEMP = tempfile.TemporaryDirectory(prefix="pc-style-tests-")
os.environ.update(PEAKCUT_DATA=TEMP.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
from studio import ai, auth, creators, ctx, db, interaction_styles as styles, interactions, jobs  # noqa: E402


def draft(name="Neon", **extra):
    return {"name": name, "background": "#101820", "ink": "#ffffff", "accent": "#00e5ff",
            "detail": "#ff8800", "seal": True, "seal_name": "Canal", **extra}


class StyleRules(unittest.TestCase):
    def test_colors_names_and_seal_are_cleaned_and_kept_readable(self):
        got = styles.clean_style(draft(name="  Estilo   neon  ", ink="#101820"))
        self.assertEqual(got["name"], "Estilo neon")
        self.assertNotEqual(got["ink"], "#101820")           # unreadable ink was moved
        self.assertGreaterEqual(styles._ratio(got["ink"], got["background"]), 4.5)
        self.assertEqual(got["accent"], "#00e5ff")
        self.assertEqual(got["seal_name"], "Canal")
        for field in ("background", "ink", "accent", "detail"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                styles.clean_style(draft(**{field: "not a color"}))
        with self.assertRaises(ValueError):
            styles.clean_style(draft(name="   "))
        with self.assertRaises(ValueError):
            styles.clean_style(draft(seal="yes"))
        self.assertEqual(styles.clean_style(draft(seal_name="x" * 40))["seal_name"], "x" * 20)

    def test_lookup_table_maps_the_artworks_three_colors(self):
        lines = styles.lut_text(styles.clean_style(draft())).splitlines()
        self.assertEqual(lines[0], "LUT_3D_SIZE 17")
        entries = lines[1:]
        self.assertEqual(len(entries), 17 ** 3)
        at = lambda r, g, b: [float(x) for x in entries[b * 289 + g * 17 + r].split()]
        background, accent, ink = at(0, 0, 0), at(16, 0, 0), at(16, 16, 16)
        for got, wanted in ((background, "#101820"), (accent, "#00e5ff"), (ink, "#ffffff")):
            for channel, hex_part in zip(got, (1, 3, 5)):
                self.assertAlmostEqual(channel, int(wanted[hex_part:hex_part + 2], 16) / 255, places=5)

    def test_seal_and_preview_are_real_images_without_needing_an_ai(self):
        from PIL import Image
        style = styles.clean_style(draft())
        seal = Image.open(io.BytesIO(styles.seal_png(style)))
        self.assertEqual(seal.mode, "RGBA")
        self.assertEqual(seal.getpixel((0, 0))[3], 0)          # the corners stay transparent
        with patch.object(styles, "_frame", return_value=_png()):
            preview = styles.preview_jpeg(style, None, "pt-BR")
        self.assertEqual(preview[:2], b"\xff\xd8")             # a JPEG the page can show
        self.assertEqual(styles.clean_snapshot(None), None)    # old clips keep the YouTube art

    def test_one_vision_call_per_idea_and_nothing_saved_until_asked(self):
        answers = [('{"name":"Azul","background":"#0a1020","ink":"#f5f5f5","accent":"#00ffcc",'
                    '"detail":"#ffaa00","seal_name":"Canal","why":"Primeira"}', 10, 4),
                   ('Here you go: {"name":"Verde","background":"#0b2010","ink":"#ffffff","accent":"#00ff00",'
                    '"detail":"#aabb00","seal_name":"Canal","why":"Segunda"}', 11, 5)]
        with patch.object(ai, "ready", return_value=True), patch.object(ai, "see", side_effect=answers) as see, \
                patch("studio.usage.record") as record:
            first = styles.suggest({"name": "Canal"}, b"picture", None)
            second = styles.suggest({"name": "Canal"}, b"picture", None)
        self.assertEqual(see.call_count, 2)
        self.assertEqual(record.call_count, 2)
        self.assertEqual(record.call_args.args[2], "Interaction style: channel art")
        self.assertEqual((first["accent"], second["accent"]), ("#00ffcc", "#00ff00"))
        self.assertEqual((first["why"], second["why"]), ("Primeira", "Segunda"))
        with patch.object(ai, "ready", return_value=False):
            with self.assertRaises(ValueError):
                styles.suggest({"name": "Canal"}, b"picture", None)

    def test_compose_tints_motions_and_adds_seal_without_moving_sounds(self):
        motion = interactions.plan(["like"], 8, 300, 1380, "pt-BR")[0]
        work = Path(TEMP.name) / "work"
        work.mkdir(exist_ok=True)
        inputs, chain = interactions.compose(work, [motion], 1, 8, styles.clean_style(draft()))
        self.assertIn("lut3d=file=style.cube", chain)
        self.assertIn("[sealv]", chain)
        self.assertIn("[3:a]asplit", chain)                 # first input, one motion and the seal come before the sounds
        self.assertTrue((work / "style.cube").is_file())
        self.assertTrue((work / "seal.png").is_file())
        self.assertEqual(len(inputs), 10)                    # motion, seal and two sound effects, as ffmpeg arguments
        plain_inputs, plain_chain = interactions.compose(work, [motion], 1, 8)
        self.assertNotIn("lut3d", plain_chain)


class StyleStorage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.user = auth.local_user().id

    def test_styles_belong_to_one_creator_and_cap_at_eight(self):
        with ctx.as_user(self.user):
            a = creators.create({"name": "Canal A", "channel": "https://www.twitch.tv/style_a"})
            b = creators.create({"name": "Canal B", "channel": "https://www.twitch.tv/style_b"})
            self.assertEqual(a.get("interaction_style"), "")
            first = creators.add_interaction_style(a["id"], draft())
            self.assertEqual(first["interaction_style"], first["interaction_styles"][0]["id"])
            self.assertEqual(b["interaction_styles"], [])
            for n in range(7):
                first = creators.add_interaction_style(a["id"], draft(f"Style {n}"))
            with self.assertRaises(ValueError):
                creators.add_interaction_style(a["id"], draft("One too many"))
            sid = first["interaction_styles"][0]["id"]
            creators.update_interaction_style(a["id"], sid, draft("Renamed"))
            self.assertEqual(creators.get(a["id"])["interaction_styles"][0]["name"], "Renamed")
            creators.select_interaction_style(a["id"], "")
            self.assertEqual(creators.get(a["id"])["interaction_style"], "")   # YouTube again
            creators.delete_interaction_style(a["id"], sid)
            self.assertEqual(len(creators.get(a["id"])["interaction_styles"]), 7)
            with self.assertRaises(KeyError):
                creators.delete_interaction_style(a["id"], sid)

    def test_new_jobs_copy_the_style_and_editing_it_later_changes_nothing(self):
        with ctx.as_user(self.user):
            c = creators.create({"name": "Snapshots", "channel": "https://www.twitch.tv/snapshots"})
            c = creators.add_interaction_style(c["id"], draft())
            sid = c["interaction_style"]
            job = jobs.create({"type": "file", "name": "Old"}, {"interactions": ["like"]},
                              enqueue=False, creator=c["id"])
            self.assertEqual(job["options"]["interaction_style"]["name"], "Neon")
            creators.update_interaction_style(c["id"], sid, draft("Changed"))
            self.assertEqual(jobs.get(job["id"])["options"]["interaction_style"]["name"], "Neon")
            with self.assertRaises(ValueError):
                jobs.clean_options({"interaction_style": "not a style"})
            changed = jobs._render_changes(job, {"start": 0., "end": 5.},
                                           {"interaction_style": draft("Fresh")})
            self.assertEqual(changed["interaction_style"]["name"], "Fresh")
            plain = jobs.create({"type": "file", "name": "No creator"}, {}, enqueue=False)
            self.assertIsNone(plain["options"]["interaction_style"])


def _png():
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(out, "PNG")
    return out.getvalue()


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        db.engine.dispose()
        TEMP.cleanup()
