import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.catalog import router_for

from app.skill_catalog import SkillCatalog
from app.skill_loader import SkillLoader
from app.skill_references import reference_path, reference_specs


ROOT = Path(__file__).resolve().parents[2]


class SkillReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'sample'
        (self.folder / 'references').mkdir(parents=True)

    def catalog(self, references=None):
        value = dict(id='sample', name='Sample', description='JSON overview',
                     method_refs=[], pipeline_refs=[], references=references or [])
        (self.folder / 'skill.json').write_text(json.dumps(value), encoding='utf8')
        return SkillCatalog(SkillLoader(self.root), None, None)

    def ref(self, identity='notes', path='references/notes.md'):
        return dict(id=identity, title=identity.title(), path=path)

    def test_registered_identity_selection_and_pagination(self):
        (self.folder / 'references/notes.md').write_text('first document', encoding='utf8')
        (self.folder / 'references/qc.md').write_text('质量检查abcdef', encoding='utf8')
        catalog = self.catalog([self.ref(), self.ref('qc', 'references/qc.md')])
        first = catalog.read_reference('sample', max_chars=5)
        self.assertEqual(first['text'], 'first')
        self.assertEqual(first['reference_id'], 'notes')
        self.assertEqual([r['id'] for r in first['references']], ['notes', 'qc'])
        page = catalog.read_reference('sample', reference_id='qc', max_chars=4)
        self.assertEqual(page['text'], '质量检查')
        second = catalog.read_reference('sample', reference_id='qc', offset=page['next_offset'])
        self.assertEqual(second['text'], 'abcdef')
        self.assertFalse(second['truncated'])
        self.assertEqual(second['source'], 'skills/sample/references/qc.md')
        for identity in ['../notes', 'references/notes.md', 'missing']:
            with self.subTest(identity=identity), self.assertRaises(KeyError):
                catalog.read_reference('sample', reference_id=identity)

    def test_undocumented_skill_uses_json_and_ignores_unregistered_markdown(self):
        (self.folder / 'SKILL.md').write_text('OLD SHELL INSTRUCTIONS', encoding='utf8')
        (self.folder / 'references/notes.md').write_text('UNREGISTERED', encoding='utf8')
        catalog = self.catalog()
        result = catalog.read_reference('sample')
        self.assertIn('JSON overview', result['text'])
        self.assertEqual(result['references'], [])
        self.assertEqual(result['source'], 'skills/sample/skill.json')
        self.assertIsNone(result['reference_id'])
        self.assertNotIn('OLD SHELL', result['text'])
        self.assertNotIn('UNREGISTERED', result['text'])

    def test_loading_and_default_prompt_do_not_read_reference_content(self):
        catalog = self.catalog([self.ref()])
        read_text = Path.read_text
        def only_json(path, *args, **kwargs):
            if path.suffix == '.md':
                raise AssertionError('Reference was read eagerly')
            return read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', only_json):
            skill = catalog.loader.find('sample')
            self.assertIn('JSON overview', skill.prompt_excerpt())
        with self.assertRaisesRegex(ValueError, 'missing'):
            catalog.read_reference('sample')

    def test_invalid_reference_registration_is_rejected(self):
        for path in ['../other.md', '/tmp/secret.md', 'SKILL.md', 'references/../secret.md',
                     'references\\notes.md', 'references//notes.md', 'references/notes.py']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                reference_specs({'references': [self.ref(path=path)]})
        for entries in [[self.ref(), self.ref()], [dict(self.ref(), extra=True)],
                        [self.ref(identity='../notes')], [dict(self.ref(), title='')]]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                reference_specs({'references': entries})

    def test_http_reference_selection_and_errors(self):
        (self.folder / 'references/notes.md').write_text('registered notes', encoding='utf8')
        catalog = self.catalog([self.ref()])
        app = FastAPI()
        app.include_router(router_for(SimpleNamespace(skill_catalog=catalog)))
        with TestClient(app) as client:
            result = client.get('/api/skills/sample/reference', params={'reference_id': 'notes', 'max_chars': 10})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['text'], 'registered')
            self.assertEqual(client.get('/api/skills/sample/reference', params={'reference_id': 'missing'}).status_code, 404)
            self.assertEqual(client.get('/api/skills/sample/reference', params={'max_chars': 12001}).status_code, 422)
            (self.folder / 'references/notes.md').unlink()
            self.assertEqual(client.get('/api/skills/sample/reference').status_code, 400)

    @unittest.skipIf(os.name == 'nt', 'Windows symlinks require additional privileges')
    def test_symlink_cannot_leave_registered_reference_directory(self):
        outside = self.root / 'outside.md'
        outside.write_text('SECRET', encoding='utf8')
        (self.folder / 'references/notes.md').symlink_to(outside)
        catalog = self.catalog([self.ref()])
        with self.assertRaisesRegex(ValueError, 'leaves'):
            catalog.read_reference('sample')

    def test_all_native_references_are_present_and_legacy_documents_are_retired(self):
        loader = SkillLoader(ROOT / 'skills')
        self.assertEqual(len(loader.load_all()), 105)
        self.assertEqual(list((ROOT / 'skills').glob('*/SKILL.md')), [])
        for skill in loader.load_all():
            for reference in skill.references:
                with self.subTest(skill=skill.name, reference=reference.id):
                    self.assertTrue(reference_path(skill.path, reference).read_text(encoding='utf8').strip())
        for name in ['software-ants', 'software-mrtrix3']:
            self.assertTrue(loader.find(name).references)


if __name__ == '__main__':
    unittest.main()
