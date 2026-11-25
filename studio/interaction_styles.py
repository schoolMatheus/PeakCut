"""Custom interaction styles: the colors of the audience motions, the channel seal, and the AI that reads a
channel's art. The style is kept per creator and copied into each new video, so editing a style later never
changes a clip that was already made.

Colors are applied to the ready-made motion artwork by replacing its three base colors (the dark ink, the white
body and the YouTube red) with the creator's own. The channel seal is a round picture of the channel with its
short name, drawn once with Pillow and placed in the empty side of the motion, away from captions and title."""
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import ai, usage

MAX_STYLES = 8
NAME_MAX = 40
SEAL_NAME_MAX = 20
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
STYLE_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{2,23}$")

# What the built-in YouTube artwork already uses. "Start from YouTube" fills the fields with these.
YOUTUBE = {"background": "#0f0f0f", "ink": "#ffffff", "accent": "#fd0000", "detail": "#ff4d4d"}


def _text(value, limit):
    """One line of printable text, as the person typed it, cut to the limit."""
    clean = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(clean.split())[:limit]


def _color(value, field):
    got = str(value or "").strip()
    if not HEX.fullmatch(got):
        raise ValueError(f"Choose a color for {field}.")
    return got.lower()


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _lum(hex_color):
    r, g, b = (c / 255 for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(a, b, t):
    return _hex(tuple(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))))


def _readable(ink, background, wanted=4.5):
    """Keep the words readable on the background, moving the ink towards white or black only as far as needed."""
    if _ratio(ink, background) >= wanted:
        return ink
    target = "#ffffff" if _lum(background) < 0.35 else "#000000"
    best = target
    for step in range(1, 11):
        got = _mix(ink, target, step / 10)
        if _ratio(got, background) >= wanted:
            return got
        if _ratio(got, background) > _ratio(best, background):
            best = got
    return best


def _visible(accent, background, wanted=2.2):
    """Large shapes may be less contrasty than words, but must still stand out from the background."""
    if _ratio(accent, background) >= wanted:
        return accent
    target = "#ffffff" if _lum(background) < 0.35 else "#000000"
    best = target
    for step in range(1, 11):
        got = _mix(accent, target, step / 10)
        if _ratio(got, background) >= wanted:
            return got
        if _ratio(got, background) > _ratio(best, background):
            best = got
    return best


def _style_id():
    return "sty-" + hashlib.sha1(os.urandom(8)).hexdigest()[:8]


def clean_style(value, sid=None) -> dict:
    """One style, with colors that can actually be seen. Raises ValueError with a sentence a person can act on."""
    if not isinstance(value, dict):
        raise ValueError("That interaction style isn't valid.")
    name = _text(value.get("name"), NAME_MAX)
    if not name:
        raise ValueError("Give the style a name.")
    background = _color(value.get("background", YOUTUBE["background"]), "the background")
    ink = _readable(_color(value.get("ink", YOUTUBE["ink"]), "the text"), background)
    accent = _visible(_color(value.get("accent", YOUTUBE["accent"]), "the highlight"), background)
    detail = _visible(_color(value.get("detail", YOUTUBE["detail"]), "the detail"), background, 1.6)
    seal = value.get("seal", True)
    if not isinstance(seal, bool):
        raise ValueError("The channel seal must be on or off.")
    got_id = sid or value.get("id") or ""
    if not isinstance(got_id, str) or not STYLE_ID.fullmatch(got_id):
        got_id = _style_id()
    created = value.get("created")
    try:
        created = float(created)
    except (TypeError, ValueError):
        created = time.time()
    return {"id": got_id, "name": name, "background": background, "ink": ink, "accent": accent,
            "detail": detail, "seal": seal, "seal_name": _text(value.get("seal_name"), SEAL_NAME_MAX),
            "source": "ai" if value.get("source") == "ai" else "manual", "created": created}


def clean_styles(value) -> list:
    """The styles saved for a creator: at most eight, each with its own id."""
    if not isinstance(value, list):
        raise ValueError("The interaction styles must be a list.")
    if len(value) > MAX_STYLES:
        raise ValueError(f"You can keep up to {MAX_STYLES} custom styles. Delete one first.")
    out, seen = [], set()
    for item in value:
        style = clean_style(item)
        while style["id"] in seen:
            style["id"] = _style_id()
        seen.add(style["id"])
        out.append(style)
    return out


def clean_snapshot(value) -> dict | None:
    """The copy a video keeps. Older videos have none, and then the YouTube artwork is used unchanged."""
    if not value:
        return None
    return clean_style(value)


def _short_name(creator) -> str:
    names = [(creator.get("profile") or {}).get("name"), creator.get("name")]
    for name in names:
        got = _text(name, SEAL_NAME_MAX).lstrip("@")
        if got:
            return got
    return ""


def for_creator(creator) -> dict | None:
    """The style a new video of this creator starts with, or None for the built-in YouTube look."""
    if not creator:
        return None
    wanted = creator.get("interaction_style") or ""
    for style in creator.get("interaction_styles") or []:
        if style.get("id") == wanted:
            got = dict(style)
            got["seal_name"] = got.get("seal_name") or _short_name(creator)
            return clean_snapshot(got)
    return None


def _platform(creator) -> str:
    from . import creators
    if creator.get("channel"):
        key = creators.platform_key(creator["channel"])
        if key:
            return key
    for key, profile in (creator.get("profiles") or {}).items():
        if profile.get("pic"):
            return key
    return ""


def _creator_file(cid, creator, prefix: str):
    """The cached picture of a creator's main channel, or None."""
    from . import creators
    if not cid or not creator:
        return None
    key = _platform(creator)
    profile = (creator.get("profiles") or {}).get(key) or {}
    field = "pic" if prefix == "avatar" else "cover"
    if not profile.get(field):
        return None
    path = creators.root(cid) / f"{prefix}-{key}.img"
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def avatar_for(cid) -> bytes | None:
    from . import creators
    return _creator_file(cid, creators.get(cid) if cid else None, "avatar")


def cover_for(cid) -> bytes | None:
    from . import creators
    return _creator_file(cid, creators.get(cid) if cid else None, "cover")


def _map_color(r, g, b, background, ink, accent):
    """One pixel of the artwork: dark becomes the background, white the ink, red the highlight, and every shade
    in between follows the same blend. Keeps antialiased edges smooth instead of leaving red fringes."""
    v = max(r, g, b) / 255.0
    red = max(0.0, (r - max(g, b)) / 255.0)
    out = []
    for i in range(3):
        base = background[i] + v * (ink[i] - background[i])
        hot = background[i] + v * (accent[i] - background[i])
        out.append(base + red * (hot - base))
    return out


def lut_text(style) -> str:
    """A small color lookup table ffmpeg applies to the motion artwork. 17 steps per channel is plenty for flat,
    antialiased shapes and keeps the file around 180 KB."""
    background, ink, accent = (_rgb(style[k]) for k in ("background", "ink", "accent"))
    size = 17
    lines = [f"LUT_3D_SIZE {size}"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rgb = _map_color(r * 255 / (size - 1), g * 255 / (size - 1), b * 255 / (size - 1),
                                 background, ink, accent)
                lines.append(" ".join(f"{max(0.0, min(1.0, c / 255)):.6f}" for c in rgb))
    return "\n".join(lines) + "\n"


def _initials(name):
    words = [w for w in re.split(r"\s+", name or "") if w]
    return ("".join(w[0] for w in words[:2]) or "?").upper()


def seal_png(style, avatar: bytes | None = None, width: int = 170) -> bytes:
    """The round channel picture with its short name underneath, ready to sit in the empty side of a motion."""
    from PIL import Image, ImageDraw, ImageFont
    diameter = int(width * 0.88)
    pad = (width - diameter) // 2
    name = _text(style.get("seal_name"), SEAL_NAME_MAX)
    font_path = Path(__file__).resolve().parent / "fonts" / "ArchivoBlack-Regular.ttf"
    height = diameter + 62
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    box = (pad, 2, pad + diameter, diameter + 2)

    # A smooth circle: draw it large, then shrink it, which softens the edge.
    scale = 4
    mask = Image.new("L", (diameter * scale, diameter * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter * scale - 1, diameter * scale - 1), fill=255)
    mask = mask.resize((diameter, diameter), Image.LANCZOS)

    picture = None
    if avatar:
        try:
            picture = Image.open(io.BytesIO(avatar)).convert("RGB")
            side = min(picture.size)
            picture = picture.crop(((picture.width - side) // 2, (picture.height - side) // 2,
                                    (picture.width + side) // 2, (picture.height + side) // 2))
            picture = picture.resize((diameter, diameter), Image.LANCZOS)
        except Exception:  # noqa: BLE001 - a broken picture still gets a seal with the initials
            picture = None
    if picture is None:
        picture = Image.new("RGB", (diameter, diameter), _rgb(style["accent"]))
        initials = _initials(name)
        try:
            font = ImageFont.truetype(str(font_path), int(diameter * 0.42))
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
        ink = Image.new("RGBA", picture.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ink)
        try:
            left, top, right, bottom = d.textbbox((0, 0), initials, font=font)
            d.text(((diameter - (right - left)) / 2 - left, (diameter - (bottom - top)) / 2 - top),
                   initials, font=font, fill=_rgb(style["ink"]) + (255,))
        except Exception:  # noqa: BLE001
            d.text((diameter / 3, diameter / 3), initials, font=font, fill=_rgb(style["ink"]) + (255,))
        picture = Image.alpha_composite(picture.convert("RGBA"), ink).convert("RGB")
    canvas.paste(picture, box[:2], mask)
    draw.ellipse((box[0], box[1], box[2] - 1, box[3] - 1), outline=_rgb(style["accent"]) + (255,), width=max(4, width // 28))

    if name:
        size = 27
        while size > 13:
            try:
                font = ImageFont.truetype(str(font_path), size)
            except Exception:  # noqa: BLE001
                font = ImageFont.load_default()
            if font.getlength(name) <= width - 18:
                break
            size -= 2
        while name and font.getlength(name) > width - 18:
            name = name[:-1]
        if name:
            left, top, right, bottom = draw.textbbox((0, 0), name, font=font)
            text_w, text_h = right - left, bottom - top
            pill_w = min(width, text_w + 20)
            pill_h = text_h + 14
            x0, y0 = (width - pill_w) / 2, diameter + 8
            draw.rounded_rectangle((x0, y0, x0 + pill_w, y0 + pill_h), radius=pill_h / 2,
                                   fill=_rgb(style["background"]) + (238,),
                                   outline=_rgb(style["accent"]) + (255,), width=2)
            draw.text((width / 2 - text_w / 2 - left, y0 + pill_h / 2 - text_h / 2 - top), name,
                      font=font, fill=_rgb(style["ink"]) + (255,))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def _tint(image, style):
    """Apply the same color blend to a picture, for the preview in the page."""
    import numpy as np
    from PIL import Image
    arr = np.asarray(image.convert("RGBA")).astype(np.float32)
    rgb = arr[..., :3]
    background, ink, accent = (_rgb(style[k]) for k in ("background", "ink", "accent"))
    v = rgb.max(axis=2, keepdims=True) / 255.0
    red = np.clip((rgb[..., 0:1] - np.maximum(rgb[..., 1:2], rgb[..., 2:3])) / 255.0, 0, 1)
    bg = np.array(background, dtype=np.float32)
    ink_a = np.array(ink, dtype=np.float32)
    acc = np.array(accent, dtype=np.float32)
    base = bg + v * (ink_a - bg)
    hot = bg + v * (acc - bg)
    out = base + red * (hot - base)
    arr[..., :3] = np.clip(out, 0, 255)
    return Image.fromarray(arr.astype("uint8"))


_frame_lock = threading.Lock()
_frames: dict = {}


def _frame(language: str) -> bytes:
    """One frame of the subscribe motion, kept in memory. The preview needs a real motion, not a mock-up."""
    from .media import NO_WINDOW
    language = "pt-BR" if language == "pt-BR" else "en"
    with _frame_lock:
        if language in _frames:
            return _frames[language]
    from .interactions import ASSETS
    asset = ASSETS / language / "subscribe.webm"
    with tempfile.TemporaryDirectory(prefix="pk-style") as temp:
        out = Path(temp) / "frame.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-c:v", "libvpx-vp9", "-ss", "1.05", "-i", str(asset),
                        "-frames:v", "1", str(out)], capture_output=True, creationflags=NO_WINDOW, timeout=60)
        if not out.is_file():
            raise RuntimeError("The motion artwork could not be read.")
        data = out.read_bytes()
    with _frame_lock:
        _frames[language] = data
    return data


def preview_jpeg(style, avatar: bytes | None, language: str = "en", width: int = 1000) -> bytes:
    """A small picture of the subscribe motion in these colors, with the seal, for the style dialog."""
    from PIL import Image
    frame = Image.open(io.BytesIO(_frame(language))).convert("RGBA")
    tinted = _tint(frame, style)
    canvas = Image.new("RGB", (width, 320), (18, 18, 18))
    scale = (width - 200) / frame.width
    shown = tinted.resize((int(frame.width * scale), int(frame.height * scale)), Image.LANCZOS)
    canvas.paste(shown, (170, (320 - shown.height) // 2), shown)
    if style.get("seal"):
        seal = Image.open(io.BytesIO(seal_png(style, avatar))).convert("RGBA")
        seal = seal.resize((170, int(seal.height * 170 / seal.width)), Image.LANCZOS)
        canvas.paste(seal, (8, max(4, (320 - seal.height) // 2)), seal)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88)
    return out.getvalue()


def _art_image(avatar: bytes | None, cover: bytes | None) -> bytes:
    """The two pictures side by side, in one JPEG, so one AI call can look at both."""
    from PIL import Image
    canvas = Image.new("RGB", (960, 420), (16, 16, 16))
    if cover:
        try:
            got = Image.open(io.BytesIO(cover)).convert("RGB")
            got.thumbnail((640, 380), Image.LANCZOS)
            canvas.paste(got, (20, (420 - got.height) // 2))
        except Exception:  # noqa: BLE001
            pass
    if avatar:
        try:
            got = Image.open(io.BytesIO(avatar)).convert("RGB")
            side = min(got.size)
            got = got.crop(((got.width - side) // 2, (got.height - side) // 2,
                            (got.width + side) // 2, (got.height + side) // 2)).resize((240, 240), Image.LANCZOS)
            canvas.paste(got, (690, (420 - 240) // 2))
        except Exception:  # noqa: BLE001
            pass
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=86)
    return out.getvalue()


def _json_answer(text: str) -> dict:
    """The JSON object inside an answer, even when the model wrapped it in a sentence or a code fence."""
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The AI did not send colors it could use.")
    try:
        got = json.loads(raw[start:end + 1])
    except ValueError:
        raise ValueError("The AI did not send colors it could use.")
    if not isinstance(got, dict):
        raise ValueError("The AI did not send colors it could use.")
    return got


PROMPT = """You are choosing the colors of short animated cards ("subscribe", "like", "comment", "share", "save")
that appear in vertical clips made from this streamer's videos.

The picture shows the streamer's channel art. On the left is the channel cover, used only as a color reference.
On the right is the channel avatar.

Answer with one JSON object and nothing else, using lowercase hex colors:
{"name": "a short style name, at most 3 words", "background": "#rrggbb", "ink": "#rrggbb",
 "accent": "#rrggbb", "detail": "#rrggbb", "seal_name": "the channel name, at most 20 characters",
 "why": "one short sentence in {language}"}

Rules:
- background is the dark color behind the cards; it must be dark and calm.
- ink is the text and light shapes; it must be easy to read on the background.
- accent is the bright color of the main button and highlight.
- detail is a second, quieter color for small marks and the seal ring.
- Use the channel's own identity, not a generic palette. If the art has no clear colors, use a clean, bold pair.
- The cover is never shown in the video. Never copy a logo or a face into the answer; only colors and a name."""


def suggest(creator, avatar: bytes | None, cover: bytes | None, language: str = "en") -> dict:
    """One look at the channel art. Returns a draft style, not saved: asking again gives another idea."""
    if not ai.ready():
        raise ValueError("The AI is not set up yet. You can still pick the colors by hand.")
    if not avatar and not cover:
        raise ValueError("This creator has no channel picture yet. You can still pick the colors by hand.")
    language = "pt-BR" if language == "pt-BR" else "en"
    spoken = "Brazilian Portuguese" if language == "pt-BR" else "English"
    image = _art_image(avatar, cover)
    text, sent, wrote = ai.see(PROMPT.replace("{language}", spoken), image, timeout=120)
    usage.record(sent, wrote, "Interaction style: channel art")
    got = _json_answer(text)
    draft = clean_style({
        "name": got.get("name") or (creator.get("name") or "Channel style"),
        "background": got.get("background", YOUTUBE["background"]),
        "ink": got.get("ink", YOUTUBE["ink"]),
        "accent": got.get("accent", YOUTUBE["accent"]),
        "detail": got.get("detail", YOUTUBE["detail"]),
        "seal": True,
        "seal_name": got.get("seal_name") or _short_name(creator),
        "source": "ai",
    })
    draft["why"] = _text(got.get("why"), 160)
    return draft
