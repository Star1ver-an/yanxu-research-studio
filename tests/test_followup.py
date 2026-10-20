import base64
import copy
import json
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from app.service import Service
from app.storage import Store
from app.followup import followup, project_stamp, changed_basis
from app.workflows import VERSION


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Store(self.temp.name))
        self.store, self.chat = self.service.store, self.service.assistant
        self.p = self.store.create({'title': 'RAG 拒答策略', 'flow': 'full', 'experiments': 'none'})
        self.other = self.store.create({'title': '边缘模型压缩', 'flow': 'scientific_data'})
        self.store.save_settings({'mode': 'api', 'api_key': 'test-not-real', 'api_model': 'mock'})

    def tearDown(self):
        self.chat.close()
        for _ in range(200):
            if not self.chat.busy(): break
            time.sleep(.01)
        self.service.backups.close()
        self.temp.cleanup()

    def save_memory(self, **kw):
        return self.chat.memory_save({'project': self.p['id'], 'revision': 0,
                                     'record': {'title': '设备', 'text': '只有 8 GB 显存，尚无结果。'}, **kw})

    def advice(self, **kw):
        p = self.store.get(self.p['id'])
        turn = uuid.uuid4().hex
        message = {'id': uuid.uuid4().hex, 'turn': turn, 'role': 'assistant', 'text': '先冻结评测数据。',
                   'status': 'completed', 'mode': 'api', 'state': project_stamp(p), 'version': VERSION,
                   'basis': changed_basis(p), 'stage': 'research',
                   'options': {'history': True, 'report': False, 'materials': [], 'memory': True}, **kw}
        session = p.setdefault('assistant_chat', {'messages': [], 'revision': 0})
        session['messages'] += [{'id': uuid.uuid4().hex, 'turn': turn, 'role': 'user',
                                 'status': 'completed', 'text': '该如何评测？'}, message]
        self.store.put(p)
        return message

    def test_checks_are_read_only_and_no_model(self):
        p = self.store.get(self.p['id']); before = copy.deepcopy(p)
        with patch('app.assistant.api_complete') as model:
            f = followup(p); self.service.public_project(p['id']); self.service.project_list()
        model.assert_not_called()
        self.assertEqual(p, before)
        self.assertEqual(self.store.get(p['id']), before)
        self.assertEqual(f['next']['target'], {'tab': 'work', 'stage': 'research'})
        self.assertIn('导入第一份表格', followup(self.other)['next']['title'])

    def test_stale_reports_and_completed_next_step(self):
        p = self.store.get(self.p['id'])
        from app.workflows import FLOWS
        stages = FLOWS[p['flow']]['stages']
        p['cursor'] = len(stages); p['outputs'] = {s: {'text': '计划', 'approved': True} for s in stages}
        self.assertEqual(followup(p)['next']['target'], {'action': 'export'})
        p['outputs'][stages[1]]['stale'] = True
        p['outputs'][stages[3]]['stale'] = True
        f = followup(p)
        self.assertEqual(f['checks'][0]['id'], 'stale-reports')
        self.assertEqual(f['next']['target']['stage'], stages[1])
        self.assertEqual(f['timeline'][1]['status'], '需重核')

    def test_memory_is_explicit_scoped_and_opt_out(self):
        p = self.save_memory()
        self.assertEqual(p['followup']['memory']['items'][0]['source'], '用户确认')
        text, sources, *_ = self.chat._context(self.p['id'], {})
        self.assertIn('8 GB', text); self.assertTrue(any('项目记忆' in x for x in sources))
        self.assertNotIn('8 GB', self.chat._context(self.p['id'], {'options': {'memory': False}})[0])
        self.assertNotIn('8 GB', self.chat._context(self.other['id'], {})[0])
        self.assertNotIn('8 GB', self.chat._context('', {})[0])
        self.assertEqual(self.chat.state({'project': self.p['id']})['memory_count'], 1)

    def test_memory_edit_disable_and_conflict_are_atomic(self):
        before = self.store.get(self.p['id'])
        p = self.save_memory(); item = p['followup']['memory']['items'][0]
        self.assertEqual(p['outputs'], before['outputs']); self.assertEqual(p['cursor'], before['cursor'])
        with self.assertRaisesRegex(ValueError, '已变化'): self.save_memory()
        self.save_memory(revision=1, record={'id': item['id'], 'title': '设备', 'text': '已更新条件', 'enabled': False})
        self.assertNotIn('已更新条件', self.chat._context(self.p['id'], {})[0])
        self.assertEqual(self.chat.state({'project': self.p['id']})['memory_count'], 0)
        stored = self.store.get(self.p['id'])
        with patch.object(self.store, 'put', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.save_memory(revision=2)
        self.assertEqual(stored, self.store.get(self.p['id']))

    def test_memory_rejects_bad_shapes_cross_project_and_archived(self):
        for record in ({'title': '', 'text': 'x'}, {'title': 'x', 'text': []},
                       {'title': 'x', 'text': 'x', 'enabled': 'yes'}, {'title': 'x', 'text': 'x', 'source': 'AI'}):
            with self.assertRaises(ValueError): self.save_memory(record=record)
        p = self.save_memory(); item = p['followup']['memory']['items'][0]
        with self.assertRaisesRegex(ValueError, '本项目'):
            self.save_memory(project=self.other['id'], record={'id': item['id'], 'title': 'x', 'text': 'x'})
        p = self.store.get(self.p['id']); p['lifecycle'] = 'archived'
        self.store.put(p)
        with self.assertRaises(ValueError): self.save_memory(revision=1)

    def test_defer_does_not_expire_answer_and_changes_reopen(self):
        self.service.research_save(self.p['id'], {'collection': 'experiments', 'record': {'title': '评测', 'status': 'planned'}})
        m = self.advice()
        p = self.store.get(self.p['id']); check = next(x for x in followup(p)['checks'] if x['id'].startswith('experiment:'))
        before = project_stamp(p)
        self.chat.followup_defer({'project': p['id'], 'id': check['id'], 'signature': check['signature'], 'defer': True})
        p = self.store.get(p['id'])
        self.assertEqual(project_stamp(p), before)
        self.assertTrue(next(x for x in followup(p)['checks'] if x['id'] == check['id'])['deferred'])
        self.assertFalse(self.chat.state({'project': p['id']})['messages'][-1]['outdated'])
        item = p['research']['experiments'][0]
        self.service.research_save(p['id'], {'collection': 'experiments', 'record': {**item, 'baseline': '同一基础模型'}})
        check2 = next(x for x in followup(self.store.get(p['id']))['checks'] if x['id'] == check['id'])
        self.assertFalse(check2['deferred'])
        with self.assertRaisesRegex(ValueError, '依据已变化'):
            self.chat.followup_defer({'project': p['id'], 'id': check['id'], 'signature': check['signature'], 'defer': True})

    def test_cross_stage_checks_link_to_exact_record_and_citations(self):
        p = self.store.get(self.p['id'])
        p['research'] = {'experiments': [{'id': '1'*32, 'title': '压缩评测', 'baseline': 'FP32'}],
                         'issues': [{'id': '2'*32, 'title': '说明划分', 'priority': 'high', 'status': 'todo'}],
                         'tasks': [{'id': '3'*32, 'title': '准备材料', 'due': '2000-01-01', 'status': 'todo'}]}
        p['outputs']['research'] = {'text': '证据 [@' + '4'*32 + ']'}
        f = followup(p); checks = {c['id']: c for c in f['checks']}
        self.assertEqual(checks['experiment:'+'1'*32]['target']['record'], '1'*32)
        self.assertIn('数据划分', checks['experiment:'+'1'*32]['detail'])
        self.assertIn('citations:research', checks); self.assertIn('issue:'+'2'*32, checks)
        self.assertIn('overdue:'+'3'*32, checks)

    def test_recheck_retains_selected_scope_but_not_new_materials(self):
        self.service.upload(self.p['id'], {'name': 'a.md', 'data': base64.b64encode(b'notes A').decode()})
        p = self.store.get(self.p['id']); aid = p['assets'][0]['id']
        m = self.advice(options={'report': False, 'history': True, 'materials': [aid], 'memory': False})
        self.service.upload(self.p['id'], {'name': 'b.md', 'data': base64.b64encode(b'notes B').decode()})
        f = followup(self.store.get(self.p['id']))
        self.assertTrue(any(x['id'] == 'advice:'+m['id'] for x in f['checks']))
        result = self.chat.recheck({'project': self.p['id'], 'message': m['id']})
        self.assertEqual(result['options']['materials'], [aid])
        self.assertFalse(result['options']['history']); self.assertFalse(result['options']['memory'])
        self.assertEqual(result['text'], '该如何评测？')
        with self.assertRaises(ValueError): self.chat.recheck({'project': self.other['id'], 'message': m['id']})

    def test_completed_recheck_supersedes_old_and_new_change_reopens_latest(self):
        m = self.advice()
        self.save_memory()
        data = {'project': self.p['id'], 'text': '按最新情况复核', 'review_of': m['id'], 'request_id': uuid.uuid4().hex}
        with patch('app.assistant.api_complete', return_value='保留冻结数据的建议；新条件下先完善方案。'):
            data['context_sha256'] = self.chat.preview(data)['sha256']; self.chat.send(data)
            for _ in range(200):
                if not self.chat.busy(): break
                time.sleep(.01)
        p = self.store.get(self.p['id'])
        self.assertEqual(followup(p)['counts']['advice'], 0)
        self.save_memory(revision=1)
        checks = [x for x in followup(self.store.get(self.p['id']))['checks'] if x['id'].startswith('advice:')]
        self.assertEqual(len(checks), 1); self.assertNotEqual(checks[0]['id'], 'advice:'+m['id'])

    def test_failed_recheck_keeps_original_pending(self):
        from app.providers import ProviderError
        m = self.advice(); self.save_memory()
        data = {'project': self.p['id'], 'text': '复核', 'review_of': m['id'], 'request_id': uuid.uuid4().hex}
        with patch('app.assistant.api_complete', side_effect=ProviderError('401')):
            data['context_sha256'] = self.chat.preview(data)['sha256']; self.chat.send(data)
            for _ in range(200):
                if not self.chat.busy(): break
                time.sleep(.01)
        self.assertEqual(followup(self.store.get(self.p['id']))['counts']['advice'], 1)

    def test_local_answers_saved_proposals_not_pending_scientific_advice(self):
        self.advice(mode='local')
        self.advice(proposal={'status': 'saved'})
        self.save_memory()
        self.assertEqual(followup(self.store.get(self.p['id']))['counts']['advice'], 0)

    def test_progress_and_backup_retain_memory_but_restore_requires_reenable(self):
        self.save_memory()
        raw = self.chat.progress_export({'project': self.p['id']}).decode()
        self.assertIn('8 GB', raw); self.assertIn('本机状态', raw)
        self.assertNotIn('test-not-real', raw); self.assertNotIn('边缘模型压缩', raw)
        _, archive, _ = self.service.export(self.p['id'], 'zip')
        restored = self.service.backups.parse(archive)[0][0]
        self.assertEqual(restored['assistant_memory']['items'][0]['text'], '只有 8 GB 显存，尚无结果。')
        self.assertFalse(restored['assistant_memory']['items'][0]['enabled'])
        self.assertNotIn('assistant_followup', restored)
        self.assertNotIn('assistant_chat', restored)

    def test_memory_change_invalidates_prepared_model_request(self):
        data = {'project': self.p['id'], 'text': '下一步', 'request_id': uuid.uuid4().hex}
        data['context_sha256'] = self.chat.preview(data)['sha256']
        self.save_memory()
        with patch('app.assistant.api_complete') as model:
            with self.assertRaisesRegex(ValueError, '预览后发生变化'): self.chat.send(data)
        model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
