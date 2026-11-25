"""Custom audience motions. The AI writes a short motion brief; PeakCut turns it into a real Lottie scene,
renders a filmstrip of frames for the person to approve, and only then encodes a transparent WebM.

The Lottie scene follows the same contract as the built-in motions: 800x240, 30 fps, 120 frames, transparent,
with a readable entrance, action, hold and exit. Authoring uses a separate diffusionstudio/lottie checkout;
playing clips needs only ffmpeg, as before."""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import brain, config, interactions
from .media import NO_WINDOW

W, H, FPS, FRAMES = interactions.WIDTH, interactions.HEIGHT, 30, 120
SOURCE_IMAGE = "source-image.png"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DRAFT_ID = re.compile(r"^[a-f0-9]{16}$")

# What the upload can be asked to do. Free text from the person is mapped to one of these by the AI.
BEHAVIORS = ("wave", "jump", "spin", "enter", "pop", "cry", "laugh")
LABELS = {
    "subscribe": {"en": ("SUBSCRIBE", "SUBSCRIBED"), "pt-BR": ("INSCREVA-SE", "INSCRITO")},
    "like": {"en": ("Like this video", "Liked!"), "pt-BR": ("Deixe seu like", "Like ativado!")},
    "comment": {"en": ("Leave a comment", "Comment sent"), "pt-BR": ("Deixe seu comentário", "Comentário enviado")},
    "share": {"en": ("Share this video", "Shared!"), "pt-BR": ("Compartilhe o vídeo", "Compartilhado!")},
    "save": {"en": ("Save this video", "Saved!"), "pt-BR": ("Salve este vídeo", "Vídeo salvo!")},
}

MOTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "style": {"type": "STRING"},
        "background": {"type": "STRING"},
        "ink": {"type": "STRING"},
        "accent": {"type": "STRING"},
        "image_action": {"type": "STRING"},
        "label": {"type": "STRING"},
        "note": {"type": "STRING"},
    },
    "required": ["style", "background", "ink", "accent", "image_action", "label", "note"],
}

PROMPT = """You are the art director for a short animated card that appears in a vertical video. The card asks the
viewer to {action}. It is 800x240, transparent, 4 seconds at 30 fps, and plays silently with a woosh and a click.

The person described what they want:
{brief}

{image_line}

Answer with one JSON object and nothing else:
{{"style":"one short sentence about the visual direction","background":"#rrggbb","ink":"#rrggbb",
  "accent":"#rrggbb","image_action":"one of wave, jump, spin, enter, pop, cry, laugh","label":"short call to action, at most 22 characters","note":"one short sentence in {language} explaining your choice"}}

Rules:
- background is a dark color for the card, ink is readable text on it, accent is the one bright highlight.
- image_action is how the uploaded picture should move. If there is no picture, use pop.
  wave = friendly side-to-side greeting; jump = excited bounce; spin = playful full turn; enter = slides in from the side;
  pop = grows and settles; cry = sinks and wobbles; laugh = bounces with a happy wobble.
- Keep the card readable when shown small over a video. One focal subject, one accent, no clutter.
- Never describe or copy a logo, a face or a person's identity in the label."""


def _text(value, limit):
    got = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(got.split())[:limit]


def _color(value, fallback):
    got = str(value or "").strip()
    return got.lower() if HEX.fullmatch(got) else fallback


def clean_brief(value) -> dict:
    """Only the handful of values we actually draw. Raises with a sentence a person can act on."""
    if not isinstance(value, dict):
        raise ValueError("The AI did not send a motion it could use.")
    action = str(value.get("action") or "")
    if action not in LABELS:
        raise ValueError("Choose one of the audience requests.")
    behavior = str(value.get("image_action") or "pop")
    if behavior not in BEHAVIORS:
        behavior = "pop"
    has_image = bool(value.get("has_image"))
    if not has_image and behavior == "cry":
        behavior = "pop"
    language = "pt-BR" if value.get("language") == "pt-BR" else "en"
    fallback = LABELS[action][language][0]
    label = _text(value.get("label"), 22) or fallback
    return {"action": action, "style": _text(value.get("style"), 200),
            "background": _color(value.get("background"), "#121212"),
            "ink": _color(value.get("ink"), "#ffffff"),
            "accent": _color(value.get("accent"), "#ff4d4d"),
            "image_action": behavior, "label": label, "note": _text(value.get("note"), 160),
            "has_image": has_image, "language": language}


def _answer(text: str) -> dict:
    """The JSON object inside an answer, even when the model wrapped it in a sentence or a code fence."""
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The AI did not send a motion it could use.")
    try:
        got = json.loads(raw[start:end + 1])
    except ValueError:
        raise ValueError("The AI did not send a motion it could use.")
    if not isinstance(got, dict):
        raise ValueError("The AI did not send a motion it could use.")
    return got


def ask_brief(value: dict) -> dict:
    """One AI call. The returned brief is not saved until the person approves the filmstrip."""
    from . import ai
    if not ai.ready():
        raise ValueError("The AI is not set up yet. Add a key in Settings first.")
    brief = clean_brief(value)
    description = " ".join([brief["style"], _text(value.get("description"), 600),
                            _text(value.get("image_prompt"), 300)]).strip()
    if not description:
        description = "A short, friendly animation for this audience request."
    image_line = ("The person uploaded a picture; on the right is what it should do: "
                  + (_text(value.get("image_prompt"), 300) or "a friendly greeting.")) if brief["has_image"] else \
                 "No picture was uploaded: use vector shapes only."
    spoken = "Brazilian Portuguese" if brief["language"] == "pt-BR" else "English"
    prompt = PROMPT.format(action=brief["action"], brief=description, image_line=image_line, language=spoken)
    got, _model, _tokens = brain.ask_ai(prompt, MOTION_SCHEMA, purpose="Interaction motion: brief", timeout=120)
    parsed = _answer(got)
    merged = {**brief, **{k: parsed.get(k) for k in ("style", "background", "ink", "accent",
                                                     "image_action", "label", "note")}}
    return clean_brief(merged)


def _static(value):
    return {"a": 0, "k": value}


def _animated(frames):
    """Easing anchors from the Lottie motion guide: a sharp entrance and a soft settle."""
    out = []
    for i, (t, value) in enumerate(frames):
        item = {"t": t, "s": value if isinstance(value, list) else [value]}
        if i < len(frames) - 1:
            item["o"] = {"x": [.20], "y": [.75]}
            item["i"] = {"x": [.34], "y": [.94]}
        out.append(item)
    return {"a": 1, "k": out}


def _transform(extra=None):
    base = {"o": _static(100), "r": _static(0), "p": _static([0, 0, 0]),
            "a": _static([0, 0, 0]), "s": _static([100, 100, 100])}
    base.update(extra or {})
    return base


def _fill(color):
    return {"ty": "fl", "c": _static([int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)] + [1]), "o": _static(100), "r": 1}


def _stroke(color, width=4):
    return {"ty": "st", "c": _static([int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)] + [1]),
            "o": _static(100), "w": _static(width), "lc": 2, "lj": 2}


def _rect(x, y, w, h, radius=18):
    return {"ty": "rc", "p": _static([x, y]), "s": _static([w, h]), "r": _static(radius), "d": 1}


def _path(points, closed=True):
    return {"ty": "sh", "ks": _static({"v": points, "i": [[0, 0]] * len(points),
                                      "o": [[0, 0]] * len(points), "c": closed})}


def _motion_for(behavior):
    """One clear gesture on the uploaded picture, baked to keyframes so Skottie renders it without expressions."""
    if behavior == "wave":
        return {"r": _animated([(0, -10), (22, 12), (44, -8), (66, 10), (88, 0), (119, 0)])}
    if behavior == "jump":
        return {"p": _animated([(0, [180, 136, 0]), (20, [180, 76, 0]), (42, [180, 136, 0]),
                                (58, [180, 110, 0]), (76, [180, 136, 0]), (119, [180, 136, 0])])}
    if behavior == "spin":
        return {"r": _animated([(0, 0), (70, 360), (119, 360)])}
    if behavior == "enter":
        return {"p": _animated([(0, [-60, 120, 0]), (26, [180, 120, 0]), (38, [190, 120, 0]),
                                (52, [180, 120, 0]), (119, [180, 120, 0])])}
    if behavior == "cry":
        return {"p": _animated([(0, [180, 108, 0]), (40, [180, 142, 0]), (60, [178, 130, 0]),
                                (80, [180, 142, 0]), (119, [180, 142, 0])]),
                "s": _animated([(0, [100, 100, 100]), (40, [94, 104, 100]), (119, [94, 104, 100])])}
    if behavior == "laugh":
        return {"s": _animated([(0, [100, 100, 100]), (20, [112, 88, 100]), (38, [92, 110, 100]),
                                (56, [106, 96, 100]), (74, [100, 100, 100]), (119, [100, 100, 100])]),
                "r": _animated([(0, -4), (38, 4), (74, 0), (119, 0)])}
    return {"s": _animated([(0, [70, 70, 100]), (20, [114, 114, 100]), (38, [96, 96, 100]),
                            (54, [100, 100, 100]), (119, [100, 100, 100])])}


def build_lottie(brief: dict, image_name: str = "") -> dict:
    """A deterministic, Skottie-safe scene built from the brief. No expressions, no external assets."""
    brief = clean_brief(brief)
    bg, ink, accent = brief["background"], brief["ink"], brief["accent"]
    layers = []
    next_index = 2

    def add(name, shapes, ip=0, op=FRAMES, ks=None):
        nonlocal next_index
        layers.append({"ddd": 0, "ind": next_index, "ty": 4, "nm": name, "sr": 1,
                       "ks": _transform(ks), "ao": 0, "shapes": shapes, "ip": ip, "op": op, "st": 0, "bm": 0})
        next_index += 1

    asset_id = "picture" if image_name else ""
    add("Card", [_rect(410, 120, 640, 124, 24), _fill(bg)], 0, FRAMES,
        {"a": _static([400, 120, 0]),
         "p": _animated([(0, [400, 156, 0]), (16, [400, 120, 0]), (104, [400, 120, 0]), (119, [420, 100, 0])]),
         "o": _animated([(0, 0), (7, 100), (105, 100), (119, 0)])})
    add("Accent", [_rect(140, 120, 26, 96, 12), _fill(accent)], 12, 108,
        {"o": _animated([(12, 0), (20, 100), (102, 100), (108, 0)])})
    tick = [[590, 120], [600, 132], [622, 104]]
    add("Confirmation", [_path(tick, False), _stroke(accent, 7)], 86, FRAMES,
        {"o": _animated([(86, 0), (94, 100), (119, 100)])})

    document = {"t": brief["label"], "f": "Poppins", "s": 40, "j": 0, "tr": 0, "lh": 50, "ls": 0,
                "fc": [int(ink[i:i + 2], 16) / 255 for i in (1, 3, 5)]}
    layers.append({"ddd": 0, "ind": next_index, "ty": 5, "nm": "Call to action", "sr": 1,
                   "ks": _transform({"a": _static([0, 0, 0]),
                                     "p": _animated([(0, [400, 156, 0]), (16, [400, 120, 0]),
                                                     (104, [400, 120, 0]), (119, [420, 100, 0])]),
                                     "o": _animated([(0, 0), (8, 100), (104, 100), (119, 0)])}),
                   "ao": 0, "ip": 0, "op": FRAMES, "st": 0, "bm": 0,
                   "t": {"d": {"k": [{"t": 0, "s": document}]}, "m": {"g": 1, "a": _static([0, 0])}, "a": []}})
    if image_name:
        # The picture sits on top of the card, to the left of the words, and performs the requested gesture.
        layers.append({"ddd": 0, "ind": next_index, "ty": 2, "nm": "Picture", "sr": 1,
                       "ks": _transform({"a": _static([110, 110, 0]),
                                         "p": _animated([(0, [180, 120, 0]), (14, [180, 120, 0]),
                                                         (104, [180, 120, 0]), (119, [210, 100, 0])]),
                                         "o": _animated([(0, 0), (10, 100), (104, 100), (119, 0)]),
                                         **_motion_for(brief["image_action"])}),
                       "ao": 0, "ip": 0, "op": FRAMES, "st": 0, "bm": 0, "refId": asset_id})

    return {"v": "5.12.2", "fr": FPS, "ip": 0, "op": FRAMES, "w": W, "h": H,
            "nm": "Custom / " + brief["action"],
            "assets": [{"id": asset_id, "w": 220, "h": 220, "u": "", "p": image_name, "e": 0}] if image_name else [],
            "fonts": {"list": [{"fName": "Poppins", "fFamily": "Poppins ExtraBold", "fStyle": "ExtraBold", "ascent": 75}]},
            "layers": list(reversed(layers))}


def _node_script():
    path = Path(__file__).resolve().parent.parent / "tools" / "render_custom_interaction.mjs"
    if not path.is_file():
        raise ValueError("The motion renderer is missing from this PeakCut install.")
    return path


def checkout_status(checkout: str = "") -> dict:
    """Is there a diffusionstudio/lottie checkout with CanvasKit, and is Node installed?"""
    node = shutil.which("node") or ""
    candidates = []
    for value in (checkout, os.environ.get("PEAKCUT_LOTTIE_CHECKOUT", ""),
                  Path(config.ROOT) / "tools" / "lottie", Path("C:/peakcut-lottie"),
                  Path.home() / "peakcut-lottie"):
        if value and str(value) not in [str(c) for c in candidates]:
            candidates.append(Path(value))
    found = ""
    for folder in candidates:
        if folder.is_dir() and (folder / "package.json").is_file():
            canvaskit = folder / "node_modules" / "canvaskit-wasm"
            if canvaskit.is_dir():
                found = str(folder)
                break
    if found and node:
        return {"ready": True, "checkout": found, "node": node, "message": ""}
    if not node:
        return {"ready": False, "checkout": found, "node": "", "message": "Node.js is needed to create a custom motion. Install Node, then set the Lottie checkout in Settings."}
    return {"ready": False, "checkout": "", "node": node,
            "message": "A diffusionstudio/lottie checkout with its npm dependencies is needed. Clone it, run npm install, then set its folder in Settings."}


def _run(checkout: str, scene: Path, out: Path, image: Path | None, webm: bool):
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = ["node", str(_node_script()), checkout, str(scene), str(out), ffmpeg]
    if image:
        cmd.append(str(image))
    if webm:
        cmd.append("--webm")
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NO_WINDOW, timeout=300)
    if done.returncode != 0:
        raise ValueError((done.stderr or done.stdout or "The motion could not be rendered.").strip()[-300:])
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError("The motion renderer did not return any frames.")


def _draft_dir(cid: str, did: str) -> Path:
    from . import creators
    return creators.root(cid) / "custom-motion-drafts" / did


def new_draft_id() -> str:
    return hashlib.sha1(os.urandom(16)).hexdigest()[:16]


def draft(cid: str, brief: dict, image: bytes | None, checkout: str) -> dict:
    """Make the Lottie scene and a filmstrip of at least six real frames. Nothing is saved for use yet."""
    status = checkout_status(checkout)
    if not status["ready"]:
        raise ValueError(status["message"])
    did = new_draft_id()
    folder = _draft_dir(cid, did)
    folder.mkdir(parents=True, exist_ok=True)
    scene = folder / "lottie.json"
    scene.write_text(json.dumps(build_lottie(brief, SOURCE_IMAGE if image else "")), encoding="utf-8")
    if image:
        from PIL import Image
        source = Image.open(io.BytesIO(image)).convert("RGBA")
        source.thumbnail((220, 220), Image.LANCZOS)
        source.save(folder / SOURCE_IMAGE, "PNG")
    try:
        result = _run(status["checkout"], scene, folder, folder / SOURCE_IMAGE if image else None, False)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    frames = [f for f in result.get("frames") or [] if re.fullmatch(r"frame-\d{4}\.png", f)]
    if len(frames) < 6:
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError("The renderer did not make enough frames to show the motion.")
    return {"id": did, "frames": frames, "brief": clean_brief(brief)}


def filmstrip_data(cid: str, did: str, frames: list | None = None) -> bytes:
    """One JPEG strip with the frames in order, so the person can judge the whole progression at a glance."""
    from PIL import Image, ImageDraw
    if not DRAFT_ID.fullmatch(did):
        raise ValueError("That motion draft is not valid.")
    folder = _draft_dir(cid, did)
    frames = frames or sorted(p.name for p in _draft_dir(cid, did).glob("frame-*.png"))
    shown = frames if len(frames) <= 11 else frames[::2]
    cell_w, cell_h, cols = 220, 78, 4
    rows = (len(shown) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (18, 18, 18))
    draw = ImageDraw.Draw(canvas)
    for i, name in enumerate(shown):
        if not re.fullmatch(r"frame-\d{4}\.png", name):
            raise ValueError("That motion draft is not valid.")
        frame = Image.open(folder / name).convert("RGBA")
        frame.thumbnail((cell_w - 8, cell_h - 8), Image.LANCZOS)
        background = Image.new("RGBA", frame.size, (18, 18, 18, 255))
        background.alpha_composite(frame)
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        canvas.paste(background.convert("RGB"), (x + 4, y + 4))
        draw.text((x + 6, y + cell_h - 14), str(i + 1), fill=(170, 170, 170))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88)
    return out.getvalue()


def frame_jpeg(cid: str, did: str, name: str) -> bytes:
    """One bigger frame, for people who want to look at the expressiveness closely."""
    if not DRAFT_ID.fullmatch(did) or not re.fullmatch(r"frame-\d{4}\.png", name):
        raise ValueError("That motion draft is not valid.")
    from PIL import Image
    frame = Image.open(_draft_dir(cid, did) / name).convert("RGBA")
    background = Image.new("RGBA", frame.size, (18, 18, 18, 255))
    background.alpha_composite(frame)
    out = io.BytesIO()
    background.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def approve(cid: str, did: str, checkout: str) -> dict:
    """After approval only: encode the alpha WebM and keep the Lottie source for later editing."""
    if not DRAFT_ID.fullmatch(did):
        raise ValueError("That motion draft is not valid.")
    status = checkout_status(checkout)
    if not status["ready"]:
        raise ValueError(status["message"])
    from . import creators
    folder = _draft_dir(cid, did)
    scene = folder / "lottie.json"
    if not scene.is_file():
        raise ValueError("That motion draft has expired. Ask for it again.")
    picture = folder / SOURCE_IMAGE if (folder / SOURCE_IMAGE).is_file() else None
    _run(status["checkout"], scene, folder, picture, True)
    webm = folder / "motion.webm"
    if not webm.is_file():
        raise ValueError("The motion could not be encoded. Ask for it again.")
    scene_data = json.loads(scene.read_text(encoding="utf-8"))
    mid = "motion-" + hashlib.sha1(os.urandom(8)).hexdigest()[:10]
    dest = creators.root(cid) / "custom-motions"
    dest.mkdir(parents=True, exist_ok=True)
    webm.replace(dest / f"{mid}.webm")
    (dest / f"{mid}.json").write_text(json.dumps(scene_data), encoding="utf-8")
    return {"id": mid, "lottie": scene_data, "asset": dest / f"{mid}.webm"}


def discard(cid: str, did: str):
    if DRAFT_ID.fullmatch(did):
        shutil.rmtree(_draft_dir(cid, did), ignore_errors=True)


def cleanup_old_drafts(cid: str, keep_hours=24):
    from . import creators
    root = creators.root(cid) / "custom-motion-drafts"
    if not root.is_dir():
        return
    now = time.time()
    for folder in root.iterdir():
        if folder.is_dir() and now - folder.stat().st_mtime > keep_hours * 3600:
            shutil.rmtree(folder, ignore_errors=True)
