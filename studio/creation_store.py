"""Creation projects, preferences and private service settings belong to one user."""
import copy
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

from . import config, creators, ctx
from .creation_prompts import Pending, digest

LOCK = threading.RLock()
ID = re.compile(r'v[a-f0-9]{20}')
DOC = re.compile(r'0[1-5]_[a-z_]+\.md')
BUSY = {'queued', 'running'}


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with LOCK:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        for attempt in range(8):
            try:
                temp.replace(path)
                return
            except PermissionError:
                time.sleep(.03 * (attempt + 1))
        # Keep the complete new version rather than overwriting a file in place.
        raise Pending('O Windows está usando um arquivo do projeto. A nova versão foi preservada; aguarde e retome a etapa.')


def read(path, default=None):
    try:
        with LOCK:
            return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (OSError, ValueError):
        raise Pending('Um arquivo do projeto não pôde ser lido. Preserve-o e tente novamente.') from None


def channel_dir(cid):
    if not creators.get(cid):
        raise FileNotFoundError('Canal não encontrado nesta conta.')
    return creators.root(cid) / 'creation'


def project_dir(cid, pid):
    if not isinstance(pid, str) or not ID.fullmatch(pid):
        raise ValueError('Identificador de projeto inválido.')
    return channel_dir(cid) / 'projects' / pid


def folder(project):
    if project.get('user') != ctx.uid():
        raise FileNotFoundError('Projeto não encontrado nesta conta.')
    return project_dir(project['creator'], project['id'])


def artifact(project, name):
    base = folder(project).resolve()
    raw = Path(str(name))
    if raw.is_absolute() or '..' in raw.parts or not str(name) or ':' in str(name):
        raise ValueError('Caminho de arquivo inválido.')
    path = (base / raw).resolve()
    if not path.is_relative_to(base) or path == base:
        raise ValueError('O arquivo precisa pertencer a este projeto.')
    return path


def save(project):
    with LOCK:
        project['updated'] = time.time()
        atomic(folder(project) / 'project.json', project)
    ctx.bump()
    return project


def get(cid, pid):
    project = read(project_dir(cid, pid) / 'project.json')
    if not isinstance(project, dict) or project.get('user') != ctx.uid() or project.get('creator') != cid or project.get('id') != pid:
        raise FileNotFoundError('Projeto não encontrado nesta conta.')
    return project


def create(cid, body):
    theme = str(body.get('theme') or '').strip()
    if not theme or len(theme) > 2000:
        raise ValueError('Informe um tema com até 2.000 caracteres.')
    duration = number(body.get('duration', 60), 5, 3600, 'Duração')
    refs = body.get('references') or []
    if isinstance(refs, str):
        refs = [v.strip() for v in refs.splitlines() if v.strip()]
    if not isinstance(refs, list) or len(refs) > 80 or any(not isinstance(v, str) or len(v) > 2000 for v in refs):
        raise ValueError('Informe até 80 referências, uma por linha.')
    preferences = memory_snapshot(cid)
    project = {'id': 'v' + uuid.uuid4().hex[:20], 'creator': cid, 'user': ctx.uid(),
               'theme': theme, 'duration': duration, 'references': refs, 'aspect': '9:16',
               'platform': str(body.get('platform') or 'YouTube Shorts')[:100],
               'brief': str(body.get('brief') or '')[:10000], 'memory': preferences,
               'created': time.time(), 'stage': 1, 'status': 'ready' if preferences.get('confirmed') else 'waiting_niche',
               'message': '', 'documents': {}, 'history': [], 'feedback': [], 'artifacts': {},
               'metrics': [], 'revision': 0, 'approved_revision': None, 'allow_fewer': False}
    folder(project).mkdir(parents=True, exist_ok=False)
    return save(project)


def list_projects(cid=None):
    out = []
    for creator in ([creators.get(cid)] if cid else creators.all_creators()):
        if not creator:
            continue
        for path in channel_dir(creator['id']).glob('projects/*/project.json'):
            try:
                project = get(creator['id'], path.parent.name)
                out.append(public(project))
            except (ValueError, FileNotFoundError):
                continue
    return sorted(out, key=lambda p: p['created'], reverse=True)


def public(project):
    keys = ('id', 'creator', 'theme', 'duration', 'aspect', 'platform', 'created', 'updated', 'stage',
            'status', 'message', 'documents', 'revision', 'approved_revision', 'artifacts', 'questions',
            'source_count', 'feedback', 'metrics', 'history', 'niche_proposal')
    return {key: copy.deepcopy(project[key]) for key in keys if key in project}


def document(project, name, text):
    if not DOC.fullmatch(name) or not isinstance(text, str) or not text.strip():
        raise ValueError('O documento gerado está vazio ou tem nome inválido.')
    with LOCK:
        directory = artifact(project, f'documents/{name[:-3]}/placeholder.md').parent
        existing = [int(p.stem[1:]) for p in directory.glob('r*.md') if re.fullmatch(r'r\d+', p.stem)]
        revision = max(existing + [project.setdefault('document_versions', {}).get(name, 0)]) + 1
        relative = f'documents/{name[:-3]}/r{revision:04d}.md'
        path = artifact(project, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as output:
            output.write(text)
        item = {'path': relative, 'revision': revision, 'sha256': digest(text), 'created': time.time()}
        project['documents'][name] = item
        project['document_versions'][name] = revision
        save(project)
        return item


def read_documents(project, names):
    out = {}
    for name in names:
        item = project.get('documents', {}).get(name)
        if not item:
            raise Pending(f'Documento obrigatório ainda não disponível: {name}.')
        try:
            text = artifact(project, item['path']).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            raise Pending(f'Não foi possível ler o documento obrigatório: {name}.') from None
        if digest(text) != item['sha256']:
            raise Pending(f'O documento {name} mudou fora do fluxo. Refaça a etapa para registrar uma revisão válida.')
        out[name] = text
    return out


def memory(cid):
    return read(channel_dir(cid) / 'memory.json', {'niche': '', 'confirmed': False, 'preferences': [], 'voices': [], 'history': []})


def memory_snapshot(cid):
    value = memory(cid)
    return {k: copy.deepcopy(value.get(k)) for k in ('niche', 'confirmed', 'preferences', 'voices')}


def save_memory(cid, body):
    with LOCK:
        old = memory(cid)
        value = copy.deepcopy(old)
        if 'niche' in body:
            value['niche'] = str(body['niche']).strip()[:300]
            value['confirmed'] = bool(body.get('confirmed', False)) and bool(value['niche'])
        for key in ('preferences', 'voices'):
            if key in body:
                items = body[key]
                if not isinstance(items, list) or len(items) > 100:
                    raise ValueError('A memória aceita até 100 itens por categoria.')
                clean = []
                allowed = ('key', 'value', 'status', 'source') if key == 'preferences' else ('provider', 'voice', 'model', 'name', 'direction', 'speed')
                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError('Item de memória inválido.')
                    entry = {k: str(item[k])[:4000] for k in allowed if k in item}
                    if key == 'preferences' and entry.get('status', 'declared') not in ('declared', 'confirmed', 'inferred'):
                        raise ValueError('Situação da preferência inválida.')
                    entry.update(at=time.time(), source=entry.get('source', 'user'))
                    clean.append(entry)
                value[key] = clean
        value['updated'] = time.time()
        value['history'] = (old.get('history', []) + [{'at': time.time(), 'source': 'user',
            'previous': {k: old.get(k) for k in ('niche', 'confirmed', 'preferences', 'voices')}}])[-100:]
        atomic(channel_dir(cid) / 'memory.json', value)
    ctx.bump()
    return value


def number(value, low, high, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}: informe um número.') from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}: use um valor entre {low:g} e {high:g}.')
    return value


def _stored_settings():
    defaults = {'search': {'provider': 'serpapi', 'key': ''},
                'voice': {'provider': 'elevenlabs', 'key': '', 'model': 'eleven_multilingual_v2', 'voice': '', 'speed': 1.0},
                'review': {'key': '', 'model': ''}, 'max_context_chars': 240000}
    saved = read(config.user_dir(ctx.uid()) / 'creation-settings.json', {})
    for section in ('search', 'voice', 'review'):
        defaults[section].update(saved.get(section) or {})
    defaults['max_context_chars'] = saved.get('max_context_chars', defaults['max_context_chars'])
    return defaults


def settings(*, public=False):
    defaults = _stored_settings()
    if not config.ACCOUNTS:
        for section, variable in [('search', 'PEAKCUT_SEARCH_KEY'), ('review', 'PEAKCUT_REVIEW_KEY')]:
            defaults[section]['key'] = defaults[section].get('key') or config._secret(variable, variable + '_FILE')
        variable = 'PEAKCUT_CREATION_OPENAI_KEY' if defaults['voice']['provider'] == 'openai' else 'PEAKCUT_ELEVENLABS_KEY'
        defaults['voice']['key'] = defaults['voice'].get('key') or config._secret(variable, variable + '_FILE')
    if public:
        for section in ('search', 'voice', 'review'):
            defaults[section]['has_key'] = bool(defaults[section].pop('key', ''))
    return defaults


def save_settings(body):
    with LOCK:
        value = _stored_settings()
        voice_change = body.get('voice', {})
        if isinstance(voice_change, dict) and voice_change.get('provider', value['voice']['provider']) != value['voice']['provider']:
            # A key for one company must never be sent to a different provider.
            value['voice']['key'] = ''
        allowed = {'search': ('key',), 'voice': ('provider', 'key', 'model', 'voice', 'speed', 'direction', 'stability', 'similarity_boost', 'style'),
                   'review': ('key', 'model')}
        for section, fields in allowed.items():
            if section not in body:
                continue
            if not isinstance(body[section], dict):
                raise ValueError('Configuração de serviço inválida.')
            for field in fields:
                if field in body[section]:
                    v = body[section][field]
                    if field in ('speed', 'stability', 'similarity_boost', 'style'):
                        v = number(v, 0.25 if field == 'speed' else 0, 4 if field == 'speed' else 1, field)
                    else:
                        v = str(v).strip()
                        if len(v) > (4000 if field in ('key', 'direction') else 160):
                            raise ValueError('Configuração longa demais.')
                        if field in ('model', 'voice') and v and not re.fullmatch(r'[\w.\-:/]+', v):
                            raise ValueError('Identificador de modelo ou voz inválido.')
                    value[section][field] = v
        if value['voice']['provider'] not in ('openai', 'elevenlabs'):
            raise ValueError('Escolha OpenAI ou ElevenLabs para a voz.')
        if 'max_context_chars' in body:
            value['max_context_chars'] = int(number(body['max_context_chars'], 20000, 2000000, 'Contexto'))
        atomic(config.user_dir(ctx.uid()) / 'creation-settings.json', value)
    ctx.bump()
    return settings(public=True)
