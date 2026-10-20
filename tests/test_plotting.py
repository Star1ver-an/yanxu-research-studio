import base64
import copy
import io
import json
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

from app.service import Service
from app.storage import Store


class PlottingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=Service(Store(self.temp.name))
        self.store=self.service.store
        self.pid=self.store.create({'title':'合成验收｜训练曲线','flow':'full','experiments':'none'})['id']
        self.service.upload(self.pid,{'name':'synthetic.csv','data':base64.b64encode(b'epoch,loss,group\n1,0.9,A\n2,0.6,A\n3,0.4,A\n').decode()})
        self.aid=self.store.get(self.pid)['assets'][0]['id']
        self.spec={'version':1,'kind':'line','title':'合成训练曲线','source_ids':[self.aid],
            'mapping':{'x':'epoch','y':'loss'},'style':{'dpi':100},'options':{}}

    def tearDown(self):
        self.service.close_plotting();self.service.assistant.close();self.service.backups.close()
        self.temp.cleanup()

    def save(self,**extra):
        return self.service.plot_save(self.pid,{'spec':self.spec,**extra})['plotting']['figures'][-1]

    def test_real_render_formats_and_source_bundle(self):
        preview=self.service.plot_render(self.pid,{'spec':self.spec})
        self.assertTrue(base64.b64decode(preview['preview']).startswith(b'\x89PNG'))
        row=self.save()
        for fmt,magic in [('png',b'\x89PNG'),('pdf',b'%PDF'),('svg',b'<?xml'),('tiff',b'II')]:
            name,raw,mime=self.service.plot_export(self.pid,{'id':row['id'],'format':fmt})
            self.assertTrue(raw.startswith(magic),(fmt,raw[:10]))
        name,raw,_=self.service.plot_export(self.pid,{'id':row['id'],'format':'zip'})
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertEqual(json.loads(z.read('recipe.json'))['figure']['spec']['kind'],'line')
            self.assertEqual(z.read('sources/'+self.aid+'.csv'),self.service._plot_sources(self.store.get(self.pid),[self.aid])[0]['raw'])

    def test_versions_restore_and_manual_caption_survives_style_edit(self):
        row=self.save(caption='作者提供的合成数据图注。')
        self.spec['style']['font_size']=13
        new=self.save(id=row['id'],expected_revision=1)
        self.assertEqual(new['caption'],'作者提供的合成数据图注。')
        self.assertEqual(new['revision'],2)
        self.assertEqual(len(new['history']),1)
        restored=self.service.plot_restore(self.pid,{'id':row['id'],'version_id':new['history'][0]['id']})['plotting']['figures'][0]
        self.assertEqual(restored['revision'],3)
        self.assertEqual(restored['spec'],row['spec'])

    def test_failed_render_or_revision_does_not_overwrite(self):
        row=self.save();before=self.store.get(self.pid)
        broken=copy.deepcopy(self.spec);broken['mapping']['y']='missing'
        with self.assertRaises(ValueError):self.service.plot_save(self.pid,{'id':row['id'],'expected_revision':1,'spec':broken})
        with self.assertRaisesRegex(ValueError,'变化'):self.service.plot_save(self.pid,{'id':row['id'],'expected_revision':0,'spec':self.spec})
        self.assertEqual(before,self.store.get(self.pid))

    def test_source_excluded_from_model_remains_available_for_local_plotting(self):
        row=self.save()
        p=self.service.toggle_asset(self.pid,{'id':self.aid,'enabled':False})
        self.assertFalse(p['plotting']['figures'][0]['stale'])
        self.assertTrue(self.service.plot_export(self.pid,{'id':row['id']})[1].startswith(b'\x89PNG'))

    def test_hash_tamper_and_cross_project_sources_rejected(self):
        other=self.store.create({'title':'别的项目'})['id']
        with self.assertRaisesRegex(ValueError,'不属于'):self.service.plot_inspect(other,{'asset_id':self.aid})
        asset=self.store.get(self.pid)['assets'][0]
        (self.store.root/asset['file']).write_bytes(b'epoch,loss\n1,99\n')
        with self.assertRaisesRegex(ValueError,'校验'):self.service.plot_render(self.pid,{'spec':self.spec})

    def test_copy_and_portable_restore_keep_editable_recipe_and_remap_sources(self):
        row=self.save()
        copied=self.service.manage_projects({'ids':[self.pid],'action':'duplicate','mode':'reports'})['created_ids'][0]
        p=self.store.get(copied)
        self.assertNotEqual(p['plotting']['figures'][0]['spec']['source_ids'][0],self.aid)
        self.service.plot_render(copied,{'spec':p['plotting']['figures'][0]['spec']})
        _,raw,_=self.service.export(self.pid,'zip')
        restored=self.service.backups.restore(raw)['created_ids'][0]
        p=self.store.get(restored)
        self.assertNotEqual(p['plotting']['figures'][0]['spec']['source_ids'][0],self.aid)
        self.assertEqual(len(p['plotting']['figures']),1)
        self.assertTrue(self.service.plot_export(restored,{'id':row['id'],'format':'png'})[1].startswith(b'\x89PNG'))

    def test_backup_rejects_unowned_or_dangerous_recipe(self):
        self.save();_,raw,_=self.service.export(self.pid,'zip')
        output=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as original,zipfile.ZipFile(output,'w') as target:
            for name in original.namelist():
                value=original.read(name)
                if name=='项目备份.json':
                    data=json.loads(value);data['project']['plotting']['figures'][0]['spec']['code']='open("secret")'
                    value=json.dumps(data).encode()
                target.writestr(name,value)
        with self.assertRaises(ValueError):self.service.backups.restore(output.getvalue())
        self.assertEqual(len(self.store.list()),1)

    def test_duplicate_and_atomic_delete_preserve_original_sources(self):
        row=self.save()
        p=self.service.plot_duplicate(self.pid,{'id':row['id']})
        self.assertEqual(len(p['plotting']['figures']),2)
        other=p['plotting']['figures'][1]
        with self.assertRaises(ValueError):self.service.plot_delete(self.pid,{'ids':[other['id'],'bad']})
        self.assertEqual(len(self.store.get(self.pid)['plotting']['figures']),2)
        self.service.plot_delete(self.pid,{'ids':[row['id'],other['id']]})
        self.assertEqual(len(self.store.get(self.pid)['assets']),1)

    def test_local_recommendation_and_style_edit_do_not_call_model(self):
        with patch('app.plotting.api_complete',side_effect=AssertionError('unwanted model call')):
            result=self.service.plot_recommend(self.pid,{'source_ids':[self.aid],'purpose':'训练曲线'})
            self.assertLessEqual(len(result['recommendations']),3)
            self.save()

    def test_ai_is_scoped_and_same_request_is_not_billed_twice_even_after_restart(self):
        self.store.save_settings({'mode':'api','api_key':'synthetic-test-key'})
        inputs=[]
        candidate=copy.deepcopy(self.spec);candidate['title']='建议标题'
        def model(settings,key,system,prompt,*args):
            inputs.append(json.loads(prompt));return json.dumps({'spec':candidate,'explanation':'仅修改标题'})
        data={'spec':self.spec,'instruction':'标题改为建议标题','request_id':uuid.uuid4().hex}
        with patch('app.plotting.api_complete',side_effect=model) as complete:
            result=self.service.plot_ai(self.pid,data)
            self.assertEqual(result,self.service.plot_ai(self.pid,data))
            self.assertEqual(complete.call_count,1)
            self.service.close_plotting()
            restarted=Service(self.store)
            try:self.assertEqual(result,restarted.plot_ai(self.pid,data))
            finally:restarted.close_plotting();restarted.assistant.close();restarted.backups.close()
            self.assertEqual(complete.call_count,1)
        self.assertNotIn('preview',json.dumps(inputs))
        self.assertNotIn('0.9',json.dumps(inputs))
        self.assertNotIn('plotting',self.store.get(self.pid))

    def test_invalid_ai_candidate_fails_once_without_mutation(self):
        self.store.save_settings({'mode':'api','api_key':'synthetic-test-key'})
        bad=copy.deepcopy(self.spec);bad['mapping']['y']='invented'
        data={'spec':self.spec,'instruction':'修改图','request_id':uuid.uuid4().hex}
        with patch('app.plotting.api_complete',return_value=json.dumps({'spec':bad})) as complete:
            for _ in range(2):
                with self.assertRaises(ValueError):self.service.plot_ai(self.pid,data)
            self.assertEqual(complete.call_count,1)
        self.assertNotIn('plotting',self.store.get(self.pid))

    def test_word_contains_real_figure_image(self):
        self.save()
        _,raw,_=self.service.export(self.pid,'docx')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertTrue(any(n.startswith('word/media/') for n in z.namelist()))
            self.assertIn('研究图表',z.read('word/document.xml').decode())

    def test_xlsx_and_image_survive_backup(self):
        from openpyxl import Workbook
        from PIL import Image
        book=Workbook();book.active.append(['epoch','loss']);book.active.append([1,0.8])
        out=io.BytesIO();book.save(out)
        image=io.BytesIO();Image.new('RGB',(80,60),'white').save(image,format='PNG')
        for name,raw in [('data.xlsx',out.getvalue()),('original.png',image.getvalue())]:
            self.service.upload(self.pid,{'name':name,'data':base64.b64encode(raw).decode()})
        _,raw,_=self.service.export(self.pid,'zip')
        restored=self.service.backups.restore(raw)['created_ids'][0]
        self.assertEqual(len(self.store.get(restored)['assets']),3)
        self.assertEqual(len(self.service.plot_inspect(restored,{'asset_id':self.store.get(restored)['assets'][1]['id']})['columns']),2)

    def test_experiment_change_marks_figure_stale_and_limits_stage_export(self):
        p=self.service.research_save(self.pid,{'collection':'experiments','record':{'title':'合成验收实验方案','baseline':'原模型'}})
        exp=p['research']['experiments'][0]
        row=self.save(experiment_id=exp['id'],stage='outline')
        self.service.research_save(self.pid,{'collection':'experiments','record':{**exp,'baseline':'不同原模型'}})
        p=self.service.public_project(self.pid)
        self.assertTrue(p['plotting']['figures'][0]['stale'])
        self.assertTrue(any(c['id']=='plot:'+row['id'] for c in p['followup']['checks']))
        # An unrelated stage can still export; the associated stage requires review.
        self.service.export(self.pid,'docx','research')
        with self.assertRaisesRegex(ValueError,'过期'):self.service.export(self.pid,'docx','outline')

    def test_experiment_changed_during_render_is_not_silently_acknowledged(self):
        p=self.service.research_save(self.pid,{'collection':'experiments','record':{'title':'实验方案'}})
        exp=p['research']['experiments'][0]
        original=self.service._plot_render
        def racing(*args,**kwargs):
            result=original(*args,**kwargs)
            p=self.store.get(self.pid);p['research']['experiments'][0]['baseline']='并发修改';self.store.put(p)
            return result
        with patch.object(self.service,'_plot_render',side_effect=racing):
            with self.assertRaisesRegex(ValueError,'绘制期间'):self.save(experiment_id=exp['id'])
        self.assertNotIn('plotting',self.store.get(self.pid))

    def test_busy_plot_blocks_archive_and_invalid_long_caption_does_not_save(self):
        import threading
        self.service.plot_jobs['synthetic']={'pid':self.pid,'cancel':threading.Event()}
        with self.assertRaisesRegex(ValueError,'正在绘图'):self.service.manage_projects({'ids':[self.pid],'action':'archive'})
        self.service.plot_jobs.clear()
        with self.assertRaises(ValueError):self.save(caption='过长图注'*351)
        self.assertNotIn('plotting',self.store.get(self.pid))

    def test_backup_preserves_manual_caption_history(self):
        row=self.save(caption='第一版人工图注')
        self.save(id=row['id'],expected_revision=1,caption='第二版人工图注')
        _,raw,_=self.service.export(self.pid,'zip')
        restored=self.service.backups.restore(raw)['created_ids'][0]
        old=self.store.get(restored)['plotting']['figures'][0]['history'][0]
        p=self.service.plot_restore(restored,{'id':row['id'],'version_id':old['id']})
        self.assertEqual(p['plotting']['figures'][0]['caption'],'第一版人工图注')

    def test_recommendation_rejects_multiple_tables_before_any_model_call(self):
        p=self.service.upload(self.pid,{'name':'second.csv','data':base64.b64encode(b'x,y\n1,2\n').decode()})
        with patch('app.plotting.api_complete',side_effect=AssertionError('unwanted call')):
            with self.assertRaisesRegex(ValueError,'一张表格'):
                self.service.plot_recommend(self.pid,{'source_ids':[a['id'] for a in p['assets']],'use_ai':True})


if __name__=='__main__':unittest.main()
