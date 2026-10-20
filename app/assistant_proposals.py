"""Validated draft proposals with explicit, atomic, idempotent application."""
import copy
import difflib
import hashlib
import json

from .research import workspace
from .storage import now
from .workflows import FLOWS, STAGES, VERSION

KINDS = {'tasks':'研究待办', 'experiments':'实验方案', 'issues':'修改意见', 'report':'报告修改'}
FIELDS = {
    'tasks': ('title','purpose','materials','criterion','due','stage'),
    'experiments': ('title','hypothesis','baseline','variables','controls','dataset','split','metric',
                    'unit','seeds','sample_reason','analysis','compute','license'),
    'issues': ('title','priority','location','stage'),
}


def proposal_instruction(kind, stage):
    schema = ({'answer':'简短说明草稿用途、缺项与局限', 'text':'修改后的完整阶段报告 Markdown'} if kind=='report' else
              {'answer':'简短说明草稿用途、缺项与局限', 'records':[{key:'' for key in FIELDS[kind]}]})
    return ('\n本次用户明确选择生成“'+KINDS[kind]+'”草稿。仅返回一个 JSON 对象，不加 Markdown 代码围栏。结构为：'
            +json.dumps(schema,ensure_ascii=False)+
            '\n所有字段为字符串，不添加任何其他字段、操作、URL、记录ID或工具调用。'
            '\n列表仅1至5条；实验方案仅1条。title必填。tasks.due未知留空，日期用YYYY-MM-DD；stage为当前或项目内阶段ID（当前：'
            +stage+'）；issues.priority只能high、medium、low。意见是AI建议，不冒充审稿人原话。'
            '\n实验内容全部用计划或待确认表述；指标阈值、重复数与数据集未知时写待确认，不得虚构已执行结果。'
            '\n报告修改仅基于用户选中的报告；保留真实数据、证据边界及已有引用ID，不添加无来源事实。返回完整替换文本。'
            '\n你只生成草稿，尚未保存到研究工具或报告，不能声称操作成功。')


class AssistantProposals:
    def _draft_kind(self, p, opts, value):
        if not isinstance(value,str) or value not in ('', *KINDS):
            raise ValueError('请选择有效的协助类型。')
        if value:
            if not p:
                raise ValueError('请打开一个项目后生成协助草稿。')
            self.service.assert_idle(p)
            if value=='report':
                out=p['outputs'].get(opts['stage'])
                if not opts['options']['report'] or not out or not out.get('text','').strip():
                    raise ValueError('修改报告前，请在参考范围中选择当前阶段报告。')
                if out.get('stale'):
                    raise ValueError('当前报告需要重核，请先到研究流程处理前序变化。')
                if STAGES[opts['stage']].get('local'):
                    raise ValueError('本机计算报告由工具产生，请调整输入后重新计算；助手不能替换计算结果。')
        return value

    def _normalize_draft(self, kind, draft, p, stage):
        if not isinstance(draft,dict):
            raise ValueError('草稿格式无效。')
        if kind=='report':
            if set(draft)!={'text'} or not isinstance(draft['text'],str) or not draft['text'].strip() or len(draft['text'])>80000:
                raise ValueError('报告草稿须为 1–80000 字的完整文本。')
            return {'text':draft['text']}
        if set(draft)!={'records'} or not isinstance(draft['records'],list) or not 1<=len(draft['records'])<=(1 if kind=='experiments' else 5):
            raise ValueError('待办或意见每次 1–5 条，实验方案每次 1 条。')
        records=[]
        for supplied in draft['records']:
            if not isinstance(supplied,dict) or set(supplied)-set(FIELDS[kind]):
                raise ValueError('草稿含不支持的字段，请重新生成或编辑。')
            r={k:supplied.get(k,'') for k in FIELDS[kind]}
            if any(not isinstance(v,str) or len(v)>6000 for v in r.values()):
                raise ValueError('草稿字段应为不超过 6000 字的文本。')
            r={k:v.strip() for k,v in r.items()}
            if not r['title'] or len(r['title'])>500:
                raise ValueError('每条草稿须有 1–500 字的名称。')
            if 'stage' in r:
                r['stage']=r['stage'] or stage
                if r['stage'] not in FLOWS[p['flow']]['stages']:
                    raise ValueError('草稿关联阶段不属于当前项目。')
            if kind=='issues':
                r['priority']=r['priority'] or 'medium'
                if r['priority'] not in ('high','medium','low'):
                    raise ValueError('修改意见优先级无效。')
            records.append(r)
        return {'records':records}

    def _draft_candidate(self, p, proposal, draft):
        kind,stage=proposal['kind'],proposal['stage']
        draft=self._normalize_draft(kind,draft,p,stage)
        candidate=copy.deepcopy(p);created=[]
        if kind=='report':
            self.service.prepare_report_edit(candidate,{'stage':stage,'text':draft['text']})
        else:
            for r in draft['records']:
                record=dict(r,status='planned' if kind=='experiments' else 'todo')
                if kind=='issues':record.update(source='AI 建议（待研究者核对）',response='')
                created.append(self.service.prepare_research_record(candidate,kind,record))
        # Preserve a stable preview: generated IDs and timestamps are not its identity.
        before=p['outputs'];after=candidate['outputs']
        affected=[sid for sid in FLOWS[p['flow']]['stages'] if sid in before and
                  (before[sid].get('approved')!=after[sid].get('approved') or before[sid].get('stale')!=after[sid].get('stale') or
                   (kind=='report' and sid==stage and before[sid].get('text')!=after[sid].get('text')))]
        warnings=[]
        if kind=='experiments':
            missing=[k for k in FIELDS[kind] if not draft['records'][0][k]]
            if missing:warnings.append(f'实验方案仍有 {len(missing)} 个空白字段，保存不代表方案已完整或实验已执行。')
            if workspace(p).get('enabled'):
                warnings.append('研究工具记录已用于模型上下文；新增实验方案将使已有成果需要重新核对。')
        if kind=='issues':warnings.append('将保存为 AI 建议、未处理；不会登记为真实审稿意见或已完成修改。')
        return draft,candidate,created,affected,warnings

    def _parse_proposal(self, raw, job):
        kind=job['draft_kind']
        text=raw.strip()
        if text.startswith('```') and text.endswith('```'):
            lines=text.splitlines();text='\n'.join(lines[1:-1])
        try:
            obj=json.loads(text)
            if not isinstance(obj,dict) or set(obj)!=({'answer','text'} if kind=='report' else {'answer','records'}):
                raise ValueError()
            if not isinstance(obj['answer'],str) or not obj['answer'].strip() or len(obj['answer'])>6000:
                raise ValueError()
            draft={k:v for k,v in obj.items() if k!='answer'}
            proposal={'kind':kind,'stage':job['stage'],'status':'draft','base_state':job['base_state']}
            draft,*_=self._draft_candidate(job['project_snapshot'],proposal,draft)
            proposal.update(draft=draft,created=now(),version=job['version'])
            return obj['answer'],proposal
        except (ValueError,KeyError,TypeError) as e:
            raise ValueError('模型草稿结构或字段未通过校验，未写入研究记录。请缩小需求后重新生成。') from e

    def _proposal(self, data):
        pid=data.get('project','')
        if not pid:raise ValueError('协助草稿仅属于具体研究项目。')
        p=self._project(pid);session=self._read(pid)
        message=next((m for m in session['messages'] if m['id']==data.get('message') and m['role']=='assistant'
                      and m['status']=='completed' and m.get('proposal')),None)
        if not message:raise ValueError('未找到当前项目的有效草稿。')
        return p,session,message,message['proposal']

    def proposal_preview(self, data):
        from .assistant import fingerprint,digest
        with self.service.lock:
            p,session,message,proposal=self._proposal(data)
            self.service.assert_idle(p)
            if self.busy():raise ValueError('请等待 AI 助手回答结束后再预览保存。')
            if proposal['status']!='draft':raise ValueError('这份草稿已保存或舍弃，请刷新对话。')
            if proposal.get('version')!=VERSION:
                raise ValueError('草稿生成后软件版本已变化，请重新生成并核对保存。')
            if proposal['base_state']!=fingerprint(p):
                raise ValueError('项目在生成草稿后已变化。请保留需要的文字，重新生成后核对再保存。')
            draft,candidate,created,affected,warnings=self._draft_candidate(p,proposal,data.get('draft'))
            sha=digest({'message':message['id'],'draft':draft,'base_state':fingerprint(p),'kind':proposal['kind']})
            stage=proposal['stage'];kind=proposal['kind']
            diff=''
            if kind=='report':
                diff='\n'.join(difflib.unified_diff(p['outputs'][stage]['text'].splitlines(),draft['text'].splitlines(),fromfile='当前报告',tofile='草稿报告',lineterm=''))
                if not diff:raise ValueError('草稿与当前报告相同，无需保存。')
            return {'sha256':sha,'draft':draft,'kind':kind,'stage':stage,'affected':affected,
                    'cursor_before':p['cursor'],'cursor_after':candidate['cursor'],
                    'warnings':warnings,'diff':diff,'count':len(created),'creates_snapshot':kind=='report',
                    'meaning':'预览未写入任何研究内容；确认后将原子保存全部选定条目。'}

    def proposal_apply(self, data):
        from .assistant import fingerprint
        with self.service.lock:
            p,session,message,proposal=self._proposal(data)
            self.service.assert_idle(p)
            if proposal['status']=='saved':
                if proposal.get('applied_sha256')!=data.get('preview_sha256'):
                    raise ValueError('这份草稿已保存，请刷新对话查看结果。')
                return {'state':self.state({'project':p['id']}),'project':self.service.public_project(p['id']),'receipt':proposal['receipt']}
            if data.get('acknowledge') is not True:raise ValueError('请核对草稿与影响范围后确认保存。')
            preview=self.proposal_preview(data)
            if data.get('preview_sha256')!=preview['sha256']:
                raise ValueError('草稿或项目在预览后发生变化，请重新查看差异与影响。')
            draft,candidate,created,affected,warnings=self._draft_candidate(p,proposal,preview['draft'])
            receipt={'id':message['id'],'kind':proposal['kind'],'saved_at':now(),'created_ids':[r['id'] for r in created],
                     'count':len(created),'stage':proposal['stage'],'affected':affected,'cursor_after':candidate['cursor'],
                     'text_sha256':hashlib.sha256(draft['text'].encode()).hexdigest() if proposal['kind']=='report' else '',
                     'meaning':'已保存；计划与建议仍需研究者核对。未执行实验、运行阶段或确认科研成果。'}
            proposal.update(status='saved',draft=draft,receipt=receipt,applied_sha256=preview['sha256'])
            session['revision']+=1
            candidate['assistant_chat']=session
            if proposal['kind']=='report':candidate['outputs'][proposal['stage']]['assistant_applied_from']=message['id']
            # Record creation/report edit and the receipt share one SQLite commit.
            self.service.store.put(candidate)
            return {'state':self.state({'project':p['id']}),'project':self.service.public_project(p['id']),'receipt':receipt}

    def proposal_discard(self,data):
        with self.service.lock:
            p,session,message,proposal=self._proposal(data)
            if self.service.updating or self.busy():raise ValueError('请等待问答或更新结束。')
            if data.get('acknowledge') is not True:raise ValueError('请确认舍弃这份协助草稿。')
            if proposal['status']!='draft':raise ValueError('这份草稿已保存或舍弃。')
            proposal.update(status='discarded',discarded_at=now())
            self._write(p['id'],session)
        return self.state(data)
