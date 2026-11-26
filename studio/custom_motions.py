"""Custom audience motions. The AI writes a short motion brief; PeakCut turns it into a real Lottie scene,
renders a filmstrip of frames for the person to approve, and only then encodes a transparent WebM.

The Lottie scene follows the same contract as the built-in motions: 800x240, 30 fps, 120 frames, transparent,
with a readable entrance, action, hold and exit. Authoring uses a separate diffusionstudio/lottie checkout;
playing clips needs only ffmpeg, as before."""
import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from . import brain, config, interactions, usage
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

EXTRAS = ("none", "stars", "sparkles", "confetti", "waves", "hearts")

# When the click happens, in frames. A quick request moves it earlier; the card still holds until the end.
CLICK_AT = {"early": 34, "normal": 48, "late": 72}

# A goodbye needs a readable hand movement, not six copies of a standing character. The image tool gets this hint.
WAVE_POSES = ("Six new pictures in order: neutral pose; raise the hand or wing beside the face; "
              "hand high with the palm open; hand tilted to the other side; hand back near the face; "
              "settle with a small smile. Keep the character's identity in every picture.")

MOTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "style": {"type": "STRING"},
        "background": {"type": "STRING"},
        "ink": {"type": "STRING"},
        "accent": {"type": "STRING"},
        "detail": {"type": "STRING"},
        "image_action": {"type": "STRING"},
        "label": {"type": "STRING"},
        "note": {"type": "STRING"},
        "action_style": {"type": "STRING"},
        "card_shape": {"type": "STRING"},
        "motion_feel": {"type": "STRING"},
        "beats": {"type": "STRING"},
        "extras": {"type": "STRING"},
        "electric": {"type": "STRING"},
        "cursor": {"type": "STRING"},
        "click_timing": {"type": "STRING"},
    },
    "required": ["style", "background", "ink", "accent", "detail", "image_action", "label", "note",
                 "action_style", "card_shape", "motion_feel", "beats", "extras", "electric", "cursor",
                 "click_timing"],
}

PROMPT = """You are the art director for two short motions that will play together in a vertical video. Both are 800x240,
transparent, 4 seconds at 30 fps, with a woosh and a click on their entrance.

Motion 1 asks the viewer to {action}: a simple card with a short label, one accent and a confirmation. A pointer
arrives, presses the card and releases it; the label changes to the confirmed wording and a tick appears.
Motion 2 is the character motion. It must be six NEW pictures of the same cut-out character, one for each beat,
with the expression and pose described by the person (waving goodbye, laughing with closed eyes, crying with tears,
jumping, and so on). The uploaded cut-out is the identity and color reference: keep its beak, glasses, shirt,
feathers and proportions. Draw each picture separately on a transparent background, with no card, no box, no
shadow and no background.

The person's description of the action motion:
{brief}

The person's description of the character motion:
{image_line}
{pose_line}

Answer with one JSON object and nothing else:
{{"style":"one short sentence about the card's visual direction","background":"#rrggbb","ink":"#rrggbb",
  "accent":"#rrggbb","detail":"#rrggbb","image_action":"one of wave, jump, spin, enter, pop, cry, laugh",
  "label":"short call to action, at most 22 characters","note":"one short sentence in {language} describing the six new drawings",
  "action_style":"one of bold, clean, playful","card_shape":"one of rounded, pill, square",
  "motion_feel":"one of snappy, bouncy, calm","beats":"six short comma-separated descriptions of the character's six new drawings",
  "extras":"one of none, stars, sparkles, confetti, waves, hearts","electric":"one of none, lightning",
  "cursor":"one of none, pointer","click_timing":"one of early, normal, late"}}

{palette_line}

Rules:
- background is the dark card, ink is readable text, accent is the bright highlight, detail is a quiet second color for outlines and small marks.
- extras is the small decorative touch from the person's description: stars, sparkles, confetti, waves or hearts. Use none when they asked for nothing extra.
- electric is lightning when the person asked for lightning, electric or an electric spark; otherwise none.
- cursor is pointer when the person asked for a cursor, a mouse or a click; otherwise none.
- click_timing is early when the person asked for something quick or fast, late when they asked for something slow or calm, otherwise normal.
- action_style, card_shape and motion_feel must come from the person's description of the action motion. Bold uses heavy marks and strong contrast; clean uses thin outlines and calm motion; playful uses soft round shapes and a small bounce. Rounded, pill and square change the card corners. Snappy, bouncy and calm change the timing.
- image_action is the behavior the new pictures must show across the beats:
  wave = friendly side-to-side greeting with a raised hand; jump = excited bounce with a changing pose;
  spin = playful full turn seen from different angles; enter = slides in from the side and settles;
  pop = grows and settles with a changing expression; cry = tears appear and the face drops;
  laugh = closed smiling eyes and a bouncing pose.
- beats must name six different new pictures in order. Describe poses and expressions, never say the same picture is merely moved, rotated or scaled.
- The six pictures must be six separate transparent PNG drawings, redrawn for every beat. Never use the uploaded cut-out itself as a beat, and never answer with vector shapes instead of pictures.
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
    style = str(value.get("action_style") or "clean")
    shape = str(value.get("card_shape") or "rounded")
    feel = str(value.get("motion_feel") or "snappy")
    extras = str(value.get("extras") or "none").lower()
    if extras not in EXTRAS and extras != "lightning":
        extras = "none"
    electric = str(value.get("electric") or "").lower()
    if electric not in ("none", "lightning"):
        electric = "lightning" if extras == "lightning" else "none"
    cursor = str(value.get("cursor") or "pointer").lower()
    if cursor not in ("none", "pointer"):
        cursor = "pointer"
    timing = str(value.get("click_timing") or "normal").lower()
    if timing not in CLICK_AT:
        timing = "normal"
    return {"action": action, "style": _text(value.get("style"), 200),
            "background": _color(value.get("background"), "#121212"),
            "ink": _color(value.get("ink"), "#ffffff"),
            "accent": _color(value.get("accent"), "#ff4d4d"),
            "detail": _color(value.get("detail"), "#ff8800"),
            "image_action": behavior, "label": label, "note": _text(value.get("note"), 160),
            "action_style": style if style in ("bold", "clean", "playful") else "clean",
            "card_shape": shape if shape in ("rounded", "pill", "square") else "rounded",
            "motion_feel": feel if feel in ("snappy", "bouncy", "calm") else "snappy",
            "beats": _text(value.get("beats"), 240),
            "extras": extras if extras in EXTRAS else "none",
            "electric": electric, "cursor": cursor, "click_timing": timing,
            "character_color": _color(value.get("character_color"), _color(value.get("accent"), "#ff4d4d")),
            "has_image": has_image, "language": language}


def _answer(value) -> dict:
    """The AI's brief, whether the provider already parsed the JSON or left it as text."""
    got = value
    if not isinstance(got, dict):
        raw = str(value or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("The AI did not send a motion it could use.")
        try:
            got = json.loads(raw[start:end + 1])
        except ValueError:
            raise ValueError("The AI did not send a motion it could use.")
    if isinstance(got, dict) and isinstance(got.get("items"), dict):
        got = got["items"]      # some providers wrap the object in the schema's "items" key
    if not isinstance(got, dict):
        raise ValueError("The AI did not send a motion it could use.")
    return got


def ask_brief(value: dict, images_out: list | None = None) -> dict:
    """One AI call. When Codex is connected it also draws the six new pictures for the character."""
    from . import ai
    if not ai.ready():
        raise ValueError("The AI is not set up yet. Add a key in Settings first.")
    brief = clean_brief(value)
    description = " ".join([brief["style"], _text(value.get("action_description"), 600)]).strip()
    if not description:
        description = "A short, friendly card for this audience request."
    image_line = _text(value.get("image_prompt"), 300) or "a friendly greeting."
    palette = value.get("palette_colors")
    if palette:
        palette_line = ("Use exactly these colors for the motion: background " + palette["background"]
                        + ", ink " + palette["ink"] + ", accent " + palette["accent"]
                        + ", detail " + palette.get("detail", "#ff8800") + ". Do not choose different ones.")
    else:
        palette_line = "Choose the four colors yourself, following the description."
    spoken = "Brazilian Portuguese" if brief["language"] == "pt-BR" else "English"
    pose_line = "\n" + WAVE_POSES if brief["image_action"] == "wave" else ""
    prompt = PROMPT.format(action=brief["action"], brief=description, image_line=image_line,
                           pose_line=pose_line, language=spoken, palette_line=palette_line)
    if images_out is not None and ai.provider() == "codex":
        try:
            got, sent, wrote, made = ai.draw(prompt, image=value.get("reference_image"), timeout=120, count=6)
        except RuntimeError as error:
            raise ValueError(str(error)) from None   # the adapter's messages are already safe to show
        usage.record(sent, wrote, "Interaction motion: brief and drawings")
        if len(made) < 6:
            raise ValueError("The picture tool did not return the six poses. Try again; if it keeps happening, "
                             "check that image generation is available for this Codex account.")
        images_out.extend(made)
    else:
        got, _model, _tokens = brain.ask_ai(prompt, MOTION_SCHEMA, purpose="Interaction motion: brief", timeout=120)
    parsed = _answer(got)
    merged = {**brief, **{k: parsed.get(k) for k in ("style", "background", "ink", "accent", "detail",
                                                     "image_action", "label", "note", "action_style",
                                                     "card_shape", "motion_feel", "beats", "extras", "electric",
                                                     "cursor", "click_timing")}}
    if palette:
        # A chosen palette wins: the AI may only decide the drawing and the words.
        for key in ("background", "ink", "accent", "detail"):
            merged[key] = palette.get(key, merged[key])
    return clean_brief(merged)


def _static(value):
    return {"a": 0, "k": value}


EASE = {"snappy": (.20, .75, .34, .94),
        "bouncy": (.94, .75, .34, 1.12),
        "calm": (.00, .65, .51, .99)}


def _animated(frames, feel="snappy"):
    """Easing anchors from the Lottie motion guide, chosen by how the motion should feel."""
    x1, y1, x2, y2 = EASE.get(feel, EASE["snappy"])
    out = []
    for i, (t, value) in enumerate(frames):
        item = {"t": t, "s": value if isinstance(value, list) else [value]}
        if i < len(frames) - 1:
            item["o"] = {"x": [x1], "y": [y1]}
            item["i"] = {"x": [x2], "y": [y2]}
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


def _ellipse(x, y, size):
    return {"ty": "el", "p": _static([x, y]), "s": _static([size, size]), "d": 1}


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


def _extras_shapes(extras, accent, detail):
    """The small decoration the person actually asked for, drawn in the chosen palette."""
    shapes = []
    if extras == "stars":
        for x in (300, 520, 640):
            star = [[x, 95], [x + 5, 106], [x + 17, 108], [x + 8, 116], [x + 11, 128],
                    [x, 121], [x - 11, 128], [x - 8, 116], [x - 17, 108], [x - 5, 106]]
            shapes += [_path(star), _fill(accent)]
    elif extras == "sparkles":
        for x in (300, 540, 650):
            shapes += [_path([[x, 96], [x, 126]], False), _stroke(accent, 4),
                       _path([[x - 15, 111], [x + 15, 111]], False), _stroke(detail, 4)]
    elif extras == "confetti":
        for i, x in enumerate((280, 320, 560, 620, 660)):
            shapes += [_rect(x, 96 + (i % 2) * 20, 10, 10, 2), _fill(accent if i % 2 else detail)]
    elif extras == "waves":
        for x in (300, 600):
            shapes += [_path([[x, 108], [x + 10, 102], [x + 20, 108], [x + 30, 102], [x + 40, 108]], False),
                       _stroke(detail, 4)]
    elif extras == "hearts":
        for x in (300, 620):
            shapes += [_path([[x, 108], [x - 8, 98], [x - 18, 104], [x, 122], [x + 18, 104], [x + 8, 98]]),
                       _fill(accent)]
    return shapes


def _group(name, items):
    """Keep a part's fill and stroke from leaking into the other parts of a drawing."""
    transform = {"ty": "tr", "p": _static([0, 0]), "a": _static([0, 0]), "s": _static([100, 100]),
                 "r": _static(0), "o": _static(100), "sk": _static(0), "sa": _static(0)}
    return {"ty": "gr", "nm": name, "it": [*items, transform]}


def _lightning_shapes(accent, detail):
    """Electric zigzags. Each bolt is its own group so no fill spills onto the card."""
    bolts = []
    for i, (x, y) in enumerate(((58, 92), (250, 56), (520, 52), (726, 84), (690, 168), (96, 162))):
        direction = 1 if i % 2 else -1
        points = [[x, y], [x + 12 * direction, y + 14], [x + 4 * direction, y + 14],
                  [x + 17 * direction, y + 34], [x + 7 * direction, y + 20], [x + 13 * direction, y + 20]]
        bolts.append(_group(f"Bolt {i + 1}", [_path(points), _fill(accent if i % 2 else detail)]))
    return bolts


def build_lottie(brief: dict, kind: str, image_names=None, generic=False) -> dict:
    """One of two Skottie-safe scenes: the action card, or the cut-out character on its own.

    Both share the same canvas and timing, so they can be overlaid into one WebM after approval. No expressions,
    no external assets, and no background baked into the character scene. A character motion needs the six
    generated poses; the generic vector drawing is only used when the caller asks for it by name."""
    if kind not in ("action", "character"):
        raise ValueError("That motion part isn't valid.")
    brief = clean_brief(brief)
    bg, ink, accent, detail = (brief[k] for k in ("background", "ink", "accent", "detail"))
    feel, style, shape = brief["motion_feel"], brief["action_style"], brief["card_shape"]
    layers = []
    next_index = 2

    def add(name, shapes, ip=0, op=FRAMES, ks=None):
        nonlocal next_index
        layers.append({"ddd": 0, "ind": next_index, "ty": 4, "nm": name, "sr": 1,
                       "ks": _transform(ks), "ao": 0, "shapes": shapes, "ip": ip, "op": op, "st": 0, "bm": 0})
        next_index += 1

    if kind == "character" and not generic and len(image_names or []) < 6:
        raise ValueError("The character motion needs six new poses. The picture tool may not have returned them; "
                         "try again, or check that image generation is available for this account.")
    if kind == "character" and (image_names or generic):
        # Real new pictures from the image tool, one per beat. Neighbouring poses overlap a few frames so the
        # hand does not jump between stills. Every pose is normalised to the declared 220x220 transparent canvas.
        assets = []
        for i, name in enumerate((image_names or [])[:6]):
            ip, op = max(0, i * 20 - (4 if i else 0)), min(FRAMES, (i + 1) * 20 + (4 if i < 5 else 0))
            ref = f"pose{i}"
            assets.append({"id": ref, "w": 220, "h": 220, "u": "", "p": name, "e": 0})
            sway = _animated([(ip, -3 if i % 2 else 3), (ip + 10, 3 if i % 2 else -3), (op, 0)], "calm") \
                if brief["image_action"] == "wave" else _static(0)
            layers.append({"ddd": 0, "ind": next_index, "ty": 2, "nm": f"New picture {i + 1}", "sr": 1,
                           "ks": _transform({"a": _static([110, 110, 0]),
                                             "p": _static([180, 120, 0]),
                                             "r": sway,
                                             "s": _animated([(ip, [94, 94, 100]), (ip + 6, [100, 100, 100]),
                                                             (op - 6, [100, 100, 100]), (op, [104, 104, 100])], "calm"),
                                             "o": _animated([(ip, 0), (ip + 6, 100), (op - 6, 100), (op, 0)], "calm")}),
                           "ao": 0, "ip": ip, "op": op, "st": 0, "bm": 0, "refId": ref})
            next_index += 1
        if generic:
            # The vector fallback is opt-in only. Each part is grouped so fills and strokes stay with their part.
            color, behavior = brief["character_color"], brief["image_action"]
            for beat in range(6):
                ip, op = beat * 20, (beat + 1) * 20
                dy = -10 if behavior in ("jump", "pop", "laugh") and beat % 2 else 0
                cx, cy = 180, 120 + dy
                details = []
                if behavior == "laugh" and beat % 2:
                    for ex in (cx - 19, cx + 19):
                        details.append(_group("Closed eye", [_path([[ex - 9, cy - 30], [ex, cy - 37],
                                                                     [ex + 9, cy - 30]], False), _stroke(ink, 4)]))
                else:
                    for ex in (cx - 19, cx + 19):
                        details.append(_group("Eye", [_ellipse(ex, cy - 32, 13), _fill(ink)]))
                if behavior == "laugh":
                    mouth = [[cx - 18, cy + 4], [cx, cy + 18], [cx + 18, cy + 4]]
                elif behavior == "cry":
                    mouth = [[cx - 16, cy + 18], [cx, cy + 8], [cx + 16, cy + 18]]
                elif behavior in ("wave", "jump", "pop", "enter", "spin"):
                    mouth = [[cx - 13, cy + 10], [cx + 13, cy + 10]]
                else:
                    mouth = [[cx - 13, cy + 12], [cx + 13, cy + 12]]
                details.append(_group("Mouth", [_path(mouth, False),
                                                _stroke(accent if behavior == "laugh" else detail, 4)]))
                if behavior == "cry":
                    details += [_group("Tear left", [_ellipse(cx - 28, cy - 10 + beat * 2, 11), _fill(accent)]),
                                _group("Tear right", [_ellipse(cx + 28, cy - 8 + beat * 2, 11), _fill(accent)])]
                if behavior == "wave":
                    lift = -30 - beat * 8
                    arm = [[cx + 28, cy + 20], [cx + 52, cy + 20 + lift // 3], [cx + 68, cy + 14 + lift // 2]]
                    details += [_group("Waving arm", [_path(arm, False), _stroke(color, 10)]),
                                _group("Waving hand", [_ellipse(cx + 70, cy + 12 + lift // 2, 20),
                                                       _fill(color), _stroke(detail, 3)])]
                elif behavior in ("jump", "pop", "laugh"):
                    details += [_group("Left arm", [_path([[cx - 30, cy + 18], [cx - 58, cy - beat * 3]], False),
                                                    _stroke(color, 9)]),
                                _group("Right arm", [_path([[cx + 30, cy + 18], [cx + 58, cy - beat * 3]], False),
                                                     _stroke(color, 9)])]
                if beat in (1, 3, 5):
                    details.append(_group("Spark", [_ellipse(cx + 96, cy - 60, 12), _fill(accent)]))
                # Skottie paints the last shape first: body and head must be last so the face stays on top.
                shapes = details + [_group("Head", [_ellipse(cx, cy - 32, 94), _fill(color), _stroke(detail, 4)]),
                                    _group("Body", [_ellipse(cx, cy + 34, 106), _fill(color), _stroke(detail, 4)])]
                add(f"Generic drawing {beat + 1}", shapes, ip, op,
                    {"o": _animated([(ip, 0), (ip + 3, 100), (op - 3, 100), (op, 0)], "calm")})
            assets = []
    else:
        # The click is now a real event in the scene, not just a word in the brief. A quick request moves it
        # earlier and settles the card sooner; a slow one keeps the invitation on screen longer.
        click = CLICK_AT[brief["click_timing"]]
        settle = 10 if brief["click_timing"] == "early" else 16
        confirmed_at = click + 4
        target = [588, 132] if brief["action"] == "subscribe" else [164, 128]
        corner = {"pill": 62, "square": 6, "rounded": 24}.get(shape, 24)
        if style == "bold":
            corner = max(6, corner - 8)
        radius = 24 if style == "bold" else 18
        add("Card", [_rect(410, 120, 640, 124, corner), _fill(bg), _stroke(detail, radius // 6)], 0, FRAMES,
            {"a": _static([400, 120, 0]),
             "p": _animated([(0, [400, 156, 0]), (settle, [400, 120, 0]),
                             (104, [400, 120, 0]), (119, [420, 100, 0])], feel),
             "o": _animated([(0, 0), (settle - 3, 100), (105, 100), (119, 0)], feel)})
        symbols = {
            "subscribe": [[134, 102], [134, 140], [170, 121]],
            "like": [[124, 141], [124, 108], [137, 108], [150, 80], [161, 80], [166, 88],
                     [160, 109], [186, 109], [194, 118], [181, 157], [148, 157], [137, 148], [124, 148]],
            "comment": [[117, 86], [186, 86], [186, 138], [151, 138], [128, 160], [128, 138], [117, 138]],
            "share": [[119, 150], [128, 119], [158, 107], [158, 84], [198, 121], [158, 157], [158, 134], [138, 136]],
            "save": [[127, 86], [181, 86], [181, 158], [154, 141], [127, 158]],
        }
        add("Action symbol", [_path(symbols[brief["action"]]), _fill(accent)], 8, 108,
            {"o": _animated([(8, 0), (18, 100), (100, 100), (108, 0)], feel)})
        extras = _extras_shapes(brief["extras"], accent, detail)
        if extras:
            add("Requested detail", extras, 14, 106,
                {"o": _animated([(14, 0), (24, 100), (98, 100), (106, 0)], "calm")})
        if brief["electric"] == "lightning":
            add("Electric reply", _lightning_shapes(accent, detail), max(0, click - 8), min(FRAMES, click + 16),
                {"o": _animated([(click - 8, 0), (click - 5, 100), (click - 1, 30),
                                 (click + 3, 100), (click + 8, 20), (click + 14, 0)], "snappy")})
        tick = [[590, 120], [600, 132], [622, 104]]
        add("Confirmation tick", [_group("Tick", [_path(tick, False), _stroke(accent, 8 if style == "bold" else 6)])],
            confirmed_at, FRAMES,
            {"o": _animated([(confirmed_at, 0), (confirmed_at + 6, 100), (119, 100)], feel)})
        add("Click response", [_group("Click ring", [_ellipse(0, 0, 36), _stroke(accent, 3)])],
            click, min(FRAMES, click + 15),
            {"p": _static([*target, 0]),
             "s": _animated([(click, [60, 60, 100]), (click + 15, [190, 190, 100])]),
             "o": _animated([(click, 70), (click + 15, 0)])})
        if brief["cursor"] == "pointer":
            pointer = [[0, 0], [0, 46], [12, 35], [23, 56], [33, 51], [22, 30], [39, 28]]
            add("Pointer press and release", [_group("Pointer", [_path(pointer), _fill(ink), _stroke(accent, 3)])],
                12, 108,
                {"p": _animated([(12, [738, 205, 0]), (click - 8, [*target, 0]),
                                 (click + 16, [*target, 0]), (108, [732, 207, 0])]),
                 "s": _animated([(12, [100, 100, 100]), (click - 2, [100, 100, 100]),
                                 (click + 2, [82, 82, 100]), (click + 8, [100, 100, 100])]),
                 "o": _animated([(12, 0), (18, 100), (100, 100), (108, 0)])})

        def text_layer(name, value, opacity, scale=None):
            document = {"t": value, "f": "Poppins", "s": 44 if style == "bold" else 40,
                        "j": 0, "tr": 0, "lh": 50, "ls": 0,
                        "fc": [int(ink[i:i + 2], 16) / 255 for i in (1, 3, 5)]}
            ks = {"p": _animated([(0, [400, 156, 0]), (settle, [400, 120, 0]),
                                  (104, [400, 120, 0]), (119, [420, 100, 0])], feel),
                  "o": opacity}
            if scale:
                ks["s"] = scale
            layers.append({"ddd": 0, "ind": next_index, "ty": 5, "nm": name, "sr": 1,
                           "ks": _transform(ks), "ao": 0, "ip": 0, "op": FRAMES, "st": 0, "bm": 0,
                           "t": {"d": {"k": [{"t": 0, "s": document}]}, "m": {"g": 1, "a": _static([0, 0])}, "a": []}})

        text_layer("Call to action", brief["label"],
                   _animated([(0, 0), (settle - 2, 100), (confirmed_at, 100), (confirmed_at + 3, 0)], feel))
        text_layer("Confirmed action", LABELS[brief["action"]][brief["language"]][1],
                   _animated([(0, 0), (confirmed_at, 0), (confirmed_at + 4, 100), (104, 100), (119, 0)], feel),
                   _animated([(confirmed_at, [90, 90, 100]), (confirmed_at + 4, [108, 108, 100]),
                              (confirmed_at + 10, [100, 100, 100])], "bouncy"))
        assets = []

    return {"v": "5.12.2", "fr": FPS, "ip": 0, "op": FRAMES, "w": W, "h": H,
            "nm": f"Custom / {kind} / {brief['action']}", "assets": assets,
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


def _run(checkout: str, scene: Path, out: Path, images=None, webm: bool = False):
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = ["node", str(_node_script()), checkout, str(scene), str(out), ffmpeg]
    for image in (images or []):
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


KINDS = ("action", "character")


def check_cutout(image: bytes) -> bytes:
    """The character motion needs a PNG whose background was removed, not a photo with a solid background."""
    from PIL import Image
    try:
        source = Image.open(io.BytesIO(image))
        if source.format != "PNG":
            raise ValueError("Use a PNG picture with the background removed.")
        source = source.convert("RGBA")
    except (ValueError, OSError):
        raise ValueError("That picture could not be opened. Use a PNG with the background removed.")
    low, high = source.getchannel("A").getextrema()
    if low != 0 or high == 0:
        raise ValueError("The picture needs a transparent background. Remove the background before uploading.")
    source.thumbnail((220, 220), Image.LANCZOS)
    out = io.BytesIO()
    source.save(out, "PNG")
    return out.getvalue()


def dominant_color(image: bytes) -> str:
    """The main color of the cut-out, so the drawn character keeps the reference's identity."""
    from PIL import Image
    picture = Image.open(io.BytesIO(image)).convert("RGBA")
    picture.thumbnail((64, 64))
    pixels = [p[:3] for p in picture.getdata() if p[3] > 128]
    if not pixels:
        return "#ffffff"
    r, g, b = Counter(pixels).most_common(1)[0][0]
    return f"#{r:02x}{g:02x}{b:02x}"


def _normalize_pose(data, target: Path, checkout: str):
    """Turn one generated PNG or SVG into a transparent 220x220 pose, or explain what went wrong."""
    from PIL import Image
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("the picture was empty")
    if data[:4] == b"\x89PNG":
        with Image.open(io.BytesIO(data)) as opened:
            picture = opened.convert("RGBA")
    else:
        head = bytes(data[:400]).lower()
        if b"<svg" not in head and b"<?xml" not in head:
            raise ValueError("the picture was not a PNG or an SVG")
        # Generated SVG is only an intermediate file. It never becomes an asset in the scene.
        svg = target.with_suffix(".svg")
        svg.write_bytes(data)
        target.unlink(missing_ok=True)
        try:
            script = Path(__file__).resolve().parent.parent / "tools" / "svg_to_png.mjs"
            done = subprocess.run(["node", str(script), checkout, str(svg), str(target)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  creationflags=NO_WINDOW, timeout=120)
            if done.returncode != 0 or not target.is_file():
                logging.getLogger(__name__).warning("SVG pose conversion failed: %s",
                                                    (done.stderr or done.stdout or "")[-300:])
                raise ValueError("the SVG could not be converted to PNG")
        finally:
            svg.unlink(missing_ok=True)
        with Image.open(target) as opened:
            picture = opened.convert("RGBA")
    low, high = picture.getchannel("A").getextrema()
    if low != 0 or high == 0:
        raise ValueError("the picture has no transparent background")
    picture.thumbnail((220, 220), Image.LANCZOS)
    canvas = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    canvas.alpha_composite(picture, ((220 - picture.width) // 2, (220 - picture.height) // 2))
    canvas.save(target, "PNG")


def draft(cid: str, brief: dict, image: bytes, checkout: str, generated=None) -> dict:
    """Make two Lottie scenes and a filmstrip for each. Nothing is saved for use yet."""
    status = checkout_status(checkout)
    if not status["ready"]:
        raise ValueError(status["message"])
    cutout = check_cutout(image)
    did = new_draft_id()
    folder = _draft_dir(cid, did)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        # The uploaded cut-out only gives the new drawings their color and identity.
        brief = {**clean_brief(brief), "character_color": dominant_color(cutout)}
        image_names, failures = [], []
        for i, data in enumerate((generated or [])[:6]):
            name = f"pose-{i}.png"
            try:
                _normalize_pose(data, folder / name, status["checkout"])
                image_names.append(name)
            except Exception as error:  # noqa: BLE001 - record the step that failed, never hide it
                detail = str(error).strip()[-160:] or "unknown problem"
                failures.append(f"pose {i + 1}: {detail}")
                logging.getLogger(__name__).warning("Custom motion pose %d could not be saved: %s", i + 1, detail)
        if len(image_names) < 6:
            if generated:
                reason = "; ".join(failures[:3])
            else:
                reason = "no pictures were generated; drawing poses needs Codex with image generation"
            raise ValueError("The character motion needs six usable pictures (" + reason + "). Try again; "
                             "the character was not replaced with a generic drawing.")
        scenes = {"action": build_lottie(brief, "action"),
                  "character": build_lottie(brief, "character", image_names)}
        frames_by_kind = {}
        for kind in KINDS:
            scene = folder / f"{kind}.json"
            scene.write_text(json.dumps(scenes[kind]), encoding="utf-8")
            out = folder / kind
            out.mkdir(exist_ok=True)
            pictures = [folder / name for name in image_names] if kind == "character" else []
            result = _run(status["checkout"], scene, out, pictures, False)
            frames = [f for f in result.get("frames") or [] if re.fullmatch(r"frame-\d{4}\.png", f)]
            if len(frames) < 6:
                raise ValueError("The renderer did not make enough frames to show the motion.")
            frames_by_kind[kind] = frames
        return {"id": did, "frames": frames_by_kind, "brief": clean_brief(brief)}
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("That motion part isn't valid.")
    return kind


def filmstrip_data(cid: str, did: str, kind: str = "action") -> bytes:
    """One JPEG strip with the frames in order, so the person can judge the whole progression at a glance."""
    from PIL import Image, ImageDraw
    if not DRAFT_ID.fullmatch(did):
        raise ValueError("That motion draft is not valid.")
    folder = _draft_dir(cid, did) / _kind(kind)
    frames = sorted(p.name for p in folder.glob("frame-*.png"))
    shown = frames if len(frames) <= 11 else frames[::2]
    cell_w, cell_h, cols = 220, 78, 4
    rows = (len(shown) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (18, 18, 18))
    draw = ImageDraw.Draw(canvas)
    for i, name in enumerate(shown):
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


def frame_jpeg(cid: str, did: str, kind: str, name: str) -> bytes:
    """One bigger frame, for people who want to look at the expressiveness closely."""
    if not DRAFT_ID.fullmatch(did) or not re.fullmatch(r"frame-\d{4}\.png", name):
        raise ValueError("That motion draft is not valid.")
    from PIL import Image
    frame = Image.open(_draft_dir(cid, did) / _kind(kind) / name).convert("RGBA")
    background = Image.new("RGBA", frame.size, (18, 18, 18, 255))
    background.alpha_composite(frame)
    out = io.BytesIO()
    background.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def approve(cid: str, did: str, checkout: str) -> dict:
    """After approval only: encode both motions and lay them into one transparent WebM for the clip."""
    if not DRAFT_ID.fullmatch(did):
        raise ValueError("That motion draft is not valid.")
    status = checkout_status(checkout)
    if not status["ready"]:
        raise ValueError(status["message"])
    from . import creators
    folder = _draft_dir(cid, did)
    scenes = {}
    for kind in KINDS:
        scene = folder / f"{kind}.json"
        if not scene.is_file():
            raise ValueError("That motion draft has expired. Ask for it again.")
        scenes[kind] = json.loads(scene.read_text(encoding="utf-8"))
        pictures = sorted(folder.glob("pose-*.png")) if kind == "character" else []
        _run(status["checkout"], scene, folder / kind, pictures, True)
    action_webm, character_webm = folder / "action" / "motion.webm", folder / "character" / "motion.webm"
    if not action_webm.is_file() or not character_webm.is_file():
        raise ValueError("The motion could not be encoded. Ask for it again.")
    merged = folder / "motion.webm"
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    combined = subprocess.run([ffmpeg, "-y", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(action_webm),
                               "-c:v", "libvpx-vp9", "-i", str(character_webm),
                               "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[out]", "-map", "[out]",
                               "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "22",
                               "-deadline", "good", "-cpu-used", "4", "-auto-alt-ref", "0", "-an", str(merged)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=NO_WINDOW, timeout=300)
    if combined.returncode != 0 or not merged.is_file():
        raise ValueError("The two motions could not be joined. Ask for them again.")
    mid = "motion-" + hashlib.sha1(os.urandom(8)).hexdigest()[:10]
    dest = creators.root(cid) / "custom-motions"
    dest.mkdir(parents=True, exist_ok=True)
    merged.replace(dest / f"{mid}.webm")
    (dest / f"{mid}.json").write_text(json.dumps(scenes), encoding="utf-8")
    return {"id": mid, "lottie": scenes["action"], "asset": dest / f"{mid}.webm"}


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
