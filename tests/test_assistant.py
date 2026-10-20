import base64
import copy
import json
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from app.assistant import Assistant, safe_error
from app.providers import ProviderError
from app.server import LocalServer
from app.service import Service
from app.storage import Store


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.service = Service(self.store)
        self.chat = self.service.assistant
        self.p = self.store.create({'title': 'RAG 可靠性评估', 'topic': 'TOPIC_A_PRIVATE', 'flow': 'full'})
        self.other = self.store.create({'title': '边缘 AI 评测', 'topic': 'TOPIC_B_PRIVATE', 'flow': 'scientific_data'})
        self.store.save_settings({'mode': 'api', 'api_key': 'qa-private-secret-key', 'api_model': 'mock-model'})

    def tearDown(self):
        self.chat.close()
        for _ in range(100):
            if not self.chat.busy(): break
            time.sleep(.01)
        self.temp.cleanup()

    def data(self, **kw):
        return {'project': self.p['id'], 'view': 'project', 'stage': 'research', **kw}

    def local(self, intent='next', **kw):
        return self.chat.send(self.data(intent=intent, request_id=uuid.uuid4().hex, **kw))

    def wait(self, pid=None):
        for _ in range(200):
            if not self.chat.busy():
                return self.chat.state({'project': pid or self.p['id']})
            time.sleep(.01)
        self.fail('chat did not finish')

    def ai(self, **kw):
        data = self.data(text='请指导下一步', request_id=uuid.uuid4().hex, **kw)
        data['context_sha256'] = self.chat.preview(data)['sha256']
        self.chat.send(data)
        return data

    def test_local_guidance_without_model_or_research_mutation(self):
        before = self.store.get(self.p['id'])
        with patch('app.assistant.api_complete') as provider:
            s = self.local()
        provider.assert_not_called()
        self.assertIn('尚无启用材料', s['messages'][-1]['text'])
        after = self.store.get(self.p['id']); after.pop('assistant_chat')
        self.assertEqual(before, after)
        self.assertEqual(s['messages'][-1]['mode'], 'local')

    def test_project_and_workspace_isolation(self):
        self.local()
        self.assertEqual(self.chat.state({'project': self.other['id']})['messages'], [])
        self.assertEqual(self.chat.state({})['messages'], [])
        context = self.chat._context('', {'view': 'home'})[0]
        self.assertNotIn('TOPIC_A_PRIVATE', context)
        self.assertNotIn('TOPIC_B_PRIVATE', context)
        context = self.chat._context(self.p['id'], self.data())[0]
        self.assertIn('TOPIC_A_PRIVATE', context)
        self.assertNotIn('TOPIC_B_PRIVATE', context)
        self.assertNotIn('qa-private-secret-key', context)

    def test_report_and_material_text_are_opt_in(self):
        self.service.upload(self.p['id'], {'name': 'notes.md', 'data': base64.b64encode(b'PRIVATE_MATERIAL_SENTINEL').decode()})
        p = self.store.get(self.p['id']); p['outputs']['research'] = {'text': 'PRIVATE_REPORT_SENTINEL', 'stale': False, 'approved': False}; self.store.put(p)
        context = self.chat._context(p['id'], self.data())[0]
        self.assertNotIn('PRIVATE_MATERIAL_SENTINEL', context)
        self.assertNotIn('PRIVATE_REPORT_SENTINEL', context)
        opts = {'report': True, 'materials': [p['assets'][0]['id']], 'history': False}
        context = self.chat._context(p['id'], self.data(options=opts))[0]
        self.assertIn('PRIVATE_MATERIAL_SENTINEL', context)
        self.assertIn('PRIVATE_REPORT_SENTINEL', context)
        self.service.toggle_asset(p['id'], {'id': p['assets'][0]['id'], 'enabled': False})
        with self.assertRaisesRegex(ValueError, '停用'):
            self.chat.preview(self.data(options=opts))

    def test_history_is_optional_and_bounded_to_six_turns(self):
        for _ in range(8): self.local()
        context, sources, *_ = self.chat._context(self.p['id'], self.data())
        self.assertIn('最近 6 轮对话', sources)
        context, sources, *_ = self.chat._context(self.p['id'], self.data(options={'history': False}))
        self.assertNotIn('最近对话（可能过时', context)

    def test_preview_rejects_stale_project_and_conversation(self):
        data = self.data(text='问题', request_id=uuid.uuid4().hex)
        data['context_sha256'] = self.chat.preview(data)['sha256']
        self.local()
        with self.assertRaisesRegex(ValueError, '预览后'):
            self.chat.send(data)

    def test_preview_rejects_changed_model_connection(self):
        data=self.data(text='问题',request_id=uuid.uuid4().hex)
        preview=self.chat.preview(data);data['context_sha256']=preview['sha256']
        self.assertIn('mock-model',preview['connection_label'])
        self.store.save_settings({'api_model':'another-model'})
        with self.assertRaisesRegex(ValueError,'预览后'):
            self.chat.send(data)
        data['context_sha256'] = self.chat.preview(data)['sha256']
        p = self.store.get(self.p['id']); p['topic'] = 'changed'; self.store.put(p)
        with self.assertRaisesRegex(ValueError, '预览后'):
            self.chat.send(data)

    def test_only_final_answer_is_saved_and_one_provider_call(self):
        def complete(settings, key, system, prompt, progress, cancel):
            progress('commentary', 'DO_NOT_SAVE_PROGRESS')
            self.assertNotIn('qa-private-secret-key', prompt)
            return '建议：先明确评测数据划分。'
        with patch('app.assistant.api_complete', side_effect=complete) as provider:
            self.ai(); s = self.wait()
        provider.assert_called_once()
        self.assertEqual(s['messages'][-1]['text'], '建议：先明确评测数据划分。')
        self.assertNotIn('DO_NOT_SAVE_PROGRESS', json.dumps(s))

    def test_codex_uses_separate_read_only_client_and_stored_connection(self):
        self.store.save_settings({'mode': 'codex', 'codex_model': 'configured-model'})
        with patch('app.assistant.CodexClient') as client:
            client.return_value.__enter__.return_value.complete.return_value = '请先阅读当前阶段目标。'
            self.ai(); s = self.wait()
        args = client.return_value.__enter__.return_value.complete.call_args.args
        self.assertEqual(args[2], 'configured-model')
        self.assertIn('assistant-invocations', client.call_args.args[1])
        self.assertEqual(s['messages'][-1]['mode'], 'codex')

    def test_failure_preserves_old_answers_and_reports(self):
        self.local(); before = self.store.get(self.p['id'])
        with patch('app.assistant.api_complete', side_effect=ProviderError('401 token=private-bad-secret')):
            self.ai(); s = self.wait()
        self.assertEqual(s['messages'][-1]['status'], 'failed')
        self.assertNotIn('private-bad-secret', s['messages'][-1]['text'])
        self.assertEqual(s['messages'][1]['status'], 'completed')
        after = self.store.get(self.p['id'])
        self.assertEqual(before['outputs'], after['outputs'])
        self.assertEqual(before['cursor'], after['cursor'])

    def test_cancel_blocks_late_answer_and_management_until_finished(self):
        entered, release = threading.Event(), threading.Event()
        def complete(*args):
            entered.set(); release.wait(4); return 'LATE_ANSWER'
        with patch('app.assistant.api_complete', side_effect=complete):
            self.ai(); self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, '助手正在回答'):
                self.service.manage_projects({'ids': [self.p['id']], 'action': 'archive'})
            with self.assertRaisesRegex(ValueError, '等待'):
                self.chat.clear(self.data(acknowledge=True))
            self.chat.stop(self.data()); release.set(); s = self.wait()
        self.assertEqual(s['messages'][-1]['status'], 'stopped')
        self.assertNotIn('LATE_ANSWER', s['messages'][-1]['text'])
        with patch('app.assistant.api_complete', return_value='重试成功'):
            self.ai(); self.assertEqual(self.wait()['messages'][-1]['text'], '重试成功')

    def test_duplicate_http_request_is_idempotent(self):
        data = self.data(intent='next', request_id=uuid.uuid4().hex)
        self.chat.send(data); s = self.chat.send(data)
        self.assertEqual(len(s['messages']), 2)

    def test_changed_project_marks_old_advice_outdated(self):
        self.local()
        self.assertFalse(self.chat.state(self.data())['messages'][-1]['outdated'])
        p = self.store.get(self.p['id']); p['topic'] = 'new'; self.store.put(p)
        self.assertTrue(self.chat.state(self.data())['messages'][-1]['outdated'])

    def test_guidance_checks_gates_completion_and_lifecycle(self):
        p = self.store.get(self.p['id']); p['outputs']['research'] = {'text': 'draft', 'approved': False}; self.store.put(p)
        self.assertIn('尚未确认', self.local('blocked')['messages'][-1]['text'])
        p = self.store.get(p['id']); p['outputs']['research']['stale'] = True; self.store.put(p)
        self.assertIn('需要重新运行', self.local()['messages'][-1]['text'])
        p = self.store.get(p['id']); p['experiments'] = 'undecided'; self.store.put(p)
        self.assertIn('实验来源尚未决定', self.local()['messages'][-1]['text'])
        p = self.store.get(p['id']); p['lifecycle'] = 'archived'; self.store.put(p)
        self.assertIn('先在研究项目列表恢复', self.local()['messages'][-1]['text'])

    def test_completed_workflow_recommends_review_export_and_backup(self):
        from app.workflows import FLOWS
        for p in (self.p,self.other):
            p=self.store.get(p['id']);p['cursor']=len(FLOWS[p['flow']]['stages']);self.store.put(p)
            for intent in ('next','blocked'):
                s=self.local(intent,project=p['id'],stage=FLOWS[p['flow']]['stages'][-1])
                text=s['messages'][-1]['text']
                self.assertIn('建议收尾',text)
                self.assertIn('导出成果并创建备份',text)
                self.assertNotIn('下一步目标',text)

    def test_out_of_order_stage_and_cross_project_material_rejected(self):
        with self.assertRaisesRegex(ValueError, '已到达'):
            self.chat.preview(self.data(stage='draft'))
        with self.assertRaisesRegex(ValueError, '不存在'):
            self.chat.preview(self.data(options={'materials': [uuid.uuid4().hex]}))
        with self.assertRaisesRegex(ValueError, '工作区'):
            self.chat.preview({'options': {'report': True}})

    def test_oversize_and_bad_inputs_make_no_call_or_new_messages(self):
        p = self.store.get(self.p['id']); p['topic'] = 'x' * 120001; self.store.put(p)
        with self.assertRaisesRegex(ValueError, '12 万'):
            self.chat.preview(self.data())
        for extra in ({'intent': 'delete_all'}, {'text': 'x'*6001}, {'intent': 'next', 'request_id': '../'}):
            with self.assertRaises(ValueError):
                data=self.data(request_id=uuid.uuid4().hex);data.update(extra);self.chat.send(data)

    def test_clear_scope_export_and_restart_persistence(self):
        self.local(); self.local(project=self.other['id'], stage='kd_scope')
        self.assertIn('助手', self.chat.export(self.data()).decode('utf-8-sig'))
        with self.assertRaisesRegex(ValueError, '确认'):
            self.chat.clear(self.data())
        self.chat.clear(self.data(acknowledge=True))
        self.assertEqual(self.chat.state(self.data())['messages'], [])
        self.assertEqual(len(Assistant(self.service).state({'project': self.other['id']})['messages']), 2)

    def test_running_chat_recovered_as_interrupted(self):
        self.local(); p = self.store.get(self.p['id']); p['assistant_chat']['messages'][-1].update(status='running', text='partial'); self.store.put(p)
        s = Assistant(self.service).state(self.data())
        self.assertEqual(s['messages'][-1]['status'], 'interrupted')
        self.assertNotEqual(s['messages'][-1]['text'], 'partial')

    def test_project_copy_does_not_mix_chat_and_public_payload_hides_it(self):
        self.local()
        self.assertNotIn('assistant_chat', self.service.public_project(self.p['id']))
        self.assertTrue(all('assistant_chat' not in p for p in self.service.project_list()))
        result = self.service.manage_projects({'ids': [self.p['id']], 'action': 'duplicate'})
        self.assertEqual(self.chat.state({'project': result['created_ids'][0]})['messages'], [])
        _, raw, _ = self.service.export(self.p['id'], 'json')
        self.assertEqual(len(json.loads(raw)['project']['assistant_chat']['messages']), 2)

    def test_error_redaction_and_deterministic_diagnostic(self):
        self.assertNotIn('SECRET', safe_error('API_KEY=SECRET token=SECRET Bearer SECRETabcdefg C:\\Users\\SECRET\\data'))
        s = self.local('error', error='文件超过 20 MB')
        self.assertIn('拆分文件', s['messages'][-1]['text'])
        self.assertTrue(all(a['id'] in ('work','materials','research','history','settings','help','pathways','backups','projects') for a in s['messages'][-1]['actions']))

    def test_http_endpoints_require_token_and_serve_assets(self):
        server = LocalServer(self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            req = Request(base+'/api/assistant/state', b'{}', headers={'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as err: urlopen(req)
            self.assertEqual(err.exception.code, 403)
            req.add_header('X-Research-Token', server.token)
            with urlopen(req) as r: self.assertEqual(json.load(r)['title'], '工作区帮助')
            for file in ('assistant.js','assistant.css'):
                with urlopen(base+'/'+file) as r: self.assertEqual(r.status, 200)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_desktop_export_uses_native_save_dialog_and_handles_cancel(self):
        from main import DesktopAPI
        self.local();desktop=DesktopAPI(self.service);desktop._window=Mock()
        path=self.store.root/'chat.md'
        desktop._window.create_file_dialog.return_value=[str(path)]
        self.assertTrue(desktop.save_assistant_export(self.p['id'])['saved'])
        self.assertIn('当前判断',path.read_text(encoding='utf-8-sig'))
        desktop._window.create_file_dialog.return_value=None
        self.assertFalse(desktop.save_assistant_export(self.p['id'])['saved'])


if __name__ == '__main__': unittest.main()
