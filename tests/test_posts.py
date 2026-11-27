"""Queue a batch without contacting a platform or touching real clips."""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, ".")
os.environ.update(PEAKCUT_DATA=tempfile.mkdtemp(prefix="pc-posts-"), DATABASE_URL="",
                  PEAKCUT_ACCOUNTS="1", PEAKCUT_VERIFY_EMAIL="0")

from fastapi.testclient import TestClient
from studio import account, accounts, auth, creators, ctx, db, jobs, posting, server


class BatchPosts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.owner, _ = auth._create("poster@example.com", "Poster", password_hash="x")
        cls.other, _ = auth._create("other@example.com", "Other", password_hash="x")
        cls.client = TestClient(server.app, base_url="http://localhost", follow_redirects=False)
        cls.client.cookies.set(auth.COOKIE, auth.start_session(cls.owner.id))
        with ctx.as_user(cls.other.id):
            accounts._put({"id": "foreign", "platform": "youtube", "name": "Other channel", "added": 0})
            cls.foreign = jobs.create({"type": "file", "name": "Other video"}, {}, enqueue=False)
            cls.foreign["clips"] = [{"id": "private", "status": "done"}]
            jobs.save(cls.foreign)

    def setUp(self):
        self.user = ctx.as_user(self.owner.id)
        self.user.__enter__()
        posting._posts.clear()
        for aid in ("youtube", "instagram"):
            accounts._put({"id": aid, "platform": aid, "name": aid, "added": 0})
        creator = creators.create({"name": "Creator", "post_gap_min": 30, "daily_cap": 10})
        self.job = jobs.create({"type": "file", "name": "Video"}, {}, enqueue=False, creator=creator["id"])
        self.job["clips"] = [{"id": cid, "status": status, "title": cid}
                             for cid, status in (("one", "done"), ("two", "done"), ("busy", "rendering"))]
        jobs.save(self.job)
        self.pairs = [[self.job["id"], "one"], [self.job["id"], "two"]]

    def tearDown(self):
        self.user.__exit__(None, None, None)

    def post(self, **body):
        return self.client.post("/api/posts/bulk", json={"clips": self.pairs, "accounts": ["youtube"], **body},
                                headers={"Origin": "http://localhost"})

    def test_batch_queues_each_clip_for_each_account_once(self):
        version = ctx.version()
        response = self.post(clips=self.pairs + self.pairs[:1], accounts=["youtube", "instagram", "youtube"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"queued": 4, "clips": 2, "skipped": 0})
        self.assertEqual(len(posting._posts), 4)
        self.assertGreater(ctx.version(), version)
        self.assertEqual(len(jobs.get(self.job["id"])["clips"][0]["posts"]), 2)
        retry = self.post(accounts=["youtube", "instagram"])
        self.assertEqual(retry.json(), {"queued": 0, "clips": 0, "skipped": 2})

    def test_queue_spacing_and_post_now(self):
        self.assertEqual(self.post().status_code, 200)
        times = [p["due_at"] for p in posting._posts]
        self.assertGreaterEqual(times[1] - times[0], 1800)
        posting._posts.clear()
        before = time.time()
        self.assertEqual(self.post(now=True).status_code, 200)
        self.assertTrue(all(before <= p["due_at"] <= time.time() for p in posting._posts))

    def prepare_upload(self, aid="youtube"):
        clip = self.job["clips"][0]
        clip["file"] = "one.mp4"
        folder = Path(self.job["dir"]) / "clips"
        folder.mkdir(exist_ok=True)
        (folder / clip["file"]).write_bytes(b"test video")
        jobs.save(self.job)
        acc = accounts.get(aid)
        accounts._put({**acc, "access_token": "test-token", "expires": time.time() + 3600})
        self.assertEqual(self.post(clips=self.pairs[:1], accounts=[aid], now=True).status_code, 200)
        post = posting._posts[-1]
        post["status"] = "posting"
        return post

    def test_youtube_refusals_show_the_reason_without_rescheduling(self):
        for phase, status, reason in (("start", 400, "uploadLimitExceeded"),
                                      ("start", 403, "forbidden"),
                                      ("finish", 400, "invalidTitle"),
                                      ("finish", 503, "backendError"),
                                      ("start", 502, None)):
            with self.subTest(phase=phase, status=status, reason=reason):
                posting._posts.clear()
                post = self.prepare_upload()
                original_due = post["due_at"]
                refusal = httpx.Response(status, json={"error": {
                    "message": "The platform refused this test video.", "errors": [{"reason": reason}]}})
                if reason is None:
                    refusal = httpx.Response(status, text="Bad gateway")
                accepted = httpx.Response(200, headers={"Location": "https://upload.invalid/test"})
                with patch.object(accounts.httpx, "post", return_value=refusal if phase == "start" else accepted), \
                     patch.object(accounts.httpx, "put", return_value=refusal), \
                     patch.object(posting.alerts, "notify"):
                    posting._post(post)
                self.assertEqual(post["status"], "failed")
                self.assertEqual(post["tries"], 1)
                self.assertEqual(post["due_at"], original_due)
                expected = "upload limit" if reason == "uploadLimitExceeded" else "The platform refused" if reason else "HTTP 502"
                self.assertIn(expected, post["error"])
                self.assertNotIn('"error":', post["error"])
                mirrored = jobs.get(self.job["id"])["clips"][0]["posts"][post["id"]]
                self.assertEqual((mirrored["status"], mirrored["error"]), ("failed", post["error"]))

    def test_other_platforms_keep_their_existing_retry_behavior(self):
        post = self.prepare_upload("instagram")
        now = time.time()
        with patch.object(accounts, "instagram_upload", side_effect=RuntimeError("Temporary connection failure")), \
             patch("studio.posting.time.time", return_value=now):
            posting._post(post)
        self.assertEqual(post["status"], "scheduled")
        self.assertEqual(post["due_at"], now + 600)

    def test_queue_has_no_overnight_posting_window(self):
        cases = [
            ("2026-11-20 19:00", 10, "2026-11-20 19:30"),
            ("2026-11-20 19:00", 1, "2026-11-20 19:30"),
            ("2026-11-20 23:50", 1, "2026-11-21 00:20"),
            ("2026-12-31 19:00", 1, "2026-12-31 19:30"),
        ]
        for start, cap, second in cases:
            with self.subTest(start=start, cap=cap):
                posting._posts.clear()
                creators.update(self.job["creator"], {"daily_cap": cap})
                now = datetime.strptime(start, "%Y-%m-%d %H:%M").timestamp()
                expected = datetime.strptime(second, "%Y-%m-%d %H:%M").timestamp()
                with patch("studio.posting.time.time", return_value=now):
                    for clip in self.job["clips"][:2]:
                        posting.enqueue(self.job, clip, ["youtube"])
                self.assertEqual([p["due_at"] for p in posting._posts], [now, expected])

    def test_new_posts_continue_after_the_last_scheduled_video(self):
        creators.update(self.job["creator"], {"daily_cap": 1})
        scheduled = datetime(2026, 11, 20, 20, 35).timestamp()
        with patch("studio.posting.time.time", return_value=scheduled):
            posting.enqueue(self.job, self.job["clips"][0], ["youtube"])
        creators.update(self.job["creator"], {"post_gap_min": 5})
        earlier = datetime(2026, 11, 20, 19).timestamp()
        with patch("studio.posting.time.time", return_value=earlier):
            response = self.post(clips=self.pairs[1:], accounts=["youtube", "instagram"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([p["due_at"] for p in posting._posts], [
            scheduled, datetime(2026, 11, 20, 20, 40).timestamp(), earlier])

    def test_daily_limit_only_applies_when_the_user_enables_it(self):
        now = datetime(2026, 11, 20, 19).timestamp()
        for enabled, expected in ((True, datetime(2026, 11, 21).timestamp()),
                                  (False, datetime(2026, 11, 20, 19, 30).timestamp())):
            with self.subTest(enabled=enabled):
                posting._posts.clear()
                response = self.client.put(f"/api/creators/{self.job['creator']}",
                                           json={"daily_cap_enabled": enabled, "daily_cap": 1},
                                           headers={"Origin": "http://localhost"})
                self.assertEqual(response.status_code, 200, response.text)
                # Read settings back from disk, as happens after a restart.
                creators.forget(self.owner.id)
                with patch("studio.posting.time.time", return_value=now):
                    response = self.post()
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([p["due_at"] for p in posting._posts], [now, expected])

    def window(self, **settings):
        return creators.update(self.job["creator"], {
            "post_window_enabled": True, "post_start_time": "09:00", "post_end_time": "21:00",
            "daily_cap_enabled": True, "daily_cap": 6, **settings})

    def queue_clips(self, count, aid="youtube"):
        self.job["clips"] = [{"id": f"clip-{i}", "status": "done", "file": "one.mp4"} for i in range(count)]
        jobs.save(self.job)
        for clip in self.job["clips"]:
            posting.enqueue(self.job, clip, [aid])

    def test_window_spreads_daily_limit_and_carries_over_excess(self):
        self.window()
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            self.queue_clips(8)
        expected = [datetime(2026, 11, 20, h).timestamp() for h in (9, 11, 13, 15, 17, 19)]
        expected += [datetime(2026, 11, 21, h).timestamp() for h in (9, 11)]
        self.assertEqual([p["due_at"] for p in posting._posts], expected)

    def test_window_late_arrivals_and_end_boundary(self):
        self.window()
        for moment, expected in ((datetime(2026, 11, 20, 10), datetime(2026, 11, 20, 11)),
                                 (datetime(2026, 11, 20, 21), datetime(2026, 11, 21, 9)),
                                 (datetime(2026, 12, 31, 22), datetime(2027, 1, 1, 9))):
            with self.subTest(moment=moment):
                posting._posts.clear()
                with patch("studio.posting.time.time", return_value=moment.timestamp()):
                    self.queue_clips(1)
                self.assertEqual(posting._posts[0]["due_at"], expected.timestamp())

    def test_window_without_cap_keeps_interval(self):
        self.window(daily_cap_enabled=False, post_gap_min=30)
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 20, 45).timestamp()):
            self.queue_clips(2)
        self.assertEqual([p["due_at"] for p in posting._posts], [
            datetime(2026, 11, 20, 20, 45).timestamp(), datetime(2026, 11, 21, 9).timestamp()])

    def test_window_shared_account_reserves_slots_across_creators(self):
        c = self.window(daily_cap=2)
        other_creator = creators.create({**c, "name": "Second creator"})
        other_job = jobs.create({"type": "file", "name": "Second video"}, {}, enqueue=False, creator=other_creator["id"])
        other_job["clips"] = [{"id": "other", "status": "done"}]
        jobs.save(other_job)
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            posting.enqueue(self.job, self.job["clips"][0], ["youtube"])
            posting.enqueue(other_job, other_job["clips"][0], ["youtube"])
            posting.enqueue(self.job, self.job["clips"][1], ["youtube"])
        self.assertEqual([p["due_at"] for p in posting._posts], [
            datetime(2026, 11, 20, 9).timestamp(), datetime(2026, 11, 20, 15).timestamp(),
            datetime(2026, 11, 21, 9).timestamp()])

    def test_window_queues_more_than_thirty_days_without_breaking_the_cap(self):
        self.window(daily_cap=1)
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 1, 8).timestamp()):
            self.queue_clips(40)
        self.assertEqual(posting._posts[-1]["due_at"], datetime(2026, 12, 10, 9).timestamp())
        self.assertEqual(len({posting._day(p["due_at"]) for p in posting._posts}), 40)

    def test_window_settings_are_validated_and_saved(self):
        endpoint = f"/api/creators/{self.job['creator']}"
        headers = {"Origin": "http://localhost"}
        valid = {"post_window_enabled": True, "post_start_time": "08:30", "post_end_time": "18:45"}
        response = self.client.put(endpoint, json=valid, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        creators.forget(self.owner.id)
        saved = creators.get(self.job["creator"])
        self.assertEqual({k: saved.get(k) for k in valid}, valid)
        for invalid in ({"post_start_time": "25:00"}, {"post_end_time": "9:00"},
                        {"post_start_time": ""}, {"post_start_time": None},
                        {"post_end_time": "08:30"}, {"post_end_time": "07:00"},
                        {"post_window_enabled": "false"}):
            with self.subTest(invalid=invalid):
                version = ctx.version()
                response = self.client.put(endpoint, json=invalid, headers=headers)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(creators.get(self.job["creator"]), saved)
                self.assertEqual(ctx.version(), version)

    def test_several_ranges_spread_the_daily_limit_across_them(self):
        self.window(post_windows=[{"start": "11:00", "end": "13:00"}, {"start": "18:00", "end": "22:00"}],
                    daily_cap=4)
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            self.queue_clips(5)
        expected = [datetime(2026, 11, 20, h, m).timestamp() for h, m in ((11, 0), (12, 30), (19, 0), (20, 30))]
        expected.append(datetime(2026, 11, 21, 11, 0).timestamp())
        self.assertEqual([p["due_at"] for p in posting._posts], expected)

    def test_fixed_times_use_each_time_once_a_day(self):
        self.window(post_fixed_times=["12:00", "18:30"], daily_cap_enabled=False)
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            self.queue_clips(5)
        expected = [datetime(2026, 11, 20, 12).timestamp(), datetime(2026, 11, 20, 18, 30).timestamp(),
                    datetime(2026, 11, 21, 12).timestamp(), datetime(2026, 11, 21, 18, 30).timestamp(),
                    datetime(2026, 11, 22, 12).timestamp()]
        self.assertEqual([p["due_at"] for p in posting._posts], expected)

    def test_fixed_times_skip_times_that_have_passed(self):
        self.window(post_fixed_times=["12:00", "18:30"])
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 13).timestamp()):
            self.queue_clips(1)
        self.assertEqual(posting._posts[0]["due_at"], datetime(2026, 11, 20, 18, 30).timestamp())

    def test_overdue_fixed_time_waits_for_the_next_one(self):
        self.window(post_fixed_times=["12:00", "18:30"])
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            self.queue_clips(1)
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 13).timestamp()):
            posting._run_due()
        self.assertEqual(posting._posts[0]["status"], "scheduled")
        self.assertEqual(posting._posts[0]["due_at"], datetime(2026, 11, 20, 18, 30).timestamp())

    def test_fixed_time_still_goes_out_a_little_late(self):
        self.window(post_fixed_times=["12:00"])
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            self.queue_clips(1)
        with patch.object(accounts, "youtube_upload", return_value=("remote", "https://example.com/video")), \
             patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 12, 20).timestamp()):
            posting._run_due()
        self.assertEqual(posting._posts[0]["status"], "posted")

    def test_ranges_and_fixed_times_are_validated_and_saved(self):
        endpoint = f"/api/creators/{self.job['creator']}"
        headers = {"Origin": "http://localhost"}
        valid = {"post_window_enabled": True,
                 "post_windows": [{"start": "18:00", "end": "22:00"}, {"start": "11:00", "end": "13:00"}],
                 "post_fixed_times": ["18:30", "12:00", "12:00"]}
        response = self.client.put(endpoint, json=valid, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        creators.forget(self.owner.id)
        saved = creators.get(self.job["creator"])
        self.assertEqual(saved["post_windows"], [{"start": "11:00", "end": "13:00"},
                                                 {"start": "18:00", "end": "22:00"}])
        self.assertEqual(saved["post_fixed_times"], ["12:00", "18:30"])
        self.assertEqual((saved["post_start_time"], saved["post_end_time"]), ("11:00", "13:00"))
        for invalid in ({"post_windows": "11:00-13:00"},
                        {"post_windows": [{"start": "11:00"}]},
                        {"post_windows": [{"start": "13:00", "end": "11:00"}]},
                        {"post_windows": [{"start": "25:00", "end": "26:00"}]},
                        {"post_windows": [{"start": "11:00", "end": "12:00"}] * 9},
                        {"post_fixed_times": "12:00"},
                        {"post_fixed_times": ["9:00"]},
                        {"post_fixed_times": ["12:00"] * 25}):
            with self.subTest(invalid=invalid):
                version = ctx.version()
                response = self.client.put(endpoint, json=invalid, headers=headers)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(creators.get(self.job["creator"]), saved)
                self.assertEqual(ctx.version(), version)

    def test_overlapping_ranges_are_merged(self):
        c = creators.update(self.job["creator"], {
            "post_window_enabled": True,
            "post_windows": [{"start": "18:00", "end": "22:00"}, {"start": "11:00", "end": "13:00"},
                             {"start": "12:00", "end": "19:00"}]})
        self.assertEqual(c["post_windows"], [{"start": "11:00", "end": "22:00"}])

    def test_changing_window_reschedules_waiting_posts_and_updates_sync(self):
        now = datetime(2026, 11, 20, 8).timestamp()
        with patch("studio.posting.time.time", return_value=now):
            self.queue_clips(2)
            version = ctx.version()
            response = self.client.put(f"/api/creators/{self.job['creator']}", json={
                "post_window_enabled": True, "post_start_time": "09:00", "post_end_time": "21:00",
                "daily_cap_enabled": True, "daily_cap": 6}, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([p["due_at"] for p in posting._posts], [
            datetime(2026, 11, 20, 9).timestamp(), datetime(2026, 11, 20, 11).timestamp()])
        self.assertGreater(ctx.version(), version)
        mirrored = jobs.get(self.job["id"])["clips"][1]["posts"][posting._posts[1]["id"]]
        self.assertEqual(mirrored["due_at"], datetime(2026, 11, 20, 11).timestamp())

    def test_changing_one_creators_daily_limit_leaves_other_creators_queue_alone(self):
        first = self.window(daily_cap=2)
        second = creators.create({**first, "name": "Second creator"})
        other_job = jobs.create({"type": "file", "name": "Second video"}, {}, enqueue=False, creator=second["id"])
        other_job["clips"] = [{"id": f"other-{i}", "status": "done"} for i in range(3)]
        jobs.save(other_job)
        now = datetime(2026, 11, 20, 8).timestamp()
        with patch("studio.posting.time.time", return_value=now):
            self.queue_clips(3)
            for clip in other_job["clips"]:
                posting.enqueue(other_job, clip, ["youtube"])
            before = [p["due_at"] for p in posting._posts if p["creator"] == second["id"]]
            response = self.client.put(f"/api/creators/{first['id']}", json={"daily_cap": 4},
                                       headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(before, [datetime(2026, 11, 21, 15).timestamp(),
                                  datetime(2026, 11, 22, 9).timestamp(),
                                  datetime(2026, 11, 22, 15).timestamp()])
        self.assertEqual([p["due_at"] for p in posting._posts if p["creator"] == second["id"]], before)

    def test_manual_post_now_bypasses_window(self):
        self.window()
        now = datetime(2026, 11, 20, 23).timestamp()
        with patch("studio.posting.time.time", return_value=now):
            self.assertEqual(self.post(now=True).status_code, 200)
        self.assertEqual([p["due_at"] for p in posting._posts], [now, now])

    def test_overdue_posts_wait_for_next_window_after_restart(self):
        self.window()
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            self.queue_clips(2)
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 22).timestamp()):
            posting._run_due()
        self.assertEqual([p["status"] for p in posting._posts], ["scheduled", "scheduled"])
        self.assertEqual([p["due_at"] for p in posting._posts], [
            datetime(2026, 11, 21, 9).timestamp(), datetime(2026, 11, 21, 11).timestamp()])

    def test_overdue_posts_do_not_burst_or_skip_a_slot_because_upload_was_late(self):
        self.window()
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            self.queue_clips(3)
        with patch.object(accounts, "youtube_upload", return_value=("remote", "https://example.com/video")):
            with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 15, 30).timestamp()):
                posting._run_due()
            self.assertEqual([p["status"] for p in posting._posts], ["posted", "scheduled", "scheduled"])
            self.assertEqual([p["due_at"] for p in posting._posts[1:]], [
                datetime(2026, 11, 20, 17).timestamp(), datetime(2026, 11, 20, 19).timestamp()])
            with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 17, 0, 7).timestamp()):
                posting._run_due()
        self.assertEqual([p["status"] for p in posting._posts], ["posted", "posted", "scheduled"])

    def test_retry_respects_window(self):
        self.window()
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 19).timestamp()):
            self.queue_clips(1, "instagram")
        post = posting._posts[0]
        post["status"] = "posting"
        with patch.object(accounts, "instagram_upload", side_effect=RuntimeError("Temporary failure")), \
             patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 20, 55).timestamp()):
            posting._post(post)
        self.assertEqual(post["status"], "scheduled")
        self.assertEqual(post["due_at"], datetime(2026, 11, 21, 9).timestamp())
        post["status"] = "failed"
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 21, 22).timestamp()):
            posting.retry(post["id"])
        self.assertEqual(post["due_at"], datetime(2026, 11, 22, 9).timestamp())

    def test_enabling_window_keeps_existing_retry_cooldown(self):
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 10, 58).timestamp()):
            self.queue_clips(1, "instagram")
            post = posting._posts[0]
            post["status"] = "posting"
            with patch.object(accounts, "instagram_upload", side_effect=RuntimeError("Temporary failure")):
                posting._post(post)
        self.assertEqual(post["due_at"], datetime(2026, 11, 20, 11, 8).timestamp())
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 10, 59).timestamp()):
            response = self.client.put(f"/api/creators/{self.job['creator']}", json={
                "post_window_enabled": True, "post_start_time": "09:00", "post_end_time": "21:00",
                "daily_cap_enabled": True, "daily_cap": 6}, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(post["due_at"], datetime(2026, 11, 20, 13).timestamp())

    def test_invalid_batches_have_no_partial_effect(self):
        invalid = [
            ({"accounts": []}, 400), ({"accounts": ["foreign"]}, 400),
            ({"accounts": "youtube"}, 400), ({"accounts": ["youtube"] * 21}, 400),
            ({"clips": []}, 400), ({"clips": "one"}, 400),
            ({"clips": [None]}, 400), ({"clips": [[[], "one"]]}, 400),
            ({"clips": self.pairs * 76}, 400),
            ({"clips": self.pairs + [[self.job["id"], "busy"]]}, 404),
            ({"clips": self.pairs + [[self.foreign["id"], "private"]]}, 404),
        ]
        for body, expected in invalid:
            with self.subTest(body=body):
                response = self.post(**body)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual(posting._posts, [])

    def test_previously_queued_clip_does_not_block_the_rest(self):
        self.assertEqual(self.post(clips=self.pairs[:1]).status_code, 200)
        response = self.post()
        self.assertEqual(response.json(), {"queued": 1, "clips": 1, "skipped": 1})

    def reorder(self, order):
        return self.client.post(f"/api/creators/{self.job['creator']}/timeline", json={"order": order},
                                headers={"Origin": "http://localhost"})

    def test_timeline_lists_waiting_posts_and_reordering_swaps_their_times(self):
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            posting.enqueue(self.job, self.job["clips"][0], ["youtube"])
            posting.enqueue(self.job, self.job["clips"][1], ["youtube"])
        rows = posting.timeline(self.job["creator"])
        self.assertEqual([r["title"] for r in rows], ["one", "two"])
        slots = [r["due_at"] for r in rows]
        version = ctx.version()
        response = self.reorder([rows[1]["id"], rows[0]["id"]])
        self.assertEqual(response.status_code, 200, response.text)
        after = response.json()["timeline"]
        self.assertEqual([r["title"] for r in after], ["two", "one"])
        self.assertEqual(sorted(r["due_at"] for r in after), slots)   # the same posting times, swapped
        self.assertGreater(ctx.version(), version)

    def test_reordering_one_account_leaves_the_other_accounts_times_alone(self):
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            posting.enqueue(self.job, self.job["clips"][0], ["youtube", "instagram"])
            posting.enqueue(self.job, self.job["clips"][1], ["youtube"])
        youtube = [r for r in posting.timeline(self.job["creator"]) if r["account"] == "youtube"]
        instagram = {r["id"]: r["due_at"] for r in posting.timeline(self.job["creator"]) if r["account"] == "instagram"}
        self.assertEqual(self.reorder([youtube[1]["id"], youtube[0]["id"]]).status_code, 200)
        after = posting.timeline(self.job["creator"])
        self.assertEqual([r["title"] for r in after if r["account"] == "youtube"], ["two", "one"])
        self.assertEqual({r["id"]: r["due_at"] for r in after if r["account"] == "instagram"}, instagram)

    def test_reorder_refuses_unknown_finished_or_invented_posts(self):
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            posting.enqueue(self.job, self.job["clips"][0], ["youtube"])
            posting.enqueue(self.job, self.job["clips"][1], ["youtube"])
        rows = posting.timeline(self.job["creator"])
        saved = [(p["id"], p["due_at"]) for p in posting._posts]
        for order, expected in (([rows[0]["id"], "nope"], 400),
                                ([rows[0]["id"], rows[0]["id"]], 400),
                                ("not-a-list", 400),
                                ([rows[0]["id"] + "x"], 400)):
            with self.subTest(order=order):
                response = self.reorder(order)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual([(p["id"], p["due_at"]) for p in posting._posts], saved)
        response = self.client.post("/api/creators/missing/timeline", json={"order": []},
                                    headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 404)
        posting.cancel(rows[0]["id"])
        self.assertEqual(self.reorder([rows[0]["id"]]).status_code, 400)

    def test_creator_payload_carries_the_timeline_for_the_setup_page(self):
        with patch("studio.posting.time.time", return_value=datetime(2026, 11, 20, 8).timestamp()):
            posting.enqueue(self.job, self.job["clips"][0], ["youtube"])
        response = self.client.get("/api/creators")
        self.assertEqual(response.status_code, 200, response.text)
        row = next(c for c in response.json() if c["id"] == self.job["creator"])
        self.assertEqual([p["title"] for p in row["timeline"]], ["one"])

    def test_deleting_a_video_takes_its_waiting_posts_and_history_out(self):
        self.assertEqual(self.post().status_code, 200)
        posting._posts[0].update(status="posted", posted_at=time.time())
        self.job["status"] = "done"
        jobs.save(self.job)
        self.assertEqual(len(posting.timeline(self.job["creator"])), 1)
        self.assertEqual(account.delete_many([self.job["id"]], []), {"videos": 1, "clips": 0, "skipped": 0})
        self.assertEqual(posting.timeline(self.job["creator"]), [])
        self.assertEqual(posting._posts, [])
        self.assertNotIn(self.job["id"], posting._FILE.read_text(encoding="utf-8"))

    def test_deleting_one_clip_takes_only_its_posts_out_of_the_timeline(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertTrue(account.delete_clip(self.job["id"], "one"))
        self.assertEqual([p["title"] for p in posting.timeline(self.job["creator"])], ["two"])
        self.assertEqual([p["clip"] for p in posting._posts], ["two"])

    def test_posts_of_an_already_deleted_video_never_reach_the_timeline(self):
        self.assertEqual(self.post(clips=self.pairs[:1]).status_code, 200)
        jobs.delete(self.job["id"])
        self.assertEqual(posting.timeline(self.job["creator"]), [])
        self.assertEqual(posting._posts, [])

    def test_old_posts_of_deleted_videos_are_cleared_when_the_queue_starts(self):
        posting._posts.extend([
            {"id": "old", "user": self.owner.id, "job": "missing", "clip": "gone", "status": "scheduled", "due_at": 0},
            {"id": "keep", "user": self.owner.id, "job": self.job["id"], "clip": "one", "status": "scheduled", "due_at": 0},
        ])
        with ctx.as_user(None), patch("studio.posting.threading.Thread"):   # the server starts before anyone signs in
            posting.start()
        self.assertEqual([p["id"] for p in posting._posts], ["keep"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
