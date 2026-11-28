"""Private FFmpeg preparation, local Remotion composition and one final audio mix."""
from collections import deque
from contextlib import AbstractContextManager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from . import config, tools
from .creation_prompts import Pending

_ROOT = Path(__file__).resolve().parent.parent
_NODE = _ROOT / 'my-video'
_FONT = Path(__file__).resolve().parent / 'fonts' / 'Poppins-ExtraBold.ttf'
_PROFILE = {'fps': 30, 'videoCodec': 'h264', 'pixelFormat': 'yuv420p', 'audioCodec': 'aac',
            'sampleRate': 48000, 'integratedLufs': -16, 'truePeakDb': -1.5, 'loudnessRange': 11}


def _worker_env():
    """A video worker needs the operating system, never the server's credentials."""
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE',
               'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'SYSTEMDRIVE',
               'COMSPEC', 'PATHEXT', 'LANG', 'LC_ALL'}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _stop(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                       timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run(arguments, *, check_cancel=None, on_event=None, timeout=600, cwd=None, env=None):
    """Drain bounded output while checking cancellation, including silent subprocesses."""
    if check_cancel:
        check_cancel()
    options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen([str(item) for item in arguments], shell=False, cwd=cwd,
                               env=env or _worker_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               **options)
    lines = queue.Queue(maxsize=256)
    stopped = threading.Event()
    def read():
        try:
            while not stopped.is_set():
                raw = process.stdout.readline(65537)
                if not raw:
                    break
                while not stopped.is_set():
                    try:
                        lines.put(raw[:65536].decode('utf-8', errors='replace').rstrip(), timeout=.1)
                        break
                    except queue.Full:
                        pass
        finally:
            process.stdout.close()
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    began = time.monotonic()
    tail = deque()
    output_bytes = 0
    error = None
    try:
        while process.poll() is None or reader.is_alive() or not lines.empty():
            if check_cancel:
                check_cancel()
            if time.monotonic() - began > timeout:
                raise RuntimeError('O render passou do tempo permitido. Reduza a duração e tente novamente.')
            try:
                line = lines.get(timeout=.1)
            except queue.Empty:
                continue
            tail.append(line)
            output_bytes += len(line)
            while output_bytes > 1024 * 1024 and len(tail) > 1:
                output_bytes -= len(tail.popleft())
            if on_event and line.startswith('{'):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    on_event(event)
                    if event.get('type') == 'error':
                        error = str(event.get('message', 'O worker informou uma falha.'))[:2000]
        if process.returncode or error:
            raise RuntimeError(error or ('Falha no render local: ' + '\n'.join(tail)[-8000:]))
        return '\n'.join(tail)
    except BaseException:
        _stop(process)
        raise
    finally:
        stopped.set()
        reader.join(timeout=2)


class _AssetServer(AbstractContextManager):
    """An exact allowlist, bound only to loopback, lasting for one render job."""
    def __init__(self, files):
        allowed = {}
        keys = {}
        for key, path in files.items():
            path = Path(path).resolve(strict=True)
            token = '/' + secrets.token_urlsafe(32) + path.suffix.lower()
            allowed[token] = path
            keys[key] = token
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_HEAD(self):
                self._serve(False)
            def do_GET(self):
                self._serve(True)
            def _serve(self, body):
                path = allowed.get(self.path)
                if path is None:
                    self.send_error(404)
                    return
                try:
                    size = path.stat().st_size
                    start, end = 0, size - 1
                    partial = self.headers.get('Range')
                    if partial:
                        if not partial.startswith('bytes=') or ',' in partial:
                            raise ValueError()
                        left, right = partial[6:].split('-', 1)
                        if left:
                            start = int(left)
                            end = min(int(right), size - 1) if right else size - 1
                        else:
                            start = max(0, size - int(right))
                        if start < 0 or start >= size or end < start:
                            raise ValueError()
                    self.send_response(206 if partial else 200)
                    self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(end - start + 1))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Cache-Control', 'private, max-age=3600')
                    if partial:
                        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    if body:
                        with path.open('rb') as stream:
                            stream.seek(start)
                            remaining = end - start + 1
                            while remaining:
                                chunk = stream.read(min(262144, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                except ValueError:
                    self.send_error(416, 'Invalid byte range')
                except OSError:
                    self.close_connection = True
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        origin = f'http://127.0.0.1:{self.server.server_port}'
        self.urls = {key: origin + token for key, token in keys.items()}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _sha(path, check_cancel=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            if check_cancel:
                check_cancel()
            digest.update(chunk)
    return digest.hexdigest()


def _local(folder, name):
    root = Path(folder).resolve()
    raw = Path(name)
    if raw.is_absolute() or '..' in raw.parts or ':' in str(name):
        raise ValueError('A mídia precisa estar dentro deste projeto.')
    path = (root / raw).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('A mídia precisa estar dentro deste projeto.')
    return path


def _templates_hash():
    digest = hashlib.sha256()
    paths = sorted([p for p in (_NODE / 'src').rglob('*') if p.is_file()])
    paths += [p for p in [_NODE / 'worker.mjs', _NODE / 'package.json', _NODE / 'package-lock.json',
                         _NODE / 'remotion.config.ts'] if p.is_file()]
    for path in paths:
        digest.update(str(path.relative_to(_NODE)).replace('\\', '/').encode())
        digest.update(path.read_bytes())
    digest.update(_FONT.read_bytes())
    digest.update(Path(__file__).read_bytes())
    digest.update(json.dumps(_PROFILE, sort_keys=True, separators=(',', ':')).encode())
    return digest.hexdigest()


def _identity(canonical, folder, check_cancel=None):
    from . import creation_timeline as contract
    return {'timeline_sha256': contract.fingerprint(canonical),
            'assets_sha256': {item['id']: _sha(_local(folder, item['file']), check_cancel)
                              for item in canonical['assets']},
            'templates_sha256': _templates_hash()}


def _bundle_dir(folder, templates_hash):
    # This cache contains application code only. Keep it short on Windows; media
    # and per-user props stay in their private project and the temporary job.
    project = hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:12]
    base = Path(tempfile.gettempdir()).resolve() / 'pc-rb'
    result = base / project / templates_hash[:16]
    if not result.resolve().is_relative_to(base):
        raise ValueError('O cache de código do Remotion saiu de sua pasta.')
    return result


def identity(timeline, folder):
    """The approved edit includes actual source bytes, templates and the bundled font."""
    from . import creation_timeline as contract
    return _identity(contract.validate(timeline, folder), folder)


def _ffmpeg(arguments, check_cancel, timeout=600):
    return _run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                 '-threads', '2', *arguments], check_cancel=check_cancel, timeout=timeout)


def _input(clip, source, duration, work, tag, check_cancel, *, video=False):
    """Loop the authorized source interval, rather than the rest of its file."""
    source_in = clip.get('sourceInSeconds', 0)
    source_out = clip.get('sourceOutSeconds')
    if os.name == 'nt' and len(str(source)) > 180:
        short = work / (tag + '-source' + source.suffix)
        shutil.copyfile(source, short)
        source = short
    if clip.get('loop') and (source_in or source_out is not None):
        segment = work / (tag + ('-segment.mp4' if video else '-segment.wav'))
        length = min(duration, source_out - source_in) if source_out is not None else duration
        encoding = ['-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18'] if video else ['-vn', '-ar', '48000', '-c:a', 'pcm_s16le']
        _ffmpeg(['-ss', str(source_in), '-protocol_whitelist', 'file,pipe', '-i', source,
                 '-t', str(length), *encoding, segment], check_cancel,
                 timeout=max(120, min(1800, duration * 15)))
        source, source_in = segment, 0
    length = duration if clip.get('loop') or source_out is None else min(duration, source_out - source_in)
    return [*(['-stream_loop', '-1'] if clip.get('loop') else []), '-ss', str(source_in),
            '-protocol_whitelist', 'file,pipe', '-t', str(length), '-i', str(source)]


def _prepare(canonical, folder, cache, work, hashes, height, check_cancel):
    assets = {item['id']: item for item in canonical['assets']}
    prepared = {}
    for track in canonical['tracks']:
        if track['type'] != 'video':
            continue
        for clip in track['clips']:
            asset = assets[clip['assetId']]
            source = _local(folder, asset['file'])
            duration = clip['durationInFrames'] / canonical['format']['fps']
            key = hashlib.sha256(json.dumps([hashes[asset['id']], asset['kind'],
                clip.get('sourceInSeconds', 0), clip.get('sourceOutSeconds'), duration,
                clip.get('loop', False), height, 'proxy-v2'],
                separators=(',', ':')).encode()).hexdigest()
            target = cache / (key + ('.png' if asset['kind'] == 'image' else '.mp4'))
            if not target.is_file():
                if check_cancel:
                    check_cancel()
                short = source
                if asset['kind'] == 'image' and os.name == 'nt' and len(str(source)) > 180:
                    short = work / ('source-' + key[:12] + source.suffix)
                    shutil.copyfile(source, short)
                temporary = work / (key[:20] + target.suffix)
                # Preserve source aspect and let the visual template apply fit/focus.
                scale = f'scale=w=min(iw\\,{height * 2}):h=min(ih\\,{height * 2}):force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1'
                if asset['kind'] == 'image':
                    _ffmpeg(['-protocol_whitelist', 'file,pipe', '-i', short, '-vf', scale,
                             '-frames:v', '1', temporary], check_cancel)
                else:
                    arguments = _input(clip, source, duration, work, key[:12], check_cancel, video=True)
                    _ffmpeg([*arguments, '-t', str(duration), '-an',
                        '-vf', scale + ',fps=30,tpad=stop_mode=clone:stop_duration=0.034,format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast',
                        '-crf', '18', '-movflags', '+faststart', temporary], check_cancel,
                        timeout=max(120, min(1800, duration * 15)))
                pending = target.with_suffix(target.suffix + '.' + secrets.token_hex(6) + '.pending')
                try:
                    shutil.copyfile(temporary, pending)
                    os.replace(pending, target)
                finally:
                    pending.unlink(missing_ok=True)
            prepared[clip['id']] = target
    return prepared


def _mix(canonical, folder, work, check_cancel):
    fps = canonical['format']['fps']
    duration = canonical['format']['durationInFrames'] / fps
    assets = {item['id']: item for item in canonical['assets']}
    inputs = ['-f', 'lavfi', '-i', f'anullsrc=r=48000:cl=stereo:d={duration}']
    filters = ['[0:a]anull[bed]']
    tracks = {}
    next_input = 1
    for track in canonical['tracks']:
        if track['type'] != 'audio' or not track['clips']:
            continue
        labels = []
        for clip in track['clips']:
            source = _local(folder, assets[clip['assetId']]['file'])
            clip_duration = clip.get('audioDurationSeconds')
            if clip_duration is None:
                clip_duration = clip['durationInFrames'] / fps
            inputs += _input(clip, source, clip_duration, work, 'audio-' + str(next_input), check_cancel)
            label = f'c{next_input}'
            chain = f'[{next_input}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration={clip_duration},asetpts=PTS-STARTPTS,volume={clip.get("gainDb", 0)}dB'
            fade_in, fade_out = clip.get('fadeInFrames', 0) / fps, clip.get('fadeOutFrames', 0) / fps
            if fade_in:
                chain += f',afade=t=in:d={fade_in}'
            if fade_out:
                chain += f',afade=t=out:st={max(0, clip_duration - fade_out)}:d={fade_out}'
            # Sample delays retain the audio clock rather than truncating milliseconds.
            start = clip.get('audioStartSeconds')
            delay = round((clip['startFrame'] / fps if start is None else start) * 48000)
            filters.append(chain + f',adelay={delay}S:all=1,apad,atrim=duration={duration}[{label}]')
            labels.append(f'[{label}]')
            next_input += 1
        output = f't{len(tracks)}'
        filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=longest:normalize=0[{output}]')
        tracks[track['id']] = (output, track)
    # Split narration before ducking so each music track gets its own sidechain.
    duckers = {}
    for name, (label, track) in tracks.items():
        target = track.get('duckUnder')
        if target and target in tracks:
            duckers.setdefault(target, []).append(name)
    final = ['[bed]']
    for name, (label, track) in tracks.items():
        if name in duckers:
            copies = [label + 'main'] + [label + 'duck' + str(i) for i in range(len(duckers[name]))]
            filters.append(f'[{label}]asplit={len(copies)}' + ''.join(f'[{copy}]' for copy in copies))
            label = copies[0]
        target = track.get('duckUnder')
        if target and target in tracks:
            original = tracks[target][0]
            index = duckers[target].index(name)
            ducked = label + 'lower'
            filters.append(f'[{label}][{original}duck{index}]sidechaincompress=threshold=0.025:ratio=8:attack=15:release=250[{ducked}]')
            label = ducked
        final.append(f'[{label}]')
    filters.append(''.join(final) + f'amix=inputs={len(final)}:normalize=0:duration=longest,atrim=duration={duration},loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,alimiter=limit=0.8414:level=false:latency=true[aout]')
    output = work / 'mix.wav'
    script = work / 'audio-filter.txt'
    script.write_text(';\n'.join(filters), encoding='utf-8')
    # FFmpeg 9 removed filter_complex_script; slash options read the same graph from a file.
    version = _run(['ffmpeg', '-version'], timeout=10).splitlines()[0]
    import re
    match = re.search(r'version\s+(?:n)?(\d+)', version)
    graph_option = '-/filter_complex' if match and int(match.group(1)) >= 7 else '-filter_complex_script'
    _ffmpeg([*inputs, graph_option, script, '-map', '[aout]', '-t', str(duration),
             '-ar', '48000', '-c:a', 'pcm_s16le', output], check_cancel,
             timeout=max(120, min(1800, duration * 5)))
    return output


def _stamp(seconds):
    value = round(seconds * 1000)
    return f'{value // 3600000:02}:{value // 60000 % 60:02}:{value // 1000 % 60:02},{value % 1000:03}'


def _measure_audio(path, check_cancel):
    import math
    text = _run(['ffmpeg', '-hide_banner', '-nostdin', '-i', path, '-vn', '-af',
                 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
                check_cancel=check_cancel, timeout=600)
    try:
        data, _ = json.JSONDecoder().raw_decode(text[text.rfind('{'):])
        def finite(name):
            value = float(data[name])
            return value if math.isfinite(value) else None
        return {'integratedLufs': finite('input_i'), 'truePeakDb': finite('input_tp'),
                'loudnessRange': finite('input_lra')}
    except (KeyError, ValueError):
        raise RuntimeError('O FFmpeg não devolveu as medidas de áudio para a revisão.') from None


def _subtitles(canonical):
    fps = canonical['format']['fps']
    events = []
    for track in canonical['tracks']:
        if track['type'] == 'captions':
            for clip in track['clips']:
                text = clip.get('text') or ' '.join(word['text'] for word in clip.get('words', []))
                if text:
                    events.append((clip['startFrame'] / fps, (clip['startFrame'] + clip['durationInFrames']) / fps, text))
    return '\n'.join(f'{i}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n' for i, (start, end, text) in enumerate(sorted(events), 1))


def render(timeline, folder, output, height=960, *, check_cancel=None, on_progress=None):
    """Render the entire edit; an unavailable Remotion worker is an explicit pending step."""
    from . import creation_timeline as contract
    if check_cancel:
        check_cancel()
    canonical = contract.validate(timeline, folder, check_cancel=check_cancel)
    if type(height) is not int or height < 160 or height > 1920 or height % 32:
        raise ValueError('A altura de saída precisa ser múltipla de 32, entre 160 e 1920 pixels, para manter 9:16.')
    node = shutil.which('node')
    if not node or not (_NODE / 'worker.mjs').is_file() or not (_NODE / 'node_modules' / '@remotion' / 'renderer').is_dir():
        raise Pending('Remotion pendente: instale Node.js e execute npm ci na pasta my-video da instalação do PeakCut.')
    missing = tools.setup()
    if missing:
        raise RuntimeError(tools.HELP)
    folder, output = Path(folder).resolve(), Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = folder / '.render-cache' / 'ffmpeg-remotion'
    if not cache.resolve().is_relative_to(folder):
        raise ValueError('O cache precisa estar dentro deste projeto.')
    cache.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    timings = {}
    def progress(phase, value):
        if on_progress:
            on_progress({'phase': phase, 'progress': round(value, 4)})
    progress('prepare', 0)
    identities = _identity(canonical, folder, check_cancel)
    duration = canonical['format']['durationInFrames'] / canonical['format']['fps']
    width = round(height * canonical['format']['width'] / canonical['format']['height'] / 2) * 2
    with tempfile.TemporaryDirectory(prefix='pc-r-') as temp:
        work = Path(temp)
        prepared = _prepare(canonical, folder, cache, work, identities['assets_sha256'], height, check_cancel)
        timings['prepare'] = round(time.monotonic() - began, 3)
        visual = work / 'visual.mp4'
        t0 = time.monotonic()
        files = {**prepared, '__font': _FONT}
        with _AssetServer(files) as server:
            media_urls = {key: value for key, value in server.urls.items() if key != '__font'}
            job = {'timeline': canonical, 'media': media_urls, 'fontUrl': server.urls['__font'],
                   'output': str(visual), 'height': height,
                   'bundleDir': str(_bundle_dir(folder, identities['templates_sha256']))}
            job_path = work / 'job.json'
            job_path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
            progress('visual', .2)
            def event(item):
                if item.get('type') == 'progress':
                    value = item.get('progress', 0)
                    if isinstance(value, (float, int)) and 0 <= value <= 1:
                        progress('visual', .2 + value * .55)
            try:
                _run([node, _NODE / 'worker.mjs', job_path], check_cancel=check_cancel, on_event=event,
                     timeout=max(180, min(7200, duration * 60)), cwd=_NODE, env=_worker_env())
            except RuntimeError as error:
                if 'Install the Remotion browser' in str(error) or 'browser is not installed' in str(error):
                    raise Pending('O navegador do Remotion está pendente. Na pasta my-video, execute npm exec remotion browser ensure e retome a etapa.') from None
                raise
        if not visual.is_file():
            raise RuntimeError('O Remotion terminou sem produzir o vídeo visual.')
        timings['visual'] = round(time.monotonic() - t0, 3)
        progress('audio', .78)
        t0 = time.monotonic()
        mix = _mix(canonical, folder, work, check_cancel)
        finished = work / 'finished.mp4'
        _ffmpeg(['-i', visual, '-i', mix, '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                 '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', str(duration),
                 '-movflags', '+faststart', finished], check_cancel)
        timings['audio'] = round(time.monotonic() - t0, 3)
        progress('verify', .93)
        t0 = time.monotonic()
        inspection = json.loads(_run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                     '-of', 'json', finished], check_cancel=check_cancel, timeout=30))
        streams = inspection.get('streams', [])
        video = next((item for item in streams if item.get('codec_type') == 'video'), {})
        audio = next((item for item in streams if item.get('codec_type') == 'audio'), {})
        actual_duration = float(inspection.get('format', {}).get('duration', 0))
        if (video.get('codec_name') != 'h264' or video.get('pix_fmt') != 'yuv420p' or
            video.get('width') != width or video.get('height') != height or
            audio.get('codec_name') != 'aac' or str(audio.get('sample_rate')) != '48000' or
            abs(actual_duration - duration) > .15):
            measured = {'videoCodec': video.get('codec_name'), 'pixelFormat': video.get('pix_fmt'),
                        'width': video.get('width'), 'height': video.get('height'),
                        'audioCodec': audio.get('codec_name'), 'sampleRate': audio.get('sample_rate'),
                        'duration': actual_duration, 'expectedDuration': duration}
            raise RuntimeError('A verificação do vídeo encontrou resolução, áudio ou duração incorretos: ' + json.dumps(measured))
        _ffmpeg(['-i', finished, '-f', 'null', '-'], check_cancel,
                 timeout=max(60, min(1200, duration * 3)))
        measured_audio = _measure_audio(finished, check_cancel)
        timings['verify'] = round(time.monotonic() - t0, 3)
        versions = {'ffmpeg': _run(['ffmpeg', '-version'], timeout=10).splitlines()[0],
                    'node': _run([node, '--version'], timeout=10).strip(),
                    'remotion': json.loads((_NODE / 'node_modules' / 'remotion' / 'package.json').read_text(encoding='utf-8'))['version']}
        manifest = {'schemaVersion': '1.0', 'renderer': 'ffmpeg_remotion', **identities,
                    'font_sha256': _sha(_FONT), 'profile': {**_PROFILE, 'width': width, 'height': height},
                    'engineVersions': versions, 'timingsSeconds': timings,
                    'verification': {'duration': actual_duration, 'fullDecode': True, 'audio': True,
                                     'audioMeasurements': measured_audio},
                    'provenance': [{'assetId': asset['id'], 'sha256': identities['assets_sha256'][asset['id']]}
                                   for asset in canonical['assets']]}
        # Publish only a complete, decoded file. A cancelled replacement keeps the prior pilot.
        pending = output.with_suffix(output.suffix + '.' + secrets.token_hex(6) + '.pending')
        try:
            shutil.copyfile(finished, pending)
            if check_cancel:
                check_cancel()
            if _identity(canonical, folder, check_cancel) != identities:
                raise Pending('As mídias ou os templates mudaram durante o render. Refaça o piloto antes de aprovar.')
            os.replace(pending, output)
        finally:
            pending.unlink(missing_ok=True)
        output.with_suffix('.timeline.json').write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding='utf-8')
        output.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        output.with_suffix('.srt').write_text(_subtitles(canonical), encoding='utf-8')
    progress('complete', 1)
    return {'duration': actual_duration, 'width': width, 'height': height, 'aspect': '9:16',
            'renderer': 'ffmpeg_remotion', 'manifest': manifest}
