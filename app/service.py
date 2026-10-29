import base64
import hashlib
import html
import io
import json
from pathlib import Path
import re
import threading
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid
import zipfile

from .storage import Store, now, extract_file
from .workflows import STAGES,FLOWS,BASE_INSTRUCTION,SCIENTIFIC_INSTRUCTION,role_context,SOURCE_COMMIT
from .providers import CodexClient,ProviderError,Cancelled,api_complete
from .literature import required_terms,identifier_terms,filter_entries
from .project_management import ProjectManagement,lifecycle,project_title
from .research import ResearchTools, snapshot, bibliography
from .backups import Backups
from .assistant import Assistant
from .followup import followup
from .plotting import Plotting
from .analysis_jobs import AnalysisJobs
from .paper_figures import PaperFigures
from .i18n import output_language,output_instruction,language,tr
from .studies import Studies, brief_context
from .source_workflow import SourceWorkflow

class Service(ProjectManagement,ResearchTools,Plotting,AnalysisJobs,PaperFigures,Studies,SourceWorkflow):
    def __init__(self,store=None):
        self.store=store or Store()
        self.lock=self.store.lock
        self.jobs={}
        self.updating=False
        self.init_plotting()
        self.backups=Backups(self)
        self.assistant=Assistant(self)

    def project_list(self):
        return [{k:v for k,v in p.items() if k not in ('runs','outputs','assets','literature','confirmations','research','versions','assistant_chat','assistant_followup','assistant_memory','plotting','study_brief','study_brief_history')} |
                {'asset_count':len(p['assets']),'output_count':len(p['outputs']),
                 'study_status':p.get('study_brief',{}).get('status',''),
                 'followup_next':followup(p)['next'],
                 'research_status':self.research_status(p),
                 'running':any(r['status']=='running' for r in p['runs'])} for p in self.store.list()]

    def public_project(self,pid):
        p=self.store.get(pid)
        p['followup']=followup(p)
        p['plotting']=self.plot_public(p)
        p.pop('assistant_followup',None)
        p.pop('assistant_chat',None)
        p.pop('study_brief_history',None)
        p['research_status']=self.research_status(p)
        p['versions']=[{k:v for k,v in item.items() if k!='text'} for item in p.get('versions',[])]
        for a in p['assets']:
            a.pop('text',None)
            a.pop('file',None)
        for r in p['runs']:
            j=self.jobs.get(r['id'])
            if j is not None:
                r['progress']=j.get('progress','正在准备')
                r['partial']=j.get('partial','')
        return p

    def assert_idle(self,p,allow_inactive=False,allow_analysis=False):
        if self.updating:
            raise ValueError('软件正在安装更新，请稍后修改。')
        if not allow_analysis and self.analysis_busy(p['id']):
            raise ValueError('此项目正在分析，请等待完成或先取消分析。')
        if any(r['status']=='running' for r in p['runs']):
            raise ValueError('此项目正在运行，请等待完成或先停止。')
        if not allow_inactive and lifecycle(p)!='active':
            raise ValueError('此项目已归档或在回收站，请恢复后再修改或运行。')

    def invalidate_inputs(self,p):
        if p['outputs']:
            for sid in p['outputs']:snapshot(p,sid,'输入材料或研究目标变更前')
            p['cursor']=0
            for output in p['outputs'].values():
                output.update(stale=True,approved=False)

    def upload(self,pid,data):
        name=Path(str(data.get('name','材料.txt')).replace('\\','/')).name[:200]
        try:
            raw=base64.b64decode(data['data'],validate=True)
        except Exception:
            raise ValueError('文件数据无效。') from None
        if len(raw)>20*1024*1024:
            raise ValueError('单个文件请控制在 20 MB 内。')
        try:
            text=extract_file(name,raw)
        except ValueError:
            raise
        except Exception:
            raise ValueError('文件无法解析，可能已损坏或扩展名与内容不符。请用原软件重新另存后导入。') from None
        aid=uuid.uuid4().hex
        target=self.store.root/'materials'/pid/(aid+Path(name).suffix.lower())
        with self.lock:
            p=self.store.get(pid)
            self.assert_idle(p)
            if len(p['assets'])>=80:
                raise ValueError('一个项目最多导入 80 份材料，请拆成多个项目。')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(raw)
            p['assets'].append(dict(id=aid,name=name,size=len(raw),chars=len(text),text=text,
                            file=str(target.relative_to(self.store.root)),enabled=True,created=now(),
                            sha256=hashlib.sha256(raw).hexdigest()))
            self.invalidate_inputs(p)
            self.store.put(p)
        return self.public_project(pid)

    def toggle_asset(self,pid,data):
        with self.lock:
            p=self.store.get(pid)
            self.assert_idle(p)
            for a in p['assets']:
                if a['id']==data['id']:
                    if a['enabled']!=bool(data['enabled']):
                        a['enabled']=bool(data['enabled'])
                        self.invalidate_inputs(p)
            self.store.put(p)
        return self.public_project(pid)

    def toggle_literature(self,pid,data):
        with self.lock:
            p=self.store.get(pid)
            self.assert_idle(p)
            matches=[x for x in p['literature'] if data.get('all') or x['id']==data.get('id')]
            if not matches and not data.get('all'):
                raise ValueError('未找到此检索记录。')
            changed=False
            for entry in matches:
                if entry.get('enabled',True)!=bool(data.get('enabled')):
                    entry['enabled']=bool(data['enabled'])
                    changed=True
            if changed:
                self.invalidate_inputs(p)
                self.store.put(p)
        return self.public_project(pid)

    def prepare_report_edit(self,p,data):
        """Apply an edit and downstream invalidation in memory."""
        sid=data['stage']
        stages=FLOWS[p['flow']]['stages']
        if sid not in stages:
            raise ValueError('无效阶段。')
        if len(str(data.get('text','')))>800000:
            raise ValueError('报告过长。')
        previous=p['outputs'].get(sid,{})
        text=str(data.get('text',''))
        if text==previous.get('text'):
            return False
        snapshot(p,sid,'编辑保存前')
        p['outputs'][sid]={**previous,'text':text,'approved':False,'edited':now(),'stale':False}
        self.invalidate(p,stages.index(sid))
        return True

    def edit(self,pid,data):
        with self.lock:
            p=self.store.get(pid)
            self.assert_idle(p)
            if not self.prepare_report_edit(p,data):
                return self.public_project(pid)
            self.store.put(p)
        return self.public_project(pid)

    def invalidate(self,p,index):
        stages=FLOWS[p['flow']]['stages']
        p['cursor']=min(p['cursor'],index)
        for sid in stages[index+1:]:
            if sid in p['outputs']:
                p['outputs'][sid].update(stale=True,approved=False)

    def confirm(self,pid,data):
        with self.lock:
            p=self.store.get(pid)
            self.assert_idle(p)
            stages=FLOWS[p['flow']]['stages']
            sid=data['stage']
            if p['cursor']>=len(stages) or stages[p['cursor']]!=sid:
                raise ValueError('请按顺序确认当前阶段。')
            output=p['outputs'].get(sid,{})
            if not output.get('text','').strip() or output.get('stale'):
                raise ValueError('请先运行当前阶段并检查报告。')
            if STAGES[sid]['gate'] and not data.get('acknowledge'):
                raise ValueError('请阅读核验报告，并明确勾选确认。')
            if p['experiments']=='undecided' and sid in ('research','outline','integrity'):
                raise ValueError('请先在项目概况中补充实验来源声明。')
            output['approved']=True
            output['approved_at']=now()
            actor=data.get('actor','unspecified')
            if actor not in ('author','delegate','unspecified'):
                raise ValueError('无效的确认身份。')
            output['approved_actor']=actor
            p['confirmations'].append(dict(stage=sid,time=now(),note=str(data.get('note',''))[:5000],
                         sha256=hashlib.sha256(output['text'].encode()).hexdigest(),
                         actor=actor,meaning='操作确认已阅读，非真实性认证'))
            p['cursor']+=1
            self.store.put(p)
        return self.public_project(pid)

    def update_info(self,pid,data):
        with self.lock:
            p=self.store.get(pid)
            self.assert_idle(p)
            if 'output_language' in data:
                value=data['output_language']
                if value!='auto':language(value)
                p['output_language']=value
            old_info={k:p[k] for k in ('title','topic','discipline','requirements','citation','experiments')}
            for k in ('title','topic','discipline','requirements','citation'):
                if k in data:
                    p[k]=project_title(data[k]) if k=='title' else str(data[k])[:20000]
            if 'experiments' in data:
                if data['experiments'] not in ('none','declared','undecided'):
                    raise ValueError('无效的实验来源声明。')
                p['experiments']=data['experiments']
            if not p['title'].strip():
                raise ValueError('项目名称不能为空。')
            if any(p[k]!=v for k,v in old_info.items() if k!='title'):
                self.invalidate_inputs(p)
            self.store.put(p)
        return self.public_project(pid)

    def search(self,pid,query,terms=None,journal_only=True,source='crossref'):
        with self.lock:self.assert_idle(self.store.get(pid))
        query=query.strip()
        if not query or len(query)>300:
            raise ValueError('请输入 1–300 字的检索词；英文检索词通常更全面。')
        terms=required_terms(terms) if terms is not None else identifier_terms(query)
        if source not in ('crossref','pubmed'):raise ValueError('不支持的文献来源。')
        if source=='pubmed':
            from .pubmed import retrieve
            entries,total=retrieve(query,journal_only)
            for entry in entries:entry['retrieved']=now()
            entries,rejected=filter_entries(entries,terms,journal_only)
            return self._save_search(pid,query,query,terms,journal_only,entries,rejected,'PubMed',total)
        candidate_query=' '.join(terms) if terms else query
        url='https://api.crossref.org/works?query.bibliographic='+quote(candidate_query)+'&rows=50'
        if journal_only:
            url+='&filter=type:journal-article'
        try:
            with urlopen(Request(url,headers={'User-Agent':'YanxuResearchStudio/0.1 (noncommercial research)'}),timeout=25) as r:
                items=json.loads(r.read(3000000))['message']['items']
        except Exception:
            raise ValueError('Crossref 检索暂时不可用，请稍后再试或导入数据库导出文件。') from None
        entries=[]
        def plain(value):
            return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',value))).strip()
        for i in items:
            title=plain(' '.join(i.get('title',[])))
            if not title:
                continue
            authors=[' '.join([a.get('given',''),a.get('family','')]).strip() for a in i.get('author',[])][:12]
            date=i.get('published',{}).get('date-parts',[[]])[0]
            abstract=plain(i.get('abstract',''))
            entries.append(dict(id=i.get('DOI',uuid.uuid4().hex),title=title,authors=authors,
                       year=date[0] if date else None,doi=i.get('DOI',''),url=i.get('URL',''),
                       journal=plain(' '.join(i.get('container-title',[]))),abstract=abstract,
                       type=i.get('type',''),issue=i.get('issue',''),is_supplement=bool(i.get('relation',{}).get('is-supplement-to')),
                       enabled=False,source='Crossref',status='metadata_only',query=query,retrieved=now()))
        entries,rejected=filter_entries(entries,terms,journal_only)
        return self._save_search(pid,query,candidate_query,terms,journal_only,entries,rejected,'Crossref')

    def _save_search(self,pid,query,candidate_query,terms,journal_only,entries,rejected,source,total=None):
        with self.lock:
            p=self.store.get(pid)
            self.assert_idle(p)
            known={i['id'].lower() for i in p['literature']}
            new=[]
            for entry in entries:
                if entry['id'].lower() not in known:
                    new.append(entry)
                    known.add(entry['id'].lower())
            p['literature'] += new
            p.setdefault('searches',[]).append(dict(query=query,source=source,time=now(),total_available=total,
                candidate_query=candidate_query,required_terms=terms,journal_only=journal_only,candidates=len(entries)+len(rejected),
                returned=len(entries),added=len(new),rejected=rejected))
            self.store.put(p)
        return {'entries':entries,'rejected':rejected,'required_terms':terms,'added':len(new),'project':self.public_project(pid)}

    def context(self,p,sid,note):
        config={k:p[k] for k in ('title','topic','discipline','citation','requirements','experiments')}
        config.update(route=p.get('route','ars'),goal=p.get('goal','unsure'),data_kind=p.get('data_kind','unknown'),
                      selected_skill=p.get('selected_skill',''))
        chunks=[output_instruction(output_language(p,self.store)),'作者声明与任务配置：\n'+json.dumps(config,ensure_ascii=False,indent=2)]
        chunks += ['\n作者本次要求：\n'+note, brief_context(p)]
        if p.get('research',{}).get('enabled'):
            chunks.append('\n研究工具记录（由作者整理，完成状态不是真实性认证；实验计划不得写成已执行结果）。如引用库中文献，请使用 [@完整文献ID] 标记，不得虚构ID；未核实的支持关系应保留待核实。书目核对不等于全文支持：\n'+json.dumps(p['research'],ensure_ascii=False))
        selected=[l for l in p['literature'] if l.get('enabled',True)]
        if selected:
            chunks.append('\n勾选的检索结果（仅元数据或摘要，未核验全文支持关系）：\n'+json.dumps(selected,ensure_ascii=False))
        for a in p['assets']:
            if a.get('enabled',True):
                if a['name'].lower().endswith(('.xlsx','.png','.jpg','.jpeg','.h5ad','.zip','.gmt','.fcs','.vcf')) or (p['flow']=='scientific_data' and a['name'].lower().endswith(('.csv','.tsv'))):
                    chunks.append('\n表格材料（内容由本机工具读取）：'+json.dumps({k:a.get(k) for k in ('name','size','chars','sha256')},ensure_ascii=False))
                    continue
                chunks.append('\n<material name='+json.dumps(a['name'],ensure_ascii=False)+'>\n'+a['text']+'\n</material>')
        steps=FLOWS[p['flow']]['stages']
        for prior in steps[:steps.index(sid)+1]:
            output=p['outputs'].get(prior)
            if output and not output.get('stale'):
                chunks.append(f'\n已有阶段报告 {prior}（成果已确认：{output.get("approved",False)}）：\n'+output['text'])
        if sid=='summary':
            chunks.append('\n实际执行日志：\n'+json.dumps(p['runs'],ensure_ascii=False)+'\n作者确认：\n'+json.dumps(p['confirmations'],ensure_ascii=False))
        text='\n'.join(chunks)
        if len(text)>240000:
            raise ValueError('本次材料超过 24 万字符。请在材料页取消勾选无关材料，或拆分项目；应用没有静默截断材料。')
        return text

    def start(self,pid,data):
        sid=data['stage']
        settings=self.store.get_settings(secret=not STAGES.get(sid,{}).get('local',False))
        with self.lock:
            if self.updating:
                raise ValueError('软件正在准备安装更新，请重启完成后再运行研究任务。')
            p=self.store.get(pid)
            self.assert_idle(p)
            steps=FLOWS[p['flow']]['stages']
            if sid not in steps or steps.index(sid)>p['cursor']:
                raise ValueError('请先完成并确认前面的阶段。')
            if sid in ('integrity','review','revise','re_review','screen','final_integrity'):
                if not any(a.get('enabled',True) for a in p['assets']) and not p['outputs']:
                    raise ValueError('请先导入需要审阅的稿件或待筛选文献。')
            context=self.context(p,sid,str(data.get('note',''))[:20000])
            if data.get('context_sha256') and data['context_sha256']!=hashlib.sha256(context.encode()).hexdigest():
                raise ValueError('输入在预览后发生变化，请重新查看运行前确认。')
            rid=uuid.uuid4().hex
            route=FLOWS[p['flow']]['route']
            from .scientific import manifest
            commit=manifest()['commit'] if route=='scientific' else SOURCE_COMMIT
            run=dict(id=rid,stage=sid,status='running',started=now(),mode='local' if STAGES[sid].get('local') else settings['mode'],
                     model='' if STAGES[sid].get('local') else settings['codex_model'] if settings['mode']=='codex' else settings['api_model'],
                     provider=settings['provider'] if settings['mode']=='api' else None,
                     route=route,context_chars=len(context),source_commit=commit,selected_skill=p.get('selected_skill',''))
            run['note']=str(data.get('note',''))[:20000]
            run['output_language']=output_language(p,self.store)
            settings['output_language']=run['output_language']
            p['runs'].append(run)
            self.store.put(p)
            self.jobs[rid]={'cancel':threading.Event(),'progress':'正在准备本机计算' if STAGES[sid].get('local') else '正在连接模型','partial':''}
        threading.Thread(target=self._run,args=(pid,rid,sid,context,settings),daemon=True).start()
        return self.public_project(pid)

    def _run(self,pid,rid,sid,context,settings):
        job=self.jobs[rid]
        cancel=job['cancel']
        provider_details=[]
        def progress(label,partial=''):
            job.update(progress=label,partial=partial)
        def complete(system,prompt):
            system+=output_instruction(settings.get('output_language','zh-CN'))
            if cancel.is_set():
                raise Cancelled('任务已停止，已保存之前的成果。')
            if settings['mode']=='codex':
                workdir=self.store.root/'invocations'/rid
                workdir.mkdir(parents=True,exist_ok=True)
                with CodexClient(settings,str(workdir),cancel) as client:
                    result=client.complete(system,prompt,settings.get('codex_model',''),progress)
                    provider_details.append(getattr(client,'response_metadata',{}))
                    return result
            return api_complete(settings,settings.get('api_key',''),system,prompt,progress,cancel)
        try:
            s=STAGES[sid]
            with self.lock:project=self.store.get(pid)
            scientific=FLOWS[project['flow']]['route']=='scientific'
            system=(SCIENTIFIC_INSTRUCTION if scientific else BASE_INSTRUCTION)+'\n当前阶段：'+s['title']+'\n阶段目标：'+s['instruction']
            execution=None
            if s.get('local'):
                from .data_tools import table_report
                progress('正在本机计算表格质控')
                text,execution=table_report(project['assets'],settings.get('output_language','zh-CN'))
            elif scientific:
                from .scientific import method_context
                text=complete(system+method_context(s,project.get('selected_skill','')),context)
            elif sid=='review':
                roles=[('期刊适配与定位','eic_agent'),('研究方法','methodology_reviewer_agent'),
                       ('领域贡献','domain_reviewer_agent'),('跨学科视角','perspective_reviewer_agent'),
                       ('核心论证质疑','devils_advocate_reviewer_agent')]
                reports=[]
                for i,(label,agent) in enumerate(roles):
                    progress(f'审阅视角 {i+1}/5：{label}')
                    result=complete(system+role_context(s,agent)+'\n本次只从“'+label+'”视角审阅。',context)
                    reports.append('## '+tr(label,settings.get('output_language','zh-CN'))+'\n\n'+result)
                    job['partial']='\n\n---\n\n'.join(reports)
                progress('正在汇总修改路线')
                synthesis=complete(system+'\n依据五份模拟审阅报告，合并重复问题，保留分歧，输出按重要性排序的修改路线。不得给出正式录用承诺。',
                                   '\n\n'.join(reports))
                text='# 多视角模拟审阅\n\n> 五个角色使用同一连接，角色分工不代表统计独立性；结果未经校准。\n\n'+synthesis+'\n\n---\n\n'+'\n\n'.join(reports)
            elif sid=='screen':
                reports=[]
                for i in range(2):
                    progress(f'独立提示的筛选角色 {i+1}/2')
                    reports.append(complete(system+role_context(s)+'\n你是筛选角色 '+str(i+1)+'，只看材料，不参考另一位的决定。',context))
                progress('正在裁决筛选分歧')
                adjudication=complete(system+'\n比较两份报告，只对分歧做有证据的裁决。保持原始记录ID。缺少信息维持 uncertain。',
                                     context+'\n角色 A：\n'+reports[0]+'\n角色 B：\n'+reports[1])
                text='# 文献筛选结果\n\n> 两个同模型角色分别判断，未开展独立人工双筛。最终决定需研究团队确认。\n\n'+adjudication+'\n\n## 角色 A\n'+reports[0]+'\n\n## 角色 B\n'+reports[1]
            else:
                text=complete(system+role_context(s),context)
            if cancel.is_set():
                raise Cancelled('任务已停止。')
            with self.lock:
                p=self.store.get(pid)
                snapshot(p,sid,'重新生成报告前')
                self.invalidate(p,FLOWS[p['flow']]['stages'].index(sid))
                p['outputs'][sid]=dict(text=text,approved=False,stale=False,run=rid,created=now(),
                                      verification='human_review_required',output_language=settings.get('output_language','zh-CN'))
                r=next(r for r in p['runs'] if r['id']==rid)
                r.update(status='completed',finished=now(),output_chars=len(text))
                if provider_details:
                    r['provider_details']=provider_details
                if execution:
                    r['tool_execution']=execution
                self.store.put(p)
            progress('报告已保存，等待确认',text)
        except Exception as e:
            with self.lock:
                p=self.store.get(pid)
                r=next(r for r in p['runs'] if r['id']==rid)
                # Provider messages never include API keys or remote response bodies.
                message=str(e) if isinstance(e,(ValueError,ProviderError)) else '运行遇到异常，请重试；已有成果已保留。'
                r.update(status='cancelled' if cancel.is_set() else 'failed',finished=now(),error=message)
                if job.get('partial'):
                    r['partial_saved']=job['partial']
                self.store.put(p)
            progress(message)
        finally:
            settings.pop('api_key',None)
            self.jobs.pop(rid,None)

    def stop(self,pid):
        p=self.store.get(pid)
        for r in p['runs']:
            j=self.jobs.get(r['id'])
            if r['status']=='running' and j is not None:
                j['cancel'].set()
        return {'ok':True}

    def inspect_codex(self):
        settings=self.store.get_settings()
        cwd=self.store.root/'connection-check'
        cwd.mkdir(exist_ok=True)
        with CodexClient(settings,str(cwd)) as c:
            return c.inspect()

    def export(self,pid,fmt='md',sid=''):
        p=self.store.get(pid)
        locale=self.store.language()['ui_language']
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',p['title']).rstrip(' .')[:90] or '研究项目'
        stages=FLOWS[p['flow']]['stages']
        route=FLOWS[p['flow']]['route']
        from .routing import ROUTES
        source_note=tr('研究路径：',locale)+tr(ROUTES[route]['name'],locale)+' · '+ROUTES[route]['source']
        if route=='scientific':
            from .scientific import manifest
            source_note+=' · '+manifest()['version']+' · '+manifest()['commit']
        chosen=[sid] if sid in stages else stages
        parts=['# '+p['title'],'> '+tr('研序 Research Studio 导出',locale)+' · '+now()+'\n> '+source_note+'\n> '+tr('AI 辅助内容须由作者核实。',locale)]
        for key in chosen:
            if key in p['outputs']:
                o=p['outputs'][key]
                state='已过期，需重新运行' if o.get('stale') else ('成果已确认' if o.get('approved') else '待确认')
                parts+=['\n---\n\n# '+tr(STAGES[key]['title'],locale)+'\n\n> '+tr(state,locale)+'\n\n'+o['text']]
        if len(parts)==2:
            parts.append('\n'+tr('尚无阶段报告。',locale)+'\n\n'+tr('研究目标：',locale)+p['topic'])
        md='\n\n'.join(parts)
        md=bibliography(p,md)
        if fmt=='md':
            return name+'.md',md.encode('utf-8-sig'),'text/markdown'
        if fmt=='docx':
            return name+'.docx',make_docx(md,self.plot_word_figures(pid,sid if sid in stages else ''),locale=locale),'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if fmt=='json':
            return name+'.json',json.dumps({'format':'yanxu-project-v1','project':p},ensure_ascii=False,indent=2).encode('utf-8'),'application/json'
        if fmt=='zip':
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('项目备份.json',json.dumps({'format':'yanxu-project-v1','project':p},ensure_ascii=False,indent=2))
                z.writestr('阶段报告.md',md)
                z.writestr('研究工具记录.md',self.research_export(pid,'research')[1])
                for a in p['assets']:
                    path=self.store.root/a['file']
                    if path.is_file():
                        z.write(path,'materials/'+Path(a['file']).name)
                for key,raw in self.analysis_bundle(p).items():
                    z.writestr('analysis-results/'+key+'.json',raw)
                z.writestr('来源与说明.txt',source_note+'\nARS 来源：https://github.com/Imbad0202/academic-research-skills\n版本提交：'+SOURCE_COMMIT+'\nCC BY-NC 4.0；本应用为非官方图形化改编。\nScientific Agent Skills 来源：https://github.com/K-Dense-AI/scientific-agent-skills\n该库根许可 MIT，各技能另有许可声明。\n备份不包含模型密钥或 Codex 登录凭据。')
            return name+'.zip',output.getvalue(),'application/zip'
        raise ValueError('不支持的导出格式。')


def make_docx(md,figures=None,locale='zh-CN'):
    from .word_export import make_docx as build
    return build(md,figures=figures,locale=locale)
