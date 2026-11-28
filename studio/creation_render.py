"""A small, validated editing vocabulary for vertical creation timelines."""
import json
import math
from pathlib import Path

from . import media

MOTIONS = ('none', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'ken_burns_v1')
TRANSITIONS = ('cut', 'fade', 'crossfade', 'slide_left')


def validate_asset(path, *, check_cancel=None):
    """Do not let a downloaded playlist make FFmpeg fetch other files or URLs."""
    path = Path(path)
    with path.open('rb') as stream:
        beginning = stream.read(64).lstrip()
    if beginning.startswith((b'#EXTM3U', b'ffconcat', b'<', b'[playlist]', b'{')):
        raise ValueError('A mídia recebida é uma lista ou documento, não um arquivo audiovisual independente.')
    try:
        command = ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                   '-show_entries', 'format=format_name,duration:stream=codec_type,width,height,channels,avg_frame_rate',
                   '-of', 'json', str(path)]
        if check_cancel:
            from .creation_remotion import _run
            result = _run(command, check_cancel=check_cancel, timeout=30)
        else:
            result = media.run(command, timeout=30)
        data = json.loads(result)
    except (RuntimeError, ValueError):
        raise ValueError('A mídia não é um arquivo audiovisual válido.') from None
    formats = set(data.get('format', {}).get('format_name', '').split(','))
    allowed = {'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2', 'matroska', 'webm', 'mp3', 'wav', 'ogg', 'flac',
               'aac', 'image2', 'jpeg_pipe', 'png_pipe', 'webp_pipe', 'bmp_pipe'}
    if not formats or not formats.issubset(allowed):
        raise ValueError('O contêiner da mídia não é suportado para edição local.')
    return data


def local(folder, name):
    root = Path(folder).resolve()
    raw = Path(str(name))
    if raw.is_absolute() or '..' in raw.parts or ':' in str(name):
        raise ValueError('A timeline só aceita arquivos deste projeto.')
    result = (root / raw).resolve()
    if not result.is_relative_to(root) or not result.is_file():
        raise ValueError('Uma mídia da timeline está ausente ou fora do projeto.')
    return result


def _number(value, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('Tempo ou parâmetro de edição inválido.') from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Tempo ou parâmetro de edição fora dos limites.')
    return value


def validate(timeline, folder):
    if not isinstance(timeline, dict) or timeline.get('aspect') != '9:16':
        raise ValueError('O vídeo precisa ser vertical 9:16.')
    scenes = timeline.get('scenes')
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 300:
        raise ValueError('A timeline precisa de 1 a 300 cenas.')
    total = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ValueError('Cena inválida.')
        total += _number(scene.get('duration'), 0.1, 600)
        _number(scene.get('start', 0), 0, 86400)
        _number(scene.get('focus_x', 0.5), 0, 1)
        _number(scene.get('focus_y', 0.5), 0, 1)
        if scene.get('motion', 'none') not in MOTIONS or scene.get('transition', 'cut') not in TRANSITIONS:
            raise ValueError('A timeline solicitou um movimento ou transição não implementado.')
        if scene.get('fit', 'cover') not in ('cover', 'contain'):
            raise ValueError('Modo de enquadramento inválido.')
        validate_asset(local(folder, scene.get('media', '')))
        if scene.get('audio'):
            validate_asset(local(folder, scene['audio']))
        for field in ('text', 'caption'):
            if not isinstance(scene.get(field, ''), str) or len(scene.get(field, '')) > 1200:
                raise ValueError('Texto de cena inválido ou longo demais.')
        if scene.get('captions'):
            caption_events(scene)
    if total > 7200:
        raise ValueError('A timeline excede duas horas.')
    for track in timeline.get('tracks', []):
        validate_asset(local(folder, track.get('file', '')))
        _number(track.get('at', 0), 0, total)
        _number(track.get('volume', 0.12), 0, 2)
        _number(track.get('duration', total), 0.1, 7200)
    return total


def _ass_text(value):
    return str(value).replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\r', '').replace('\n', '\\N')


def _stamp(seconds, *, ass=False):
    if ass:
        value = int(round(seconds * 100))
        return f'{value // 360000}:{value // 6000 % 60:02}:{value // 100 % 60:02}.{value % 100:02}'
    value = int(round(seconds * 1000))
    return f'{value // 3600000:02}:{value // 60000 % 60:02}:{value // 1000 % 60:02},{value % 1000:03}'


def _subtitles(scene, width, height):
    size = max(14, round(height * 0.034))
    header = (f'[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n'
              'WrapStyle: 0\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, '
              'SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, '
              'ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n')
    margin = int(width * 0.1)
    header += f'Style: Caption,Poppins ExtraBold,{size},&H00FFFFFF,&H00FFFFFF,&H00141414,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,{margin},{margin},{int(height * .20)},1\n'
    header += f'Style: Title,Poppins ExtraBold,{int(size * 1.15)},&H00FFFFFF,&H00FFFFFF,&H00141414,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,8,{margin},{margin},{int(height * .12)},1\n'
    header += '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    if scene.get('text'):
        header += f"Dialogue: 0,0:00:00.00,{_stamp(scene['duration'], ass=True)},Title,,0,0,0,,{_ass_text(scene['text'])}\n"
    for item in caption_events(scene):
        header += f"Dialogue: 0,{_stamp(item['start'], ass=True)},{_stamp(item['end'], ass=True)},Caption,,0,0,0,,{_ass_text(item['text'])}\n"
    return header


def caption_events(scene):
    events = []
    parts = scene.get('captions') or [{'start': 0, 'end': scene['duration'], 'text': scene.get('caption', '')}]
    for part in parts:
        words = part['text'].split()
        chunks = [words[i:i+6] for i in range(0, len(words), 6)]
        start, end = float(part['start']), float(part['end'])
        _number(start, 0, scene['duration'])
        _number(end, start, scene['duration'])
        for index, chunk in enumerate(chunks):
            events.append({'start': start + (end-start)*index/len(chunks),
                           'end': start + (end-start)*(index+1)/len(chunks), 'text': ' '.join(chunk)})
    return events


def canonicalize(timeline, folder, *, check_cancel=None):
    from .creation_timeline import validate as validated
    return validated(timeline, folder, check_cancel=check_cancel)


def capabilities():
    from .creation_timeline import capabilities as available
    return available()


def identity(timeline, folder):
    from .creation_remotion import identity as render_identity
    return render_identity(timeline, folder)


def render(timeline, folder, output, height=960, *, check_cancel=None, on_progress=None):
    from .creation_remotion import render as compose
    return compose(timeline, folder, output, height=height,
                   check_cancel=check_cancel, on_progress=on_progress)


def inspect(path):
    data = json.loads(media.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    audio = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
    if not video or not audio or video['width'] * 16 != video['height'] * 9:
        raise ValueError('A exportação precisa de vídeo 9:16 e faixa de áudio.')
    duration = float(data['format'].get('duration', 0))
    media.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'], timeout=max(120, duration * 3))
    return {'width': video['width'], 'height': video['height'], 'duration': duration,
            'audio': True, 'aspect': '9:16', 'decoded_entire_file': True}


def audio_duration(path):
    value = media.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(path)])
    return _number(value.strip(), 0.01, 7200)
