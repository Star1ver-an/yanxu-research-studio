import base64
import copy
import hashlib
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import zipfile

from app.storage import Store
from app.service import Service
from app.server import LocalServer
from app.research import bibliography, workspace


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.service=Service(self.store)
        self.pid=self.store.create({'title':'检索增强生成的回答可靠性评估','flow':'full','discipline':'人工智能'})['id']
        self.service.upload(self.pid,{'name':'rag-notes.txt','data':base64.b64encode('RAG evaluation requires a held-out test set. Repeated measurements record random seeds.\nThis is a synthetic software test fixture, not an experimental finding.'.encode()).decode()})
        self.asset=self.store.get(self.pid)['assets'][0]['id']

    def save(self,group,**data):
        self.service.research_save(self.pid,{'collection':group,'record':data})
        return self.store.get(self.pid)['research'][group][-1]

    def evidence(self):
        return self.save('evidence',title='独立测试集要求',asset_id=self.asset,page='1',quote='RAG evaluation requires a held-out test set.',level='direct',finding='这是测试笔记中的说明，不能当作真实研究结果。')

    def test_task_dependency_cycle_and_completion(self):
        a=self.save('tasks',title='定义评价指标')
        b=self.save('tasks',title='执行对照评测',depends_on=a['id'])
        self.assertEqual(self.service.public_project(self.pid)['research_status']['blocked'],1)
        with self.assertRaisesRegex(ValueError,'前置'):self.save('tasks',**{**b,'status':'done'})
        with self.assertRaisesRegex(ValueError,'循环'):self.save('tasks',**{**a,'depends_on':b['id']})
        self.save('tasks',**{**a,'status':'done'})
        self.save('tasks',**{**b,'status':'done'})
        with self.assertRaisesRegex(ValueError,'后置'):self.save('tasks',**{**a,'status':'todo'})

    def test_demo_is_explicit_computing_plan_without_model_runs(self):
        result=self.service.research_demo();p=self.store.get(result['id'])
        self.assertTrue(p['demo']);self.assertIn('RAG',p['title']);self.assertEqual(p['runs'],[])
        self.assertEqual(p['experiments'],'none');self.assertTrue(p['research']['checklist'])
        self.assertIn('虚构',p['assets'][0]['text']);self.assertFalse(p['outputs']['research']['approved'])

    def test_pdf_pages_and_authenticated_original(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter()
        for text in ['Synthetic AI research note. This is page one and contains no experimental result.',
                     'Page two: freeze evaluation rules before running the benchmark.']:
            page=writer.add_blank_page(width=595,height=842)
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject();stream.set_data(('BT /F1 10 Tf 40 780 Td ('+text+') Tj ET').encode())
            page[NameObject('/Contents')]=writer._add_object(stream)
        output=io.BytesIO();writer.write(output)
        self.service.upload(self.pid,{'name':'ai-fixture.pdf','data':base64.b64encode(output.getvalue()).decode()})
        aid=self.store.get(self.pid)['assets'][-1]['id']
        page=self.service.material_page(self.pid,{'id':aid,'page':2})
        self.assertEqual(page['count'],2);self.assertIn('Page two',page['text'])
        raw,mime=self.service.material_bytes(self.pid,aid);self.assertEqual(mime,'application/pdf');self.assertEqual(raw,output.getvalue())

    def test_evidence_quote_and_page_checked(self):
        result=self.evidence();self.assertTrue(result['quote_matched'])
        self.assertEqual(result['source_sha256'],self.store.get(self.pid)['assets'][0]['sha256'])
        for page,text in [('2','RAG'),('1','Invented result'),('1','...')]:
            with self.assertRaises(ValueError):self.save('evidence',title='bad',asset_id=self.asset,page=page,quote=text)

    def test_claims_need_evidence_and_explanation(self):
        with self.assertRaises(ValueError):self.save('claims',title='所有数据集均有效',assessment='supports')
        ev=self.evidence()
        claim=self.save('claims',title='评测方案需分离测试集',evidence_id=ev['id'],assessment='partial',reason='仅有测试笔记，还需原始论文',stage='research')
        self.assertEqual(claim['assessment'],'partial')
        with self.assertRaisesRegex(ValueError,'关联'):self.service.research_delete(self.pid,{'collection':'evidence','id':ev['id'],'acknowledge':True})

    def test_references_deduplicate_and_citation_export(self):
        ref=self.save('references',title='Example RAG Benchmark',authors='A. Researcher; B. Engineer',year='2026',doi='https://doi.org/10.1234/EXAMPLE')
        self.assertEqual(ref['doi'],'10.1234/example')
        with self.assertRaisesRegex(ValueError,'已在'):self.save('references',title='Different title',doi='10.1234/example')
        with self.assertRaises(ValueError):self.save('references',title='Bad URL',url='javascript:alert(1)')
        self.service.edit(self.pid,{'stage':'research','text':'A claim [@'+ref['id']+']'})
        md=self.service.export(self.pid,'md')[1].decode('utf-8-sig')
        self.assertIn('A claim [1]',md);self.assertIn('参考文献',md);self.assertIn('10.1234/example',md)
        with self.assertRaisesRegex(ValueError,'引用'):self.service.research_delete(self.pid,{'collection':'references','id':ref['id'],'acknowledge':True})
        self.service.research_style(self.pid,{'style':'author-year'})
        self.assertIn('[A. Researcher, 2026]',self.service.export(self.pid,'md')[1].decode('utf-8-sig'))

    def test_csl_roundtrip_and_ris_atomic_import(self):
        csl=[{'title':'RAG Reference','author':[{'given':'Ada','family':'Example'}],'DOI':'10.1234/rag','issued':{'date-parts':[[2026]]}}]
        result=self.service.reference_import(self.pid,{'format':'csl','text':json.dumps(csl)})
        self.assertEqual(result['added'],1)
        self.assertEqual(self.service.reference_import(self.pid,{'format':'csl','text':json.dumps(csl)})['skipped'],1)
        self.assertEqual(json.loads(self.service.research_export(self.pid,'csl')[1])[0]['title'],'RAG Reference')
        ris='TY  - CONF\nTI  - Software Benchmark\nAU  - Example, Ada\nPY  - 2025\nER  - \n'
        self.assertEqual(self.service.reference_import(self.pid,{'format':'ris','text':ris})['added'],1)
        before=copy.deepcopy(self.store.get(self.pid))
        with self.assertRaises(ValueError):self.service.reference_import(self.pid,{'format':'csl','text':json.dumps([{'title':'Valid first'},{'title':'Invalid','DOI':'not doi'}])})
        self.assertEqual(self.store.get(self.pid),before)

    def test_verification_is_metadata_only_and_failure_does_not_verify(self):
        ref=self.save('references',title='RAG Reference',doi='10.1234/rag')
        response=io.BytesIO(json.dumps({'message':{'title':['RAG Reference'],'update-to':[]}}).encode())
        with patch('app.research.urlopen',return_value=response):self.service.reference_verify(self.pid,{'id':ref['id']})
        verified=self.store.get(self.pid)['research']['references'][0]['verification']
        self.assertTrue(verified['title_match']);self.assertIn('不证明',verified['meaning'])
        self.save('references',**{**ref,'title':'Changed title'})
        self.assertNotIn('verification',self.store.get(self.pid)['research']['references'][0])
        with patch('app.research.urlopen',side_effect=OSError('offline')):
            with self.assertRaises(ValueError):self.service.reference_verify(self.pid,{'id':ref['id']})
        self.assertNotIn('verification',self.store.get(self.pid)['research']['references'][0])

    def test_experiment_completion_needs_real_material_and_figure_detects_design_change(self):
        with self.assertRaises(ValueError):self.save('experiments',title='RAG 对比实验',status='completed')
        exp=self.save('experiments',title='RAG 对比实验',status='planned',baseline='无检索模型')
        figure=self.save('figures',title='图1 评测结果',asset_id=self.asset,experiment_id=exp['id'])
        self.assertEqual(figure['status'],'draft')
        self.save('experiments',**{**exp,'baseline':'固定检索基线'})
        self.assertTrue(any('图表需' in w for w in self.service.public_project(self.pid)['research_status']['warnings']))
        with self.assertRaises(ValueError):self.save('figures',**{**figure,'status':'reviewed'})

    def test_issues_checklists_and_exports_keep_unresolved_items(self):
        self.service.issues_import(self.pid,{'text':'补充基线比较\n说明数据划分','source':'导师反馈'})
        rows=self.store.get(self.pid)['research']['issues'];self.assertEqual(len(rows),2)
        with self.assertRaises(ValueError):self.save('issues',**{**rows[0],'status':'done'})
        self.save('issues',**{**rows[0],'response':'已补充方案，实验尚未执行','location':'方案第3节','status':'done'})
        self.service.research_seed(self.pid,{'kind':'ml'})
        checks=self.store.get(self.pid)['research']['checklist'];self.assertTrue(checks)
        self.service.research_seed(self.pid,{'kind':'ml'})
        self.assertEqual(len(self.store.get(self.pid)['research']['checklist']),len(checks))
        with self.assertRaises(ValueError):self.save('checklist',**{**checks[0],'status':'done'})
        self.assertIn('说明数据划分',self.service.research_export(self.pid,'responses')[1].decode('utf-8-sig'))

    def test_version_restore_keeps_current_and_invalidates_downstream(self):
        self.service.edit(self.pid,{'stage':'research','text':'Original draft'})
        p=self.store.get(self.pid);p['outputs']['outline']={'text':'Outline','approved':True};p['cursor']=1;self.store.put(p)
        self.service.edit(self.pid,{'stage':'research','text':'Edited draft'})
        p=self.store.get(self.pid);v=p['versions'][0]
        self.assertNotIn('text',self.service.public_project(self.pid)['versions'][0])
        comparison=self.service.version_compare(self.pid,{'id':v['id']});self.assertIn('-Original draft',comparison['diff'])
        with self.assertRaises(ValueError):self.service.version_restore(self.pid,{'id':v['id']})
        self.service.version_restore(self.pid,{'id':v['id'],'acknowledge':True})
        p=self.store.get(self.pid);self.assertEqual(p['outputs']['research']['text'],'Original draft')
        self.assertTrue(p['outputs']['outline']['stale']);self.assertFalse(p['outputs']['research']['approved'])
        self.assertTrue(any(v['text']=='Edited draft' for v in p['versions']))

    def test_context_is_explicit_and_preview_does_not_call_model(self):
        self.save('experiments',title='RAG计划',baseline='保持相同基础模型')
        p=self.store.get(self.pid);self.assertNotIn('RAG计划',self.service.context(p,'research',''))
        self.service.research_context(self.pid,{'enabled':True})
        preview=self.service.run_preview(self.pid,{'stage':'research'})
        self.assertEqual(preview['calls'],1);self.assertTrue(preview['research_enabled'])
        self.assertEqual(len(preview['sha256']),64);self.assertEqual(self.store.get(self.pid)['runs'],[])
        with patch.object(self.store,'get_settings',return_value={'mode':'api','provider':'compatible','api_model':'test','api_key':''}):
            with self.assertRaisesRegex(ValueError,'预览后'):self.service.start(self.pid,{'stage':'research','context_sha256':'bad'})

    def test_running_and_archived_records_cannot_be_changed(self):
        p=self.store.get(self.pid);p['lifecycle']='archived';self.store.put(p)
        with self.assertRaises(ValueError):self.save('tasks',title='Blocked')
        self.assertTrue(self.service.material_page(self.pid,{'id':self.asset})['text'])

    def test_duplicate_remaps_research_materials(self):
        self.evidence()
        result=self.service.manage_projects({'action':'duplicate','ids':[self.pid]})
        duplicate=self.store.get(result['created_ids'][0])
        self.assertEqual(duplicate['research']['evidence'][0]['asset_id'],duplicate['assets'][0]['id'])
        self.assertNotEqual(duplicate['assets'][0]['id'],self.asset)

    def test_whole_workspace_backup_restore_is_independent_without_keys(self):
        self.evidence();self.service.edit(self.pid,{'stage':'research','text':'RAG plan'})
        with self.store.connect() as c:c.execute('INSERT OR REPLACE INTO settings VALUES(1,?)',(json.dumps({'encrypted_key':'DO_NOT_EXPORT'}),))
        state=self.service.backups.create();path=self.service.backups.path(state['items'][0]['id']);raw=path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:self.assertNotIn('DO_NOT_EXPORT',z.read('manifest.json').decode())
        preview=self.service.backups.restore(raw,preview=True);self.assertEqual(len(preview['projects']),1);self.assertEqual(len(self.store.list()),1)
        result=self.service.backups.restore(raw);new=self.store.get(result['created_ids'][0])
        self.assertNotEqual(new['id'],self.pid);self.assertFalse(new['outputs']['research']['approved'])
        self.assertEqual(new['research']['evidence'][0]['asset_id'],new['assets'][0]['id'])
        self.assertEqual(self.store.get(self.pid)['title'],'检索增强生成的回答可靠性评估')

    def test_legacy_project_zip_restores_and_material_corruption_is_atomic(self):
        raw=self.service.export(self.pid,'zip')[1]
        self.assertEqual(self.service.backups.restore(raw)['count'],1)
        out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as original,zipfile.ZipFile(out,'w') as changed:
            for item in original.infolist():changed.writestr(item.filename,b'corrupted' if item.filename.startswith('materials/') else original.read(item))
        before=self.store.list()
        with self.assertRaisesRegex(ValueError,'校验'):self.service.backups.restore(out.getvalue())
        self.assertEqual(self.store.list(),before)

    def test_zip_traversal_duplicates_and_oversize_are_rejected(self):
        for name in ['../outside','/absolute','materials\\escape','C:/escape']:
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as archive:archive.writestr(name,'x')
            with self.assertRaises(ValueError):self.service.backups.restore(out.getvalue())
        with self.assertRaises(ValueError):self.service.backups.path('../workspace.sqlite3')

    def test_restore_file_failure_rolls_back_all_records_and_materials(self):
        raw=self.service.export(self.pid,'zip')[1];before=len(self.store.list())
        with patch('pathlib.Path.write_bytes',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.service.backups.restore(raw)
        self.assertEqual(len(self.store.list()),before)
        self.assertEqual(len(list((self.store.root/'materials').iterdir())),1)

    def test_auto_backups_skip_unchanged_and_retain_bound(self):
        self.service.backups.configure({'enabled':True,'retain':3})
        for index in range(5):
            self.service.update_info(self.pid,{'title':'RAG project '+str(index)})
            self.service.backups.create(automatic=True)
        self.assertEqual(len(self.service.backups.status()['items']),3)
        self.service.backups.create(automatic=True)
        self.assertEqual(len(self.service.backups.status()['items']),3)

    def test_auto_backup_worker_finishes_before_workspace_cleanup(self):
        self.service.backups.start();self.service.backups.close()
        self.assertFalse(self.service.backups.worker.is_alive())
        self.assertTrue(self.service.backups.stop.is_set())

    def test_api_authorization_and_preview_import(self):
        self.service.backups.configure({'enabled':False,'retain':3})
        server=LocalServer(self.service);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
        with self.assertRaises(HTTPError):urlopen(base+'/api/backups')
        raw=self.service.export(self.pid,'zip')[1]
        request=Request(base+'/api/backups/import?preview=1',data=raw,headers={'X-Research-Token':server.token,'Content-Type':'application/zip'})
        self.assertEqual(len(json.load(urlopen(request))['projects']),1)
        self.assertEqual(len(self.store.list()),1)
        with self.assertRaises(HTTPError):urlopen(Request(base+'/api/backups/import',data=raw,headers={'X-Research-Token':server.token}))
        for endpoint,data in [('research-seed',{'kind':'tasks'}),('material-page',{'id':self.asset}),('run-preview',{'stage':'research'})]:
            self.assertEqual(urlopen(Request(base+'/api/project/'+self.pid+'/'+endpoint,data=json.dumps(data).encode(),headers={'X-Research-Token':server.token})).status,200)


if __name__=='__main__':unittest.main()
