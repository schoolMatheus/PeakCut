"""Audience requests stay optional, sequential and clear of spoken captions."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
TEMP = tempfile.TemporaryDirectory(prefix="pc-interactions-")
os.environ.update(PEAKCUT_DATA=TEMP.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
from studio import accounts, auth, creators, ctx, db, jobs, live, render, server  # noqa: E402

class InteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.user = auth.local_user().id

    def test_options_reject_malformed_lists_and_remove_duplicates(self):
        self.assertEqual(jobs.clean_options({"interactions": ["like", "like", "save"]}),
                         {"interactions": ["like", "save"]})
        for invalid in (None, "like", True, {}, ["unknown"], [1], [["like"]]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                jobs.clean_options({"interactions": invalid})

    def test_jobs_templates_and_rerenders_keep_the_selection(self):
        with ctx.as_user(self.user):
            job = jobs.create({"type": "file", "name": "Test"}, {"interactions": ["like", "save"]}, enqueue=False)
            self.assertEqual(jobs.get(job["id"])["options"]["interactions"], ["like", "save"])
            template = server.add_clip_template({"name": "Requests", "options": job["options"]})
            saved = next(t for t in accounts.clip_templates() if t["id"] == template["id"])
            self.assertEqual(saved["options"]["interactions"], ["like", "save"])
            clip = {"start": 0., "end": 5., "interactions": ["like", "save"]}
            self.assertEqual(jobs._render_changes(job, clip, {"layout": "fit"})["interactions"], ["like", "save"])
            self.assertEqual(jobs._render_changes(job, clip, {"interactions": []})["interactions"], [])

    def test_old_options_and_clips_do_not_add_requests(self):
        with ctx.as_user(self.user):
            job = jobs.create({"type": "file", "name": "Old"}, {}, enqueue=False)
        self.assertEqual(job["options"]["interactions"], [])
        ass = render.build_ass([], 0, 5, "Title", "bounce", [], 300, 1380)
        self.assertNotIn(",Interaction,", ass)

    def test_creator_defaults_reach_live_jobs_and_allow_video_overrides(self):
        with ctx.as_user(self.user):
            creator = creators.create({"name": "Audience requests", "channel": "https://www.twitch.tv/peakcut_test",
                                       "interactions": ["like", "save"]})
            # Starting a recorder contacts the stream; keep job creation and settings real.
            with patch.object(live.Session, "start"), patch("studio.tools.missing", return_value=[]):
                live_job = live.start_session(creator)
            self.assertEqual(live_job["options"]["interactions"], ["like", "save"])
            video_job = jobs.create({"type": "file", "name": "Override"}, {"interactions": []},
                                    enqueue=False, creator=creator["id"])
            self.assertEqual(video_job["options"]["interactions"], [])
            with self.assertRaises(ValueError):
                creators.update(creator["id"], {"interactions": ["unknown"]})

    def test_captions_off_still_renders_requests(self):
        with patch.object(render, "_render_in", return_value={}) as encode:
            render.render_clip(Path(TEMP.name) / "unused.mp4", Path(TEMP.name) / "out", "test",
                               {"start": 0., "end": 5., "captions": False,
                                "interactions": ["comment"], "caption_language": "pt-BR"},
                               [{"w": "spoken", "s": 1., "e": 2.}], 1920, 1080)
        ass = encode.call_args.args[8]
        self.assertEqual(encode.call_args.kwargs["motions"][0]["action"], "comment")
        self.assertEqual(encode.call_args.kwargs["motions"][0]["asset"].parent.name, "pt-BR")
        self.assertNotIn(",Cap,", ass)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        db.engine.dispose()
        TEMP.cleanup()
