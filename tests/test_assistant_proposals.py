import copy
import json
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from app.assistant import Assistant
from app.research import workspace
from app.service import Service
from app.storage import Store
from app.workflows import FLOWS


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name);self.service=Service(self.store);self.chat=self.service.assistant
        self.p=self.store.create({'title':'RAG 可靠性研究','flow':'full','topic':'尚无实验数据，先设计方案','experiments':'none'})
        self.pid=self.p['id'];self.store.save_settings({'mode':'api','api_model':'mock','api_key':'local-test-key'})

    def tearDown(self):
        self.chat.close()
        for _ in range(100):
            if not self.chat.busy():break
            time.sleep(.01)
        self.temp.cleanup()

    def data(self,**kw):return {'project':self.pid,'view':'project','stage':'research',**kw}

    def generate(self,kind='tasks',obj=None,**kw):
        if obj is None:
            obj={'answer':'仅为研究计划草稿，未执行实验。','records':[{'title':'冻结评测数据划分','criterion':'记录来源与测试集隔离要求'}] if kind=='tasks' else [{'title':'RAG 公平比较计划','baseline':'无检索设置','dataset':'待研究者确定','metric':'待确认指标'}] if kind=='experiments' else [{'title':'补充基线公平性说明','priority':'high','location':'方法部分'}]}
            if kind=='report':obj={'answer':'修订草稿待核对。','text':'# 修订报告\n仍属研究计划，未执行实验。'}
        d=self.data(draft_kind=kind,text='按课题生成可核对草稿',request_id=uuid.uuid4().hex,**kw)
        d['context_sha256']=self.chat.preview(d)['sha256']
        with patch('app.assistant.api_complete',return_value=json.dumps(obj,ensure_ascii=False)) as model:
            self.chat.send(d)
            for _ in range(200):
                if not self.chat.busy():break
                time.sleep(.01)
            model.assert_called_once()
        return self.chat.state(self.data())['messages'][-1]

    def review(self,m,draft=None):
        d=self.data(message=m['id'],draft=draft or m['proposal']['draft'])
        preview=self.chat.proposal_preview(d)
        return d,preview

    def apply(self,m,draft=None):
        d,preview=self.review(m,draft);return self.chat.proposal_apply({**d,'preview_sha256':preview['sha256'],'acknowledge':True})

    def report(self):
        p=self.store.get(self.pid);p['cursor']=2;p['outputs']={sid:{'text':'# 原报告\n研究尚无实验结果。','approved':True,'stale':False} for sid in ('research','outline','draft')};self.store.put(p)

    def test_generation_creates_no_research_records_or_output_mutation(self):
        before=self.service.public_project(self.pid);m=self.generate()
        self.assertEqual(m['status'],'completed');self.assertEqual(m['proposal']['status'],'draft')
        self.assertEqual(before,self.service.public_project(self.pid))
        self.assertEqual(workspace(self.store.get(self.pid))['tasks'],[])

    def test_edited_subset_saved_as_todo_with_receipt_and_idempotent_retry(self):
        m=self.generate(obj={'answer':'三条待办','records':[{'title':f'待办{i}'} for i in range(3)]})
        edited={'records':[dict(m['proposal']['draft']['records'][1],title='用户核对后的待办')]}
        d,preview=self.review(m,edited);payload={**d,'preview_sha256':preview['sha256'],'acknowledge':True}
        result=self.chat.proposal_apply(payload);again=self.chat.proposal_apply(payload)
        self.assertEqual(result['receipt'],again['receipt'])
        rows=self.store.get(self.pid)['research']['tasks'];self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['title'],'用户核对后的待办');self.assertEqual(rows[0]['status'],'todo')
        self.assertEqual(rows[0]['id'],result['receipt']['created_ids'][0]);self.assertEqual(result['state']['messages'][-1]['proposal']['status'],'saved')

    def test_model_may_not_set_execution_or_completion_state(self):
        for kind in ('tasks','experiments','issues'):
            m=self.generate(kind,obj={'answer':'禁止伪造状态','records':[{'title':'恶意字段','status':'completed','id':uuid.uuid4().hex}]})
            self.assertEqual(m['status'],'failed');self.assertNotIn('proposal',m)
        self.assertEqual(workspace(self.store.get(self.pid))['experiments'],[])

    def test_experiment_is_planned_and_enabled_context_invalidates_existing_outputs(self):
        self.report();p=self.store.get(self.pid);workspace(p)['enabled']=True;self.store.put(p)
        m=self.generate('experiments');d,preview=self.review(m)
        self.assertEqual(set(preview['affected']),{'research','outline','draft'})
        self.assertEqual(preview['cursor_after'],0);self.assertTrue(preview['warnings'])
        result=self.apply(m);p=self.store.get(self.pid)
        self.assertEqual(p['research']['experiments'][0]['status'],'planned')
        self.assertTrue(p['outputs']['draft']['stale']);self.assertFalse(p['outputs']['research']['approved'])
        self.assertEqual(len(p['confirmations']),0);self.assertEqual(len(p['runs']),0)

    def test_experiment_without_context_keeps_reports_and_cursor(self):
        self.report();before=self.store.get(self.pid);m=self.generate('experiments');self.apply(m);after=self.store.get(self.pid)
        self.assertEqual(before['outputs'],after['outputs']);self.assertEqual(before['cursor'],after['cursor'])

    def test_context_enabled_save_from_later_stage_returns_current_state_after_reset(self):
        self.report();p=self.store.get(self.pid);workspace(p)['enabled']=True;self.store.put(p)
        m=self.generate('experiments',stage='draft');d,preview=self.review(m)
        result=self.chat.proposal_apply({**d,'stage':'draft','preview_sha256':preview['sha256'],'acknowledge':True})
        self.assertEqual(result['state']['stage'],'research');self.assertEqual(result['project']['cursor'],0)

    def test_updated_software_version_requires_regeneration(self):
        m=self.generate()
        with patch('app.assistant_proposals.VERSION','99.0.0'):
            with self.assertRaisesRegex(ValueError,'软件版本'):
                self.review(m)

    def test_ai_issues_cannot_impersonate_reviewer_or_mark_done(self):
        m=self.generate('issues');self.apply(m);r=self.store.get(self.pid)['research']['issues'][0]
        self.assertEqual(r['source'],'AI 建议（待研究者核对）');self.assertEqual(r['status'],'todo');self.assertEqual(r['response'],'')
        m=self.generate('issues',obj={'answer':'冒充审稿','records':[{'title':'意见','source':'真实审稿人'}]})
        self.assertEqual(m['status'],'failed')

    def test_report_requires_explicit_original_report_and_rejects_local_calculation(self):
        self.report()
        with self.assertRaisesRegex(ValueError,'选择当前阶段报告'):
            self.chat.preview(self.data(draft_kind='report'))
        p=self.store.create({'title':'边缘模型评测','flow':'scientific_data'});p['cursor']=1;p['outputs']['kd_data']={'text':'6行','stale':False};self.store.put(p)
        with self.assertRaisesRegex(ValueError,'本机计算报告'):
            self.chat.preview({'project':p['id'],'stage':'kd_data','draft_kind':'report','options':{'report':True}})

    def test_report_preview_diff_apply_preserves_snapshot_and_invalidates_downstream(self):
        self.report();m=self.generate('report',options={'report':True});d,preview=self.review(m)
        self.assertIn('-# 原报告',preview['diff']);self.assertIn('+# 修订报告',preview['diff']);self.assertTrue(preview['creates_snapshot'])
        self.assertEqual(self.store.get(self.pid)['outputs']['research']['text'],'# 原报告\n研究尚无实验结果。')
        result=self.apply(m);p=self.store.get(self.pid)
        self.assertIn('修订报告',p['outputs']['research']['text']);self.assertEqual(p['cursor'],0)
        self.assertFalse(p['outputs']['research']['approved']);self.assertTrue(p['outputs']['outline']['stale'])
        self.assertIn('原报告',p['versions'][-1]['text']);self.assertEqual(p['outputs']['research']['assistant_applied_from'],m['id'])

    def test_same_report_noop_and_stale_reports_cannot_be_applied(self):
        self.report();m=self.generate('report',options={'report':True})
        with self.assertRaisesRegex(ValueError,'相同'):
            self.review(m,{'text':self.store.get(self.pid)['outputs']['research']['text']})
        p=self.store.get(self.pid);p['outputs']['research']['stale']=True;self.store.put(p)
        with self.assertRaisesRegex(ValueError,'前序变化'):
            self.chat.preview(self.data(draft_kind='report',options={'report':True}))

    def test_project_change_or_edited_draft_after_preview_requires_new_review(self):
        m=self.generate();d,preview=self.review(m)
        d['draft']['records'][0]['title']='Changed after preview'
        with self.assertRaisesRegex(ValueError,'预览后'):
            self.chat.proposal_apply({**d,'preview_sha256':preview['sha256'],'acknowledge':True})
        p=self.store.get(self.pid);p['topic']='new input';self.store.put(p)
        with self.assertRaisesRegex(ValueError,'生成草稿后'):
            self.chat.proposal_preview(d)

    def test_full_batch_validation_failure_is_atomic(self):
        m=self.generate();draft={'records':[{'title':'valid'},{'title':'invalid date','due':'not-a-date'}]}
        before=self.store.get(self.pid)
        with self.assertRaisesRegex(ValueError,'日期'):
            self.review(m,draft)
        self.assertEqual(before,self.store.get(self.pid))

    def test_commit_failure_keeps_project_and_proposal_unsaved(self):
        m=self.generate();d,preview=self.review(m);before=self.store.get(self.pid)
        with patch.object(self.store,'put',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.chat.proposal_apply({**d,'preview_sha256':preview['sha256'],'acknowledge':True})
        self.assertEqual(before,self.store.get(self.pid))
        self.apply(m);self.assertEqual(len(self.store.get(self.pid)['research']['tasks']),1)

    def test_cross_project_and_workspace_proposal_rejected(self):
        m=self.generate();other=self.store.create({'title':'隔离项目'})
        with self.assertRaisesRegex(ValueError,'未找到'):
            self.chat.proposal_preview({'project':other['id'],'message':m['id'],'draft':m['proposal']['draft']})
        with self.assertRaisesRegex(ValueError,'打开一个项目'):
            self.chat.preview({'draft_kind':'tasks'})

    def test_discard_and_clear_never_save_research(self):
        m=self.generate();self.chat.proposal_discard(self.data(message=m['id'],acknowledge=True))
        with self.assertRaisesRegex(ValueError,'舍弃'):
            self.review(m)
        self.assertEqual(workspace(self.store.get(self.pid))['tasks'],[])
        self.assertEqual(self.chat.state(self.data())['messages'][-1]['proposal']['status'],'discarded')

    def test_preview_requires_ack_apply_and_rejects_archived_or_running(self):
        m=self.generate();d,preview=self.review(m)
        with self.assertRaisesRegex(ValueError,'确认'):
            self.chat.proposal_apply({**d,'preview_sha256':preview['sha256']})
        p=self.store.get(self.pid);p['lifecycle']='archived';self.store.put(p)
        with self.assertRaisesRegex(ValueError,'归档'):
            self.chat.proposal_preview(d)

    def test_json_schema_errors_and_limits_fail_without_raw_json_saved_as_answer(self):
        for obj in ({'answer':'a','records':[]},{'answer':'a','records':[{'title':'x'}]*6},{'answer':'a','records':[{'title':'x','stage':'foreign'}]}, {'answer':'a','records':[{'title':'x'*501}]}):
            m=self.generate(obj=obj);self.assertEqual(m['status'],'failed');self.assertNotIn('proposal',m)

    def test_saved_proposal_and_receipt_persist_across_restart_and_export(self):
        m=self.generate();self.apply(m);state=Assistant(self.service).state(self.data())
        self.assertEqual(state['messages'][-1]['proposal']['receipt']['count'],1)
        raw=self.chat.export(self.data()).decode('utf-8-sig');self.assertIn('协助草稿与保存记录',raw);self.assertIn('冻结评测数据划分',raw)

    def test_capacity_rejects_batch_without_partial_insert(self):
        p=self.store.get(self.pid);workspace(p)['tasks']=[{'id':uuid.uuid4().hex,'title':f'old{i}','status':'todo'} for i in range(199)];self.store.put(p)
        # Model proposal validation also uses the same capacity rules as final application.
        m=self.generate(obj={'answer':'too many','records':[{'title':'a'},{'title':'b'}]})
        self.assertEqual(m['status'],'failed');self.assertEqual(len(self.store.get(self.pid)['research']['tasks']),199)

    def test_local_shortcut_does_not_generate_proposal(self):
        with self.assertRaisesRegex(ValueError,'快捷指导'):
            self.chat.send(self.data(intent='next',draft_kind='tasks',request_id=uuid.uuid4().hex))


if __name__=='__main__':unittest.main()
