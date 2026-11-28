"""The creation workflow. Models propose edits; validated code carries them out."""
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid

from . import ai, config, creators, ctx, jobs, media, usage
from . import creation_prompts as instructions, creation_store as store
from . import creation_web as web, creation_render as movie

R = '01_pesquisa_referencias.md'
S = '02_roteiro_audiovisual.md'
N = '03_direcao_narracao.md'
I = '03_roteiro_integrado.md'
P = '04_revisao_piloto.md'
F = '05_entrega_final.md'
INPUTS = {0: [], 1: [], 2: [R], 3: [R, S], 4: [R, S, N, I], 5: [R, S, N, I, P]}
_active = {}


class Cancelled(Exception):
    pass


class ResearchDecision(store.Pending):
    pass


def obj(properties, required=None):
    return {'type': 'OBJECT', 'properties': properties, 'required': list(properties) if required is None else required}


STR, NUM, BOOL = {'type': 'STRING'}, {'type': 'NUMBER'}, {'type': 'BOOLEAN'}


def arr(items=STR):
    return {'type': 'ARRAY', 'items': items}


SCENE = obj({'id': STR, 'description': STR, 'visual_query': STR, 'media_id': STR, 'text': STR,
             'narration_plan': STR, 'references': arr(), 'motion': STR, 'transition': STR,
             'fit': STR, 'duration': NUM, 'start': NUM, 'focus_x': NUM, 'focus_y': NUM, 'mood': STR, 'sound_direction': STR,
             'template_id': STR, 'transition_duration': NUM, 'original_volume': NUM, 'text_x': NUM, 'text_y': NUM,
             'overlays': arr(obj({'text': STR, 'template_id': STR, 'at': NUM, 'duration': NUM, 'x': NUM, 'y': NUM}))},
            ['id', 'description', 'visual_query', 'media_id', 'text', 'narration_plan', 'references', 'motion', 'transition', 'fit', 'duration'])
TRACK = obj({'media_id': STR, 'query': STR, 'kind': STR, 'at': NUM, 'volume': NUM, 'duration': NUM, 'loop': BOOL})
SCRIPT = obj({'document': STR, 'scenes': arr(SCENE), 'tracks': arr(TRACK)})
LINES = obj({'document': STR, 'lines': arr(obj({'id': STR, 'scene_id': STR, 'text': STR, 'direction': STR, 'pause_after': NUM}))})


def key(project):
    return ctx.uid(), project['creator'], project['id']


def check(project):
    event = _active.get(key(project))
    if event and event.is_set():
        raise Cancelled()


def announce(project, message):
    check(project)
    project['message'] = message
    store.save(project)


def snapshot(project, stage, operation, extra=None):
    check(project)
    if stage >= 2:
        require_research(project)
    docs = store.read_documents(project, INPUTS[stage])
    # A partial research/review and narration directions also travel on retries.
    for name in ([R] if stage == 1 else [N, I] if stage == 3 else [P] if stage == 4 else []):
        if name in project['documents'] and name not in docs:
            docs.update(store.read_documents(project, [name]))
    context = {'theme': project['theme'], 'duration': project['duration'], 'aspect': '9:16',
               'platform': project['platform'], 'brief': project.get('brief', ''),
               'preferences': project['memory'], 'feedback': project.get('feedback', []),
               **(extra or {}), 'editing_capabilities': movie.capabilities()}
    prompt, meta = instructions.compose(stage, operation, context, docs,
                           max_chars=store.settings()['max_context_chars'])
    meta.update(created=time.time(), input_revisions=copy.deepcopy(project['documents']))
    path = store.folder(project) / 'runs' / (str(time.time_ns()) + '-' + uuid.uuid4().hex[:8] + '.json')
    store.atomic(path, meta)
    return prompt, meta, path


def require_research(project):
    sources = project.get('sources', [])
    count = len({web.canonical(v['url']) for v in sources})
    decision = project.get('reference_decision') or {}
    current = project.get('documents', {}).get(R, {})
    seed_urls = {web.canonical(v) for v in project.get('references', []) if v.startswith(('http://', 'https://'))}
    if sources and not any(web.canonical(v['url']) not in seed_urls for v in sources):
        raise store.Pending('É necessário consultar fontes além das referências fornecidas antes de escrever o roteiro.')
    if count < 20 and not (decision.get('approved') is True and decision.get('sha256') == current.get('sha256') and decision.get('count') == count):
        raise ResearchDecision('A pesquisa tem menos de 20 referências. Autorize avançar com a versão atual ou aprofunde a pesquisa.')


def ask(project, stage, operation, schema, extra=None):
    if not ai.ready() or not ai.models():
        raise store.Pending('Configure a IA de texto nas configurações do PeakCut para executar esta etapa.')
    # Rebuild context for every attempt; never retry a stale instruction string.
    last = None
    for attempt in range(2):
        prompt, meta, path = snapshot(project, stage, operation, extra)
        began, cpu = time.perf_counter(), time.process_time()
        try:
            def fresh_prompt():
                current_prompt, sent_meta, sent_path = snapshot(project, stage, operation, extra)
                sent_meta.update(model=ai.models()[0], kind='provider_send')
                store.atomic(sent_path, sent_meta)
                return current_prompt
            value, incoming, outgoing = ai.call(ai.models()[0], prompt, schema, timeout=240, prompt_factory=fresh_prompt)
            usage.set_context(project['creator'], project['theme'])
            usage.record(incoming, outgoing, f'creation_{stage}')
            if not isinstance(value, dict):
                raise ValueError('A IA não retornou um objeto de resultado.')
            meta.update(model=ai.models()[0], tokens_in=incoming, tokens_out=outgoing, ok=True)
            return value
        except ai.AIError as error:
            last = error
            meta['ok'] = False
            if getattr(error, 'wait', None) or getattr(error, 'daily', False):
                break
        finally:
            elapsed = time.perf_counter() - began
            project['metrics'].append({'stage': stage, 'operation': operation[:100], 'seconds': round(elapsed, 3),
                                       'cpu_seconds': round(time.process_time() - cpu, 3), 'characters': len(prompt)})
            store.atomic(path, meta)
            store.save(project)
    raise last or store.Pending('A IA não respondeu. Tente retomar a etapa.')


def _json_doc(text, data):
    return text.strip() + '\n\n## Dados estruturados para continuidade\n\n```json\n' + json.dumps(data, ensure_ascii=False, indent=2) + '\n```\n'


def confirm_niche(cid, niche):
    if not str(niche).strip():
        raise ValueError('Informe o nicho correto ou confirme a sugestão.')
    return store.save_memory(cid, {'niche': niche, 'confirmed': True})


def infer_niche(project):
    creator = creators.get(project['creator'])
    url = creator.get('channel') or ''
    evidence = {'status': 'no_channel'}
    if url:
        try:
            evidence = web.channel_evidence(url)
        except Exception:
            evidence = {'status': 'unavailable', 'message': 'Não foi possível consultar o canal; isso não significa que esteja vazio.'}
    if evidence.get('status') in ('no_channel', 'unavailable', 'empty'):
        snapshot(project, 0, 'Identificar disponibilidade do canal', {'evidence': evidence})
        project['niche_proposal'] = {'niche': '', 'evidence': evidence}
    else:
        result = ask(project, 0, 'Inferir o nicho com base apenas nas evidências. Não confirmar pelo usuário.',
                     obj({'niche': STR, 'reason': STR, 'subniches': arr()}), {'channel_evidence': evidence})
        project['niche_proposal'] = {**result, 'evidence': evidence}
    project.update(status='waiting_niche', message='Confirme o nicho sugerido ou informe o nicho do canal.')
    store.save(project)


def _register_media(project, candidates):
    catalog = project.setdefault('media_catalog', [])
    known = {v['url'] for v in catalog}
    for item in candidates:
        if not item.get('url') or item['url'] in known:
            continue
        url = item['url']
        catalog.append({**item, 'id': f'MID-{len(catalog)+1:03d}', 'url': url,
                        'usage': item.get('usage') or 'unverified'})
        known.add(url)
    return catalog


def research_candidates(seeds, found):
    from itertools import zip_longest
    seed_urls = {web.canonical(v['url']) for v in seeds}
    external = [v for v in found if web.canonical(v['url']) not in seed_urls]
    output, seen = [], set()
    for pair in zip_longest(external, seeds):
        for item in pair:
            if item is None:
                continue
            identity = web.canonical(item['url'])
            if identity not in seen:
                seen.add(identity)
                output.append(item)
    return output


def research(project):
    announce(project, 'Preparando consultas e relendo as instruções de pesquisa.')
    plan = ask(project, 1, 'Planejar 5 a 8 consultas complementares para Google e YouTube, incluindo fontes primárias, '
               'perspectivas distintas e acervos. Referências fornecidas não limitam a pesquisa. '
               'Inclua duas buscas de mídia e uma busca de trilha instrumental.',
               obj({'queries': arr(), 'media_queries': arr()}), {'user_references': project['references'],
               'already_consulted': [v['url'] for v in project.get('sources', [])], 'round': project.get('research_round', 0)})
    queries = [q for q in plan.get('queries', []) if isinstance(q, str) and q.strip()][:8]
    if len(queries) < 3:
        raise store.Pending('A IA não propôs consultas complementares suficientes. Retome a pesquisa.')
    seeds = [{'url': value} for value in project['references'] if value.startswith(('http://', 'https://'))]
    found = []
    for query in queries:
        announce(project, 'Pesquisando Google: ' + query[:140])
        found.extend(web.search_google(query, store.settings()['search']))
    try:
        found.extend(web.search_youtube(queries[0]))
    except Exception:
        project.setdefault('research_notes', []).append('A busca YouTube não respondeu nesta execução. Outras fontes foram pesquisadas.')
    for query in plan.get('media_queries', [])[:3]:
        if isinstance(query, str):
            _register_media(project, web.search_google(query, store.settings()['search'], images=True))
    sources = project.setdefault('sources', [])
    candidates = research_candidates(seeds, found)
    seed_urls = {web.canonical(v['url']) for v in seeds}
    known = {web.canonical(v['url']) for v in sources}
    hashes = {v.get('content_hash') for v in sources}
    attempted = set()
    for candidate in candidates[:100]:
        check(project)
        try:
            url = web.canonical(candidate['url'])
            if url in known or url in attempted:
                continue
            attempted.add(url)
            announce(project, f'Consultando fontes: {len(sources)} referências válidas até agora.')
            source = web.read_source(url)
            if source['content_hash'] in hashes:
                continue
            original = source.pop('text')
            raw_name = f"sources/{source['content_hash']}.txt"
            raw_path = store.artifact(project, raw_name)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(original, encoding='utf-8')
            source.update(consulted=time.time(), raw_path=raw_name, available_characters=len(original),
                          read_characters=min(len(original), 24000), origin='user' if url in seed_urls else 'discovered')
            if len(original) > 24000:
                source['access'] = 'partial_' + source['access']
            result = ask(project, 1, 'Resumir somente o conteúdo de fato recebido desta fonte. Avaliar relevância, '
                        'registrar fatos aproveitáveis e limitações, sem inventar o restante da página ou do vídeo.',
                        obj({'relevant': BOOL, 'summary': STR, 'facts': arr(), 'limitations': STR}),
                        {'source': source, 'consulted_content': original[:24000]})
            if result.get('relevant') is not True or not isinstance(result.get('summary'), str) or not result['summary'].strip() or not result.get('facts'):
                continue
            if not isinstance(result.get('facts'), list) or any(not isinstance(v, str) for v in result['facts']):
                continue
            source.update({k: result[k] for k in ('relevant', 'summary', 'facts', 'limitations') if k in result})
            source['id'] = f'REF-{len(sources)+1:03d}'
            sources.append(source)
            known.add(url)
            hashes.add(source['content_hash'])
            _register_media(project, source.get('images', []))
            if media.is_youtube(url):
                _register_media(project, [{'url': url, 'source': url, 'description': source['title'],
                                          'kind': 'video', 'usage': source.get('license', 'unverified')}])
            project['source_count'] = len(sources)
            store.save(project)
        except (store.Pending, Cancelled, ai.AIError):
            raise
        except Exception:
            project.setdefault('research_notes', []).append('Fonte não consultada: ' + str(candidate.get('url', ''))[:300])
        if len(sources) >= 24 and any(web.canonical(v['url']) not in seed_urls for v in sources):
            break
    if not sources:
        raise store.Pending('Nenhuma fonte pôde ser consultada. Confira o serviço de pesquisa e aprofunde a busca.')
    synthesized = ask(project, 1, 'Produzir a síntese documental completa usando somente as fontes consultadas abaixo. '
                      'Citar IDs REF, destacar divergências e lacunas. Não acrescentar referências nem dados externos.',
                      obj({'document': STR}), {'consulted_sources': sources, 'media_catalog': project.get('media_catalog', []),
                                               'notes': project.get('research_notes', [])})
    ledger = '\n\n'.join(f"### {v['id']} — {v['title']}\nURL: {v['url']}\nAutor: {v.get('author','')}\n"
                          f"Acesso: {v['access']}; caracteres consultados: {v['read_characters']}/{v['available_characters']}\n"
                          f"Resumo: {v['summary']}\nFatos: " + '\n'.join('- ' + str(f) for f in v['facts']) +
                          f"\nLimitações: {v.get('limitations','')}" for v in sources)
    briefing = {'theme': project['theme'], 'duration': project['duration'], 'aspect': '9:16',
                'platform': project['platform'], 'brief': project.get('brief', ''), 'preferences': project['memory']}
    doc = f"# Pesquisa — {project['theme']}\n\nFormato: 9:16\nReferências válidas: {len(sources)}\n\n" + synthesized['document'] + '\n\n## Registro das fontes consultadas\n\n' + ledger
    store.document(project, R, _json_doc(doc, {'briefing': briefing, 'references': sources, 'media': project.get('media_catalog', []),
                                            'allow_fewer': project.get('allow_fewer', False)}))
    project['source_count'] = len(sources)
    if len(sources) < 20:
        project.update(status='waiting_references', message=f'Foram consultadas {len(sources)} referências válidas. Avançar com elas ou aprofundar a pesquisa?')
    elif not any(web.canonical(v['url']) not in seed_urls for v in sources):
        project.update(status='pending', message='A pesquisa ainda não conseguiu consultar fontes além das referências fornecidas. Retome para ampliar a busca.')
    else:
        project.update(stage=2, status='running')
    store.save(project)


def script(project):
    announce(project, 'Montando o roteiro audiovisual vertical a partir da pesquisa documentada.')
    result = ask(project, 2, 'Escrever roteiro completo, documento detalhado e cenas executáveis. '
                 'Usar exclusivamente a pesquisa como base factual. Cada cena exige referências existentes. '
                 'Seguir editing_capabilities: motion none, zoom_in, zoom_out, pan_left, pan_right, ken_burns_v1; '
                 'transition cut, fade, crossfade ou slide_left; fit cover ou contain. '
                 'template_id title_pop_v1, lower_third_v1, cta_v1 ou comparison_v1. '
                 'transition_duration em segundos (até 2); original_volume de 0 a 1, mudo por padrão. '
                 'overlays opcionais: textos com template_id, at/duration em segundos relativos à cena e x/y normalizados. '
                 'Não solicitar comandos de shell. media_id deve existir no catálogo ou ser vazio com visual_query específico. '
                 'Tracks descrevem trilha/efeitos reais necessários: media_id disponível ou query para localizar, '
                 'kind music ou effect, at/duration em segundos, volume entre 0 e 1 e loop booleano.', SCRIPT)
    scenes = result.get('scenes')
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 300:
        raise store.Pending('O roteiro não trouxe cenas válidas. Retome a etapa.')
    refs = {v['id'] for v in project['sources']}
    for index, scene in enumerate(scenes, 1):
        scene['id'] = f'CENA-{index:03d}'
        if not scene.get('references') or not set(scene['references']).issubset(refs):
            raise store.Pending('O roteiro citou uma fonte ausente da pesquisa. Refaça a etapa de roteiro.')
        if scene.get('motion') not in movie.MOTIONS or scene.get('transition') not in movie.TRANSITIONS or scene.get('fit') not in ('cover', 'contain'):
            raise store.Pending('O roteiro solicitou uma edição não suportada. Retome para reformular o plano.')
        scene['duration'] = store.number(scene.get('duration'), .1, 600, 'Duração da cena')
        scene['start'] = store.number(scene.get('start', 0), 0, 86400, 'Início da mídia')
        scene['focus_x'] = store.number(scene.get('focus_x', .5), 0, 1, 'Foco horizontal')
        scene['focus_y'] = store.number(scene.get('focus_y', .5), 0, 1, 'Foco vertical')
    project['script'] = result
    project['script_fingerprint'] = instructions.digest(json.dumps(result, ensure_ascii=False, sort_keys=True))
    store.document(project, S, _json_doc('# Roteiro audiovisual\n\nFormato: 9:16; final: 1080×1920, 30 fps. Tempos estimados.\n\n' + result['document'],
                                      {'aspect': '9:16', 'scenes': scenes, 'tracks': result.get('tracks', [])}))
    project.update(stage=3, status='running')
    store.save(project)


def _file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def review_media(project, stage, path, expected):
    settings = store.settings()['review']
    if not settings.get('key') or not settings.get('model'):
        raise store.Pending('Configure a chave e um modelo Gemini para ouvir e analisar a mídia antes de avançar.')
    duration = movie.audio_duration(path)
    video = Path(path).suffix.lower() == '.mp4'
    reports = []
    offset = 0.0
    source_hash = _file_hash(path)
    while offset < duration - .01:
        check(project)
        length = min(25, duration - offset)
        operation = ('Analisar imagem e áudio do vídeo' if video else 'Ouvir a narração') + f' de {offset:.3f}s a {offset+length:.3f}s. '
        operation += 'Examinar o conteúdo real inteiro desta parte. Retornar JSON {ok:boolean,issues:[string],summary:string}. '
        operation += 'Relatar falhas concretas de fala, pronúncia, sincronização, enquadramento, legibilidade, conteúdo ou mixagem. '
        operation += 'Não inventar inspeção humana. Avaliar os requisitos do trecho, sem exigir que o vídeo inteiro caiba nesta parte.'
        prompt, meta, audit = snapshot(project, stage, operation, {'expected': expected, 'interval': [offset, offset+length]})
        fingerprint = instructions.digest(source_hash + instructions.digest(prompt) + settings['model'])
        receipt = store.folder(project) / 'reviews' / (fingerprint + '.json')
        report = store.read(receipt)
        if not report:
            part = store.folder(project) / 'reviews' / (fingerprint + ('.mp4' if video else '.mp3'))
            part.parent.mkdir(parents=True, exist_ok=True)
            command = ['ffmpeg', '-y', '-loglevel', 'error', '-ss', str(offset), '-i', str(path), '-t', str(length)]
            command += ['-vf', 'scale=-2:640', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-c:a', 'aac'] if video else ['-vn', '-c:a', 'libmp3lame']
            media.run(command + [str(part)], timeout=120)
            report = ai.creation_review(settings, part, prompt)
            usage.set_context(project['creator'], project['theme'])
            usage.record(report.pop('tokens_in', 0), report.pop('tokens_out', 0), 'creation_review')
            report.update(start=offset, end=offset+length, model=settings['model'], source_hash=source_hash)
            store.atomic(receipt, report)
            part.unlink(missing_ok=True)
        meta.update(report=report)
        store.atomic(audit, meta)
        reports.append(report)
        offset += length
    return {'ok': bool(reports) and all(v['ok'] for v in reports), 'parts': reports,
            'covered_seconds': duration, 'source_hash': source_hash}


def narration(project):
    announce(project, 'Preparando direção de voz e narração por cena.')
    settings = store.settings()['voice']
    if not settings.get('key') or not settings.get('voice') or not settings.get('model'):
        raise store.Pending('Configure serviço, modelo, voz e chave de narração antes de gerar os áudios.')
    if not project.get('narration') or project.get('narration_script') != project['script_fingerprint']:
        result = ask(project, 3, 'Criar a narração integral e direção detalhada. Cada fala deve pertencer a uma cena existente. '
                     'Dividir falas em até 3500 caracteres. pause_after em segundos de 0 a 5. '
                     'Separar texto falado de direção; não acrescentar fatos à pesquisa. Respeitar o estilo de voz configurado.', LINES,
                     {'voice_settings': {k: v for k, v in settings.items() if k != 'key'}})
        lines = result.get('lines')
        scene_ids = {v['id'] for v in project['script']['scenes']}
        if not isinstance(lines, list) or not lines or {v.get('scene_id') for v in lines} != scene_ids:
            raise store.Pending('A narração não cobriu todas as cenas do roteiro. Retome a etapa.')
        for index, line in enumerate(lines, 1):
            line['id'] = f'FALA-{index:03d}'
            if not isinstance(line.get('text'), str) or not 1 <= len(line['text']) <= 4096:
                raise store.Pending('A IA trouxe uma fala vazia ou longa demais. Refaça a direção de narração.')
            line['pause_after'] = store.number(line.get('pause_after', 0), 0, 5, 'Pausa')
        project['narration'], project['narration_script'] = result, project['script_fingerprint']
        store.document(project, N, _json_doc(result['document'], {'lines': lines, 'voice': {k: v for k, v in settings.items() if k != 'key'}}))
    lines = project['narration']['lines']
    for line in lines:
        check(project)
        descriptor = {k: line.get(k) for k in ('id', 'scene_id', 'text', 'direction', 'pause_after')}
        direction = '\n'.join(v for v in (settings.get('direction', ''), line['direction']) if v)
        fingerprint = instructions.digest(json.dumps([descriptor, {k: v for k, v in settings.items() if k != 'key'}], sort_keys=True))
        relative = f'audio/{line["id"]}-{fingerprint[:12]}.mp3'
        path = store.artifact(project, relative)
        receipt = path.with_suffix('.json')
        if not path.exists() or not receipt.exists():
            snapshot(project, 3, 'Sintetizar a fala com a API configurada.', {'line': line})
            announce(project, 'Gerando narração: ' + line['id'])
            result = ai.creation_speech(settings, line['text'], direction, path)
            result.update(duration=movie.audio_duration(path), path=relative, fingerprint=fingerprint)
            store.atomic(receipt, result)
        saved = store.read(receipt)
        announce(project, 'Ouvindo a narração: ' + line['id'])
        report = review_media(project, 3, path, descriptor)
        if not report['ok']:
            # A second synthesis can fix service artifacts without changing factual text.
            snapshot(project, 3, 'Refazer fala cuja revisão encontrou problemas.', {'line': line, 'review': report})
            ai.creation_speech(settings, line['text'], direction + '\nCorrigir: ' + json.dumps(report, ensure_ascii=False), path)
            saved['duration'] = movie.audio_duration(path)
            store.atomic(receipt, saved)
            report = review_media(project, 3, path, descriptor)
            if not report['ok']:
                project['narration_issues'] = report
                raise store.Pending('A revisão da voz encontrou problemas persistentes. Ajuste a voz ou refaça a narração; o relatório está nos arquivos do projeto.')
        line.update(audio=relative, duration=saved['duration'], review=report, direction_supported=saved.get('direction_supported'))
        store.save(project)
    scenes, start = [], 0.0
    for source_scene in project['script']['scenes']:
        scene = copy.deepcopy(source_scene)
        selected = [v for v in lines if v['scene_id'] == scene['id']]
        folder = store.folder(project)
        output = folder / 'audio' / f"{scene['id']}.wav"
        inputs, filters, labels, captions, cursor = [], [], [], [], 0.0
        for index, line in enumerate(selected):
            inputs += ['-i', str(store.artifact(project, line['audio']))]
            duration = line['duration'] + line['pause_after']
            filters.append(f'[{index}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]')
            labels.append(f'[a{index}]')
            captions.append({'text': line['text'], 'start': cursor, 'end': cursor + line['duration']})
            line['timeline_start'], line['timeline_end'] = start+cursor, start+cursor+line['duration']
            cursor += duration
        filters.append(''.join(labels) + f'concat=n={len(labels)}:v=0:a=1[out]')
        media.run(['ffmpeg', '-y', '-loglevel', 'error', *inputs, '-filter_complex', ';'.join(filters), '-map', '[out]', str(output)])
        scene.update(audio=str(output.relative_to(folder)).replace('\\', '/'), duration=cursor,
                     caption=' '.join(v['text'] for v in selected), captions=captions, timeline_start=start)
        start += cursor
        scenes.append(scene)
    project['integrated'] = {'aspect': '9:16', 'scenes': scenes, 'tracks': project['script'].get('tracks', []), 'duration': start}
    store.document(project, I, _json_doc('# Roteiro integrado\n\nFormato: 9:16; 1080×1920, 30 fps. Tempos medidos nos áudios.\n\n' + project['narration']['document'],
                                      {'timeline': project['integrated'], 'lines': lines}))
    project.update(stage=4, status='running')
    store.save(project)


def choose_asset(project, specification, kind='image'):
    catalog = project.setdefault('media_catalog', [])
    chosen = next((v for v in catalog if v['id'] == specification.get('media_id')), None)
    if chosen and chosen.get('path') and store.artifact(project, chosen['path']).is_file():
        return chosen
    query = specification.get('visual_query') or specification.get('query') or specification.get('description') or project['theme']
    options = [v for v in catalog if (v.get('kind', 'image') in ('image', 'video') if kind == 'image' else v.get('kind') == 'audio')]
    if not chosen and len(options) < 3:
        candidates = web.search_google(query, store.settings()['search'], images=kind == 'image')
        if kind != 'image':
            candidates = [{'url': v['url'], 'source': v['url'], 'description': v.get('title', ''), 'kind': 'audio'}
                          for v in candidates if re.search(r'\.(mp3|wav|ogg|flac)(?:\?|$)', v['url'], re.I)]
        _register_media(project, candidates)
        try:
            _register_media(project, web.search_commons(query, kind='audio' if kind != 'image' else 'image'))
            if kind != 'image' and not any(v.get('kind') == 'audio' for v in catalog):
                _register_media(project, web.search_commons('instrumental music', kind='audio'))
        except Exception:
            pass
        options = [v for v in catalog if (v.get('kind', 'image') in ('image', 'video') if kind == 'image' else v.get('kind') == 'audio')]
    failed = []
    for attempt in range(4):
        if chosen is None or chosen['id'] in failed:
            available = [v for v in options if v['id'] not in failed]
            if not available:
                raise store.Pending('Não foi possível obter uma mídia adequada para: ' + query[:150] + '. Envie uma mídia ao projeto ou refine a pesquisa.')
            selected = ask(project, 4, 'Selecionar mídia adequada à cena/faixa. Respeitar contexto e condições identificadas. '
                           'Não tratar uso não verificado como licença. Retornar um media_id existente nas opções.',
                           obj({'media_id': STR, 'reason': STR}), {'specification': specification, 'options': available})
            chosen = next((v for v in available if v['id'] == selected.get('media_id')), None)
            if not chosen:
                raise store.Pending('A IA não escolheu uma mídia existente no catálogo.')
        try:
            check(project)
            if chosen.get('path') and store.artifact(project, chosen['path']).is_file():
                return chosen
            snapshot(project, 4, 'Obter mídia selecionada e registrar procedência.', {'selected_media': chosen})
            ext = '.mp3' if kind != 'image' else '.mp4' if chosen.get('kind') == 'video' else '.jpg'
            output = store.artifact(project, f"assets/{chosen['id']}/asset{ext}")
            path = web.download_asset(chosen['url'], output, kind=kind)
            movie.validate_asset(path)
            chosen['path'] = str(path.relative_to(store.folder(project))).replace('\\', '/')
            store.save(project)
            return chosen
        except Cancelled:
            raise
        except Exception:
            failed.append(chosen['id'])
            project.setdefault('substitutions', []).append({'media_id': chosen['id'], 'reason': 'Obtenção indisponível; alternativa necessária.'})
            chosen = None
    raise store.Pending('As mídias candidatas não puderam ser obtidas. Envie uma alternativa e retome a montagem.')


def render_progress(project):
    last = [0.0, '']
    labels = {'prepare': 'Preparando mídias', 'visual': 'Compondo cenas no Remotion',
              'audio': 'Mixando áudio no FFmpeg', 'verify': 'Verificando o arquivo', 'complete': 'Render concluído'}
    def update(event):
        check(project)
        now, phase = time.monotonic(), event['phase']
        if phase == last[1] and now - last[0] < 1:
            return
        last[:] = [now, phase]
        project['render_progress'] = event
        project['message'] = f"{labels.get(phase, phase)} · {round(event['progress'] * 100)}%"
        store.save(project)
    return update


def repair_render(project, issue):
    fixed = ask(project, 4, 'Corrigir os erros concretos de validação, renderização ou revisão em render_issue. '
                'Alterar somente parâmetros suportados, enquadramento, textos visuais, mídias e trilha. '
                'Preservar IDs, fatos e áudio narrado. Não gerar código nem alegar corrigir voz com imagem.',
                SCRIPT, extra={'current': project['integrated'], 'render_issue': issue,
                               'media_catalog': project.get('media_catalog', [])})
    previous = {v['id']: v for v in project['integrated']['scenes']}
    if {v.get('id') for v in fixed.get('scenes', [])} != set(previous):
        raise store.Pending('A correção mudou as cenas narradas. Refaça o roteiro antes de continuar.')
    for scene in fixed['scenes']:
        for field in ('audio', 'duration', 'caption', 'captions', 'timeline_start'):
            scene[field] = previous[scene['id']][field]
    project['integrated'].update(scenes=fixed['scenes'], tracks=fixed.get('tracks', []))
    store.document(project, I, _json_doc(fixed['document'], project['integrated']))
    project['history'].append({'action': 'automatic_pilot_repair', 'revision': project['revision'],
                               'code': issue.get('code'), 'at': time.time()})
    store.save(project)


def pilot(project, repair_attempt=0):
    announce(project, 'Selecionando mídias e montando o piloto completo em 9:16.')
    timeline = copy.deepcopy(project['integrated'])
    for scene in timeline['scenes']:
        chosen = choose_asset(project, scene)
        scene['media'], scene['media_id'] = chosen['path'], chosen['id']
        scene['start'] = store.number(scene.get('start', 0), 0, 86400, 'Início da mídia')
    tracks = []
    for track in timeline.get('tracks', []):
        chosen = choose_asset(project, track, kind='audio')
        tracks.append({'file': chosen['path'], 'media_id': chosen['id'], 'at': track.get('at', 0),
                       'kind': track.get('kind', 'music'),
                       'volume': track.get('volume', .12), 'loop': track.get('loop', False),
                       'duration': min(track.get('duration', timeline['duration']), timeline['duration'])})
    timeline['tracks'] = tracks
    try:
        timeline = movie.canonicalize(timeline, store.folder(project), check_cancel=lambda: check(project))
    except ValueError as error:
        if repair_attempt:
            raise store.Pending('A correção da timeline não resolveu o problema: ' + str(error)) from None
        repair_render(project, {'code': 'timeline_invalid', 'message': str(error)})
        return pilot(project, repair_attempt=1)
    project['revision'] += 1
    project['approved_revision'] = None
    revision = project['revision']
    relative = f'renders/pilot-r{revision:04d}.mp4'
    output = store.artifact(project, relative)
    snapshot(project, 4, 'Executar montagem vertical de acordo com a timeline validada.', {'timeline': timeline})
    began, cpu = time.perf_counter(), time.process_time()
    try:
        rendered = movie.render(timeline, store.folder(project), output, height=960,
                               check_cancel=lambda: check(project), on_progress=render_progress(project))
    except store.Pending:
        raise
    except (ValueError, RuntimeError) as error:
        if repair_attempt:
            raise store.Pending('O render continua com problemas após uma correção: ' + str(error)) from None
        repair_render(project, {'code': 'render_failed', 'message': str(error)})
        return pilot(project, repair_attempt=1)
    technical = movie.inspect(output)
    report = review_media(project, 4, output, {'timeline': timeline, 'revision': revision})
    project['metrics'].append({'stage': 4, 'operation': 'render_pilot', 'seconds': round(time.perf_counter()-began, 3),
                               'cpu_seconds': round(time.process_time()-cpu, 3), 'duration': technical['duration']})
    timeline_path = f'timelines/r{revision:04d}.json'
    store.atomic(store.artifact(project, timeline_path), timeline)
    project['timeline'] = timeline
    project['render_identity'] = {k: rendered['manifest'][k] for k in
                                 ('timeline_sha256', 'assets_sha256', 'templates_sha256')}
    project['artifacts'].update(pilot=relative, timeline=timeline_path,
                               render_manifest=str(Path(relative).with_suffix('.manifest.json')).replace('\\', '/'))
    project['pilot_hash'] = _file_hash(output)
    store.document(project, P, _json_doc(f'# Revisão do piloto r{revision}\n\nFormato: 9:16; 540×960.\n\n'
                   'Inspeção técnica e análise audiovisual por modelo realizadas; revisão humana ainda pendente.',
                   {'revision': revision, 'technical': technical, 'audiovisual': report,
                    'render': rendered['manifest'],
                    'substitutions': project.get('substitutions', []), 'media_catalog': project.get('media_catalog', [])}))
    if not report['ok']:
        if repair_attempt == 0:
            repair_render(project, {'code': 'audiovisual_review', 'review': report})
            return pilot(project, repair_attempt=1)
        raise store.Pending('A revisão automática encontrou problemas no piloto. Consulte o relatório, envie ajustes ou refaça a montagem.')
    project.update(status='waiting_feedback', message='Piloto completo disponível. Assista, envie ajustes ou aprove esta versão.')
    store.save(project)


def apply_visual_changes(project, changes):
    updated = ask(project, 4, 'Aplicar SOMENTE as alterações objetivas descritas em requested_changes. '
                  'Não adivinhar os demais comentários ou perguntas pendentes. Preservar IDs, falas e fatos. '
                  'Usar somente operações suportadas e não alterar texto narrado.', SCRIPT,
                  {'current': project['integrated'], 'requested_changes': changes,
                   'media_catalog': project.get('media_catalog', [])})
    existing = {v['id']: v for v in project['integrated']['scenes']}
    if {v.get('id') for v in updated['scenes']} != set(existing):
        raise store.Pending('A revisão mudou a estrutura de cenas. Refaça o roteiro para manter a sincronização.')
    for scene in updated['scenes']:
        original = existing[scene['id']]
        for field in ('audio', 'duration', 'caption', 'captions', 'timeline_start'):
            scene[field] = original[field]
        scene['start'] = store.number(scene.get('start', original.get('start', 0)), 0, 86400, 'Início da mídia')
        scene['focus_x'] = store.number(scene.get('focus_x', original.get('focus_x', .5)), 0, 1, 'Foco horizontal')
        scene['focus_y'] = store.number(scene.get('focus_y', original.get('focus_y', .5)), 0, 1, 'Foco vertical')
    project['integrated']['scenes'] = updated['scenes']
    project['integrated']['tracks'] = updated.get('tracks', project['integrated']['tracks'])
    store.document(project, I, _json_doc(updated['document'], project['integrated']))


def feedback(project, text, *, remember=False, operation_id=None):
    if project['status'] not in ('waiting_feedback', 'waiting_clarification', 'pending', 'error') or not project.get('timeline'):
        raise ValueError('O piloto precisa estar disponível para receber alterações.')
    text = str(text).strip()
    if not text or len(text) > 10000:
        raise ValueError('Descreva o ajuste em até 10.000 caracteres.')
    if not operation_id or not any(v.get('operation_id') == operation_id for v in project['feedback']):
        project['feedback'].append({'text': text, 'at': time.time(), 'revision': project['revision'],
                                    'remember': bool(remember), 'operation_id': operation_id})
    project['approved_revision'] = None
    result = ask(project, 4, 'Converter feedback em mudanças objetivas. Se faltar informação, fazer perguntas específicas '
                 'sem adivinhar preferências. changes é a explicação das mudanças claras; restart_stage é 2 para '
                 'alterar conteúdo/roteiro, 3 para voz/narração ou 4 para edição visual/áudio. Não pedir esclarecimento '
                 'sobre pedidos já claros. needs_clarification booleano, questions lista de perguntas.',
                 obj({'needs_clarification': BOOL, 'questions': arr(), 'changes': STR, 'restart_stage': NUM}),
                 {'feedback': project['feedback'], 'timeline': project['timeline']})
    if remember:
        memory = store.memory(project['creator'])
        preferences = memory['preferences'] + [{'key': 'feedback', 'value': text, 'status': 'declared', 'source': 'user'}]
        store.save_memory(project['creator'], {'preferences': preferences[-100:]})
    if result.get('needs_clarification'):
        if result.get('changes', '').strip() and int(result.get('restart_stage', 4)) == 4:
            apply_visual_changes(project, result['changes'])
        project.update(status='waiting_clarification', questions=result.get('questions') or ['Qual trecho e qual resultado você deseja alterar?'],
                       message='Responda às perguntas para orientar a revisão.')
    else:
        stage = int(result.get('restart_stage', 4))
        if stage not in (2, 3, 4):
            raise ValueError('Etapa de revisão inválida.')
        project.pop('questions', None)
        project['changes'] = result['changes']
        if stage == 4:
            apply_visual_changes(project, result['changes'])
        elif stage == 3:
            project.pop('narration', None)
        invalidate(project, stage)
        project.update(stage=stage, status='ready', message='Alterações registradas. A revisão está pronta para execução.')
    if operation_id and project.get('pending_operation', {}).get('id') == operation_id:
        project['pending_operation'].update(phase='applied', result_status=project['status'])
    store.save(project)
    return store.public(project)


def invalidate(project, stage):
    for name in list(project['documents']):
        if int(name[:2]) >= stage and not (stage == 4 and name == I):
            project['documents'].pop(name)
    project['approved_revision'] = None
    project.pop('approved_identity', None)
    for name in ('final', 'subtitles'):
        project['artifacts'].pop(name, None)


def approve(project, revision):
    if project['status'] != 'waiting_feedback' or revision != project['revision']:
        raise ValueError('Aprove a versão atual do piloto depois de assisti-la.')
    if _file_hash(store.artifact(project, project['artifacts']['pilot'])) != project['pilot_hash']:
        raise ValueError('O piloto foi alterado fora do fluxo. Refaça a montagem antes de aprovar.')
    identity = movie.identity(project['timeline'], store.folder(project))
    if identity != project.get('render_identity'):
        raise ValueError('A timeline, as mídias ou os templates foram alterados. Refaça o piloto antes de aprovar.')
    project['approved_identity'] = identity
    project.update(approved_revision=revision, stage=5, status='ready', message='Piloto aprovado. Pronto para exportação final.')
    project['history'].append({'at': time.time(), 'action': 'approve', 'revision': revision})
    store.save(project)
    return store.public(project)


def finalize(project):
    if project.get('approved_revision') != project.get('revision'):
        raise store.Pending('É necessário aprovar a versão atual do piloto antes da exportação final.')
    if movie.identity(project['timeline'], store.folder(project)) != project.get('approved_identity'):
        raise store.Pending('A timeline, as mídias ou os templates foram alterados após a aprovação. Refaça e aprove o piloto.')
    announce(project, 'Exportando o vídeo final em 1080×1920 e verificando imagem e áudio.')
    snapshot(project, 5, 'Renderizar a timeline aprovada em 9:16, sem mudanças editoriais.', {'timeline': project['timeline']})
    relative = f"renders/final-r{project['revision']:04d}.mp4"
    path = store.artifact(project, relative)
    rendered = movie.render(project['timeline'], store.folder(project), path, height=1920,
                           check_cancel=lambda: check(project), on_progress=render_progress(project))
    technical = movie.inspect(path)
    report = review_media(project, 5, path, {'timeline': project['timeline'], 'revision': project['revision']})
    if not report['ok']:
        raise store.Pending('A revisão da exportação final encontrou problemas. Consulte os relatórios e refaça a etapa.')
    project['artifacts'].update(final=relative, subtitles=str(Path(relative).with_suffix('.srt')).replace('\\', '/'),
                               final_manifest=str(Path(relative).with_suffix('.manifest.json')).replace('\\', '/'))
    store.document(project, F, _json_doc('# Entrega final\n\nVídeo vertical 9:16; 1080×1920, 30 fps.\n\n'
                   'Exportação verificada tecnicamente e por análise audiovisual de modelo. Piloto aprovado pelo usuário.\n'
                   'A narração foi sintetizada por IA. O vídeo não foi publicado automaticamente.',
                   {'revision': project['revision'], 'artifacts': project['artifacts'], 'technical': technical,
                    'audiovisual': report, 'render': rendered['manifest'], 'approved_identity': project['approved_identity'],
                    'credits': project.get('media_catalog', []), 'feedback': project['feedback'], 'metrics': project['metrics']}))
    project.update(status='done', message='Vídeo final 9:16 disponível para assistir e baixar.')
    store.save(project)


def _redact(message):
    for section in ('voice', 'review', 'search'):
        secret = store.settings()[section].get('key')
        if secret:
            message = message.replace(secret, '[credencial ocultada]')
    return re.sub(r'https?://\S+', '[endereço do serviço]', message)[:1600]


def run(cid, pid, *, niche=False):
    project = store.get(cid, pid)
    try:
        project.update(status='running', message='Iniciando a etapa.')
        store.save(project)
        if niche:
            snapshot(project, 0, 'Analisar evidências do canal para propor o nicho.')
            infer_niche(project)
            return
        memory = store.memory(cid)
        if not memory.get('confirmed'):
            project.update(status='waiting_niche', message='Confirme o nicho do canal antes de criar o vídeo.')
            store.save(project)
            return
        if not project['memory'].get('confirmed'):
            project['memory'] = store.memory_snapshot(cid)
        while project['status'] == 'running':
            check(project)
            stage = project['stage']
            snapshot(project, stage, 'Iniciar ou retomar a etapa com seus documentos obrigatórios.')
            began, cpu = time.perf_counter(), time.process_time()
            {1: research, 2: script, 3: narration, 4: pilot, 5: finalize}[stage](project)
            project['metrics'].append({'stage': stage, 'operation': 'stage_total', 'seconds': round(time.perf_counter()-began, 3),
                                       'cpu_seconds': round(time.process_time()-cpu, 3)})
            store.save(project)
    except Cancelled:
        project.update(status='cancelled', message='Execução interrompida. Os arquivos já produzidos foram preservados.')
        store.save(project)
    except Exception as error:
        status = 'waiting_references' if isinstance(error, ResearchDecision) else 'pending' if isinstance(error, (store.Pending, ValueError)) else 'error'
        project.update(status=status, message=_redact(str(error)))
        store.save(project)
    finally:
        ctx.bump()


def enqueue(cid, pid, *, niche=False):
    with store.LOCK:
        project = store.get(cid, pid)
        if key(project) in _active:
            raise ValueError('Este projeto já está na fila ou em execução.')
        if project['status'] in ('waiting_feedback', 'waiting_clarification', 'waiting_references', 'done') and not niche and not project.get('pending_operation'):
            raise ValueError('Responda à decisão pendente antes de continuar.')
        _check_slots()
        project['pending_operation'] = ({'id': uuid.uuid4().hex, 'kind': 'niche'} if niche else
            project.get('pending_operation') or {'id': uuid.uuid4().hex, 'kind': 'run'})
        _active[key(project)] = threading.Event()
        project.update(status='queued', message='Aguardando a fila de criação.')
        store.save(project)
        jobs.submit_user(lambda: _execute_request(cid, pid))
        return store.public(project)


def _check_slots():
    running = active_count()
    running += sum(j.get('status') in ('queued', 'running') for j in jobs.list_jobs())
    if running >= config.MAX_ACTIVE_JOBS_PER_USER:
        raise store.Pending('Você atingiu o limite de trabalhos em andamento. Aguarde ou interrompa um deles.')


def _execute_request(cid, pid):
    project = store.get(cid, pid)
    operation = project.get('pending_operation') or {'kind': 'run'}
    try:
        check(project)
        if operation['kind'] == 'feedback':
            if operation['revision'] != project['revision']:
                raise store.Pending('O feedback pertence a outro piloto. Revise a versão atual antes de aplicar.')
            if operation.get('phase') == 'applied':
                project['status'] = operation['result_status']
            else:
                project['status'] = operation['previous_status']
                feedback(project, operation['text'], remember=operation['remember'], operation_id=operation['id'])
            if project['status'] != 'ready':
                project.pop('pending_operation', None)
                store.save(project)
                return
            project['pending_operation'] = {'id': uuid.uuid4().hex, 'kind': 'run'}
            store.save(project)
        run(cid, pid, niche=operation['kind'] == 'niche')
        project = store.get(cid, pid)
        if project['status'] in ('waiting_niche', 'waiting_feedback', 'waiting_clarification', 'waiting_references', 'done'):
            project.pop('pending_operation', None)
            store.save(project)
    except Cancelled:
        project = store.get(cid, pid)
        project.update(status='cancelled', message='Execução interrompida; arquivos preservados.')
        store.save(project)
    except Exception as error:
        project = store.get(cid, pid)
        project.update(status='pending', message=_redact(str(error)))
        store.save(project)
    finally:
        with store.LOCK:
            _active.pop(key(project), None)
        ctx.bump()


def cancel(project):
    with store.LOCK:
        event = _active.get(key(project))
        if event:
            event.set()
            project.update(status='cancelling', message='Interrompendo após a operação atual.')
        else:
            project.update(status='cancelled', message='Execução interrompida; arquivos preservados.')
        store.save(project)
    return store.public(project)


def queue_feedback(project, text, *, remember=False):
    if project['status'] not in ('waiting_feedback', 'waiting_clarification', 'pending', 'error') or not project.get('timeline'):
        raise ValueError('O piloto precisa estar disponível para receber alterações.')
    if not isinstance(text, str) or not text.strip() or len(text) > 10000:
        raise ValueError('Descreva o ajuste em até 10.000 caracteres.')
    with store.LOCK:
        if key(project) in _active:
            raise ValueError('O projeto já está em execução.')
        _check_slots()
        previous_status = project['status']
        project['pending_operation'] = {'id': uuid.uuid4().hex, 'kind': 'feedback', 'text': text,
             'remember': bool(remember), 'revision': project['revision'], 'previous_status': previous_status, 'phase': 'received'}
        _active[key(project)] = threading.Event()
        project.update(status='queued', message='Aguardando a revisão do feedback.')
        store.save(project)

        jobs.submit_user(lambda: _execute_request(project['creator'], project['id']))
    return store.public(project)


def busy(cid=None):
    with store.LOCK:
        return any(uid == ctx.uid() and (cid is None or creator == cid) for uid, creator, _ in _active)


def active_count():
    with store.LOCK:
        return sum(uid == ctx.uid() for uid, _, _ in _active)


def recover_all():
    for user in config.USERS_DIR.iterdir():
        if not user.is_dir():
            continue
        with ctx.as_user(user.name):
            for row in store.list_projects():
                if row['status'] in ('queued', 'running', 'cancelling'):
                    project = store.get(row['creator'], row['id'])
                    project.update(status='interrupted', message='O servidor reiniciou. Retome para continuar dos arquivos salvos.')
                    store.save(project)
