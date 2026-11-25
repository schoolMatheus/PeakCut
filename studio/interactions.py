"""Timed audience motions and their sound effects, ready for the video renderer."""
import shutil
from pathlib import Path

WIDTH, HEIGHT, DURATION = 800, 240, 4.0
SPEED = 1.5
ASSETS = Path(__file__).resolve().parent / "assets" / "interactions"
SOUNDS = {
    "woosh": "ribhavagrawal-woosh-230554.mp3",
    "click": "universfield-computer-mouse-click-352734.mp3",
    "typing": "son_duquotidient-son-clavier-iphone-478600.mp3",
}
ACTIONS = ("subscribe", "like", "comment", "share", "save")


def plan(actions, duration, hook_y, cap_y, language="en", *, caption_style="clean", custom_motions=None) -> list:
    """Image and sound use the same clip-relative clock, including shortened clips.

    `custom_motions` maps an audience request to an approved WebM for that creator. It has the same shape and
    length as the built-in artwork, so timing and sound stay exactly the same."""
    actions = list(dict.fromkeys(a for a in (actions or []) if isinstance(a, str) and a in ACTIONS))
    if not actions or duration <= .5:
        return []
    # Match the supplied reference: the visible body starts just below the words.
    # The asset has transparent padding above its white body.
    # Taller styles also need room for their pop, outlines and accents, including
    # the comment card's short upward exit. Clean retains the reference spacing.
    gap = {"clean": 32, "bounce": 56, "hype": 80}.get(caption_style, 56)
    cap_y = min(cap_y, 1920 - 40 - HEIGHT - gap)
    top = cap_y + gap
    center = top + HEIGHT / 2
    if abs(center - hook_y) < 300:
        return []
    language = "pt-BR" if language == "pt-BR" else "en"
    slot = (duration - .4) / len(actions)
    window = min(DURATION, slot - min(.15, slot * .15))
    hold = window / SPEED
    motions = []
    for i, action in enumerate(actions):
        start = .2 + i * slot + (slot - window) / 2
        end = start + hold
        rate = hold / DURATION
        # Start at the audible cue, rather than the MP3's leading silence. Use the same
        # speed as the artwork so tiny motions still contain the click and keyboard taps.
        sounds = [{"name": "woosh", "start": start, "end": min(end, start + .829375 * rate),
                   "source_start": .32, "source_duration": .829375, "rate": rate, "gain": .6}]
        if action == "comment":
            sounds.append({"name": "typing", "start": start + 28 / 30 * rate,
                           "end": start + 76 / 30 * rate, "source_start": .2, "source_duration": 1.6,
                           "rate": rate, "gain": .6})
        click = (88 if action == "comment" else 48) / 30 * rate
        sounds.append({"name": "click", "start": start + click, "end": min(end, start + click + .498 * rate),
                       "source_start": .15, "source_duration": .498, "rate": rate, "gain": .6})
        motions.append({"action": action, "start": start, "end": end, "rate": rate,
                        "x": (1080 - WIDTH) / 2, "y": top, "caption_y": cap_y,
                        "asset": (Path(custom_motions[action]) if custom_motions and custom_motions.get(action)
                                  else ASSETS / language / f"{action}.webm"), "sounds": sounds})
    return motions


def compose(work: Path, motions: list, first_input: int, duration: float, style: dict | None = None,
            seal_avatar: bytes | None = None):
    """Add alpha videos and quiet sounds to existing [v] and [normalized] tracks.

    A custom style recolors the artwork and adds the channel seal in the empty side of the strip. The built-in
    YouTube style passes None here, so the original animation and its colors are left exactly as they were."""
    from . import interaction_styles
    inputs, filters = [], []
    video = "v"
    lut = ""
    if style:
        (work / "style.cube").write_text(interaction_styles.lut_text(style), encoding="utf-8")
        lut = ",lut3d=file=style.cube"
    for i, m in enumerate(motions):
        name = f"m{i}.webm"
        shutil.copyfile(m["asset"], work / name)
        # ffmpeg's native VP9 decoder drops alpha; libvpx decodes the transparent plane.
        inputs += ["-c:v", "libvpx-vp9", "-i", name]
        filters.append(f"[{first_input+i}:v]setpts=(PTS-STARTPTS)*{m['rate']:.8f}+{m['start']:.8f}/TB,"
                       f"format=rgba{lut}[m{i}]")
        next_video = f"mv{i}"
        filters.append(f"[{video}][m{i}]overlay={m['x']:.0f}:{m['y']:.0f}:eof_action=pass:repeatlast=0:"
                       f"enable='gte(t,{m['start']:.8f})*lt(t,{m['end']:.8f})'[{next_video}]")
        video = next_video
    # The seal sits in the free space to the left of the artwork, never over the words or the title.
    have_seal = bool(style and style.get("seal") and motions)
    if have_seal:
        (work / "seal.png").write_bytes(interaction_styles.seal_png(style, seal_avatar))
        inputs += ["-i", "seal.png"]
        first = motions[0]["start"]
        last = max(x["end"] for x in motions)
        filters.append(f"[{first_input+len(motions)}:v]format=rgba[seali]")
        filters.append(f"[{video}][seali]overlay={max(0, int(m['x']) - 132)}:{int(m['y']) + 8}:"
                       f"enable='gte(t,{first:.8f})*lt(t,{last:.8f})'[sealv]")
        video = "sealv"
    effects = [s for m in motions for s in m["sounds"]]
    used = list(dict.fromkeys(s["name"] for s in effects))
    for i, name in enumerate(used):
        source = f"s{i}.mp3"
        shutil.copyfile(ASSETS / "sounds" / SOUNDS[name], work / source)
        inputs += ["-i", source]
        selected = [j for j, s in enumerate(effects) if s["name"] == name]
        branches = "".join(f"[raw{j}]" for j in selected)
        sound_at = first_input + len(motions) + (1 if have_seal else 0) + i
        filters.append(f"[{sound_at}:a]asplit={len(selected)}{branches}")
        for j in selected:
            s = effects[j]
            length = s["end"] - s["start"]
            fade = min(.03, length / 4)
            tempo, tempo_filters = 1 / s["rate"], []
            while tempo > 2:
                tempo_filters.append("atempo=2")
                tempo /= 2
            tempo_filters.append(f"atempo={tempo:.8f}")
            filters.append(f"[raw{j}]aresample=48000,aformat=channel_layouts=stereo,"
                           f"atrim=start={s['source_start']:.8f}:duration={s['source_duration']:.8f},"
                           f"asetpts=PTS-STARTPTS,{','.join(tempo_filters)},apad=whole_dur={length:.8f},"
                           f"atrim=duration={length:.8f},volume={s['gain']:.6f},afade=t=out:st={length-fade:.8f}:d={fade:.8f},"
                           f"adelay={round(s['start']*48000)}S:all=1[fx{j}]")
    sounds = "".join(f"[fx{j}]" for j in range(len(effects)))
    # Never normalize a quiet effect up. The limiter also disables automatic makeup gain.
    filters.append(f"[normalized]apad=whole_dur={duration:.8f},atrim=duration={duration:.8f}[base_audio]")
    filters.append(f"[base_audio]{sounds}amix=inputs={len(effects)+1}:duration=first:normalize=0,"
                   f"alimiter=limit=0.95:level=0:latency=1,atrim=duration={duration:.8f}[motion_a]")
    filters.append(f"[{video}]null[motion_v]")
    return inputs, ";".join(filters)
