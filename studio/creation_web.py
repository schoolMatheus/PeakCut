"""Public research and media access, separate from the existing clip URL path."""
import hashlib
from html.parser import HTMLParser
import ipaddress
import re
import socket
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from . import media
from .creation_prompts import Pending

MAX_PAGE = 4_000_000
MAX_MEDIA = 300_000_000


def canonical(url):
    parsed = urlsplit(str(url).strip())
    host = (parsed.hostname or '').lower()
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith('utm_') and k not in ('fbclid', 'gclid')]
    if host in ('youtu.be', 'www.youtu.be'):
        return 'https://www.youtube.com/watch?' + urlencode({'v': parsed.path.strip('/')})
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and parsed.path == '/watch':
        return 'https://www.youtube.com/watch?' + urlencode({'v': dict(query).get('v', '')})
    return urlunsplit((parsed.scheme.lower(), host + (f':{parsed.port}' if parsed.port else ''), parsed.path or '/', urlencode(query), ''))


def resolve_public(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in (80, 443):
            raise ValueError
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(row[4][0] for row in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError
        return parsed, ips[0]
    except (ValueError, OSError):
        raise ValueError('Use uma URL HTTP/HTTPS pública, sem credenciais ou portas alternativas.') from None


def fetch(url, *, limit=MAX_PAGE, method='GET', headers=None, body=None, allowed_hosts=None, response_info=None, deadline=None, output=None):
    """Validate every redirect and pin DNS to the validated public address."""
    current = str(url)
    with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            parsed, ip = resolve_public(current)
            if allowed_hosts and not any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in allowed_hosts):
                raise ValueError('O serviço tentou acessar um destino fora dos domínios permitidos.')
            authority = f'[{ip}]' if ':' in ip else ip
            target = urlunsplit((parsed.scheme, authority + (f':{parsed.port}' if parsed.port else ''), parsed.path or '/', parsed.query, ''))
            request_headers = {**(headers or {}), 'Host': parsed.netloc}
            request_headers.setdefault('User-Agent', 'PeakCut/1.0 (public research)')
            with client.stream(method, target, headers=request_headers, content=body,
                               extensions={'sni_hostname': parsed.hostname}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('A página redirecionou sem informar o destino.')
                    next_url = urljoin(current, location)
                    if urlsplit(next_url).hostname != parsed.hostname:
                        headers = {k: v for k, v in (headers or {}).items() if k.lower() not in ('authorization', 'cookie')}
                    if response.status_code == 303 or response.status_code in (301, 302) and method == 'POST':
                        method, body = 'GET', None
                    current = next_url
                    continue
                if response.status_code not in (200, 206):
                    raise ValueError(f'A fonte respondeu HTTP {response.status_code}; conteúdo não consultado.')
                try:
                    length = int(response.headers.get('content-length', '0'))
                except ValueError:
                    length = 0
                if length > limit:
                    raise ValueError('O arquivo excede o tamanho máximo desta operação.')
                chunks, total = [], 0
                stream = Path(output).open('wb') if output else None
                try:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > limit or deadline and time.monotonic() > deadline:
                            raise ValueError('A transferência excedeu o limite de tamanho ou tempo.')
                        if stream:
                            stream.write(chunk)
                        else:
                            chunks.append(chunk)
                finally:
                    if stream:
                        stream.close()
                if response_info is not None:
                    response_info.update(status=response.status_code, headers=dict(response.headers), bytes=total)
                return current, response.headers.get('content-type', '').split(';')[0], b''.join(chunks)
    raise ValueError('A fonte fez redirecionamentos demais.')


def youtube_video(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if host in ('youtu.be', 'www.youtu.be'):
        ident = parsed.path.strip('/')
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            ident = dict(parse_qsl(parsed.query)).get('v', '')
        else:
            match = re.fullmatch(r'/(?:shorts|embed)/([\w-]{11})/?', parsed.path)
            ident = match[1] if match else ''
    else:
        ident = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', ident) or parsed.username or parsed.password or parsed.port not in (None, 443, 80):
        raise ValueError('Use um link canônico de vídeo YouTube, sem redirecionadores.')
    return 'https://www.youtube.com/watch?v=' + ident


def youtube_session(options=None, *, byte_limit=MAX_MEDIA):
    """All extractor requests use the pinned public transport, including redirects."""
    import io
    import threading
    import urllib.request
    from yt_dlp.networking.common import RequestHandler, Response
    from yt_dlp.networking.exceptions import RequestError
    allowed = ('youtube.com', 'youtu.be', 'googlevideo.com', 'ytimg.com', 'googleusercontent.com', 'youtubei.googleapis.com', 'jnn-pa.googleapis.com')

    class CreationRH(RequestHandler):
        _SUPPORTED_URL_SCHEMES = ('http', 'https')
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.remaining, self.deadline, self.lock = byte_limit, time.monotonic()+600, threading.Lock()

        def _validate(self, request):
            if request.method not in ('GET', 'POST', 'HEAD'):
                raise RequestError('Método de rede não permitido nesta consulta.')

        def _send(self, request):
            with self.lock:
                if self.remaining <= 0 or time.monotonic() > self.deadline:
                    raise RequestError('A consulta YouTube excedeu o limite de tamanho ou tempo.')
                headers = self._get_headers(request)
                cookie_request = urllib.request.Request(request.url, headers=headers)
                self._get_cookiejar(request).add_cookie_header(cookie_request)
                headers.update(dict(cookie_request.header_items()))
                info = {}
                try:
                    final, _, data = fetch(request.url, method=request.method, headers=headers, body=request.data,
                        limit=self.remaining, allowed_hosts=allowed, response_info=info, deadline=self.deadline)
                except (ValueError, httpx.HTTPError) as error:
                    raise RequestError('A transferência YouTube foi recusada ou excedeu os limites.') from error
                self.remaining -= len(data)
                response_headers = {k: v for k, v in info['headers'].items() if k.lower() not in ('content-encoding', 'transfer-encoding', 'content-length')}
                response_headers['Content-Length'] = str(len(data))
                return Response(io.BytesIO(data), url=final, headers=response_headers, status=info['status'])

    options = options or {}
    ydl = media._ydl({**options, 'allowed_extractors': ['youtube', 'youtube:tab', 'youtube:search', 'youtube:search_url'],
                      'max_filesize': byte_limit, 'http_chunk_size': 5*1024*1024, 'retries': 1,
                      'fragment_retries': 1, 'external_downloader': None, 'hls_prefer_native': True})
    director = ydl._request_director
    director.close()
    director.preferences.clear()
    director.add_handler(CreationRH(logger=director.logger, cookiejar=ydl.cookiejar, headers=ydl.params.get('http_headers')))
    return ydl


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.title_on = False
        self.title = []
        self.parts = []
        self.images = []
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        if tag == 'title':
            self.title_on = True
        if tag == 'meta':
            self.metadata[attrs.get('property') or attrs.get('name') or ''] = attrs.get('content', '')
        if tag == 'img' and attrs.get('src'):
            self.images.append({'url': attrs['src'], 'description': attrs.get('alt', '')})

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip = max(0, self.skip - 1)
        if tag == 'title':
            self.title_on = False

    def handle_data(self, value):
        if self.skip:
            return
        if self.title_on:
            self.title.append(value)
        elif value.strip():
            self.parts.append(value.strip())


def page_from_bytes(url, content_type, data):
    if content_type in ('text/html', 'application/xhtml+xml'):
        page = _Page()
        page.feed(data.decode('utf-8', errors='replace'))
        text = '\n'.join(page.parts)
        return {'url': url, 'title': ''.join(page.title).strip(), 'text': text,
                'author': page.metadata.get('author', ''), 'published': page.metadata.get('article:published_time', ''),
                'access': 'page_text', 'images': [{'url': urljoin(url, v['url']), 'description': v['description'],
                     'source': url, 'usage': 'unverified'} for v in page.images[:30]],
                'content_hash': hashlib.sha256(text.encode()).hexdigest()}
    if content_type.startswith('text/'):
        text = data.decode('utf-8', errors='replace')
        return {'url': url, 'title': urlsplit(url).path, 'text': text, 'access': 'page_text', 'images': [],
                'content_hash': hashlib.sha256(text.encode()).hexdigest()}
    raise ValueError('Esta fonte não ofereceu texto legível. Ela não conta como referência consultada.')


def read_source(url):
    resolve_public(url)
    if media.is_youtube(url):
        url = youtube_video(url)
        with youtube_session(byte_limit=20_000_000) as ydl:
            data = ydl.extract_info(url, download=False)
            captions = {**(data.get('automatic_captions') or {}), **(data.get('subtitles') or {})}
            language = str(data.get('language') or '')
            keys = [language, language+'-orig'] + list(captions)
            track = next((v for key in keys for v in captions.get(key, []) if v.get('ext') == 'json3' and v.get('url')), None)
            transcript = []
            if track:
                import json
                transcript = media._caption_words(json.loads(ydl.urlopen(track['url']).read(MAX_PAGE)))
        if data.get('_type') in ('playlist', 'multi_video'):
            raise ValueError('Use um vídeo individual como referência.')
        lines = []
        if transcript:
            lines = [f"[{w['s']:.1f}] {w['w']}" for w in transcript]
        text = (data.get('description') or '') + '\n' + '\n'.join(lines)
        if len(text.strip()) < 100:
            raise ValueError('O vídeo não disponibilizou descrição ou transcrição suficiente para consulta.')
        return {'url': canonical(url), 'title': data.get('title') or '', 'author': data.get('uploader') or '',
                'published': data.get('upload_date') or '', 'text': text, 'images': [],
                'access': 'transcript_and_description' if transcript else 'description_only',
                'license': data.get('license') or 'unverified', 'content_hash': hashlib.sha256(text.encode()).hexdigest()}
    final, kind, data = fetch(url)
    result = page_from_bytes(final, kind, data)
    if len(result['text'].strip()) < 100:
        raise ValueError('A página não trouxe conteúdo suficiente para consulta.')
    return result


def search_google(query, settings, *, images=False):
    if not settings.get('key'):
        raise Pending('Configure a chave de pesquisa Google (SerpAPI) na aba Criar vídeo.')
    try:
        response = httpx.get('https://serpapi.com/search', params={
            'engine': 'google_images' if images else 'google', 'q': query, 'api_key': settings['key'],
            'hl': 'pt', 'num': 10}, timeout=35, follow_redirects=False)
        if response.status_code != 200:
            raise Pending(f'A pesquisa Google respondeu HTTP {response.status_code}. Confira a configuração do serviço.')
        data = response.json()
    except httpx.HTTPError:
        raise Pending('Não foi possível conectar ao serviço de pesquisa Google.') from None
    if data.get('error'):
        raise Pending('O serviço de pesquisa recusou a consulta. Confira sua chave e a disponibilidade da conta.')
    if images:
        return [{'url': v.get('original'), 'source': v.get('link') or v.get('source'),
                 'description': v.get('title') or '', 'usage': 'unverified', 'kind': 'image'}
                for v in data.get('images_results', []) if v.get('original')]
    return [{'url': v['link'], 'title': v.get('title', ''), 'snippet': v.get('snippet', '')}
            for v in data.get('organic_results', []) if v.get('link')]


def search_youtube(query, limit=6):
    with youtube_session({'playlistend': limit, 'extract_flat': True}, byte_limit=20_000_000) as ydl:
        data = ydl.extract_info(f'ytsearch{limit}:{query}', download=False)
    return [{'url': f"https://www.youtube.com/watch?v={v['id']}", 'title': v.get('title') or ''}
            for v in data.get('entries') or [] if v and re.fullmatch(r'[\w-]{11}', str(v.get('id', '')))]


def search_commons(query, *, kind='image'):
    """An additional public archive with original media URLs and license metadata."""
    params = {'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': 6,
              'gsrsearch': ('filetype:audio ' if kind == 'audio' else '') + query,
              'gsrlimit': 10, 'prop': 'imageinfo', 'iiprop': 'url|extmetadata|mediatype'}
    _, _, content = fetch('https://commons.wikimedia.org/w/api.php?' + urlencode(params))
    import json
    result = json.loads(content)
    out = []
    for page in result.get('query', {}).get('pages', {}).values():
        info = (page.get('imageinfo') or [{}])[0]
        media_type = info.get('mediatype', '')
        if not info.get('url') or (kind == 'audio' and media_type != 'AUDIO'):
            continue
        if kind != 'audio' and media_type not in ('BITMAP', 'DRAWING', 'VIDEO'):
            continue
        metadata = info.get('extmetadata') or {}
        def value(key):
            return re.sub('<[^>]*>', '', str(metadata.get(key, {}).get('value', '')))
        out.append({'url': info['url'], 'source': info.get('descriptionurl', ''),
                    'description': value('ImageDescription') or page.get('title', ''),
                    'author': value('Artist'), 'usage': value('LicenseShortName') or 'unverified',
                    'license_url': value('LicenseUrl'), 'attribution': value('Attribution'),
                    'kind': 'audio' if media_type == 'AUDIO' else 'video' if media_type == 'VIDEO' else 'image'})
    return out


def channel_evidence(url):
    resolve_public(url)
    if media.is_youtube(url):
        parsed = urlsplit(url)
        path = re.sub(r'/(videos|shorts|streams|featured)/?$', '', parsed.path).rstrip('/')
        if not re.fullmatch(r'/(?:@[^/?#]+|channel/[A-Za-z0-9_-]+|c/[^/?#]+|user/[^/?#]+)', path):
            raise ValueError('Use o endereço de um canal YouTube.')
        url = 'https://www.youtube.com' + path
        with youtube_session({'playlistend': 20, 'extract_flat': True}, byte_limit=20_000_000) as ydl:
            data = ydl.extract_info(url + '/videos', download=False)
        entries = [v for v in data.get('entries') or [] if v]
        return {'status': 'available' if entries else 'empty', 'title': data.get('title') or '',
                'description': data.get('description') or '', 'url': url,
                'videos': [{'title': v.get('title') or '', 'description': v.get('description') or ''} for v in entries[:20]]}
    return read_source(url)


def download_asset(url, output, *, kind='image'):
    output.parent.mkdir(parents=True, exist_ok=True)
    if media.is_youtube(url):
        url = youtube_video(url)
        with youtube_session({'format': media._video_format(1080), 'merge_output_format': 'mp4',
                              'outtmpl': str(output.parent / 'source.%(ext)s')}) as ydl:
            ydl.download([url])
        return media._biggest(output.parent, 'source')
    pending = output.with_suffix('.part')
    try:
        final, content_type, data = fetch(url, limit=MAX_MEDIA, output=pending)
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    if not content_type.startswith(('image/', 'video/', 'audio/')):
        pending.unlink(missing_ok=True)
        raise ValueError('O link selecionado não entregou uma imagem, vídeo ou áudio.')
    pending.replace(output)
    return output
