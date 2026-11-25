"""Creator workspaces. Each user has their own: users/<user>/creators/<id>/{videos,live,creator.json,log.jsonl}."""
import copy
import json
import re
import threading
import time
from pathlib import Path

from . import config, ctx


def _root() -> Path:
    return config.user_dir(ctx.uid()) / "creators"

_lock = threading.RLock()

DEFAULTS = {"name": "", "channel": "", "style": "bounce", "layout": "blur", "captions": True, "min_len": 15, "max_len": 60,
            "max_clips": 6, "min_score": 80, "auto_watch": False, "buffer_min": 20, "live_per_hour": 4, "ai_every_min": 3, "ai_only_on_spike": True, "auto_jump_pct": 60,
            "plan": "free", "decide_mode": "both", "must_clip_min": 0, "min_gap_min": 2,
            "game": "", "hook_text": "", "description_text": "",
            "trigger_words": "clip that, clip it",
            "autopost": False, "accounts": [], "post_min_score": 75, "post_gap_min": 45, "daily_cap_enabled": False, "daily_cap": 6,
            "post_window_enabled": False, "post_start_time": "09:00", "post_end_time": "21:00",
            "post_windows": [], "post_fixed_times": [],
            "privacy": "public", "post_footer": "", "rate_per_1k": 0.0, "channels": {},
            "lang": "en", "interactions": [], "interaction_styles": [], "interaction_style": "",
            "custom_motions": [], "custom_motion_selected": {}}
PLATFORMS = ("youtube", "twitch", "kick")


def clean_hook(v) -> str:
    """The big text a person typed for their clips: one line, as they wrote it (links and @names stay, a campaign
    may need them), and short enough to sit in the box on the video."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(v or "")).split())[:60]


def clean_interactions(value) -> list:
    """Only the ready-made audience requests, each selected at most once."""
    from .interactions import ACTIONS
    if not isinstance(value, list) or any(not isinstance(action, str) or action not in ACTIONS for action in value):
        raise ValueError("Audience requests must be a list of valid actions.")
    return list(dict.fromkeys(value))


MOTION_ID = re.compile(r"^motion-[a-z0-9]{10}$")


def clean_custom_motions(value) -> list:
    """Motions this creator approved: a small list of files under their own workspace."""
    from .interactions import ACTIONS
    if not isinstance(value, list):
        raise ValueError("The custom motions must be a list.")
    if len(value) > 20:
        raise ValueError("You can keep up to 20 custom motions. Delete one first.")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("That custom motion isn't valid.")
        mid, file = str(item.get("id") or ""), str(item.get("file") or "")
        action = str(item.get("action") or "")
        if not MOTION_ID.fullmatch(mid) or file != mid + ".webm" or action not in ACTIONS:
            raise ValueError("That custom motion isn't valid.")
        name = " ".join("".join(ch if ch.isprintable() else " " for ch in str(item.get("name") or "")).split())[:40]
        try:
            created = float(item.get("created"))
        except (TypeError, ValueError):
            created = 0.0
        out.append({"id": mid, "action": action, "name": name or action, "file": file, "created": created,
                    "note": " ".join(str(item.get("note") or "").split())[:160]})
    return out


def clean_custom_motion_selected(value) -> dict:
    from .interactions import ACTIONS
    if not isinstance(value, dict):
        raise ValueError("The chosen custom motions aren't valid.")
    return {a: str(v) for a, v in value.items() if a in ACTIONS and isinstance(v, str) and MOTION_ID.fullmatch(v)}


def clean_description(v) -> str:
    """The description a person typed for their clips: as they wrote it, line breaks included, so a campaign's
    set text (links, @names, hashtags) is not changed. Control characters go, and it is cut at 2000 characters."""
    nl, cr, tab = chr(10), chr(13), chr(9)
    lines = ("".join(ch if ch.isprintable() or ch == tab else " " for ch in line).rstrip()
             for line in str(v or "").replace(cr + nl, nl).replace(cr, nl).split(nl))
    return nl.join(lines).strip()[:2000]


def platform_key(url: str) -> str:
    from urllib.parse import urlparse
    h = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return "youtube" if "youtube" in h or h == "youtu.be" else "twitch" if "twitch" in h else "kick" if "kick" in h else ""


def _fix(c: dict) -> dict:
    """One channel per platform in `channels`; `channel` is the one to record from and is always one of them."""
    ch = {k: v for k, v in (c.get("channels") or {}).items() if k in PLATFORMS and v}
    if c.get("channel") and platform_key(c["channel"]) and c["channel"] not in ch.values():
        ch.setdefault(platform_key(c["channel"]), c["channel"])
    c["channels"] = ch
    if not c.get("ai_every_min"):
        c["ai_every_min"] = 3       # "Auto" (0) is gone: jumps in loudness and chat are now always checked
    if c.get("channel") not in ch.values():
        c["channel"] = next((ch[k] for k in PLATFORMS if k in ch), "")
    ids = {s.get("id") for s in c.get("interaction_styles") or []}
    if c.get("interaction_style") not in ids:
        c["interaction_style"] = ""      # the chosen style was deleted, or came from an older page: YouTube again
    motions = {m.get("id") for m in c.get("custom_motions") or []}
    c["custom_motion_selected"] = {a: mid for a, mid in (c.get("custom_motion_selected") or {}).items()
                                   if mid in motions}
    return c
NUMERIC = {"min_len": (5, 120), "max_len": (10, 180), "max_clips": (1, 20), "min_score": (0, 100), "buffer_min": (5, 120), "live_per_hour": (1, 60), "ai_every_min": (1, 30), "auto_jump_pct": (20, 300), "must_clip_min": (0, 240), "min_gap_min": (0, 60),
           "post_min_score": (0, 100), "post_gap_min": (5, 1440), "daily_cap": (1, 50)}
TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def clean_time(value) -> str:
    if not isinstance(value, str) or not TIME.fullmatch(value):
        raise ValueError("Enter posting times in HH:MM format, from 00:00 to 23:59.")
    return value


def clean_windows(value) -> list:
    """The parts of the day a creator posts in. Each range starts and ends on the same day."""
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("Posting time ranges must be a list of up to 8 ranges.")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each posting time range needs a start and an end.")
        start, end = clean_time(item.get("start")), clean_time(item.get("end"))
        if end <= start:
            raise ValueError("The posting end time must be later than the start time on the same day.")
        out.append({"start": start, "end": end})
    out.sort(key=lambda w: w["start"])
    merged = []
    for w in out:                       # ranges that touch or overlap become one, so a time is never counted twice
        if merged and w["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], w["end"])
        else:
            merged.append(dict(w))
    return merged


def clean_fixed_times(value) -> list:
    """Exact clock times, instead of spreading posts across the ranges."""
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError("Fixed posting times must be a list of up to 24 times.")
    return sorted({clean_time(v) for v in value})


def root(cid) -> Path:
    if not re.fullmatch(r"[\w\-]{1,60}", cid or ""):
        raise ValueError("bad creator id")
    return _root() / cid


def _clean(data: dict) -> dict:
    out = {}
    for k in DEFAULTS:
        if k == "interactions":
            if k in data:
                out[k] = clean_interactions(data[k])
            continue
        if k == "interaction_styles":
            if k in data:
                from . import interaction_styles
                out[k] = interaction_styles.clean_styles(data[k])
            continue
        if k == "interaction_style":
            if k in data:
                v = data[k]
                if not isinstance(v, str) or (v and not re.fullmatch(r"[a-z0-9][a-z0-9\-]{2,23}", v)):
                    raise ValueError("That interaction style isn't valid.")
                out[k] = v
            continue
        if k == "custom_motions":
            if k in data:
                out[k] = clean_custom_motions(data[k])
            continue
        if k == "custom_motion_selected":
            if k in data:
                out[k] = clean_custom_motion_selected(data[k])
            continue
        if k == "post_windows":
            if k in data:
                out[k] = clean_windows(data[k])
            continue
        if k == "post_fixed_times":
            if k in data:
                out[k] = clean_fixed_times(data[k])
            continue
        if k in ("post_start_time", "post_end_time") and k in data:
            clean_time(data[k])
        if k == "post_window_enabled" and k in data and not isinstance(data[k], bool):
            raise ValueError("The daily posting window must be on or off.")
        if k == "captions":
            if k in data:
                if not isinstance(data[k], bool):
                    raise ValueError("Captions must be on or off.")
                out[k] = data[k]
            continue
        if k not in data or data[k] is None:
            continue
        v = data[k]
        if k in NUMERIC:
            lo, hi = NUMERIC[k]
            v = max(lo, min(hi, int(float(v))))
        elif k in ("auto_watch", "autopost", "ai_only_on_spike", "daily_cap_enabled", "post_window_enabled"):
            v = bool(v)
        elif k == "channels":
            v = {p: str(u).strip()[:300] for p, u in (v.items() if isinstance(v, dict) else []) if p in PLATFORMS and str(u).strip()}
        elif k == "accounts":
            v = [str(x) for x in v][:20] if isinstance(v, list) else []
        elif k == "hook_text":
            v = clean_hook(v)
        elif k == "description_text":
            v = clean_description(v)
        elif k == "rate_per_1k":
            v = max(0.0, min(1000.0, float(v or 0)))
        elif k == "plan":
            v = v if v in ("free", "pro", "max") else "free"
        elif k == "decide_mode":
            v = v if v in ("both", "either") else "both"
        elif k == "privacy":
            v = v if v in ("public", "unlisted", "private") else "public"
        elif k == "lang":
            from . import fetch_model
            v = v if fetch_model.known(v) else "en"
        else:
            v = str(v).strip()[:300]
        out[k] = v
    # The older single start/end pair stays in step with the first range, so anything still reading it sees a
    # sensible day. When only the pair is sent (an older page), the ranges are cleared and the pair is the truth.
    if out.get("post_windows"):
        out.setdefault("post_start_time", out["post_windows"][0]["start"])
        out.setdefault("post_end_time", out["post_windows"][0]["end"])
    elif ("post_start_time" in data or "post_end_time" in data) and "post_windows" not in data:
        out["post_windows"] = []
    return out


def _validate_window(c):
    if not c["post_window_enabled"]:
        return
    for w in c.get("post_windows") or [{"start": c["post_start_time"], "end": c["post_end_time"]}]:
        if w["end"] <= w["start"]:
            raise ValueError("The posting end time must be later than the start time on the same day.")


_mem: dict = {}   # user -> {creator id: creator}. The files stay the truth; this saves re-reading them on every page check.


def _language_defaults():
    from . import accounts
    return {**DEFAULTS, "lang": "pt" if accounts.settings()["caption_language"] == "pt-BR" else "en"}


def _mine() -> dict:
    u = ctx.uid()
    got = _mem.get(u)
    if got is None:
        got = {}
        for f in sorted(_root().glob("*/creator.json")):
            try:
                c = _fix({**_language_defaults(), **json.loads(f.read_text(encoding="utf-8"))})
                got[c.get("id") or f.parent.name] = c
            except Exception:  # noqa: BLE001
                pass
        with _lock:
            got = _mem.setdefault(u, got)
    return got


def forget(uid: str):
    """Files were changed from outside this module (old data moved in): read them again next time."""
    _mem.pop(uid, None)
    ctx.bump(uid)


def all_creators() -> list:
    return [copy.deepcopy(c) for c in list(_mine().values())]


def get(cid):
    if not cid or not re.fullmatch(r"[\w\-]{1,60}", str(cid)):
        return None
    c = _mine().get(str(cid))
    return copy.deepcopy(c) if c else None


def _write(c):
    with _lock:
        d = root(c["id"])
        for sub in ("videos", "live"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        tmp = d / "creator.json.tmp"
        tmp.write_text(json.dumps(c, indent=1), encoding="utf-8")
        tmp.replace(d / "creator.json")
        _mine()[c["id"]] = copy.deepcopy(c)
    ctx.bump()


def create(data: dict) -> dict:
    c = _fix({**_language_defaults(), **_clean(data)})
    _validate_window(c)
    if not c["name"]:
        raise ValueError("Give the creator a name.")
    slug = re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")[:40] or "creator"
    cid, n = slug, 2
    while root(cid).exists():
        cid, n = f"{slug}-{n}", n + 1
    c.update(id=cid, created=time.time())
    _write(c)
    log(cid, f"Workspace created for {c['name']}")
    return c


def update(cid, data: dict) -> dict:
    c = get(cid)
    if not c:
        raise KeyError(cid)
    new = _clean(data)
    if "channels" in new and "channel" not in new and c["channel"] not in new["channels"].values():
        c["channel"] = ""  # the recording channel was removed: fall back to another one
    if "channel" in new and new["channel"] and "channels" not in new:
        new["channels"] = {**c["channels"], platform_key(new["channel"]): new["channel"]}
    c.update(new)
    _validate_window(c)
    _fix(c)
    if c["min_len"] >= c["max_len"]:
        raise ValueError("Shortest clip must be shorter than longest clip.")
    _write(c)
    return c


def add_interaction_style(cid, data: dict) -> dict:
    """Save a new custom style and choose it for the creator's next videos."""
    from . import interaction_styles
    with _lock:
        c = get(cid)
        if not c:
            raise KeyError(cid)
        styles = list(c.get("interaction_styles") or [])
        if len(styles) >= interaction_styles.MAX_STYLES:
            raise ValueError(f"You can keep up to {interaction_styles.MAX_STYLES} custom styles. Delete one first.")
        style = interaction_styles.clean_style(data)
        while any(s.get("id") == style["id"] for s in styles):
            style["id"] = interaction_styles._style_id()
        c["interaction_styles"] = [*styles, style]
        c["interaction_style"] = style["id"]
        _write(c)
    return c


def update_interaction_style(cid, sid: str, data: dict) -> dict:
    """Change one saved style. Videos already made keep the copy they were made with."""
    from . import interaction_styles
    with _lock:
        c = get(cid)
        if not c:
            raise KeyError(cid)
        styles = list(c.get("interaction_styles") or [])
        if not any(s.get("id") == sid for s in styles):
            raise KeyError(sid)
        style = interaction_styles.clean_style({**data, "id": sid}, sid=sid)
        c["interaction_styles"] = [style if s.get("id") == sid else s for s in styles]
        _write(c)
    return c


def delete_interaction_style(cid, sid: str) -> dict:
    with _lock:
        c = get(cid)
        if not c:
            raise KeyError(cid)
        styles = [s for s in c.get("interaction_styles") or [] if s.get("id") != sid]
        if len(styles) == len(c.get("interaction_styles") or []):
            raise KeyError(sid)
        c["interaction_styles"] = styles
        if c.get("interaction_style") == sid:
            c["interaction_style"] = ""      # back to the built-in YouTube look
        _write(c)
    return c


def select_interaction_style(cid, sid: str) -> dict:
    with _lock:
        c = get(cid)
        if not c:
            raise KeyError(cid)
        ids = {s.get("id") for s in c.get("interaction_styles") or []}
        if sid and sid not in ids:
            raise KeyError(sid)
        c["interaction_style"] = sid or ""
        _write(c)
    return c


def add_custom_motion(cid, data: dict) -> dict:
    """Keep an approved motion and use it for its audience request from now on."""
    with _lock:
        c = get(cid)
        if not c:
            raise KeyError(cid)
        record = clean_custom_motions([data])[0]
        motions = [m for m in c.get("custom_motions") or [] if m["id"] != record["id"]]
        if len(motions) >= 20:
            raise ValueError("You can keep up to 20 custom motions. Delete one first.")
        chosen = dict(c.get("custom_motion_selected") or {})
        chosen[record["action"]] = record["id"]
        c["custom_motions"] = [*motions, record]
        c["custom_motion_selected"] = chosen
        _write(c)
    return c


def delete_custom_motion(cid, mid: str) -> dict:
    with _lock:
        c = get(cid)
        if not c:
            raise KeyError(cid)
        motions = [m for m in c.get("custom_motions") or [] if m["id"] != mid]
        if len(motions) == len(c.get("custom_motions") or []):
            raise KeyError(mid)
        chosen = {a: v for a, v in (c.get("custom_motion_selected") or {}).items() if v != mid}
        c["custom_motions"] = motions
        c["custom_motion_selected"] = chosen
        _write(c)
    folder = root(cid) / "custom-motions"
    for suffix in (".webm", ".json"):
        (folder / (mid + suffix)).unlink(missing_ok=True)
    return c


def select_custom_motion(cid, action: str, mid: str) -> dict:
    from .interactions import ACTIONS
    if action not in ACTIONS:
        raise ValueError("Choose one of the audience requests.")
    with _lock:
        c = get(cid)
        if not c:
            raise KeyError(cid)
        motions = {m["id"]: m for m in c.get("custom_motions") or []}
        if mid and (mid not in motions or motions[mid]["action"] != action):
            raise KeyError(mid)
        chosen = dict(c.get("custom_motion_selected") or {})
        if mid:
            chosen[action] = mid
        else:
            chosen.pop(action, None)
        c["custom_motion_selected"] = chosen
        _write(c)
    return c


def custom_motion_assets(c) -> dict:
    """Which custom WebM replaces each built-in motion on this creator's next videos."""
    motions = {m["id"]: m for m in (c.get("custom_motions") or [])}
    out = {}
    for action, mid in (c.get("custom_motion_selected") or {}).items():
        if mid in motions:
            out[action] = motions[mid]["file"]
    return out


def set_profiles(cid, profiles: dict, extra: dict = None):
    """Each channel's public profile and live state, written by profiles.refresh. `profile` is the recording
    channel's one, used for the creator's picture."""
    with _lock:
        c = get(cid)
        if not c:
            return
        before = json.dumps([c.get("profiles"), {k: c.get(k) for k in (extra or {})}], sort_keys=True, default=str)
        c["profiles"] = {k: v for k, v in profiles.items() if k in c["channels"]}
        main = platform_key(c["channel"]) if c["channel"] else ""
        c["profile"] = c["profiles"].get(main) or next(iter(c["profiles"].values()), {})
        c.update(extra or {})
        same = re.sub(r'"live_at": [\d.]+', "", before) == re.sub(
            r'"live_at": [\d.]+', "", json.dumps([c["profiles"], {k: c.get(k) for k in (extra or {})}], sort_keys=True, default=str))
        if same:       # only "checked just now" moved on: remember it, but there is nothing new to save or show
            _mine()[c["id"]] = copy.deepcopy(c)
            return
        _write(c)


def log(cid, msg: str):
    if not cid or ctx.uid_or_none() is None or not root(cid).exists():
        return
    path = root(cid) / "log.jsonl"
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "msg": msg}) + "\n")
        try:
            if path.stat().st_size > LOG_MAX_BYTES:         # a stream that runs for weeks must not grow this for ever
                keep = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-LOG_KEEP_LINES:]
                path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass
    ctx.bump()


LOG_MAX_BYTES, LOG_KEEP_LINES, LOG_TAIL_BYTES = 1_000_000, 500, 64 * 1024


def read_log(cid, n=40) -> list:
    """The newest n lines, newest first. Only the end of the file is read: the page asks for this all the time."""
    f = root(cid) / "log.jsonl"
    try:
        size = f.stat().st_size
        with f.open("rb") as h:
            h.seek(max(0, size - LOG_TAIL_BYTES))
            raw = h.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    if size > LOG_TAIL_BYTES:
        lines = lines[1:]                                   # the first one starts in the middle of a line
    out = []
    for x in reversed(lines[-n:]):
        try:
            out.append(json.loads(x))
        except ValueError:
            pass
    return out
