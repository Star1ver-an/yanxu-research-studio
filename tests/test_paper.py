"""M4 real-render, transactional, provenance and Word acceptance with synthetic AI data."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET
import zipfile

from app.service import Service
from app.storage import Store
from app.paper_figures import validate_plan, paper_basis, paper_audit
from app.paper_engine import remap_spec
from app.plot_engine import validate_spec


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=Service(Store(self.temp.name));self.store=self.service.store
        self.service.backups.configure({'enabled':False,'retain':3})
        self.pid=self.store.create({'title':'合成验收 推理系统论文图表','flow':'full'})['id'];self.gates=[]
        self.aid=self.upload('latency.csv',b'epoch,loss,latency,model\n1,0.9,18,A\n2,0.6,15,A\n1,0.8,20,B\n2,0.5,17,B\n')
        self.spec={'version':1,'kind':'line','title':'合成训练记录','source_ids':[self.aid],
            'mapping':{'x':'epoch','y':'loss','group':'model'},'style':{'width':6,'height':4,'dpi':85,'font_size':10},'options':{}}
        self.first=self.save(self.spec)
        second=copy.deepcopy(self.spec);second['title']='合成时延记录';second['mapping']['y']='latency';self.second=self.save(second)

    def tearDown(self):
        for gate in self.gates:gate.set()
        end=time.monotonic()+6
        while self.service.paper_busy() and time.monotonic()<end:time.sleep(.02)
        self.service.close_plotting();self.service.assistant.close();self.service.backups.close();self.temp.cleanup()

    def upload(self,name,raw):
        self.service.upload(self.pid,{'name':name,'data':base64.b64encode(raw).decode()})
        return self.store.get(self.pid)['assets'][-1]['id']

    def save(self,spec,**extra):
        p=self.service.plot_save(self.pid,{'spec':spec,'caption':'合成数据用于软件验收，未进行真实训练。',**extra})
        return next(f for f in p['plotting']['figures'] if f['id']==extra['id']) if extra.get('id') else p['plotting']['figures'][-1]

    def config(self,ids=None,**extra):
        return {'figure_ids':ids or [self.first['id'],self.second['id']],'action':'style','style':{'palette':'gray','font_size':11},**extra}

    def wait(self,job):
        end=time.monotonic()+30
        while time.monotonic()<end:
            value=self.service.paper_job(self.pid,{'id':job['id']})
            if value['status'] not in {'queued','running','cancelling'}:return value
            time.sleep(.02)
        self.fail('Paper job did not terminate')

    def start(self,data):
        preview=self.service.paper_batch_preview(self.pid,data)
        return self.service.paper_batch_start(self.pid,{**data,'etag':preview['etag'],'request_id':uuid.uuid4().hex})

    def compose(self,**extra):
        result=self.service.paper_compose(self.pid,{'figure_ids':[self.first['id'],self.second['id']],
            'style':{'width':7.2,'height':5,'dpi':90,'font_size':9},**extra})
        return self.save(result['spec'])

    def plan(self):
        value=self.service.paper_state(self.pid);plan=value['plan']
        for entry in plan['entries']:entry['caption_reviewed']=True
        plan['requirements']['min_dpi']=72
        return self.service.paper_save(self.pid,{'plan':plan,'expected_revision':value['revision']})

    def test_plan_backwards_compatibility_and_revision_conflict(self):
        original=self.store.get(self.pid);value=self.service.paper_state(self.pid)
        self.assertEqual(len(value['plan']['entries']),2);self.assertNotIn('paper',self.store.get(self.pid)['plotting'])
        changed=copy.deepcopy(value['plan']);changed['entries'].reverse();self.service.paper_save(self.pid,{'plan':changed,'expected_revision':0})
        with self.assertRaisesRegex(ValueError,'变化'):self.service.paper_save(self.pid,{'plan':changed,'expected_revision':0})
        self.assertEqual(self.store.get(self.pid)['outputs'],original['outputs'])

    def test_plan_validation_rejects_unknown_nonfinite_duplicate_and_control(self):
        plan=self.service.paper_state(self.pid)['plan']
        for mutate in [lambda p:p.update(path='C:/secret'),lambda p:p['entries'].append(p['entries'][0]),
                       lambda p:p['requirements'].update(min_dpi=float('nan')),lambda p:p.update(title='bad\x00title'),
                       lambda p:p['requirements'].update(formats=['exe']),lambda p:p['entries'][0].update(include='true')]:
            value=copy.deepcopy(plan);mutate(value)
            with self.assertRaises(ValueError):validate_plan(value)

    def test_real_composition_formats_unique_svg_ids_and_fixed_children(self):
        row=self.compose(shared_legend=True)
        for fmt,magic in [('png',b'\x89PNG'),('svg',b'<?xml'),('pdf',b'%PDF'),('tiff',b'II')]:
            _,raw,_=self.service.plot_export(self.pid,{'id':row['id'],'format':fmt});self.assertTrue(raw.startswith(magic),fmt)
            if fmt=='svg':
                root=ET.fromstring(raw);ids=[e.attrib['id'] for e in root.iter() if 'id' in e.attrib]
                self.assertEqual(len(ids),len(set(ids)));self.assertGreater(len(list(root.iter('{http://www.w3.org/2000/svg}path'))),20)
        self.assertGreaterEqual(row['metadata']['effective_min_font_pt'],7)
        self.assertEqual(row['spec']['composition']['items'][0]['revision'],1)
        self.assertEqual(row['metadata']['panel_count'],2)
        self.assertEqual(self.store.get(self.pid)['assets'][0]['sha256'],hashlib.sha256((self.store.root/self.store.get(self.pid)['assets'][0]['file']).read_bytes()).hexdigest())

    def test_composition_refuses_nesting_cross_project_and_wrong_legend(self):
        row=self.compose()
        with self.assertRaisesRegex(ValueError,'嵌套'):self.service.paper_compose(self.pid,{'figure_ids':[row['id']]})
        with self.assertRaises(ValueError):self.service.paper_compose(self.pid,{'figure_ids':[uuid.uuid4().hex]})
        bad=copy.deepcopy(row['spec']);bad['composition']['shared_legend']=True;bad['composition']['items'][1]['spec']['style']['palette']='gray'
        with self.assertRaisesRegex(ValueError,'不能合并'):self.service.plot_render(self.pid,{'spec':bad})
        bad=copy.deepcopy(row['spec']);bad['source_ids']=[]
        with self.assertRaisesRegex(ValueError,'并集'):validate_spec(bad)

    def test_child_update_is_located_and_refresh_preserves_old_snapshot(self):
        board=self.compose();self.plan();new=copy.deepcopy(self.first['spec']);new['title']='新标题';self.save(new,id=self.first['id'],expected_revision=1)
        audit=self.service.paper_state(self.pid)['audit'];self.assertTrue(any(i['kind']=='child_changed' and i['figure_id']==board['id'] for i in audit['issues']))
        job=self.start(self.config([board['id']],action='refresh',style={}));self.assertEqual(self.wait(job)['status'],'completed')
        updated=self.service._plot_row(self.store.get(self.pid),board['id']);self.assertEqual(updated['spec']['composition']['items'][0]['revision'],2)
        self.assertEqual(updated['history'][-1]['spec']['composition']['items'][0]['revision'],1)

    def test_atomic_batch_style_preserves_analysis_inputs_and_history(self):
        board=self.compose();ids=[self.first['id'],self.second['id'],board['id']]
        data=self.config(ids);preview=self.service.paper_batch_preview(self.pid,data);self.assertEqual(len(preview['updates']),3)
        value=self.wait(self.start(data));self.assertEqual(value['status'],'completed',value)
        p=self.store.get(self.pid)
        for row in p['plotting']['figures']:
            self.assertEqual(row['revision'],2);self.assertEqual(row['spec']['style']['palette'],'gray');self.assertEqual(len(row['history']),1)
        self.assertEqual(p['plotting']['figures'][0]['spec']['mapping'],self.first['spec']['mapping'])
        board=p['plotting']['figures'][2];self.assertEqual(board['spec']['composition']['items'][0]['revision'],2)
        self.assertFalse(any(i['kind']=='child_changed' for i in paper_audit(p)['issues']))

    def test_failure_on_second_render_saves_no_partial_updates(self):
        before=copy.deepcopy(self.store.get(self.pid)['plotting']);real=self.service._plot_render;count=0
        def failing(*args,**kwargs):
            nonlocal count
            count+=1
            if count==2:raise ValueError('Synthetic render failure')
            return real(*args,**kwargs)
        with patch.object(self.service,'_plot_render',side_effect=failing):value=self.wait(self.start(self.config()))
        self.assertEqual(value['status'],'failed');self.assertEqual(self.store.get(self.pid)['plotting'],before)

    def test_cancellation_during_render_saves_no_partial_updates(self):
        entered=threading.Event();gate=threading.Event();self.gates.append(gate);before=copy.deepcopy(self.store.get(self.pid)['plotting']);real=self.service._plot_render
        def paused(*args,**kwargs):entered.set();gate.wait(5);return real(*args,**kwargs)
        with patch.object(self.service,'_plot_render',side_effect=paused):
            job=self.start(self.config());self.assertTrue(entered.wait(5));self.service.paper_cancel(self.pid,{'id':job['id']});gate.set();value=self.wait(job)
        self.assertEqual(value['status'],'cancelled');self.assertEqual(self.store.get(self.pid)['plotting'],before)

    def test_late_research_edit_conflicts_instead_of_overwriting(self):
        entered=threading.Event();gate=threading.Event();self.gates.append(gate);real=self.service._plot_render
        def paused(*args,**kwargs):entered.set();gate.wait(5);return real(*args,**kwargs)
        with patch.object(self.service,'_plot_render',side_effect=paused):
            job=self.start(self.config());self.assertTrue(entered.wait(5));p=self.store.get(self.pid);p['outputs']['research']={'text':'New manuscript','approved':False};self.store.put(p);gate.set();value=self.wait(job)
        self.assertEqual(value['status'],'failed');self.assertEqual(self.store.get(self.pid)['plotting']['figures'][0]['revision'],1)
        self.assertIn('New manuscript',self.store.get(self.pid)['outputs']['research']['text'])

    def test_idempotent_receipt_and_project_isolation(self):
        data=self.config();preview=self.service.paper_batch_preview(self.pid,data);request={**data,'etag':preview['etag'],'request_id':uuid.uuid4().hex}
        job=self.service.paper_batch_start(self.pid,request);self.wait(job)
        repeated=self.service.paper_batch_start(self.pid,request);self.assertEqual(repeated['id'],job['id'])
        self.assertEqual(self.store.get(self.pid)['plotting']['figures'][0]['revision'],2)
        other=self.store.create({'title':'Other project','flow':'full'})['id']
        with self.assertRaises(ValueError):self.service.paper_job(other,{'id':job['id']})
        with self.assertRaises(ValueError):self.service.paper_batch_start(self.pid,{**request,'style':{'palette':'muted'}})

    def test_manuscript_links_changes_missing_and_review(self):
        p=self.store.get(self.pid);p['outputs']['research']={'text':'# System plan\n[figure:'+self.first['id']+']','approved':False};self.store.put(p)
        value=self.plan();self.service.paper_review(self.pid,{'etag':value['etag']})
        p=self.store.get(self.pid);p['outputs']['research']['text']+='\nA revised hypothesis.';self.store.put(p)
        audit=self.service.paper_state(self.pid)['audit'];self.assertTrue(any(i['kind']=='manuscript_changed' for i in audit['issues']))
        p=self.store.get(self.pid);p['outputs']['research']['text']+='\n[figure:missing]';self.store.put(p)
        value=self.service.paper_state(self.pid);self.assertGreater(value['audit']['errors'],0)
        with self.assertRaisesRegex(ValueError,'缺失'):self.service.paper_review(self.pid,{'etag':value['etag']})

    def test_source_replacement_preserves_old_material_and_does_not_guess_columns(self):
        new=self.upload('new.csv',b'epoch,loss,latency,model\n1,0.7,13,A\n2,0.3,12,A\n1,0.8,14,B\n2,0.2,11,B\n')
        data=self.config(action='sources',style={},source_map={self.aid:new});value=self.wait(self.start(data));self.assertEqual(value['status'],'completed')
        row=self.store.get(self.pid)['plotting']['figures'][0];self.assertEqual(row['spec']['source_ids'],[new]);self.assertEqual(row['history'][-1]['spec']['source_ids'],[self.aid]);self.assertEqual(len(self.store.get(self.pid)['assets']),2)
        bad=self.upload('bad.csv',b'wrong,value\na,2\n')
        before=copy.deepcopy(self.store.get(self.pid)['plotting']);value=self.wait(self.start(self.config(action='sources',style={},source_map={new:bad})))
        self.assertEqual(value['status'],'failed');self.assertEqual(self.store.get(self.pid)['plotting'],before)

    def test_graphical_abstract_is_editable_vector_and_explicitly_a_plan(self):
        spec=self.service.paper_abstract(self.pid,{})['spec'];row=self.save(spec)
        _,raw,_=self.service.plot_export(self.pid,{'id':row['id'],'format':'svg'});self.assertIn(b'<path',raw)
        self.assertEqual(row['metadata']['abstract_status'],'plan');self.assertFalse(row['metadata']['model_called'])
        bad=copy.deepcopy(spec);bad['abstract']['sections'][0]['nodes']=['m']
        with self.assertRaises(ValueError):validate_spec(bad)
        bad=copy.deepcopy(spec);bad['diagram']['edges']=[{'from':'q','to':'q'}]
        with self.assertRaisesRegex(ValueError,'不同'):validate_spec(bad)

    def test_plan_and_composition_survive_copy_and_backup(self):
        board=self.compose();self.plan();result=self.service.manage_projects({'ids':[self.pid],'action':'duplicate','mode':'reports'})
        copied=self.store.get(result['created_ids'][0]);self.assertEqual(len(copied['plotting']['paper']['plan']['entries']),3)
        child=copied['plotting']['figures'][2]['spec']['composition']['items'][0]['spec'];self.assertNotIn(self.aid,child['source_ids'])
        self.service.plot_export(copied['id'],{'id':board['id'],'format':'svg'})
        _,blob,_=self.service.export(self.pid,'zip');preview=self.service.backups.restore(blob,preview=True);self.assertEqual(len(preview['projects']),1)
        restored=self.service.backups.restore(blob);self.assertTrue(restored['created_ids'])

    def test_delivery_has_consistent_word_numbering_and_no_raw_by_default(self):
        board=self.compose();value=self.plan();plan=value['plan'];plan['entries'].reverse();plan['requirements']['formats']=['png','svg','pdf']
        value=self.service.paper_save(self.pid,{'plan':plan,'expected_revision':value['revision']})
        job=self.service.paper_export_start(self.pid,{'etag':value['etag'],'allow_warnings':True,'request_id':uuid.uuid4().hex})
        value=self.wait(job);self.assertEqual(value['status'],'completed',value)
        _,raw,_=self.service.paper_download(self.pid,{'id':job['id']})
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names=z.namelist();self.assertIn('论文与图表.docx',names);self.assertFalse(any(n.startswith('sources/') for n in names))
            manifest=json.loads(z.read('manifest.json'));self.assertEqual(manifest['figures'][0]['figure_id'],board['id']);self.assertEqual(manifest['figures'][0]['number'],1)
            self.assertIn('panels/Fig01-A.svg',names);self.assertIn('panels/Fig01-A.pdf',names)
            with zipfile.ZipFile(io.BytesIO(z.read('论文与图表.docx'))) as doc:
                xml=doc.read('word/document.xml').decode();self.assertIn('图 1',xml);self.assertIn('清单图 1',xml)
        old_count=len(self.store.get(self.pid)['assets']);self.service.paper_clean_deliveries(self.pid,{})
        with self.assertRaisesRegex(ValueError,'已清理'):self.service.paper_download(self.pid,{'id':job['id']})
        self.assertEqual(len(self.store.get(self.pid)['assets']),old_count)

    def test_analysis_frozen_inside_composition_survives_gc_and_cross_store_restore(self):
        from app import analysis
        from app.analysis_jobs import cache_key
        matrix=self.upload('model-features.csv',b'sample,loss,latency\nr1,0.3,18\nr2,0.2,12\nr3,0.6,10\nr4,0.1,8\n')
        config=analysis.validate({'method':'expression_pca','inputs':{'data':matrix},
            'columns':{'features':['loss','latency']},'parameters':{'scale':True}})
        sources=self.service._plot_sources(self.store.get(self.pid),[matrix]);result=analysis.compute(config,sources)
        result['_yanxu']={'config_key':cache_key(config,sources),'engine_version':analysis.VERSION}
        raw=analysis.checked_result(result);sha=hashlib.sha256(raw).hexdigest();(self.service.analysis_cache/(sha+'.json')).write_bytes(raw)
        single=self.save({'version':1,'kind':'analysis','title':'合成模型特征 PCA','source_ids':[matrix],
            'style':{'width':6,'height':4,'dpi':90},'mapping':{},'options':{},'analysis':config,'analysis_result':sha})
        spec=self.service.paper_compose(self.pid,{'figure_ids':[single['id'],self.first['id']],
            'style':{'width':7.2,'height':5,'dpi':90,'font_size':9}})['spec'];board=self.save(spec)
        # Remove the standalone analysis figure; only the combination now owns its frozen result.
        self.service.plot_delete(self.pid,{'ids':[single['id']]})
        self.service.analysis_clean_cache();self.assertTrue((self.service.analysis_cache/(sha+'.json')).exists())
        _,bundle,_=self.service.export(self.pid,'zip')
        with tempfile.TemporaryDirectory() as folder:
            other=Service(Store(folder))
            try:
                restored=other.backups.restore(bundle)['created_ids'][0]
                blob=other.plot_export(restored,{'id':board['id'],'format':'svg'})[1];self.assertIn(b'<path',blob)
            finally:other.close_plotting();other.assistant.close();other.backups.close()

    def test_changed_manuscript_invalidates_previously_completed_delivery(self):
        value=self.plan();plan=value['plan'];plan['requirements']['formats']=['png']
        value=self.service.paper_save(self.pid,{'plan':plan,'expected_revision':value['revision']})
        job=self.wait(self.service.paper_export_start(self.pid,{'etag':value['etag'],'allow_warnings':True,'request_id':uuid.uuid4().hex}))
        self.assertFalse(job['stale']);self.service.edit(self.pid,{'stage':'draft','text':'合成验收正文已修改。'})
        self.assertTrue(self.service.paper_job(self.pid,{'id':job['id']})['stale'])
        with self.assertRaisesRegex(ValueError,'重新生成'):self.service.paper_download(self.pid,{'id':job['id']})

    def test_caption_review_tracks_content_but_survives_style_only_updates(self):
        self.plan();self.assertTrue(self.service.paper_state(self.pid)['plan']['entries'][0]['caption_reviewed'])
        self.assertEqual(self.wait(self.start(self.config()))['status'],'completed')
        self.assertTrue(self.service.paper_state(self.pid)['plan']['entries'][0]['caption_reviewed'])
        row=self.store.get(self.pid)['plotting']['figures'][0];spec=copy.deepcopy(row['spec']);spec['mapping']['y']='latency'
        self.save(spec,id=row['id'],expected_revision=row['revision'])
        self.assertFalse(self.service.paper_state(self.pid)['plan']['entries'][0]['caption_reviewed'])

    def test_active_job_is_recoverable_after_browser_refresh_and_blocks_management(self):
        entered=threading.Event();gate=threading.Event();self.gates.append(gate);real=self.service._plot_render
        def paused(*args,**kwargs):entered.set();gate.wait(5);return real(*args,**kwargs)
        with patch.object(self.service,'_plot_render',side_effect=paused):
            job=self.start(self.config());self.assertTrue(entered.wait(5));self.assertEqual(self.service.paper_state(self.pid)['job']['id'],job['id'])
            with self.assertRaises(ValueError):self.service.manage_projects({'ids':[self.pid],'action':'archive'})
            self.service.paper_cancel(self.pid,{'id':job['id']});gate.set();self.wait(job)

    def test_abstract_ai_cannot_promote_plan_to_result_and_does_not_mutate_saved_state(self):
        spec=self.service.paper_abstract(self.pid,{})['spec'];candidate=copy.deepcopy(spec);candidate['abstract']['status']='results'
        with patch.object(self.store,'get_settings',return_value={'mode':'api','api_key':'synthetic-only'}),patch('app.plotting.api_complete',return_value=json.dumps({'spec':candidate,'explanation':'Changed'})) as model:
            with self.assertRaisesRegex(ValueError,'作者声明'):self.service.plot_ai(self.pid,{'spec':spec,'instruction':'请改成灰阶','request_id':uuid.uuid4().hex})
            self.assertEqual(model.call_count,1)
        self.assertEqual(len(self.store.get(self.pid)['plotting']['figures']),2)


if __name__=='__main__':unittest.main()
