import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.server import LocalServer
from app.service import Service
from app.storage import Store
from app.studies import BLUEPRINTS, FIELDS, brief_context, restored_brief
from app.workflows import FLOWS


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Service(Store(self.tmp.name))

    def tearDown(self):
        self.s.close_plotting()
        self.s.assistant.close()
        self.s.backups.close()
        self.tmp.cleanup()

    def create(self, key='tmem43-aki'):
        return self.s.study_create({'blueprint': key})

    def save(self, p, **extra):
        b = p['study_brief']
        return self.s.study_save(p['id'], dict(revision=b['revision'], fields=copy.deepcopy(b['fields']),
                                              context_enabled=False, **extra))

    def test_three_real_topics_are_plans_with_unknown_materials_and_no_results(self):
        for plan in BLUEPRINTS:
            p = self.create(plan['id'])
            self.assertEqual(p['study_brief']['fields']['original_title'], plan['fields']['original_title'])
            self.assertEqual(p['study_brief']['status'], 'draft')
            self.assertFalse(p['study_brief']['context_enabled'])
            self.assertEqual(p['experiments'], 'none' if plan['profile']=='biomedical' else 'undecided')
            self.assertEqual((p['assets'], p['outputs'], p['runs'], p['confirmations']), ([], {}, [], []))
            self.assertTrue(all(t['status'] == 'todo' and t['criterion'] for t in p['research']['tasks']))
            self.assertEqual(p['followup']['next']['target'], {'tab': 'brief'})
        self.assertNotIn('study_brief', self.s.project_list()[0])

    def test_concurrent_retries_do_not_duplicate_projects_and_failure_leaves_no_orphan(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.create()['id'], range(4)))
        self.assertEqual(len(set(ids)), 1)
        with patch.object(self.s.store, 'put', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.create('llm-ashare')
        self.assertEqual(len(self.s.store.list()), 1)

    def test_confirmation_does_not_invent_evidence_and_subsequent_edit_requires_review(self):
        p = self.create()
        with self.assertRaises(ValueError):
            self.save(p, confirm=True)
        p = self.save(p, confirm=True, acknowledge=True)
        self.assertEqual(p['study_brief']['status'], 'confirmed')
        self.assertEqual(p['confirmations'], [])
        p['study_brief']['fields']['scope'] += '\n研究者补充范围'
        p = self.save(p)
        self.assertEqual(p['study_brief']['status'], 'draft')
        self.assertEqual(len(self.s.store.get(p['id'])['study_brief_history']), 2)

    def test_context_respects_opt_in_and_keeps_domain_boundary(self):
        p = self.create('llm-ashare')
        marker = 'private scope 47c1'
        p['study_brief']['fields']['materials'] = marker
        self.s.store.put(p)
        sid = FLOWS[p['flow']]['stages'][0]
        context = self.s.context(p, sid, '')
        self.assertNotIn(marker, context)
        self.assertIn('前视偏差', context)
        b = p['study_brief']
        p = self.s.study_save(p['id'], dict(revision=b['revision'], fields=b['fields'], context_enabled=True))
        self.assertIn(marker, self.s.context(p, sid, ''))
        preview = self.s.assistant.preview({'project': p['id'], 'stage': sid, 'text': '研究范围是什么？',
                                            'options': {'history': False, 'memory': False, 'report': False, 'materials': []}})
        self.assertTrue(any('研究简报' in source for source in preview['sources']))

    def test_input_change_invalidates_existing_output_only_when_used_as_context(self):
        p = self.create()
        sid = FLOWS[p['flow']]['stages'][0]
        p['outputs'][sid] = {'text': 'Existing work', 'approved': True, 'stale': False}
        self.s.store.put(p)
        p['study_brief']['fields']['question'] += ' 新问题'
        p = self.save(p)
        self.assertFalse(p['outputs'][sid]['stale'])
        b = p['study_brief']
        p = self.s.study_save(p['id'], dict(revision=b['revision'], fields=b['fields'], context_enabled=True))
        self.assertTrue(p['outputs'][sid]['stale'])
        self.assertEqual(p['outputs'][sid]['text'], 'Existing work')

    def test_stale_revision_invalid_fields_and_inactive_project_are_rejected_without_writes(self):
        p = self.create()
        self.save(p)
        before = self.s.store.get(p['id'])
        with self.assertRaisesRegex(ValueError, '其他页面'):
            self.save(p)
        self.assertEqual(self.s.store.get(p['id']), before)
        for key, bad in [('question', []), ('working_title', ''), ('scope', 'x' * 4001)]:
            current = self.s.public_project(p['id'])
            current['study_brief']['fields'][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.save(current)
        self.s.manage_projects({'action': 'archive', 'ids': [p['id']]})
        with self.assertRaises(ValueError):
            self.save(self.s.public_project(p['id']))

    def test_duplicate_and_zip_restore_preserve_brief_but_clear_approval_and_model_use(self):
        p = self.create()
        b = p['study_brief']
        p = self.s.study_save(p['id'], dict(revision=1, fields=b['fields'], context_enabled=True, confirm=True, acknowledge=True))
        duplicated = self.s.manage_projects({'action': 'duplicate', 'ids': [p['id']], 'mode': 'reports'})
        clone = self.s.store.get(duplicated['created_ids'][0])
        self.assertNotIn('study_origin', clone)
        self.assertEqual(clone['study_brief']['status'], 'draft')
        self.assertFalse(clone['study_brief']['context_enabled'])
        self.assertEqual(self.create()['id'], p['id'])
        _, raw, _ = self.s.export(p['id'], 'zip')
        result = self.s.backups.restore(raw, preview=False)
        restored = self.s.store.get(result['created_ids'][0])
        self.assertEqual(restored['study_brief']['fields'], b['fields'])
        self.assertEqual(restored['study_brief']['status'], 'draft')
        self.assertFalse(restored['study_brief']['context_enabled'])

    def test_export_contains_scope_sources_and_no_research_claims(self):
        p = self.create('japan-reconstruction')
        name, raw, mime = self.s.study_export(p['id'])
        self.assertEqual(name, '研究简报.md')
        text = raw.decode()
        self.assertIn('1945—1955', text)
        self.assertIn('https://www.boj.or.jp/', text)
        self.assertIn('待核对', text)
        self.assertEqual(mime, 'text/markdown; charset=utf-8')
        bad = copy.deepcopy(p['study_brief'])
        bad['fields']['question'] = 1
        with self.assertRaises(ValueError):
            restored_brief(bad)


class StudyHTTPTests(unittest.TestCase):
    def test_real_http_creation_save_export_auth_and_assets(self):
        with tempfile.TemporaryDirectory() as root:
            server = LocalServer(Service(Store(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            def request(path, data=None, authorized=True):
                headers = {'X-Research-Token': server.token} if authorized else {}
                return urlopen(Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers), timeout=15)
            try:
                with self.assertRaises(HTTPError) as failed:
                    request('/api/studies', authorized=False)
                self.assertEqual(failed.exception.code, 403)
                self.assertEqual(len(json.load(request('/api/studies'))['blueprints']), 3)
                p = json.load(request('/api/studies/create', {'blueprint': 'llm-ashare'}))
                for resource in ('/studio.js', '/studio.css'):
                    self.assertEqual(request(resource).status, 200)
                b = p['study_brief']
                saved = json.load(request('/api/project/' + p['id'] + '/study-save',
                                         {'revision': 1, 'fields': b['fields'], 'context_enabled': False}))
                self.assertEqual(saved['study_brief']['revision'], 2)
                response = request('/api/project/' + p['id'] + '/study-export', {})
                self.assertIn("filename*=UTF-8''", response.headers['Content-Disposition'])
                self.assertIn('收益、风险与交易成本', response.read().decode())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
