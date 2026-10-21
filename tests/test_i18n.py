import copy
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from app.i18n import LANGUAGES,catalogs,tr,output_language,output_instruction
from app.storage import Store
from app.service import Service
from app.server import LocalServer
from app.word_export import make_docx


class LocaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name);self.service=Service(self.store)
        self.p=self.store.create({'title':'研究项目','topic':'原始中文目标 must remain unchanged','flow':'full'})

    def tearDown(self):
        self.service.assistant.close()
        for _ in range(300):
            if not self.service.assistant.busy() and not self.service.jobs:break
            time.sleep(.01)
        self.service.close_plotting();self.tmp.cleanup()

    def wait_chat(self,pid):
        for _ in range(300):
            if not self.service.assistant.busy():return self.service.assistant.state({'project':pid})
            time.sleep(.01)
        self.fail('Assistant did not finish')

    def test_catalog_parity_placeholders_and_core_labels(self):
        bundle=catalogs();self.assertEqual(set(bundle),set(LANGUAGES));source=set(bundle['zh-CN'])
        self.assertGreater(len(source),3800)
        for locale,entries in bundle.items():
            self.assertEqual(set(entries),source)
            for key,value in entries.items():
                self.assertTrue(value.strip(),(locale,key))
                self.assertEqual(sorted(re.findall(r'\{\d+\}',key)),sorted(re.findall(r'\{\d+\}',value)),(locale,key))
        self.assertEqual(tr('研究项目','en'),'Projects')
        self.assertEqual(tr('研究项目','ja'),'研究プロジェクト')
        self.assertEqual(tr('研究项目','ko'),'연구 프로젝트')
        self.assertEqual(tr('研究项目','zh-TW'),'研究專案')

    def test_preferences_are_separate_and_invalid_language_is_atomic(self):
        project=copy.deepcopy(self.p);settings=self.store.get_settings()
        for locale in LANGUAGES:self.assertEqual(self.store.save_language({'ui_language':locale})['ui_language'],locale)
        before=self.store.language()
        for bad in [None,'fr',[],{},'en<script>']:
            with self.assertRaises(ValueError):self.store.save_language({'ui_language':bad})
            self.assertEqual(self.store.language(),before)
        self.assertEqual(project,self.store.get(project['id']));self.assertEqual(settings,self.store.get_settings())
        self.assertEqual(Store(self.tmp.name).language(),before)

    def test_output_language_defaults_overrides_and_preserves_existing_reports(self):
        self.store.save_language({'ui_language':'ja'})
        self.assertEqual(output_language(self.p,self.store),'ja')
        old=copy.deepcopy(self.p);old['outputs']['research']={'text':'原来的报告 unchanged','approved':True,'stale':False};self.store.put(old)
        self.service.update_info(self.p['id'],{'output_language':'en'})
        p=self.store.get(self.p['id']);self.assertEqual(p['outputs'],old['outputs'])
        self.assertEqual(output_language(p,self.store),'en')
        self.assertEqual(p['title'],'研究项目');self.assertEqual(p['topic'],old['topic'])
        self.assertIn('Preserve JSON keys',output_instruction('ko'))

    def test_local_reports_translate_templates_but_preserve_source_columns(self):
        from app.data_tools import table_report
        for locale in LANGUAGES:
            text,execution=table_report([{'name':'原始表.csv','text':'研究项目,value\nAlpha,1\nBeta,3\n'}],locale)
            self.assertIn('原始表.csv',text);self.assertIn('研究项目',text)
            self.assertEqual(execution['profiles'][0]['fields'][1]['mean'],2)
            self.assertNotIn('<unk>',text)
            headers=[line for line in text.splitlines() if line.startswith('|') and not line.startswith('|---')]
            self.assertEqual(headers[0].count('|'),7)
            self.assertEqual(headers[3].count('|'),8)
            if locale=='en':self.assertIn('# Local table quality checks',text)

    def test_both_routes_capture_each_output_language_with_local_model_mock(self):
        self.store.save_settings({'mode':'api','api_key':'mock-only','api_model':'mock'})
        for locale in LANGUAGES:
            self.store.save_language({'ui_language':locale})
            for route,flow in [('ars','full'),('scientific','scientific_evidence')]:
                # Use the actual first stage and context assembly from each workflow.
                from app.workflows import FLOWS
                p=self.store.create({'title':'中文原始课题','topic':'keep original 研究目标','route':route,'flow':flow})
                def complete(settings,key,system,prompt,*_):
                    self.assertIn(output_instruction(locale).strip(),system)
                    self.assertIn('keep original 研究目标',prompt)
                    return 'mock output '+locale
                with patch('app.service.api_complete',side_effect=complete):
                    sid=FLOWS[flow]['stages'][0];self.service.start(p['id'],{'stage':sid})
                    for _ in range(300):
                        if not self.service.jobs:break
                        time.sleep(.01)
                saved=self.store.get(p['id']);self.assertEqual(saved['outputs'][sid]['text'],'mock output '+locale)
                self.assertEqual(saved['outputs'][sid]['output_language'],locale)
                self.assertEqual(saved['title'],'中文原始课题')

    def test_preview_changes_with_output_language_and_model_receives_selected_language(self):
        chat=self.service.assistant;pid=self.p['id']
        self.store.save_settings({'mode':'api','api_key':'mock-only','api_model':'mock'})
        data={'project':pid,'text':'请讨论评测方案','view':'project','stage':'research','request_id':uuid.uuid4().hex}
        old=chat.preview(data);self.store.save_language({'ui_language':'en'})
        self.assertNotEqual(old['sha256'],chat.preview(data)['sha256'])
        with self.assertRaisesRegex(ValueError,'预览后'):chat.send({**data,'context_sha256':old['sha256']})
        def complete(settings,key,system,prompt,*_):
            self.assertIn('OUTPUT LANGUAGE:',system);self.assertIn('English',system)
            self.assertIn('原始中文目标 must remain unchanged',prompt)
            return 'An English response from the local mock.'
        with patch('app.assistant.api_complete',side_effect=complete):
            chat.send({**data,'context_sha256':chat.preview(data)['sha256']})
            result=self.wait_chat(pid)
        self.assertEqual(result['messages'][-1]['text'],'An English response from the local mock.')

    def test_stage_language_is_captured_and_existing_input_is_not_translated(self):
        self.store.save_settings({'mode':'api','api_key':'mock-only','api_model':'mock'})
        self.store.save_language({'ui_language':'ko'})
        def complete(settings,key,system,prompt,*_):
            self.assertIn('Korean',system);self.store.save_language({'ui_language':'en'})
            return '한국어 연구 계획. 원본 자료는 유지됩니다.'
        with patch('app.service.api_complete',side_effect=complete):
            self.service.start(self.p['id'],{'stage':'research','note':''})
            for _ in range(300):
                if not self.service.jobs:break
                time.sleep(.01)
        p=self.store.get(self.p['id']);self.assertEqual(p['outputs']['research']['output_language'],'ko')
        self.assertTrue(p['outputs']['research']['text'].startswith('한국어'))
        self.assertEqual(p['title'],'研究项目')

    def test_export_localizes_wrapper_and_preserves_report_and_word_language(self):
        p=self.store.get(self.p['id']);p['outputs']['research']={'text':'原文 report body unchanged','approved':False,'stale':False};self.store.put(p)
        self.store.save_language({'ui_language':'en'})
        raw=self.service.export(p['id'],'md')[1].decode('utf-8-sig')
        self.assertIn('Research exploration',raw);self.assertIn('原文 report body unchanged',raw);self.assertIn('# 研究项目',raw)
        for locale,font in [('ja','Yu Gothic'),('ko','Malgun Gothic'),('zh-TW','Microsoft JhengHei')]:
            with zipfile.ZipFile(io.BytesIO(make_docx('# unchanged\n原文',locale=locale))) as archive:
                styles=archive.read('word/styles.xml').decode()
                self.assertIn(font,styles);self.assertIn('w:val="'+locale+'"',styles)

    def test_navigation_questions_in_all_five_languages(self):
        from app.navigation import navigation_intent
        for question in ['我想绘图从哪里进入呢？','我想繪圖，從哪裡進入？','Where can I create a figure?','図を作成するには、どこから入ればよいですか？','그림을 그리려면 어디로 들어가야 하나요?']:
            with self.subTest(question=question):self.assertEqual(navigation_intent(question),'drawing')

    def test_http_locale_bundle_and_translated_errors_keep_projects_unchanged(self):
        server=LocalServer(self.service,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(base+'/locales.js') as response:self.assertTrue(response.read().startswith(b'window.ResearchLocales='))
            for locale in ['en','ja','ko','zh-TW']:
                req=Request(base+'/api/project',data=b'{}',headers={'X-Research-Token':server.token,'X-Research-Language':locale,'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as caught:urlopen(req)
                body=json.load(caught.exception)
                self.assertEqual(body['error'],tr('请填写项目名称。',locale))
            self.assertEqual(self.store.get(self.p['id']),self.p)
        finally:server.shutdown();server.server_close()


if __name__=='__main__':unittest.main()
