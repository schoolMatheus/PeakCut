"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import secrets
import math
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import json

from . import accounts, alerts, config, creators, ctx, jobs, live

_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"
try:
    _posts: list = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else []
except ValueError:
    _posts = []


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting")
SCHEDULE_FIELDS = ("post_gap_min", "daily_cap_enabled", "daily_cap", "post_window_enabled", "post_start_time",
                   "post_end_time", "post_windows", "post_fixed_times")
FIXED_GRACE = 30 * 60   # a post planned for a fixed time may still go out this long after it


def _save():
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_posts, indent=1), encoding="utf-8")
        tmp.replace(_FILE)


def _drop(gone):
    """Forget these posts for good and tell the page their queue changed."""
    if not gone:
        return
    ids = {p["id"] for p in gone}
    _posts[:] = [p for p in _posts if p["id"] not in ids]
    _save()
    for uid in {p.get("user") for p in gone if p.get("user")}:
        ctx.bump(uid)


def forget(uid: str):
    """Drop everything queued or recorded for a deleted account, so nothing is posted or retried for it any more."""
    with _lock:
        for p in _posts:
            if p.get("user") == uid:
                p["status"] = "deleted"          # the posting loop may be holding this one already: it then skips it
        _posts[:] = [p for p in _posts if p.get("user") != uid]
        _save()


def forget_video(jid, cid=None):
    """Drop the posts of a deleted video, or of just one of its clips. They must leave the timeline and the history
    for good: there is nothing left to post, and a deleted clip must not keep failing in the background."""
    with _lock:
        _drop([p for p in _posts if p.get("job") == jid and (cid is None or p.get("clip") == cid)])


def _drop_deleted():
    """Clear posts left behind by videos or clips deleted before this fix, so they stop showing up on the page."""
    with _lock:
        gone = []
        for p in _posts:
            job = jobs.get(p.get("job"))
            if not job or not any(c["id"] == p.get("clip") for c in job["clips"]):
                gone.append(p)
        _drop(gone)


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _mirror(p):
    """Copy the post's state onto its clip so the clip card can show it."""
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    if clip is not None:
        clip.setdefault("posts", {})[p["id"]] = {k: p.get(k) for k in ("account_name", "platform", "status", "url", "due_at", "error")}
        jobs.save(job)


def _windows(c):
    """The parts of the day the creator posts in, or None when posting hours are off. Older creators have one
    range, kept in post_start_time and post_end_time."""
    if not c.get("post_window_enabled"):
        return None
    rows = c.get("post_windows") or []
    return [(w["start"], w["end"]) for w in rows] if rows else [(c["post_start_time"], c["post_end_time"])]


def _fixed_times(c):
    """Exact clock times posts go out at, instead of being spread across the ranges."""
    return list(c.get("post_fixed_times") or []) if c.get("post_window_enabled") else []


def _timed(c):
    return bool(_windows(c) or _fixed_times(c))


def _seconds(value):
    h, m = value.split(":")
    return int(h) * 3600 + int(m) * 60


def _day_start(t):
    return datetime.combine(datetime.fromtimestamp(t).date(), datetime.min.time()).timestamp()


def _next_day(t):
    day = datetime.fromtimestamp(t).date() + timedelta(days=1)
    return datetime.combine(day, datetime.min.time()).timestamp()


def _spans(t, windows):
    day = _day_start(t)
    return [(day + _seconds(s), day + _seconds(e)) for s, e in windows]


def _next_range_start(t, windows):
    """The first moment at or after t that falls inside one of the ranges."""
    while True:
        for start, end in _spans(t, windows):
            if t < start:
                return start
            if t < end:
                return t
        t = _next_day(t)


def _at_offset(spans, offset):
    """The moment that many seconds into the day's ranges, skipping the gaps between them."""
    for start, end in spans:
        if offset <= end - start + 1e-9:
            return start + offset
        offset -= end - start
    return None


def _spread_slot(t, windows, cap):
    """The first of the cap daily times, spread evenly across every range, that is at or after t. None when the
    day's slots are used up."""
    spans = _spans(t, windows)
    step = sum(end - start for start, end in spans) / cap
    elapsed = sum(end - start if t >= end else max(0, t - start) for start, end in spans)
    index = math.ceil(elapsed / step - 1e-9)
    return None if index >= cap else _at_offset(spans, index * step)


def _current_slot(t, windows, cap):
    """The daily time slot t itself belongs to."""
    spans = _spans(t, windows)
    step = sum(end - start for start, end in spans) / cap
    elapsed = sum(end - start if t >= end else max(0, t - start) for start, end in spans)
    return _at_offset(spans, math.floor(elapsed / step + 1e-9) * step)


def _next_slot(account_id, c, *, earliest=0, exclude=()):
    """Reserve the next time for this account, following its ranges, fixed times and daily limit."""
    mine = [p for p in _mine() if p["account"] == account_id and p["status"] in OPEN + ("posted",)
            and p["id"] not in exclude]
    fixed = _fixed_times(c)
    if fixed:
        return _next_fixed(mine, fixed, c, earliest)
    window = _windows(c)
    cap = c["daily_cap"] if c.get("daily_cap_enabled", False) else None
    last = max((p.get("posted_at") or p["due_at"] for p in mine), default=0)
    t = max(time.time(), earliest, last + (0.001 if window and cap else c["post_gap_min"] * 60))
    while True:
        if window:
            if cap:
                slot = _spread_slot(t, window, cap)
                if slot is None:
                    t = _next_day(t)
                    continue
                t = slot
            else:
                t = _next_range_start(t, window)
        if cap is None or sum(1 for p in mine if _day(p.get("posted_at") or p["due_at"]) == _day(t)) < cap:
            return t
        t = _next_day(t)


def _next_fixed(mine, fixed, c, earliest):
    """The next fixed time that is free for this account, today or on a later day."""
    cap = c["daily_cap"] if c.get("daily_cap_enabled", False) else None
    taken = {p["due_at"] for p in mine}
    t = max(time.time(), earliest)
    while True:
        day = _day_start(t)
        used = sum(1 for p in mine if _day(p.get("posted_at") or p["due_at"]) == _day(day))
        for value in fixed:
            when = day + _seconds(value)
            if when < t or when in taken:
                continue
            if cap is not None and used >= cap:
                break
            return when
        t = _next_day(t)


def _scheduled_at(account_id, c, *, earliest=0, exclude=()):
    return _next_slot(account_id, c, earliest=earliest, exclude=exclude)


def _reschedule_accounts(account_ids):
    """Keep waiting clips in order when times change or the app missed a window."""
    with _lock:
        pending = sorted((p for p in _mine() if p["account"] in account_ids and p["status"] == "scheduled"
                          and not p.get("immediate")), key=lambda p: (p["due_at"], p["created"]))
        excluded = {p["id"] for p in pending}
        changed = []
        for p in pending:
            c = creators.get(p["creator"]) or creators.DEFAULTS
            due = _scheduled_at(p["account"], c, earliest=p.get("retry_at", 0), exclude=excluded)
            if due != p["due_at"]:
                p["due_at"] = due
                changed.append(p)
            excluded.remove(p["id"])
        if changed:
            _save()
            for p in changed:
                _mirror(p)
            ctx.bump()


def reschedule(creator_id):
    with _lock:
        affected = {p["account"] for p in _mine() if p["creator"] == creator_id and p["status"] == "scheduled"}
        _reschedule_accounts(affected)


def enqueue(job, clip, account_ids, now=False) -> list:
    c = creators.get(job["creator"]) or creators.DEFAULTS
    made = []
    with _lock:
        for aid in account_ids:
            acc = accounts.get(aid)
            if not acc:
                continue
            if any(p["job"] == job["id"] and p["clip"] == clip["id"] and p["account"] == aid
                   and p["status"] in OPEN + ("posted",) for p in _mine()):
                continue  # never post the same clip to the same account twice
            due = time.time() if now else _scheduled_at(aid, c)
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None,
                 "immediate": bool(now)}
            _posts.append(p)
            made.append(p)
        _save()
    for p in made:
        _mirror(p)
    return made


def on_clip_ready(job, clip):
    """Called once when a clip first finishes rendering."""
    c = creators.get(job["creator"])
    if not c or not c["autopost"] or not c["accounts"]:
        return
    if clip.get("score", 0) < c["post_min_score"]:
        return  # includes hand-made clips (score 0): those wait for you to press Post
    made = enqueue(job, clip, c["accounts"])
    if made:
        creators.log(c["id"], f"Queued for {len(made)} account(s): {clip.get('title') or clip.get('hook')}")


def _caption(clip, creator):
    # A description the person wrote themselves is posted as it is, without the AI's hashtags added to it, and
    # one that already ends with its hashtags does not get them twice.
    tags = "" if clip.get("fixed_description") or clip.get("hashtags_in_description") else " ".join("#" + h for h in clip.get("hashtags", []))
    return "\n\n".join(x for x in (clip.get("description", ""), tags, creator.get("post_footer", "")) if x)


def _post(p):
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    acc = accounts.get(p["account"])
    try:
        if not clip or clip.get("status") != "done":
            raise RuntimeError("The clip no longer exists or is not finished.")
        if not acc:
            raise RuntimeError("That account was disconnected.")
        path = Path(job["dir"]) / "clips" / clip["file"]
        creator = creators.get(job["creator"]) or dict(creators.DEFAULTS, name="clips")
        title, cap = clip.get("title") or clip.get("hook") or "Clip", _caption(clip, creator)
        if acc["platform"] == "youtube":
            rid, url = accounts.youtube_upload(acc, path, title, cap + "\n\n#Shorts", clip.get("hashtags", []), creator["privacy"])
        elif acc["platform"] == "instagram":
            rid, url = accounts.instagram_upload(acc, path, f"{title}\n\n{cap}", job["id"])
        elif acc["platform"] == "tiktok":
            rid, url = accounts.tiktok_upload(acc, path, f"{title}\n\n{cap}", creator["privacy"])
        else:
            raise RuntimeError("That kind of account is no longer supported.")
        p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None)
        creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
    except Exception as e:  # noqa: BLE001
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        if p["tries"] < 3 and clip and acc and acc["platform"] != "youtube":
            p.update(status="scheduled", due_at=time.time() + 600)  # try again in 10 minutes
            p["retry_at"] = p["due_at"]
            c = creators.get(p["creator"]) or creators.DEFAULTS
            if _timed(c) and not p.get("immediate"):
                with _lock:
                    p["due_at"] = _scheduled_at(p["account"], c, earliest=p["retry_at"], exclude={p["id"]})
        else:
            p["status"] = "failed"
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


def _window_available(p, c, now):
    """Check actual sends too: a paused or stopped app may have missed its planned times."""
    if p.get("immediate"):
        return True
    sent = [q for q in _mine() if q["id"] != p["id"] and q["account"] == p["account"]
            and q["status"] in ("posted", "posting")]
    stamps = [q.get("posted_at") or q["due_at"] for q in sent]
    fixed = _fixed_times(c)
    if fixed:
        if now < p["due_at"] or now > p["due_at"] + FIXED_GRACE:
            return False
        if c.get("daily_cap_enabled"):
            return sum(_day(t) == _day(now) for t in stamps) < c["daily_cap"]
        return True
    window = _windows(c)
    if not window:
        return True
    if not any(start <= now < end for start, end in _spans(now, window)):
        return False
    if c.get("daily_cap_enabled"):
        cap = c["daily_cap"]
        if sum(_day(t) == _day(now) for t in stamps) >= cap:
            return False
        slot = _current_slot(now, window, cap)
        return slot is not None and not any(t >= slot for t in stamps)
    return all(t + c["post_gap_min"] * 60 <= now for t in stamps)


def _run_due():
    due = sorted((p for p in list(_posts) if p["status"] == "scheduled" and p["due_at"] <= time.time()
                  and p.get("user")), key=lambda p: p["due_at"])
    for p in due:
        try:
            with ctx.as_user(p["user"]):
                if accounts.settings()["paused"]:
                    continue
                with _lock:
                    if p["status"] != "scheduled" or p["due_at"] > time.time():
                        continue
                    c = creators.get(p["creator"]) or creators.DEFAULTS
                    if not _window_available(p, c, time.time()):
                        _reschedule_accounts({p["account"]})
                        continue
                    p["status"] = "posting"
                _post(p)
        except Exception:  # noqa: BLE001
            pass


def _run():
    while True:
        time.sleep(15)
        _run_due()


def _stats_for(recent):
    for aid in {p["account"] for p in recent if p["platform"] == "youtube"}:
        acc = accounts.get(aid)
        if acc:
            views = accounts.youtube_views(acc, [p["remote_id"] for p in recent if p["account"] == aid])
            for p in recent:
                if p["account"] == aid and p["remote_id"] in views:
                    p["views"] = views[p["remote_id"]]
    for p in recent:
        acc = accounts.get(p["account"]) if p["platform"] == "instagram" else None
        if acc:
            v = accounts.instagram_views(acc, p["remote_id"])
            if v is not None:
                p["views"] = v


def _stats():
    while True:
        for uid in {p.get("user") for p in _posts if p.get("user")}:
            try:
                with ctx.as_user(uid):
                    _stats_for([p for p in _mine() if p["status"] == "posted" and time.time() - p["posted_at"] < 14 * 86400])
            except Exception:  # noqa: BLE001
                pass
        _save()
        time.sleep(1800)


def cancel(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be cancelled.")
        p["status"] = "cancelled"
        _save()
    _mirror(p)


def retry(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled posts can be retried.")
        c = creators.get(p["creator"]) or creators.DEFAULTS
        due = _scheduled_at(p["account"], c, exclude={p["id"]}) if _timed(c) and not p.get("immediate") else time.time()
        p.update(status="scheduled", due_at=due, tries=0, error=None)
        p.pop("retry_at", None)
        _save()
    _mirror(p)


def timeline(creator_id) -> list:
    """The posts still waiting for this creator, in the order they will go out, with a little of their clip for
    the page. Only what the signed-in user owns is read, like everywhere else."""
    with _lock:
        waiting = sorted((p for p in _mine() if p["creator"] == creator_id and p["status"] == "scheduled"),
                         key=lambda p: (p["due_at"], p["created"]))
    out = []
    for p in waiting:
        job = jobs.get(p["job"])
        clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
        out.append({"id": p["id"], "due_at": p["due_at"], "account": p["account"], "account_name": p["account_name"],
                    "platform": p["platform"], "title": p["title"], "score": p.get("score", 0),
                    "immediate": bool(p.get("immediate")), "job": p["job"], "clip": p["clip"],
                    "cover": (clip or {}).get("cover", ""), "version": (clip or {}).get("version", 0)})
    return out


def reorder(creator_id, ordered_ids) -> int:
    """Put the waiting posts of each account in the order the person chose, keeping the same times. Moving a video
    only changes which of those times it goes out at, so the posting hours, fixed times and daily limit still hold."""
    if not isinstance(ordered_ids, list) or len(ordered_ids) > 500 or any(not isinstance(x, str) for x in ordered_ids):
        raise ValueError("The new order must be a list of posts.")
    with _lock:
        waiting = {p["id"]: p for p in _mine() if p["creator"] == creator_id and p["status"] == "scheduled"}
        if len(set(ordered_ids)) != len(ordered_ids):
            raise ValueError("Each post can appear only once in the new order.")
        if any(pid not in waiting for pid in ordered_ids):
            raise ValueError("Some of these posts are no longer waiting.")
        changed = []
        for account_id in {waiting[pid]["account"] for pid in ordered_ids}:
            sent = [waiting[pid] for pid in ordered_ids if waiting[pid]["account"] == account_id]
            slots = sorted(p["due_at"] for p in sent)
            for p, due in zip(sent, slots):
                if p["due_at"] != due:
                    p["due_at"] = due
                    changed.append(p)
        if changed:
            _save()
            for p in changed:
                _mirror(p)
            ctx.bump()
    return len(changed)


def overview() -> dict:
    today = _day(time.time())
    _posts = _mine()  # everything below is about the signed-in user only
    rows = []
    all_cs, all_js = creators.all_creators(), jobs.list_jobs()
    for c in all_cs:
        js = [j for j in all_js if j["creator"] == c["id"]]
        clips = [k for j in js for k in j["clips"] if k.get("status") == "done"]
        ps = [p for p in _posts if p["creator"] == c["id"]]
        views = sum(p["views"] or 0 for p in ps)
        rows.append({
            "id": c["id"], "name": c["name"],
            "state": "recording" if live.active_for(c["id"]) else "waiting" if c["auto_watch"] and c["channel"] else "idle",
            "autopost": c["autopost"] and bool(c["accounts"]),
            "clips_today": sum(1 for k in clips if _day(k.get("made_at", 0)) == today), "clips_total": len(clips),
            "posted_today": sum(1 for p in ps if p["status"] == "posted" and _day(p["posted_at"]) == today),
            "posted_total": sum(1 for p in ps if p["status"] == "posted"),
            "queued": sum(1 for p in ps if p["status"] in OPEN), "failed": sum(1 for p in ps if p["status"] == "failed"),
            "views": views, "earnings": round(views / 1000 * c["rate_per_1k"], 2)})
    names = {c["id"]: c["name"] for c in all_cs}
    queue = sorted((p for p in _posts if p["status"] in OPEN), key=lambda p: p["due_at"])
    done = sorted((p for p in _posts if p["status"] in ("posted", "failed")), key=lambda p: -(p.get("posted_at") or p["due_at"]))[:15]
    show = [{**{k: v for k, v in p.items() if k != "user"}, "creator_name": names.get(p["creator"], "")} for p in queue + done]
    # last 14 days, oldest first, for the home graph
    days = [_day(time.time() - 86400 * i) for i in range(13, -1, -1)]
    made, posted, views = {d: 0 for d in days}, {d: 0 for d in days}, {d: 0 for d in days}
    for j in all_js:
        for k in j["clips"]:
            d = _day(k.get("made_at") or j["created"])
            if k.get("status") == "done" and d in made:
                made[d] += 1
    for p in _posts:
        if p["status"] == "posted" and _day(p["posted_at"]) in posted:
            posted[_day(p["posted_at"])] += 1
            views[_day(p["posted_at"])] += p["views"] or 0
    daily = [{"day": d, "clips": made[d], "posted": posted[d], "views": views[d]} for d in days]
    return {"paused": accounts.settings()["paused"], "creators": rows, "posts": show, "daily": daily,
            "alerts": alerts.configured(), "recording": len(live.active_mine())}


def digest_text() -> str:
    o = overview()
    lines = ["PeakCut daily summary"]
    for r in o["creators"]:
        lines.append(f"- {r['name']}: {r['state']}, {r['clips_today']} clips today, {r['posted_today']} posted today, "
                     f"{r['queued']} waiting, {r['failed']} failed, {r['views']} views")
    if not o["creators"]:
        lines.append("No creators set up yet.")
    if o["paused"]:
        lines.append("Posting is paused.")
    return "\n".join(lines)


def start():
    with _lock:
        _drop_deleted()
        for p in _posts:
            if p["status"] == "posting":  # interrupted mid-upload last time
                p.update(status="failed", error="The studio closed during this upload. Check the account, then retry.")
        _save()
    jobs.on_clip_ready.append(on_clip_ready)
    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_stats, daemon=True).start()
    threading.Thread(target=alerts.digest_loop, args=(digest_text,), daemon=True).start()
    threading.Thread(target=alerts.keep_awake_loop,
                     args=(lambda: bool(live.active()) or any(p["status"] in OPEN for p in _posts),), daemon=True).start()
