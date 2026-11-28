"""Authenticated creation endpoints; file paths and ownership stay on the server."""
import json
from pathlib import Path
import re
import uuid

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import ai, config, ctx, creation
from . import creation_store as store


def install(app, quota, throttle):
    router = APIRouter(prefix='/api/creation')

    def guarded(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError:
            raise HTTPException(404, 'Projeto, canal ou arquivo não encontrado.') from None
        except (ValueError, ai.AIError) as error:
            raise HTTPException(400, creation._redact(str(error))) from None

    def mutate():
        quota()
        throttle('creation:' + ctx.uid(), 40, 60)

    @router.get('/settings')
    def settings():
        return store.settings(public=True)

    @router.get('/capabilities')
    def capabilities():
        return creation.movie.capabilities()

    @router.put('/settings')
    def settings_save(body: dict = Body(...)):
        mutate()
        return guarded(store.save_settings, body)

    @router.get('/voices')
    def voices():
        throttle('creation-voices:' + ctx.uid(), 20, 60)
        return guarded(ai.creation_voices, store.settings()['voice'])

    @router.post('/voice-sample')
    def voice_sample(body: dict = Body(...)):
        mutate()
        text = str(body.get('text') or 'Esta é uma amostra de narração sintetizada para o seu vídeo.')
        if len(text) > 500:
            raise HTTPException(400, 'A amostra aceita até 500 caracteres.')
        ident = uuid.uuid4().hex
        output = config.user_dir(ctx.uid()) / 'creation-samples' / (ident + '.mp3')
        guarded(ai.creation_speech, store.settings()['voice'], text,
                store.settings()['voice'].get('direction', ''), output)
        ctx.bump()
        return {'url': '/api/creation/voice-sample/' + ident, 'synthetic': True}

    @router.get('/voice-sample/{ident}')
    def sample_file(ident: str):
        if not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise HTTPException(404)
        path = config.user_dir(ctx.uid()) / 'creation-samples' / (ident + '.mp3')
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type='audio/mpeg', headers={'Cache-Control': 'private, no-store'})

    @router.get('/memory/{cid}')
    def memory(cid: str):
        return guarded(store.memory, cid)

    @router.put('/memory/{cid}')
    def memory_save(cid: str, body: dict = Body(...)):
        mutate()
        return guarded(store.save_memory, cid, body)

    @router.get('/projects')
    def projects(creator: str = ''):
        return guarded(store.list_projects, creator or None)

    @router.post('/projects')
    def project_new(body: dict = Body(...)):
        mutate()
        cid = str(body.get('creator') or '')
        project = guarded(store.create, cid, body)
        # Niche confirmation is never inferred from the user's theme.
        guarded(creation.enqueue, cid, project['id'], niche=not project['memory'].get('confirmed'))
        return store.public(project)

    @router.get('/projects/{cid}/{pid}')
    def project_get(cid: str, pid: str):
        return store.public(guarded(store.get, cid, pid))

    @router.get('/projects/{cid}/{pid}/media')
    def project_media(cid: str, pid: str):
        return guarded(store.get, cid, pid).get('media_catalog', [])

    @router.get('/projects/{cid}/{pid}/bundle')
    def project_bundle(cid: str, pid: str):
        import zipfile
        project = guarded(store.get, cid, pid)
        if creation.key(project) in creation._active:
            raise HTTPException(409, 'Aguarde a operação atual antes de baixar o projeto completo.')
        root = store.folder(project).resolve()
        archive = root / 'exports' / 'project.zip'
        archive.parent.mkdir(exist_ok=True)
        temporary = archive.with_name(uuid.uuid4().hex + '.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as output:
                for path in root.rglob('*'):
                    if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root) and not ({'exports', '.render-cache'} & set(path.relative_to(root).parts)) and path.suffix not in ('.tmp', '.part', '.pending'):
                        output.write(path, str(path.relative_to(root)))
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        return FileResponse(archive, media_type='application/zip', filename='peakcut-' + pid + '.zip',
                            headers={'Cache-Control': 'private, no-store'})

    @router.post('/projects/{cid}/{pid}/decision')
    def decision(cid: str, pid: str, body: dict = Body(...)):
        project = guarded(store.get, cid, pid)
        action = body.get('action')
        if action == 'cancel':
            return creation.cancel(project)
        mutate()
        if creation.key(project) in creation._active:
            raise HTTPException(409, 'O projeto está em execução. Aguarde ou interrompa antes de alterar.')
        if action in ('confirm_niche', 'advance_references', 'deepen_research', 'restart', 'approve') and project.get('pending_operation'):
            project['history'].append({'action': 'replace_pending_request', 'request': project.pop('pending_operation')})
        if action == 'confirm_niche':
            guarded(creation.confirm_niche, cid, body.get('niche', ''))
            if project['stage'] == 1:
                project['memory'] = store.memory_snapshot(cid)
            project.update(status='ready', message='Nicho confirmado. Pronto para pesquisar.')
            store.save(project)
        elif action == 'niche':
            return guarded(creation.enqueue, cid, pid, niche=True)
        elif action in ('advance_references', 'deepen_research'):
            if project['status'] != 'waiting_references':
                raise HTTPException(400, 'Não há uma decisão de referências pendente.')
            project['history'].append({'action': action, 'source_count': project.get('source_count', 0)})
            if action == 'advance_references':
                project.update(allow_fewer=True, stage=2, status='ready')
                # The exception is part of the documentary base supplied to the next stage.
                current = store.read_documents(project, [creation.R])[creation.R]
                store.document(project, creation.R, current + '\n\n## Decisão do usuário\nAvançar com menos de 20 referências, autorizado explicitamente.\n')
                project['reference_decision'] = {'approved': True, 'sha256': project['documents'][creation.R]['sha256'],
                                                'count': len({creation.web.canonical(v['url']) for v in project.get('sources', [])})}
            else:
                project.update(stage=1, status='ready', research_round=project.get('research_round', 0)+1)
            store.save(project)
        elif action == 'feedback':
            return guarded(creation.queue_feedback, project, body.get('text', ''), remember=body.get('remember') is True)
        elif action == 'approve':
            if type(body.get('revision')) is not int:
                raise HTTPException(400, 'Informe a versão exata do piloto.')
            guarded(creation.approve, project, body['revision'])
        elif action == 'restart':
            stage = body.get('stage')
            if type(stage) is not int or stage not in range(1, 6):
                raise HTTPException(400, 'Escolha uma etapa entre 1 e 5.')
            if stage == 5:
                raise HTTPException(400, 'A exportação final exige a aprovação da versão atual do piloto.')
            if stage >= 2:
                guarded(creation.require_research, project)
            if stage <= 3:
                project.pop('narration', None)
            creation.invalidate(project, stage)
            project.update(stage=stage, status='ready')
            store.save(project)
        elif action != 'run':
            raise HTTPException(400, 'Decisão desconhecida.')
        return guarded(creation.enqueue, cid, pid)

    @router.post('/projects/{cid}/{pid}/assets')
    def asset_upload(cid: str, pid: str, file: UploadFile = File(...)):
        mutate()
        project = guarded(store.get, cid, pid)
        if creation.key(project) in creation._active:
            raise HTTPException(409, 'Interrompa a execução antes de adicionar uma mídia.')
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in ('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.webm', '.mov', '.mp3', '.wav', '.ogg', '.flac'):
            raise HTTPException(400, 'Envie uma imagem, vídeo ou áudio em formato compatível.')
        relative = 'assets/uploads/' + uuid.uuid4().hex + suffix
        path = store.artifact(project, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with path.open('wb') as output:
                for chunk in iter(lambda: file.file.read(1024 * 1024), b''):
                    total += len(chunk)
                    if total > min(config.MAX_UPLOAD_MB, 100) * 1024 * 1024:
                        raise ValueError('A mídia excede o limite de 100 MB ou o limite do servidor.')
                    output.write(chunk)
            if suffix in ('.jpg', '.jpeg', '.png', '.webp'):
                from PIL import Image
                with Image.open(path) as image:
                    image.verify()
                kind = 'image'
            else:
                from . import creation_render
                creation_render.validate_asset(path)
                creation_render.audio_duration(path)
                kind = 'video' if suffix in ('.mp4', '.webm', '.mov') else 'audio'
        except Exception:
            path.unlink(missing_ok=True)
            raise HTTPException(400, 'A mídia está inválida ou excede o tamanho permitido.') from None
        creation._register_media(project, [{'url': 'upload://' + path.stem, 'source': 'user',
             'description': str(file.filename or '')[:160], 'kind': kind, 'usage': 'provided_by_user', 'path': relative}])
        store.save(project)
        return project['media_catalog'][-1]

    @router.get('/projects/{cid}/{pid}/files/{name:path}')
    def project_file(cid: str, pid: str, name: str, download: int = 0):
        project = guarded(store.get, cid, pid)
        path = guarded(store.artifact, project, name)
        if not path.is_file():
            raise HTTPException(404)
        # Files are downloaded/rendered as data, never executable HTML from an external page.
        types = {'.mp4': 'video/mp4', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.srt': 'text/plain',
                 '.md': 'text/plain', '.json': 'application/json', '.txt': 'text/plain'}
        return FileResponse(path, media_type=types.get(path.suffix, 'application/octet-stream'),
                            filename=path.name if download else None,
                            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    app.include_router(router)
