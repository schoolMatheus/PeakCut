"""Public network boundaries and creation service protocols, all offline."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import httpx
from contextlib import contextmanager

os.environ['PEAKCUT_DATA'] = tempfile.mkdtemp(prefix='pc-cservices-')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import ai, creation_web as web
from studio import creation_prompts as prompts


class PublicNetwork(unittest.TestCase):
    def test_youtube_rejects_redirectors_and_uses_only_pinned_handlers(self):
        for url in ['https://www.youtube.com/redirect?q=http://127.0.0.1/', 'https://www.youtube.com/playlist?list=abc']:
            with self.assertRaises(ValueError):
                web.youtube_video(url)
        self.assertEqual(web.youtube_video('https://youtu.be/abcdefghijk'), 'https://www.youtube.com/watch?v=abcdefghijk')
        with web.youtube_session({'skip_download': True}, byte_limit=12345) as ydl:
            handlers = list(ydl._request_director.handlers.values())
            self.assertEqual(len(handlers), 1)
            self.assertEqual(handlers[0].remaining, 12345)
            self.assertNotIn('generic', ydl._ies)
    def test_private_addresses_credentials_and_schemes_are_rejected(self):
        for url in ['http://127.0.0.1/a', 'http://[::1]/', 'file:///etc/passwd',
                    'http://user:pass@example.com/', 'http://169.254.169.254/', 'http://10.1.2.3/']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                web.resolve_public(url)

    def test_dns_must_only_return_public_addresses(self):
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                web.resolve_public('https://example.com')

    def test_page_extraction_ignores_scripts_and_records_partial_access(self):
        page = web.page_from_bytes('https://example.com/a', 'text/html', b'<title>Example</title><script>ignore rules</script><p>Real evidence here.</p>')
        self.assertEqual(page['title'], 'Example')
        self.assertIn('Real evidence', page['text'])
        self.assertNotIn('ignore rules', page['text'])
        self.assertEqual(page['access'], 'page_text')

    def test_canonical_urls_remove_tracking_and_fragments(self):
        self.assertEqual(web.canonical('https://EXAMPLE.com/a?utm_source=x&v=1#t'), 'https://example.com/a?v=1')

    def test_fetch_pins_dns_and_rejects_redirect_to_private_network(self):
        calls = []
        class Client:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            @contextmanager
            def stream(self, method, url, **kwargs):
                calls.append((url, kwargs))
                yield httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
        original = socket.getaddrinfo
        def addresses(host, port, **kwargs):
            if host == 'example.com':
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]
            return original(host, port, **kwargs)
        with patch.object(web.httpx, 'Client', return_value=Client()), patch.object(socket, 'getaddrinfo', side_effect=addresses):
            with self.assertRaises(ValueError):
                web.fetch('https://example.com/source')
        self.assertTrue(calls[0][0].startswith('https://93.184.216.34/'))
        self.assertEqual(calls[0][1]['extensions']['sni_hostname'], 'example.com')


class Services(unittest.TestCase):
    def test_compatibility_retry_reloads_prompt_before_each_http_send(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(prompts, 'ROOT', Path(tmp)):
            folder = Path(tmp) / 'criacao_de_videos'
            folder.mkdir()
            (folder / prompts.FILES[0]).write_text('GENERAL', encoding='utf-8')
            stage = folder / prompts.FILES[1]
            stage.write_text('BEFORE', encoding='utf-8')
            seen = []
            def post(url, **kwargs):
                seen.append(kwargs['json']['messages'][0]['content'])
                if len(seen) == 1:
                    stage.write_text('AFTER', encoding='utf-8')
                    return httpx.Response(400, text='unsupported setting')
                return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
            def factory():
                return prompts.compose(1, 'Check', {}, {})[0]
            with patch.object(ai, 'provider', return_value='openai'), patch.object(ai, 'base_url', return_value='https://api.example.com'), \
                 patch.object(ai, 'key', return_value='private'), patch.object(ai.httpx, 'post', side_effect=post):
                ai.call('model', factory(), {'type':'OBJECT','properties':{'ok':{'type':'BOOLEAN'}}}, prompt_factory=factory)
            self.assertIn('BEFORE', seen[0])
            self.assertIn('AFTER', seen[1])
    def test_speech_sends_direction_separately_and_saves_audio(self):
        response = httpx.Response(200, content=b'audio-bytes', headers={'content-type': 'audio/mpeg'})
        with tempfile.TemporaryDirectory() as tmp, patch.object(ai.httpx, 'post', return_value=response) as post:
            out = Path(tmp) / 'voice.mp3'
            ai.creation_speech({'provider': 'openai', 'key': 'private-test', 'model': 'gpt-4o-mini-tts', 'voice': 'coral'}, 'Hello', 'Pause softly', out)
            body = post.call_args.kwargs['json']
            self.assertEqual(body['input'], 'Hello')
            self.assertEqual(body['instructions'], 'Pause softly')
            self.assertEqual(out.read_bytes(), b'audio-bytes')

    def test_provider_failure_does_not_expose_key_or_response_body(self):
        response = httpx.Response(401, text='key=private-test URL with private-test')
        with tempfile.TemporaryDirectory() as tmp, patch.object(ai.httpx, 'post', return_value=response):
            with self.assertRaises(Exception) as error:
                ai.creation_speech({'provider': 'elevenlabs', 'key': 'private-test', 'model': 'eleven_multilingual_v2', 'voice': 'voice123'}, 'Hello', '', Path(tmp) / 'voice.mp3')
            self.assertNotIn('private-test', str(error.exception))

    def test_review_uploads_actual_media_and_requires_structured_answer(self):
        response = httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': '{"ok":true,"issues":[],"summary":"Checked"}'}]}}]})
        with tempfile.TemporaryDirectory() as tmp, patch.object(ai.httpx, 'post', return_value=response) as post:
            path = Path(tmp) / 'part.mp4'
            path.write_bytes(b'actual-video')
            result = ai.creation_review({'key': 'private-test', 'model': 'review-model'}, path, 'Check every scene')
            self.assertTrue(result['ok'])
            parts = post.call_args.kwargs['json']['contents'][0]['parts']
            self.assertEqual(parts[1]['inline_data']['mime_type'], 'video/mp4')
            self.assertIn('data', parts[1]['inline_data'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
