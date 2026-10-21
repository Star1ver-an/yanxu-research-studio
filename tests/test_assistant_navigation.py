"""Local navigation never needs a model, and exposes only owned destinations."""
import copy
import json
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from app.navigation import FEATURES, feature_catalog, navigation_intent
from app.service import Service
from app.storage import Store


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.service = Service(self.store)
        self.chat = self.service.assistant
        self.projects = [self.store.create({'title': title, 'topic': 'PRIVATE_TOPIC', 'route': route, 'flow': flow})
                         for title, route, flow in [('RAG 检索评估', 'ars', 'full'), ('模型压缩评估', 'scientific', 'scientific_data')]]

    def tearDown(self):
        self.chat.close()
        for _ in range(200):
            if not self.chat.busy(): break
            time.sleep(.01)
        self.tmp.cleanup()

    def test_only_entry_questions_bypass_model(self):
        for text, target in [('我想绘图从哪里进入呢？', 'drawing'), ('统计分析入口在哪里', 'analysis'),
                             ('整篇论文图表在哪里', 'paper'), ('实验方案怎么打开', 'experiments'),
                             ('带我打开连接设置', 'settings'), ('备份在哪里', 'backups'),
                             ('在哪里上传材料', 'materials'), ('研究流程从哪里进入', 'work')]:
            with self.subTest(text=text): self.assertEqual(navigation_intent(text), target)
        for text in ['不要打开绘图页', '我想绘图，请帮我设计模型评测方案',
                     '数据分析怎么做', '在哪里绘图并且帮我计算显著性', '如何设计实验方案', '画图']:
            with self.subTest(text=text): self.assertIsNone(navigation_intent(text))

    def test_local_preview_and_send_need_no_settings_or_provider(self):
        for pid in ['', *(p['id'] for p in self.projects)]:
            data = {'project': pid, 'view': 'project' if pid else 'home', 'text': '我想绘图从哪里进入呢？', 'request_id': uuid.uuid4().hex}
            with patch('app.assistant.api_complete') as api, patch('app.assistant.CodexClient') as codex:
                preview = self.chat.preview(data)
                result = self.chat.send(data)
            api.assert_not_called(); codex.assert_not_called()
            self.assertTrue(preview['local']); self.assertEqual(preview['calls'], 0)
            message = result['messages'][-1]
            self.assertEqual(message['mode'], 'local')
            self.assertEqual(message['actions'][0]['id'], 'drawing')
            self.assertEqual(message['actions'][0]['project'], pid)
            self.assertIn('本机入口指导', message['text'])

    def test_catalog_does_not_include_project_content(self):
        self.chat.send({'text': '绘图在哪里', 'request_id': uuid.uuid4().hex})
        result = self.chat.catalog()
        self.assertEqual(len(result['scopes']), 3)
        self.assertTrue(result['scopes'][0]['latest'])
        self.assertNotIn('PRIVATE_TOPIC', json.dumps(result))
        self.assertNotIn('messages', result)
        self.assertEqual({x['id'] for x in result['features']}, set(FEATURES))
        changed = feature_catalog(); changed[0]['tab'] = 'bad'
        self.assertEqual(FEATURES['drawing']['tab'], 'research')

    def test_entry_guidance_does_not_become_outdated_when_research_content_changes(self):
        p=self.projects[0]
        self.chat.send({'project':p['id'],'text':'绘图在哪里','request_id':uuid.uuid4().hex})
        p=self.store.get(p['id']);p['topic']='研究条件更新';self.store.put(p)
        self.assertFalse(self.chat.state({'project':p['id']})['messages'][-1]['outdated'])

    def test_destinations_are_exact_and_do_not_mutate_projects(self):
        for p in self.projects:
            before = self.store.get(p['id'])
            for target, expected in FEATURES.items():
                result = self.chat.navigate({'target': target, 'project': p['id'], 'url': 'https://untrusted.invalid'})
                for key, value in expected.items(): self.assertEqual(result[key], value)
                self.assertNotIn('url', result)
                if expected['project']: self.assertEqual(result['project_id'], p['id'])
            self.assertEqual(before, self.store.get(p['id']))
        self.assertEqual(self.chat.navigate({'target': 'analysis', 'project': self.projects[0]['id']})['mode'], 'analysis')
        self.assertEqual(self.chat.navigate({'target': 'paper', 'project': self.projects[0]['id']})['mode'], 'paper')

    def test_unknown_deleted_archived_and_trashed_destinations(self):
        for bad in ['https://example.com', '../project', '', None, []]:
            with self.assertRaises(ValueError): self.chat.navigate({'target': bad})
        for pid in ['', '../bad', 'f'*32]:
            with self.assertRaises(ValueError): self.chat.navigate({'target': 'drawing', 'project': pid})
        p = self.projects[0]
        for lifecycle in ['archived', 'trash']:
            changed = copy.deepcopy(p); changed['lifecycle'] = lifecycle; self.store.put(changed)
            with self.assertRaisesRegex(ValueError, '归档或在回收站'):
                self.chat.navigate({'target': 'drawing', 'project': p['id']})
        self.assertNotIn(p['id'], [s['id'] for s in self.chat.catalog()['scopes']])

    def test_local_navigation_allowed_while_other_project_model_runs(self):
        started, release = threading.Event(), threading.Event()
        def complete(*args):
            started.set(); release.wait(3); return '只属于项目 A 的回答'
        p = self.projects[0]
        data = {'project': p['id'], 'view': 'project', 'text': '请评估研究计划', 'request_id': uuid.uuid4().hex}
        data['context_sha256'] = self.chat.preview(data)['sha256']
        try:
            with patch('app.assistant.api_complete', side_effect=complete):
                self.store.save_settings({'mode': 'api', 'api_key': 'mock-key', 'api_model': 'mock'})
                data['context_sha256'] = self.chat.preview(data)['sha256']
                self.chat.send(data); self.assertTrue(started.wait(2))
                catalog = self.chat.catalog(); self.assertEqual(catalog['jobs'][0]['project'], p['id'])
                local = self.chat.send({'text': '绘图在哪里', 'request_id': uuid.uuid4().hex})
                self.assertEqual(local['messages'][-1]['mode'], 'local')
                release.set()
                for _ in range(200):
                    if not self.chat.busy(): break
                    time.sleep(.01)
        finally: release.set()
        self.assertEqual(self.chat.state({'project': p['id']})['messages'][-1]['text'], '只属于项目 A 的回答')
        self.assertNotIn('只属于项目 A', json.dumps(self.chat.state({}), ensure_ascii=False))


if __name__ == '__main__': unittest.main()
