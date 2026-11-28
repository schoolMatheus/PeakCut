"""A real editing contract preserves speech while rejecting unsafe edits."""
import copy
import importlib
import importlib.util
from pathlib import Path
import sys
import inspect
import subprocess
import json
import tempfile
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TimelineContract(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('studio.creation_timeline'),
                             'The versioned Pydantic editing contract is missing')
        self.contract = importlib.import_module('studio.creation_timeline')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        Image.new('RGB', (160, 90), '#005533').save(self.folder / 'image.png')

    def legacy(self):
        return {'aspect': '9:16', 'scenes': [
            {'id': 'CENA-001', 'duration': 1, 'media': 'image.png', 'text': 'Explicação',
             'caption': 'Primeira cena', 'start': 12, 'transition': 'cut'},
            {'id': 'CENA-002', 'duration': 1, 'media': 'image.png', 'caption': 'Segunda cena',
             'transition': 'crossfade', 'transition_duration': .2}], 'tracks': []}

    def test_migration_preserves_duration_and_explicit_overlap(self):
        original = self.legacy()
        converted = self.contract.validate(original, self.folder)
        self.assertEqual(original, self.legacy(), 'Migration must not mutate stored projects')
        self.assertEqual(converted['schemaVersion'], '2.0')
        self.assertEqual(converted['format']['durationInFrames'], 60)
        visuals = next(t for t in converted['tracks'] if t['type'] == 'video')['clips']
        self.assertEqual(visuals[1]['startFrame'], 30)
        self.assertEqual(visuals[0]['durationInFrames'], 36)
        self.assertEqual(converted['transitions'][0]['durationInFrames'], 6)
        self.assertEqual(self.contract.validate(converted, self.folder), converted)

    def test_source_time_does_not_become_timeline_start(self):
        result = self.contract.validate(self.legacy(), self.folder)
        clips = next(t for t in result['tracks'] if t['type'] == 'video')['clips']
        self.assertEqual(clips[0]['startFrame'], 0)
        # An image has no source time. Video source times are tested with real media separately.
        self.assertEqual(clips[0]['sourceInSeconds'], 0)

    def test_unknown_fields_templates_and_nonfinite_numbers_are_rejected(self):
        baseline = self.contract.validate(self.legacy(), self.folder)
        variants = []
        item = copy.deepcopy(baseline); item['shell'] = 'run something'; variants.append(item)
        item = copy.deepcopy(baseline); item['tracks'][0]['clips'][0]['focus']['x'] = float('nan'); variants.append(item)
        item = copy.deepcopy(baseline); item['tracks'][0]['clips'][0]['startFrame'] = True; variants.append(item)
        item = copy.deepcopy(baseline)
        next(t for t in item['tracks'] if t['type'] == 'text')['clips'][0]['templateId'] = 'arbitrary_js'
        variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.contract.validate(item, self.folder)

    def test_traversal_absolute_paths_and_remote_assets_are_rejected(self):
        for path in ('../image.png', 'https://example.org/a.mp4', 'C:/private/a.png'):
            item = self.contract.validate(self.legacy(), self.folder)
            item['assets'][0]['file'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.contract.validate(item, self.folder)

    def test_invalid_references_transitions_and_visual_gaps_are_rejected(self):
        baseline = self.contract.validate(self.legacy(), self.folder)
        for change in ('missing', 'duplicate', 'gap', 'overlap'):
            item = copy.deepcopy(baseline)
            clips = item['tracks'][0]['clips']
            if change == 'missing': clips[0]['assetId'] = 'missing'
            if change == 'duplicate': clips[1]['id'] = clips[0]['id']
            if change == 'gap': clips[1]['startFrame'] = 40
            if change == 'overlap': item['transitions'][0]['durationInFrames'] = 40
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.contract.validate(item, self.folder)

    def test_fingerprint_changes_with_editorial_parameters(self):
        item = self.contract.validate(self.legacy(), self.folder)
        before = self.contract.fingerprint(item)
        item['tracks'][0]['clips'][0]['focus']['x'] = .3
        self.assertNotEqual(self.contract.fingerprint(item), before)
        self.assertIn('crossfade', self.contract.capabilities()['transitions'])

    def test_overlays_are_preserved_and_undeclared_overlaps_rejected(self):
        legacy = self.legacy()
        legacy['scenes'][0]['overlays'] = [{'text': 'Inscreva-se', 'template_id': 'cta_v1',
                                           'at': .2, 'duration': .5, 'x': .5, 'y': .6}]
        item = self.contract.validate(legacy, self.folder)
        text = [c for t in item['tracks'] if t['type'] == 'text' for c in t['clips']]
        self.assertTrue(any(c['text'] == 'Inscreva-se' and c['startFrame'] == 6 for c in text))
        item['transitions'] = []
        with self.assertRaises(ValueError):
            self.contract.validate(item, self.folder)

    def test_nested_visual_overlaps_and_non_audio_ducking_are_rejected(self):
        baseline = self.contract.validate(self.legacy(), self.folder)
        item = copy.deepcopy(baseline)
        item['format']['durationInFrames'] = 100
        track = item['tracks'][0]
        track['clips'][0].update(startFrame=0, durationInFrames=60)
        track['clips'][1].update(startFrame=30, durationInFrames=60)
        third = {**copy.deepcopy(track['clips'][1]), 'id': 'CLIP-003', 'startFrame': 50, 'durationInFrames': 50}
        track['clips'].append(third)
        item['transitions'] = [
            {'id': 'TR-1', 'type': 'crossfade', 'fromClipId': 'CLIP-001', 'toClipId': 'CLIP-002', 'startFrame': 30, 'durationInFrames': 30},
            {'id': 'TR-2', 'type': 'crossfade', 'fromClipId': 'CLIP-002', 'toClipId': 'CLIP-003', 'startFrame': 50, 'durationInFrames': 40}]
        with self.assertRaises(ValueError):
            self.contract.Timeline.model_validate(item)
        item = copy.deepcopy(baseline)
        text = next(t for t in item['tracks'] if t['type'] == 'text')
        text['role'] = 'narration'
        item['assets'].append({'id': 'AUD', 'kind': 'audio', 'file': 'voice.wav'})
        item['tracks'].append({'id': 'music', 'type': 'audio', 'role': 'music', 'duckUnder': text['id'],
                               'clips': [{'id': 'bed', 'assetId': 'AUD', 'startFrame': 0, 'durationInFrames': 60}]})
        with self.assertRaises(ValueError):
            self.contract.Timeline.model_validate(item)

    def test_validation_can_be_cancelled_before_probing_assets(self):
        self.assertIn('check_cancel', inspect.signature(self.contract.validate).parameters)
        calls = []
        def stop():
            calls.append(1)
            raise InterruptedError('cancelled by user')
        with self.assertRaises(InterruptedError):
            self.contract.validate(self.legacy(), self.folder, check_cancel=stop)
        self.assertEqual(calls, [1])

    def test_python_canonical_dump_is_accepted_by_node_contract(self):
        item = self.contract.validate(self.legacy(), self.folder)
        code = "import {timelineSchema} from './src/peakcut/schema.mjs'; let s=''; for await(const c of process.stdin)s+=c; timelineSchema.parse(JSON.parse(s));"
        result = subprocess.run(['node', '--input-type=module', '-e', code], input=json.dumps(item),
                                capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent/'my-video', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
