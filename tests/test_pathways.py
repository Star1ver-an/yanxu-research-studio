import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from app.data_tools import profile_csv,table_report
from app.routing import recommend,ROUTES
from app.scientific import catalog,manifest,skill_detail,method_context
from app.service import Service
from app.storage import Store
from app.server import LocalServer
from app.workflows import FLOWS,STAGES,ROOT


class RecommendationTests(unittest.TestCase):
    def test_explicit_goals(self):
        for goal,route,flow in [('proposal','ars','full'),('write','ars','write'),('review','ars','review'),
                ('screen','ars','screen'),('literature','scientific','scientific_evidence'),
                ('mechanism','scientific','scientific_design'),('data','scientific','scientific_data')]:
            with self.subTest(goal=goal):
                result=recommend(dict(title='TMEM43 在 IRI-AKI 中的作用',goal=goal,data_kind='none'))
                self.assertEqual((result['route'],result['flow']),(route,flow))
                self.assertTrue(result['reasons']);self.assertIn('alternative',result)
    def test_same_gene_topic_routes_by_material_and_goal(self):
        title='TMEM43 在 IRI-AKI 慢性转归中的作用与机制'
        proposal=recommend(dict(title=title,topic='尚无实验数据，先完成开题与机制研究方案'))
        data=recommend(dict(title=title,topic='已有表达矩阵，检查缺失值',data_kind='table'))
        self.assertEqual(proposal['route'],'ars');self.assertEqual(data['route'],'scientific')
    def test_no_data_and_negation_do_not_trigger_false_analysis(self):
        for text in ['暂无实验数据，不做数据分析，先完成开题','没有数据，暂不进行单细胞分析','No experimental data; prepare a proposal']:
            self.assertEqual(recommend(dict(title='TMEM43',topic=text))['route'],'ars')
    def test_unclear_title_is_marked_as_needing_information(self):
        result=recommend(dict(title='TMEM43 与肾损伤'))
        self.assertEqual(result['confidence'],'需要补充信息')
    def test_writing_with_data_can_suggest_complementary_analysis(self):
        result=recommend(dict(title='课题',goal='write',data_kind='omics'))
        self.assertEqual(result['route'],'ars');self.assertTrue(result['notes'])
    def test_invalid_or_empty_input(self):
        for data in [{},dict(title='x',goal='bad'),dict(title='x',data_kind='bad'),dict(title='x'*141)]:
            with self.assertRaises(ValueError):recommend(data)
    def test_complete_workflow_is_not_mistaken_for_existing_manuscript_review(self):
        data=dict(title='RAG 回答可靠性评估',topic='暂无实验数据，完成开题与完整研究方案，走完提纲、核验、模拟审阅、返修与定稿。')
        self.assertEqual(recommend(data)['flow'],'full')
        self.assertEqual(recommend({**data,'goal':'review'})['flow'],'review')
        self.assertEqual(recommend(dict(title='RAG',topic='已有稿件，需要模拟审阅和返修'))['flow'],'review')
        self.assertEqual(recommend(dict(title='边缘设备评测',goal='data',data_kind='table',topic='先检查 CSV，再走完整研究流程'))['flow'],'scientific_data')


class SourceTests(unittest.TestCase):
    def test_both_routes_have_complete_stage_definitions(self):
        for route,entry in ROUTES.items():
            for flow in entry['flows']:
                self.assertEqual(FLOWS[flow]['route'],route)
                self.assertTrue(all(stage in STAGES for stage in FLOWS[flow]['stages']))
    def test_catalog_and_bundled_file_hashes(self):
        data=manifest();self.assertEqual(len(catalog()['skills']),177)
        self.assertEqual(len({x['id'] for x in data['skills']}),177)
        for item in data['files']:
            path=ROOT/'resources'/'scientific'/item['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'],item['path'])
    def test_skill_selection_is_allowlisted_and_source_is_pinned(self):
        entry=skill_detail('scanpy')
        self.assertIn(manifest()['commit'],entry['source'])
        self.assertIn('Scanpy',entry['text'])
        for sid in ['../LICENSE.md','missing','/etc/passwd']:
            with self.assertRaises(ValueError):skill_detail(sid)
    def test_methods_are_from_the_selected_project(self):
        text=method_context(STAGES['kd_task'],'pydeseq2')
        self.assertIn('PyDESeq2',text);self.assertNotIn('Claude Code 原生',text)


class DataTests(unittest.TestCase):
    def test_profile_computes_actual_values(self):
        p=profile_csv('data.csv','sample,group,value\ns1,A,1\ns2,A,2\ns3,B,3\ns4,B,NA\n')
        self.assertEqual(p['rows'],4)
        v=p['fields'][2];self.assertEqual(v['missing'],1);self.assertEqual(v['mean'],2);self.assertEqual(v['sd'],1)
        self.assertEqual(p['fields'][1]['kind'],'text')
    def test_tabular_edge_cases(self):
        p=profile_csv('data.tsv','\ufeffid\tx\n1\t1\n1\t1\n2\tInf\n')
        self.assertEqual(p['duplicate_rows'],1);self.assertEqual(p['fields'][1]['nonfinite'],1)
        self.assertEqual(p['fields'][1]['kind'],'mixed')
        json.dumps(profile_csv('data.csv','x\n1.7e308\n-1.7e308\n'),allow_nan=False)
    def test_invalid_tables_fail_without_silent_truncation(self):
        for text in ['a,a\n1,2','a,\n1,2','a,b\n1,2,3','a\n','a\n"unterminated']:
            with self.assertRaises(ValueError):profile_csv('data.csv',text)
    def test_quality_issues_have_bounded_data_record_positions(self):
        text='id,accuracy,latency\na,0.9,20\nb,not_measured,\na,0.9,20\nc,NaN,22\n'
        p=profile_csv('edge.csv',text)
        self.assertEqual(p['duplicate_examples'],[{'row':3,'first_row':1}])
        self.assertEqual(p['fields'][1]['locations'],{'missing':[],'nonnumeric':[2],'nonfinite':[4]})
        self.assertEqual(p['fields'][2]['locations']['missing'],[2])
        report,_=table_report([dict(name='edge.csv',text=text)])
        self.assertIn('accuracy · 非数值：数据行 2',report)
        self.assertIn('数据行 3 与数据行 1 相同',report)
        self.assertNotIn('DESeq2',report)
        many=profile_csv('many.csv','value\n'+''.join('NA\n' for _ in range(25)))
        self.assertEqual(many['fields'][0]['missing'],25)
        self.assertEqual(len(many['fields'][0]['locations']['missing']),20)
    def test_report_uses_only_selected_tables_and_no_model(self):
        report,execution=table_report([dict(name='data.csv',text='x\n1\n3',enabled=True),dict(name='bad.csv',text='bad',enabled=False)])
        self.assertIn('均值',report);self.assertEqual(execution['profiles'][0]['fields'][0]['mean'],2)
        self.assertFalse(execution['model_called']);self.assertFalse(execution['network'])
        from app.service import make_docx
        from docx import Document
        import io
        self.assertEqual(len(Document(io.BytesIO(make_docx(report))).tables),2)
        with self.assertRaises(ValueError):table_report([])


class ProjectRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name);self.service=Service(self.store)
    def tearDown(self):self.temp.cleanup()
    def test_old_project_route_is_inferred_without_migration(self):
        p=self.store.create(dict(title='原项目',flow='full'));p.pop('route');self.store.put(p)
        before=json.dumps(self.store.get(p['id']),ensure_ascii=False)
        self.assertEqual(FLOWS[self.service.public_project(p['id'])['flow']]['route'],'ars')
        self.assertEqual(before,json.dumps(self.store.get(p['id']),ensure_ascii=False))
    def test_mismatched_route_and_arbitrary_skill_are_rejected(self):
        for data in [dict(title='x',route='ars',flow='scientific_data'),
                dict(title='x',flow='scientific_task'),dict(title='x',flow='scientific_task',selected_skill='../oops'),
                dict(title='x',flow='full',selected_skill='scanpy')]:
            with self.assertRaises(ValueError):self.store.create(data)
    def test_recommended_choice_is_verified_and_recorded(self):
        data=dict(title='开题研究',goal='proposal',data_kind='none',route='ars',flow='full',route_choice='recommended')
        p=self.store.create(data);self.assertEqual(p['routing_recommendation']['route'],'ars')
        data.update(goal='data')
        with self.assertRaisesRegex(ValueError,'推荐'):self.store.create(data)
    def test_local_profile_has_run_evidence_and_preserves_stage_order(self):
        p=self.store.create(dict(title='CSV 分析',flow='scientific_data',route='scientific'));pid=p['id']
        self.service.upload(pid,dict(name='真实数据.csv',data=base64.b64encode(b'sample,value\na,1\nb,3').decode()))
        p=self.store.get(pid);p['cursor']=1;p['outputs']['kd_scope']=dict(text='任务已明确',approved=True,stale=False);self.store.put(p)
        with patch('app.service.CodexClient',side_effect=AssertionError('local stage must not invoke model')):
            self.service.start(pid,dict(stage='kd_data'))
            for _ in range(200):
                p=self.store.get(pid)
                if p['runs'][-1]['status']!='running':break
                time.sleep(.01)
        self.assertEqual(p['runs'][-1]['status'],'completed')
        self.assertEqual(p['runs'][-1]['mode'],'local')
        self.assertEqual(p['runs'][-1]['tool_execution']['profiles'][0]['fields'][1]['mean'],2)
        self.assertFalse(p['outputs']['kd_data']['approved']);self.assertEqual(p['cursor'],1)
    def test_missing_data_fails_truthfully(self):
        p=self.store.create(dict(title='无数据分析',flow='scientific_data'));p['cursor']=1;self.store.put(p)
        self.service.start(p['id'],dict(stage='kd_data'))
        for _ in range(100):
            p=self.store.get(p['id'])
            if p['runs'][-1]['status']!='running':break
            time.sleep(.01)
        self.assertEqual(p['runs'][-1]['status'],'failed');self.assertNotIn('kd_data',p['outputs'])
    def test_large_tables_stay_local_in_model_context(self):
        p=self.store.create(dict(title='分析',flow='scientific_data'));p['assets']=[dict(name='big.csv',text='private-value'*30000,enabled=True)]
        self.assertNotIn('private-value',self.service.context(p,'kd_scope',''))
    def test_scientific_run_loads_upstream_methods_and_records_its_source(self):
        captured=[]
        class Fake:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def complete(self,system,*args):captured.append(system);return '# 计划\n需要真实材料。'
        p=self.store.create(dict(title='机制',flow='scientific_design'))
        with patch('app.service.CodexClient',Fake):
            self.service.start(p['id'],dict(stage='kd_scope'))
            for _ in range(100):
                p=self.store.get(p['id'])
                if p['runs'][-1]['status']!='running':break
                time.sleep(.01)
        self.assertEqual(p['runs'][-1]['source_commit'],manifest()['commit'])
        self.assertIn('Scientific Agent Skills',captured[0]);self.assertIn('hypothesis-generation',captured[0])
    def test_http_routes_and_recommendation_have_real_endpoints(self):
        server=LocalServer(self.service);threading.Thread(target=server.serve_forever,daemon=True).start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            for path in ['/ars','/scientific','/pathways.js']:
                with urlopen(base+path) as response:self.assertEqual(response.status,200)
            request=Request(base+'/api/recommend',data=json.dumps(dict(title='课题',goal='data',data_kind='table')).encode(),headers={'X-Research-Token':server.token,'Content-Type':'application/json'})
            with urlopen(request) as response:self.assertEqual(json.load(response)['route'],'scientific')
        finally:server.shutdown();server.server_close()


class PubMedTests(unittest.TestCase):
    def test_ncbi_records_preserve_identity_and_start_unselected(self):
        from app.pubmed import retrieve
        xml=b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>TMEM43 <i>study</i></ArticleTitle><Journal><JournalIssue><PubDate><Year>2024</Year></PubDate><Issue>1</Issue></JournalIssue><Title>Journal</Title></Journal><Abstract><AbstractText Label="RESULTS">Available abstract.</AbstractText></Abstract><PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'''
        with patch('app.pubmed.fetch',side_effect=[json.dumps({'esearchresult':{'idlist':['123'],'count':'1'}}).encode(),xml]):
            entries,total=retrieve('TMEM43')
        self.assertEqual(total,1);self.assertEqual(entries[0]['pmid'],'123');self.assertFalse(entries[0]['enabled'])
        self.assertEqual(entries[0]['title'],'TMEM43 study');self.assertIn('RESULTS',entries[0]['abstract'])
    def test_network_or_invalid_payload_fails_explicitly(self):
        from app.pubmed import retrieve
        with patch('app.pubmed.fetch',return_value=b'bad'):
            with self.assertRaisesRegex(ValueError,'未保存不完整'):retrieve('test')


if __name__=='__main__':unittest.main()
