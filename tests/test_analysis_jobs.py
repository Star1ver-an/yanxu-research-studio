"""Real process/API integration using synthetic computer science measurements."""
import base64
import copy
import io
import json
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.analysis import checked_result
from app.service import Service
from app.storage import Store


class AnalysisJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=Service(Store(self.temp.name))
        self.pid=self.service.store.create({'title':'合成｜AI 服务的运行可靠性','flow':'full','experiments':'none'})['id']
        self.service.upload(self.pid,{'name':'synthetic-reliability.csv','data':base64.b64encode(
            b'unit,time,event,group\na,1,1,A\nb,2,0,A\nc,3,1,A\nd,4,0,A\ne,1,0,B\nf,2,1,B\ng,3,0,B\nh,5,1,B\n').decode()})
        self.aid=self.service.store.get(self.pid)['assets'][0]['id']
        self.spec={'version':1,'kind':'analysis','title':'合成服务失效分析','source_ids':[self.aid],
                   'mapping':{},'options':{},'style':{'dpi':80},'analysis':{'method':'km','inputs':{'data':self.aid},
                   'columns':{'duration':'time','event':'event','group':'group'},'parameters':{'independent_units':True}}}

    def tearDown(self):
        self.service.close_plotting();self.service.assistant.close();self.service.backups.close()
        self.temp.cleanup()

    def run_job(self,spec=None):
        spec=spec or self.spec
        preview=self.service.analysis_preview(self.pid,{'config':spec['analysis']})
        data={'spec':spec,'signature':preview['signature'],'request_id':uuid.uuid4().hex}
        job=self.service.analysis_start(self.pid,data)
        # A retry is the same process, rather than a second calculation.
        self.assertEqual(self.service.analysis_start(self.pid,data)['id'],job['id'])
        end=time.monotonic()+45
        while job['status'] in {'queued','running'} and time.monotonic()<end:
            time.sleep(.05);job=self.service.analysis_job(self.pid,{'id':job['id']})
        self.assertEqual(job['status'],'completed',job.get('error',job))
        self.assertTrue(base64.b64decode(job['preview']).startswith(b'\x89PNG'))
        return job

    def test_worker_save_all_formats_backup_new_workspace_and_copy(self):
        job=self.run_job()
        self.assertIn('analysis_result',job['spec'])
        saved=self.service.plot_save(self.pid,{'spec':job['spec']})['plotting']['figures'][0]
        full=self.service.plot_analysis_result(self.pid,{'id':saved['id']})
        self.assertEqual(full['method'],'km')
        table=next(iter(full['tables']))
        self.assertTrue(self.service.analysis_table(self.pid,{'id':saved['id'],'table':table})[0].startswith(b'\xef\xbb\xbf'))
        for fmt,magic in [('png',b'\x89PNG'),('svg',b'<?xml'),('pdf',b'%PDF'),('tiff',b'II'),('zip',b'PK')]:
            blob=self.service.plot_export(self.pid,{'id':saved['id'],'format':fmt})[1]
            self.assertTrue(blob.startswith(magic),fmt)
            if fmt=='zip':
                with zipfile.ZipFile(io.BytesIO(blob)) as z:self.assertIn('analysis/result.json',z.namelist())
        other=self.service.manage_projects({'ids':[self.pid],'action':'duplicate','mode':'reports'})['created_ids'][0]
        copy_row=self.service.store.get(other)['plotting']['figures'][0]
        self.assertNotEqual(copy_row['spec']['analysis']['inputs']['data'],self.aid)
        self.assertEqual(self.service.plot_analysis_result(other,{'id':copy_row['id']})['method'],'km')
        archive=self.service.export(self.pid,'zip')[1]
        with tempfile.TemporaryDirectory() as root:
            second=Service(Store(root))
            try:
                restored=second.backups.restore(archive)['created_ids'][0]
                row=second.store.get(restored)['plotting']['figures'][0]
                self.assertEqual(second.plot_analysis_result(restored,{'id':row['id']})['method'],'km')
                self.assertTrue(second.plot_export(restored,{'id':row['id']})[1].startswith(b'\x89PNG'))
            finally:second.close_plotting();second.assistant.close();second.backups.close()

    def test_preflight_stale_signature_columns_independent_and_capability(self):
        bad=copy.deepcopy(self.spec['analysis']);bad['columns']['duration']='missing'
        with self.assertRaises(ValueError):self.service.analysis_preview(self.pid,{'config':bad})
        bad=copy.deepcopy(self.spec['analysis']);bad['parameters']['independent_units']=False
        with self.assertRaises(ValueError):self.service.analysis_preview(self.pid,{'config':bad})
        with self.assertRaises(ValueError):self.service.analysis_start(self.pid,{'spec':self.spec,'signature':'wrong','request_id':uuid.uuid4().hex})
        other=self.service.store.create({'title':'不同项目'})['id']
        with self.assertRaises(ValueError):self.service.analysis_inspect(other,{'asset_id':self.aid})
        info=self.service.analysis_inspect(self.pid,{'asset_id':self.aid})
        self.assertNotIn('preview',info)

    def test_frozen_result_tamper_rejected_and_recompute_cannot_replace_saved_version(self):
        first=self.run_job()
        saved=self.service.plot_save(self.pid,{'spec':first['spec']})['plotting']['figures'][0]
        original=self.service.plot_analysis_result(self.pid,{'id':saved['id']})
        second=self.run_job()
        self.assertEqual(first['spec']['analysis_result'],second['spec']['analysis_result'])
        self.assertEqual(original,self.service.plot_analysis_result(self.pid,{'id':saved['id']}))
        path=self.service.analysis_cache/(first['spec']['analysis_result']+'.json')
        path.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.service.plot_export(self.pid,{'id':saved['id']})
        with self.assertRaises(ValueError):self.service.plot_word_figures(self.pid)

    def test_strict_result_shapes(self):
        for value in [{'method':'km','tables':{'bad':[]}}, {'method':'km','tables':{'bad':{'columns':['x','x'],'rows':[]}}},
                      {'method':'km','tables':{},'warnings':'text'}, {'method':'km','tables':{'bad':{'columns':['x'],'rows':[{'bad':1}]}}}]:
            with self.subTest(value=value),self.assertRaises(ValueError):checked_result(value)

    def test_windows_progress_file_busy_cannot_abort_calculation(self):
        from app.analysis_worker import run
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root);source=self.service._plot_sources(self.service.store.get(self.pid),[self.aid])[0]
            path=folder/(source['id']+'.csv');path.write_bytes(source['raw'])
            manifest=folder/'input.json';out=folder/'result.json'
            manifest.write_text(json.dumps({'version':1,'config':self.spec['analysis'],'sources':[{
                'id':source['id'],'name':source['name'],'path':str(path),'sha256':hashlib.sha256(source['raw']).hexdigest()}]}),encoding='utf-8')
            real=Path.replace
            def busy(path,target):
                if path.name=='progress.tmp':raise PermissionError('Synthetic Windows sharing violation')
                return real(path,target)
            with patch.object(Path,'replace',new=busy):self.assertEqual(run(manifest,out),0)
            self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['method'],'km')

    def test_unspecified_frozen_result_cannot_save_and_gc_protects_history(self):
        job=self.run_job()
        with self.assertRaises(ValueError):self.service.plot_save(self.pid,{'spec':self.spec})
        row=self.service.plot_save(self.pid,{'spec':job['spec']})['plotting']['figures'][0]
        self.service.analysis_jobs.clear()
        changed=copy.deepcopy(job['spec']);changed['style']['font_size']=12
        row=self.service.plot_save(self.pid,{'id':row['id'],'expected_revision':1,'spec':changed})['plotting']['figures'][0]
        orphan=self.service.analysis_cache/('f'*64+'.json');orphan.write_text('{}')
        clean=self.service.analysis_clean_cache()
        self.assertEqual(clean['files_removed'],1)
        self.assertTrue((self.service.analysis_cache/(job['spec']['analysis_result']+'.json')).is_file())
        self.assertTrue(row['history'][0]['spec']['analysis_result'])

    def test_ai_analysis_is_structural_idempotent_and_cannot_confirm_for_author(self):
        self.service.store.save_settings({'mode':'api','api_key':'synthetic-test-key'})
        from app.plot_engine import validate_spec
        spec=validate_spec(self.spec)
        candidate=copy.deepcopy(spec);candidate['title']='建议标题'
        captured=[]
        def model(settings,key,system,prompt,*args):
            captured.append(json.loads(prompt))
            return json.dumps({'spec':candidate,'explanation':'修改标题'})
        request={'spec':spec,'instruction':'核对当前方法，只修改标题','request_id':uuid.uuid4().hex}
        with patch('app.plotting.api_complete',side_effect=model) as call:
            self.assertEqual(self.service.plot_ai(self.pid,request),self.service.plot_ai(self.pid,request))
            self.assertEqual(call.call_count,1)
        self.assertNotIn('preview',json.dumps(captured))
        self.assertNotIn('a,1,1,A',json.dumps(captured))
        candidate['analysis']['parameters']['independent_units']=False
        request['request_id']=uuid.uuid4().hex
        with patch('app.plotting.api_complete',side_effect=model):
            with self.assertRaises(ValueError):self.service.plot_ai(self.pid,request)
        self.assertFalse(self.service.store.get(self.pid).get('plotting'))


if __name__=='__main__':unittest.main()
