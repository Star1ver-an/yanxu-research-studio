import base64
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import uuid

from app.storage import Store
from app.service import Service
from app.server import LocalServer


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.service=Service(self.store)
        self.a=self.store.create({'title':'原始项目 A','flow':'full'})['id']
        self.b=self.store.create({'title':'原始项目 B','flow':'scientific_design'})['id']
        self.service.upload(self.a,{'name':'source.txt','data':base64.b64encode(b'Original material: scientific test fixture.').decode()})
        p=self.store.get(self.a);p['outputs']['research']={'text':'待核实报告','approved':True,'approved_actor':'author','stale':False}
        p['cursor']=1;p['confirmations']=[{'stage':'research','actor':'author'}]
        p['literature']=[{'id':uuid.uuid4().hex,'title':'Test paper','enabled':True}]
        self.store.put(p)

    def manage(self,action,ids=None,**kwargs):
        return self.service.manage_projects({'action':action,'ids':ids or [self.a],**kwargs})

    def test_legacy_projects_are_active_without_rewriting_and_rename_preserves_approvals(self):
        before=self.store.get(self.a)
        self.manage('rename',titles={self.a:'重命名后'})
        after=self.store.get(self.a)
        for key in ['outputs','confirmations','runs','assets','cursor']:self.assertEqual(before[key],after[key])
        self.assertNotIn('lifecycle',after)
        self.service.update_info(self.a,{'title':'通过概况重命名'})
        self.assertTrue(self.store.get(self.a)['outputs']['research']['approved'])
        self.service.update_info(self.a,{'topic':'改变研究问题'})
        self.assertTrue(self.store.get(self.a)['outputs']['research']['stale'])

    def test_batch_validation_is_atomic_and_running_projects_are_protected(self):
        p=self.store.get(self.b);p['runs']=[{'id':uuid.uuid4().hex,'status':'running'}];self.store.put(p)
        with self.assertRaisesRegex(ValueError,'正在运行'):
            self.manage('trash',[self.a,self.b],acknowledge=True)
        self.assertNotIn('lifecycle',self.store.get(self.a))
        with self.assertRaises(ValueError):self.manage('rename',titles={self.a:' '*3})
        self.assertEqual(self.store.get(self.a)['title'],'原始项目 A')
        for ids in [[],[self.a,self.a],[{}],['../outside'],[uuid.uuid4().hex]*51]:
            with self.assertRaises(ValueError):self.service.manage_projects({'action':'trash','ids':ids,'acknowledge':True})

    def test_archive_read_export_restore_and_trash_previous_state(self):
        self.manage('archive');self.assertEqual(self.service.public_project(self.a)['lifecycle'],'archived')
        self.assertIn('待核实报告',self.service.export(self.a,'md')[1].decode('utf-8-sig'))
        with self.assertRaisesRegex(ValueError,'恢复'):self.service.edit(self.a,{'stage':'research','text':'x'})
        with self.assertRaisesRegex(ValueError,'恢复'):self.service.start(self.a,{'stage':'research'})
        self.manage('trash',acknowledge=True);self.manage('restore')
        self.assertEqual(self.store.get(self.a)['lifecycle'],'archived')
        self.manage('restore');self.assertEqual(self.store.get(self.a)['lifecycle'],'active')

    def test_material_copy_is_independent_and_never_carries_confirmations(self):
        original=self.store.get(self.a)
        new_id=self.manage('duplicate')['created_ids'][0];p=self.store.get(new_id)
        self.assertEqual(p['outputs'],{});self.assertEqual(p['runs'],[]);self.assertEqual(p['confirmations'],[])
        self.assertEqual(p['cursor'],0);self.assertEqual(p['copied_from']['id'],self.a)
        self.assertNotEqual(p['assets'][0]['id'],original['assets'][0]['id'])
        new_file=self.store.root/p['assets'][0]['file'];old_file=self.store.root/original['assets'][0]['file']
        self.assertNotEqual(new_file,old_file);self.assertEqual(new_file.read_bytes(),old_file.read_bytes())
        new_file.write_text('independent change',encoding='utf-8')
        self.assertNotEqual(new_file.read_bytes(),old_file.read_bytes())
        self.assertNotEqual(p['literature'][0]['id'],original['literature'][0]['id'])
        self.assertEqual(original,self.store.get(self.a))

    def test_report_copy_marks_every_report_unapproved_stale_and_from_original(self):
        result=self.manage('duplicate',mode='reports',title='方案分支')
        p=self.store.get(result['created_ids'][0]);out=p['outputs']['research']
        self.assertEqual(p['title'],'方案分支');self.assertEqual(out['text'],'待核实报告')
        self.assertFalse(out['approved']);self.assertTrue(out['stale']);self.assertNotIn('approved_actor',out)
        self.assertEqual(p['confirmations'],[]);self.assertEqual(p['cursor'],0)
        self.assertEqual(len(set(self.store.get(pid)['title'] for pid in self.manage('duplicate',[self.a,self.b])['created_ids'])),2)

    def test_failed_copy_outside_ownership_creates_no_rows_or_residual_materials(self):
        p=self.store.get(self.a);outside=self.store.root/'outside.txt';outside.write_text('private fixture')
        p['assets'][0]['file']='outside.txt';self.store.put(p)
        before={x['id'] for x in self.store.list()};folders=set((self.store.root/'materials').iterdir())
        with self.assertRaisesRegex(ValueError,'不属于'):self.manage('duplicate')
        self.assertEqual(before,{x['id'] for x in self.store.list()})
        self.assertEqual(folders,set((self.store.root/'materials').iterdir()));self.assertTrue(outside.exists())

    def test_copy_transaction_failure_removes_created_files_and_rows(self):
        original_connect=self.store.connect
        @contextmanager
        def fail_insert():
            with original_connect() as connection:
                class Proxy:
                    def execute(self,sql,*args):
                        if sql.startswith('INSERT'):raise OSError('simulated database failure')
                        return connection.execute(sql,*args)
                yield Proxy()
        before=set((self.store.root/'materials').iterdir())
        with patch.object(self.store,'connect',fail_insert):
            with self.assertRaises(OSError):self.manage('duplicate',[self.a,self.b])
        self.assertEqual(len(self.store.list()),2);self.assertEqual(set((self.store.root/'materials').iterdir()),before)

    def test_purge_requires_trash_and_explicit_phrase_and_deletes_only_own_files(self):
        with self.assertRaises(ValueError):self.manage('purge',confirmation='永久删除')
        clone=self.manage('duplicate')['created_ids'][0]
        clone_file=self.store.root/self.store.get(clone)['assets'][0]['file']
        self.manage('trash',acknowledge=True)
        with self.assertRaises(ValueError):self.manage('purge',confirmation='yes')
        own=self.store.root/'materials'/self.a;self.assertTrue(own.is_dir())
        self.manage('purge',confirmation='永久删除')
        with self.assertRaises(ValueError):self.store.get(self.a)
        self.assertFalse(own.exists());self.assertTrue(clone_file.is_file());self.store.get(self.b)

    def test_purge_database_failure_restores_files_and_rows(self):
        self.manage('trash',acknowledge=True);original_connect=self.store.connect
        @contextmanager
        def fail_delete():
            with original_connect() as connection:
                class Proxy:
                    def execute(self,*args):return connection.execute(*args)
                    def executemany(self,*args):raise OSError('simulated deletion failure')
                yield Proxy()
        source=self.store.root/self.store.get(self.a)['assets'][0]['file'];raw=source.read_bytes()
        with patch.object(self.store,'connect',fail_delete):
            with self.assertRaises(OSError):self.manage('purge',confirmation='永久删除')
        self.assertEqual(source.read_bytes(),raw);self.assertEqual(self.store.get(self.a)['lifecycle'],'trash')

    def test_pin_is_independent_of_research_and_persists_across_restart(self):
        self.manage('pin',[self.a,self.b]);self.assertTrue(Store(self.temp.name).get(self.b)['pinned'])
        self.manage('unpin');self.assertFalse(self.store.get(self.a)['pinned'])

    def test_onboarding_defaults_progress_skip_restart_and_completion(self):
        self.assertEqual(self.store.onboarding(),{'status':'not_started','step':0})
        for status,step in [('in_progress',2),('dismissed',2),('in_progress',0),('completed',5)]:
            expected={'status':status,'step':step};self.assertEqual(self.store.save_onboarding(expected),expected)
            self.assertEqual(Store(self.temp.name).onboarding(),expected)
        for value in [{'status':'completed','step':6},{'status':'unknown','step':0},{'status':'in_progress','step':True}]:
            with self.assertRaises(ValueError):self.store.save_onboarding(value)
        self.assertNotIn('onboarding',self.store.get_settings())

    def test_management_and_guide_http_endpoints_have_auth_and_real_persistence(self):
        server=LocalServer(self.service);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        def post(path,data,authorized=True):
            headers={'Content-Type':'application/json'}
            if authorized:headers['X-Research-Token']=server.token
            with urlopen(Request(base+path,data=json.dumps(data).encode(),headers=headers)) as response:return json.load(response)
        with self.assertRaises(HTTPError) as error:post('/api/projects/manage',{'action':'archive','ids':[self.a]},False)
        self.assertEqual(error.exception.code,403)
        self.assertEqual(post('/api/projects/manage',{'action':'archive','ids':[self.a]})['count'],1)
        post('/api/preferences/onboarding',{'status':'completed','step':5})
        with urlopen(Request(base+'/api/bootstrap',headers={'X-Research-Token':server.token})) as response:boot=json.load(response)
        self.assertEqual(boot['onboarding'],{'status':'completed','step':5})
        self.assertEqual(next(p for p in boot['projects'] if p['id']==self.a)['lifecycle'],'archived')


if __name__=='__main__':unittest.main()
