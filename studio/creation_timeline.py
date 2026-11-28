"""The shared, versioned editing contract. Models describe data, never code."""
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ID = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')]
Frame = Annotated[int, Field(ge=0, le=216000, strict=True)]
Length = Annotated[int, Field(ge=1, le=216000, strict=True)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Seconds = Annotated[float, Field(ge=0, le=86400, allow_inf_nan=False)]
MOTIONS = ('none', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'ken_burns_v1')
TRANSITIONS = ('cut', 'fade', 'crossfade', 'slide_left')
TEMPLATES = ('title_pop_v1', 'lower_third_v1', 'cta_v1', 'comparison_v1')
STYLES = ('segment_v1', 'karaoke_v1')


class Data(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Point(Data):
    x: Unit = .5
    y: Unit = .5


class Motion(Data):
    id: Literal['none', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'ken_burns_v1'] = 'none'
    scaleFrom: Annotated[float, Field(ge=1, le=2)] = 1
    scaleTo: Annotated[float, Field(ge=1, le=2)] = 1.08


class Word(Data):
    text: Annotated[str, Field(min_length=1, max_length=100)]
    startFrame: Frame
    endFrame: Length


class Clip(Data):
    id: ID
    sceneId: str = ''
    assetId: str = ''
    startFrame: Frame
    durationInFrames: Length
    sourceInSeconds: Seconds = 0
    sourceOutSeconds: Seconds | None = None
    audioStartSeconds: Seconds | None = None
    audioDurationSeconds: Seconds | None = None
    fit: Literal['cover', 'contain'] = 'cover'
    focus: Point = Field(default_factory=Point)
    motion: Motion = Field(default_factory=Motion)
    templateId: Literal['title_pop_v1', 'lower_third_v1', 'cta_v1', 'comparison_v1'] = 'title_pop_v1'
    text: Annotated[str, Field(max_length=1200)] = ''
    position: Point = Field(default_factory=lambda: Point(x=.5, y=.2))
    styleId: Literal['segment_v1', 'karaoke_v1'] = 'segment_v1'
    words: Annotated[list[Word], Field(max_length=300)] = Field(default_factory=list)
    gainDb: Annotated[float, Field(ge=-80, le=12)] = 0
    fadeInFrames: Frame = 0
    fadeOutFrames: Frame = 0
    loop: bool = False
    opacity: Unit = 1
    fadeVisualFrames: Frame = 0


class Track(Data):
    id: ID
    type: Literal['video', 'text', 'captions', 'audio']
    layer: Annotated[int, Field(ge=0, le=20)] = 0
    role: Literal['narration', 'music', 'sfx', 'original'] | None = None
    duckUnder: str | None = None
    clips: Annotated[list[Clip], Field(min_length=1, max_length=1000)]


class Asset(Data):
    id: ID
    kind: Literal['video', 'image', 'audio']
    file: Annotated[str, Field(min_length=1, max_length=240)]


class Format(Data):
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    fps: Literal[30] = 30
    durationInFrames: Length
    safeAreaProfile: Literal['shorts-default-v1'] = 'shorts-default-v1'


class Transition(Data):
    id: ID
    fromClipId: ID
    toClipId: ID
    type: Literal['crossfade', 'slide_left']
    startFrame: Frame
    durationInFrames: Annotated[int, Field(ge=1, le=60, strict=True)]


class Timeline(Data):
    schemaVersion: Literal['2.0']
    format: Format
    assets: Annotated[list[Asset], Field(min_length=1, max_length=1000)]
    tracks: Annotated[list[Track], Field(min_length=1, max_length=32)]
    transitions: Annotated[list[Transition], Field(max_length=300)] = Field(default_factory=list)

    @model_validator(mode='after')
    def relationships(self):
        def unique(items, label):
            ids = [v.id for v in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f'IDs duplicados em {label}.')
        unique(self.assets, 'assets')
        unique(self.tracks, 'tracks')
        unique(self.transitions, 'transitions')
        clips = [c for t in self.tracks for c in t.clips]
        if len(clips) > 2000:
            raise ValueError('A timeline excede 2000 elementos.')
        unique(clips, 'clips')
        assets = {v.id: v for v in self.assets}
        tracks = {v.id: v for v in self.tracks}
        visuals, owners = {}, {}
        coverage = []
        for track in self.tracks:
            if track.duckUnder and (track.type != 'audio' or track.role != 'music' or
                    track.duckUnder not in tracks or tracks[track.duckUnder].type != 'audio' or
                    tracks[track.duckUnder].role != 'narration'):
                raise ValueError(f'{track.id}: ducking exige uma faixa de narração existente.')
            for clip in track.clips:
                end = clip.startFrame + clip.durationInFrames
                if end > self.format.durationInFrames:
                    raise ValueError(f'{clip.id}: elemento ultrapassa o fim do vídeo.')
                if max(clip.fadeInFrames, clip.fadeOutFrames, clip.fadeVisualFrames) > clip.durationInFrames:
                    raise ValueError(f'{clip.id}: fade maior que o elemento.')
                if clip.sourceOutSeconds is not None and clip.sourceOutSeconds <= clip.sourceInSeconds:
                    raise ValueError(f'{clip.id}: trecho de origem inválido.')
                if track.type in ('audio', 'video'):
                    asset = assets.get(clip.assetId)
                    if not asset or (track.type == 'video' and asset.kind not in ('image', 'video')) or (
                            track.type == 'audio' and asset.kind not in ('audio', 'video')):
                        raise ValueError(f'{clip.id}: mídia inexistente ou tipo incompatível.')
                if track.type == 'video':
                    visuals[clip.id] = clip
                    owners[clip.id] = track.id
                    if track.layer == 0:
                        coverage.append((clip.startFrame, end))
                if track.type == 'captions':
                    if clip.styleId == 'karaoke_v1' and not clip.words:
                        raise ValueError(f'{clip.id}: karaokê exige timestamps por palavra.')
                    previous = clip.startFrame
                    for word in clip.words:
                        if not previous <= word.startFrame < word.endFrame <= end:
                            raise ValueError(f'{clip.id}: timestamps de palavras inválidos.')
                        previous = word.endFrame
        covered = 0
        for start, end in sorted(coverage):
            if start > covered:
                raise ValueError('A timeline contém uma lacuna visual não declarada.')
            covered = max(covered, end)
        if covered != self.format.durationInFrames:
            raise ValueError('A faixa visual precisa cobrir o vídeo inteiro.')
        for transition in self.transitions:
            left, right = visuals.get(transition.fromClipId), visuals.get(transition.toClipId)
            if not left or not right or left is right or owners[left.id] != owners[right.id]:
                raise ValueError(f'{transition.id}: clipes da transição inválidos.')
            overlap_end = min(left.startFrame + left.durationInFrames, right.startFrame + right.durationInFrames)
            if transition.startFrame != right.startFrame or transition.startFrame <= left.startFrame or (
                    transition.startFrame + transition.durationInFrames != overlap_end) or (
                    overlap_end != left.startFrame + left.durationInFrames) or (
                    transition.durationInFrames >= min(left.durationInFrames, right.durationInFrames)):
                raise ValueError(f'{transition.id}: sobreposição não corresponde aos clipes.')
        pairs = [(t.fromClipId, t.toClipId) for t in self.transitions]
        if len(pairs) != len(set(pairs)):
            raise ValueError('Transição duplicada entre os mesmos clipes.')
        for track in self.tracks:
            if track.type == 'video':
                ordered = sorted(track.clips, key=lambda c: c.startFrame)
                for index, left in enumerate(ordered):
                    for right in ordered[index+1:]:
                        if right.startFrame >= left.startFrame + left.durationInFrames:
                            break
                        if (left.id, right.id) not in pairs:
                            raise ValueError(f'{right.id}: sobreposição visual sem transição declarada.')
            for clip in track.clips:
                if track.type != 'audio' and (clip.audioStartSeconds is not None or clip.audioDurationSeconds is not None):
                    raise ValueError(f'{clip.id}: tempos precisos de áudio em uma faixa visual.')
                if track.type == 'audio':
                    start = clip.audioStartSeconds if clip.audioStartSeconds is not None else clip.startFrame/30
                    length = clip.audioDurationSeconds if clip.audioDurationSeconds is not None else clip.durationInFrames/30
                    if length <= 0 or start + length > self.format.durationInFrames/30 + 1e-8:
                        raise ValueError(f'{clip.id}: áudio ultrapassa a duração da timeline.')
        return self


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def capabilities():
    return {'schemaVersion': '2.0', 'renderer': 'ffmpeg_remotion', 'fps': 30, 'aspect': '9:16',
            'motions': list(MOTIONS), 'transitions': list(TRANSITIONS), 'templates': list(TEMPLATES),
            'caption_styles': list(STYLES), 'karaoke_requires_word_alignment': True,
            'safe_area': {'left': .10, 'right': .10, 'top': .12, 'bottom': .20}}


def _seconds(value, low=0, high=7200):
    if isinstance(value, bool):
        raise ValueError('Tempo inválido.')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Tempo fora dos limites.')
    return value


def migrate(legacy):
    """Retain old source-time semantics and speech length; crossfades get visual handles."""
    if legacy.get('aspect') != '9:16' or not isinstance(legacy.get('scenes'), list) or not 1 <= len(legacy['scenes']) <= 300:
        raise ValueError('A timeline exige cenas verticais 9:16.')
    assets, tracks, clips, transitions, seen = [], [], [], [], {}
    titles, captions, voice = [], [], []
    starts, precise_starts, cursor = [], [], 0.0
    for scene in legacy['scenes']:
        starts.append(round(cursor * 30))
        precise_starts.append(cursor)
        cursor += _seconds(scene['duration'], .1, 600)
    if cursor > 7200:
        raise ValueError('A timeline excede duas horas.')
    total = math.ceil(cursor * 30 - 1e-8)
    ends = starts[1:] + [total]

    def asset(file, kind):
        if not isinstance(file, str) or not file:
            raise ValueError('Mídia ausente na timeline.')
        key = (file, kind)
        if key not in seen:
            seen[key] = f'MID-{len(assets)+1:03d}'
            assets.append({'id': seen[key], 'kind': kind, 'file': file})
        return seen[key]

    for index, scene in enumerate(legacy['scenes']):
        sid, start, end = scene.get('id', f'CENA-{index+1:03d}'), starts[index], ends[index]
        source = scene.get('media')
        kind = 'image' if Path(str(source)).suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp') else 'video'
        transition = scene.get('transition', 'cut')
        if transition not in TRANSITIONS:
            raise ValueError('Transição não implementada.')
        if index and transition in ('crossfade', 'slide_left'):
            overlap = min(round(_seconds(scene.get('transition_duration', .2), .03, 2) * 30),
                          clips[-1]['durationInFrames']-1, end-start-1)
            if overlap < 1:
                raise ValueError('Cenas curtas demais para a transição.')
            clips[-1]['durationInFrames'] += overlap
            transitions.append({'id': f'TR-{index:03d}', 'fromClipId': clips[-1]['id'],
                                'toClipId': f'CLIP-{index+1:03d}', 'type': transition,
                                'startFrame': start, 'durationInFrames': overlap})
        clip = {'id': f'CLIP-{index+1:03d}', 'sceneId': sid, 'assetId': asset(source, kind),
                'startFrame': start, 'durationInFrames': end-start,
                'sourceInSeconds': _seconds(scene.get('start', 0), 0, 86400) if kind == 'video' else 0,
                'fit': scene.get('fit', 'cover'), 'focus': {'x': scene.get('focus_x', .5), 'y': scene.get('focus_y', .5)},
                'motion': {'id': scene.get('motion', 'none')}, 'loop': kind == 'video',
                'fadeVisualFrames': min(6, (end-start)//4) if transition == 'fade' else 0}
        clips.append(clip)
        if scene.get('text'):
            titles.append({'id': f'TITLE-{index+1:03d}', 'sceneId': sid, 'text': scene['text'],
                           'templateId': scene.get('template_id', 'title_pop_v1'),
                           'position': {'x': scene.get('text_x', .5), 'y': scene.get('text_y', .2)},
                           'startFrame': start, 'durationInFrames': end-start})
        for overlay in scene.get('overlays') or []:
            at = round(_seconds(overlay.get('at', 0), 0, scene['duration'])*30)
            length = min(end-start-at, round(_seconds(overlay.get('duration', scene['duration']), .03, 600)*30))
            if length < 1:
                raise ValueError('Um texto adicional começa depois do fim da cena.')
            titles.append({'id': f'OVERLAY-{index+1}-{len(titles)+1}', 'sceneId': sid,
                           'text': overlay['text'], 'templateId': overlay.get('template_id', 'title_pop_v1'),
                           'position': {'x': overlay.get('x', .5), 'y': overlay.get('y', .2)},
                           'startFrame': start+at, 'durationInFrames': length})
        # These are segment timings measured from speech, not invented word alignment.
        parts = scene.get('captions') or [{'start': 0, 'end': scene['duration'], 'text': scene.get('caption', '')}]
        for part in parts:
            if not part.get('text'):
                continue
            a, b = _seconds(part['start'], 0, scene['duration']), _seconds(part['end'], 0, scene['duration'])
            if b <= a:
                raise ValueError('Intervalo de legenda inválido.')
            begin, finish = start+round(a*30), min(end, start+math.ceil(b*30))
            captions.append({'id': f'CAP-{len(captions)+1:04d}', 'sceneId': sid,
                             'text': part['text'], 'startFrame': begin, 'durationInFrames': max(1, finish-begin),
                             'position': {'x': .5, 'y': .72}})
        if scene.get('audio'):
            voice.append({'id': f'VOICE-{index+1:03d}', 'sceneId': sid, 'assetId': asset(scene['audio'], 'audio'),
                          'startFrame': start, 'durationInFrames': end-start,
                          'audioStartSeconds': precise_starts[index], 'audioDurationSeconds': float(scene['duration'])})
        original = _seconds(scene.get('original_volume', 0), 0, 1)
        if original and kind == 'video':
            tracks.append({'id': f'original-{index}', 'type': 'audio', 'role': 'original', 'clips': [
                {'id': f'ORIGINAL-{index}', 'assetId': clip['assetId'], 'startFrame': start,
                 'durationInFrames': end-start, 'sourceInSeconds': clip['sourceInSeconds'],
                 'gainDb': max(-80, 20*math.log10(original)), 'loop': True}]})
    tracks.insert(0, {'id': 'visual', 'type': 'video', 'clips': clips})
    if titles: tracks.append({'id': 'titles', 'type': 'text', 'layer': 1, 'clips': titles})
    if captions: tracks.append({'id': 'captions', 'type': 'captions', 'layer': 2, 'clips': captions})
    if voice: tracks.append({'id': 'voice', 'type': 'audio', 'role': 'narration', 'clips': voice})
    for index, track in enumerate(legacy.get('tracks', [])):
        begin = round(_seconds(track.get('at', 0), 0, cursor)*30)
        duration = min(total-begin, round(_seconds(track.get('duration', cursor), .01, 7200)*30))
        if duration < 1:
            continue
        volume = _seconds(track.get('volume', .12), 0, 2)
        role = 'music' if track.get('kind') == 'music' else 'sfx'
        tracks.append({'id': f'audio-{index}', 'type': 'audio', 'role': role,
                       'duckUnder': 'voice' if role == 'music' and voice else None,
                       'clips': [{'id': f'AUDIO-{index}', 'assetId': asset(track['file'], 'audio'),
                                  'startFrame': begin, 'durationInFrames': duration,
                                  'gainDb': max(-80, 20*math.log10(volume)) if volume else -80,
                                  'loop': bool(track.get('loop', False)),
                                  'fadeInFrames': min(3, duration), 'fadeOutFrames': min(6, duration)}]})
    return {'schemaVersion': '2.0', 'format': {'durationInFrames': total},
            'assets': assets, 'tracks': tracks, 'transitions': transitions}


def validate(timeline, folder, *, check_cancel=None):
    """Validate data and private files before either engine receives a job."""
    from . import creation_render as old
    if check_cancel:
        check_cancel()
    if not isinstance(timeline, dict):
        raise ValueError('A timeline precisa ser um objeto JSON.')
    data = copy.deepcopy(timeline) if 'schemaVersion' in timeline else migrate(timeline)
    try:
        model = Timeline.model_validate(data)
    except ValidationError as error:
        details = [{'field': '/'.join(map(str, e['loc'])), 'message': e['msg']} for e in error.errors()]
        raise ValueError('Timeline inválida: ' + json.dumps(details, ensure_ascii=False)) from None
    root = Path(folder).resolve()
    metadata = {}
    for asset in model.assets:
        if check_cancel:
            check_cancel()
        path = old.local(root, asset.file)
        probe = old.validate_asset(path, check_cancel=check_cancel)
        streams = probe.get('streams', [])
        required = 'audio' if asset.kind == 'audio' else 'video'
        if not any(s.get('codec_type') == required for s in streams):
            raise ValueError(f'{asset.id}: arquivo sem faixa {required}.')
        if asset.kind == 'image' and path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
            raise ValueError(f'{asset.id}: imagem deve ser um arquivo raster local.')
        metadata[asset.id] = (asset, probe)
    for track in model.tracks:
        for clip in track.clips:
            if track.type not in ('video', 'audio'):
                continue
            asset, probe = metadata[clip.assetId]
            if asset.kind == 'image':
                continue
            length = float(probe.get('format', {}).get('duration', 0))
            if not math.isfinite(length) or length <= 0 or clip.sourceInSeconds >= length:
                raise ValueError(f'{clip.id}: início fora da duração da mídia.')
            available = (clip.sourceOutSeconds if clip.sourceOutSeconds is not None else length) - clip.sourceInSeconds
            if clip.sourceOutSeconds is not None and clip.sourceOutSeconds > length + .05:
                raise ValueError(f'{clip.id}: fim fora da duração da mídia.')
            # Quantization can add up to one video frame; audio is padded by the mixer.
            needed = clip.audioDurationSeconds if clip.audioDurationSeconds is not None else clip.durationInFrames/30
            if not clip.loop and available + 1/30 + .001 < needed:
                raise ValueError(f'{clip.id}: mídia curta demais; looping não foi autorizado.')
    return model.model_dump(mode='json')
