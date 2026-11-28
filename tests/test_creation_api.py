"""Creation APIs in local and multi-account modes; no external calls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

os.environ.update(PEAKCUT_DATA=tempfile.mkdtemp(prefix='pc-capi-'), DATABASE_URL='', PEAKCUT_FETCH_MODEL='0', PEAKCUT_UPDATE_CHECK='0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import server, config, auth, ctx, creation, creation_store as store


class CreationAPI(unittest.TestCase):
    def test_narration_form_uses_existing_settings_without_changing_subscription(self):
        import subprocess
        result=subprocess.run(['node','tests/test_creation_settings_ui.js'],cwd=Path(__file__).resolve().parent.parent,
                              capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_local_create_confirm_niche_documents_and_settings(self):
        with patch.object(config, 'ACCOUNTS', False), patch.object(creation, 'enqueue') as enqueue, TestClient(server.app, base_url='http://localhost') as client:
            headers = {'Origin': str(client.base_url).rstrip('/')}
            c = client.post('/api/creators', json={'name': 'Local creation'}, headers=headers)
            self.assertEqual(c.status_code, 200, c.text)
            cid = c.json()['id']
            r = client.post('/api/creation/projects', json={'creator': cid, 'theme': 'A topic'}, headers=headers)
            self.assertEqual(r.status_code, 200, r.text)
            pid = r.json()['id']
            self.assertEqual(r.json()['aspect'], '9:16')
            base = f'/api/creation/projects/{cid}/{pid}'
            r = client.post(base + '/decision', json={'action': 'confirm_niche', 'niche': 'Science'}, headers=headers)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(client.get('/api/creation/memory/' + cid).json()['niche'], 'Science')
            self.assertEqual(client.get(base + '/files/not-found.txt').status_code, 404)
            r = client.put('/api/creation/settings', json={'voice': {'provider': 'openai', 'key': 'private-api-secret', 'voice': 'coral', 'model': 'tts-test'}}, headers=headers)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn('private-api-secret', r.text)
            self.assertTrue(client.get('/api/creation/settings').json()['voice']['has_key'])
            self.assertEqual(client.post(base + '/decision', json={'action': 'approve', 'revision': 0}, headers=headers).status_code, 400)
            with ctx.as_user(auth.local_user().id):
                project=store.get(cid,pid)
                project.update(status='waiting_references', sources=[{'id':'REF-001','url':'https://example.com/one'}],source_count=1)
                store.document(project,creation.R,'# Partial research')
                store.save(project)
            self.assertEqual(client.post(base+'/decision',json={'action':'restart','stage':2},headers=headers).status_code,400)
            accepted=client.post(base+'/decision',json={'action':'advance_references'},headers=headers)
            self.assertEqual(accepted.status_code,200,accepted.text)
            with ctx.as_user(auth.local_user().id):
                creation.require_research(store.get(cid,pid))
            with patch.object(server.load,'disk_full',return_value=True):
                cancelled=client.post(base+'/decision',json={'action':'cancel'},headers=headers)
            self.assertEqual(cancelled.status_code,200,cancelled.text)

    def test_anonymous_and_other_accounts_cannot_read_projects(self):
        with patch.object(config, 'ACCOUNTS', True), patch.object(creation, 'enqueue'), TestClient(server.app) as owner:
            headers = {'Origin': str(owner.base_url).rstrip('/')}
            signup = owner.post('/api/auth/signup', json={'email': 'creation-owner@example.com', 'password': 'testing-pass-123', 'name': 'Owner'}, headers=headers)
            self.assertEqual(signup.status_code, 200, signup.text)
            # Creation belongs to the authenticated account regardless of a forged user field.
            cid = owner.post('/api/creators', json={'name': 'Private channel'}, headers=headers).json()['id']
            result = owner.post('/api/creation/projects', json={'creator': cid, 'theme': 'Private subject', 'user': 'another-user'}, headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            pid = result.json()['id']
            with TestClient(server.app) as stranger:
                self.assertEqual(stranger.get('/api/creation/projects').status_code, 401)
                stranger.post('/api/auth/signup', json={'email': 'creation-stranger@example.com', 'password': 'testing-pass-123', 'name': 'Stranger'}, headers=headers)
                self.assertEqual(stranger.get(f'/api/creation/projects/{cid}/{pid}').status_code, 404)
                self.assertEqual(stranger.get('/api/creation/memory/' + cid).status_code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
