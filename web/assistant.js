/* Application-owned navigation; the selected conversation is independent of the page. */
const AS={open:false,scope:null,state:null,project:null,loading:false,sending:false,error:'',preview:null,
 drafts:{},kinds:{},rechecks:{},options:{},errors:{},stages:{},stageOverrides:{},scrolls:{},pending:{},seen:{},unread:{},
 contextOpen:false,scopeOpen:false,scopeQuery:'',pick:null,epoch:0,navEpoch:0,timer:null,opener:null,
 catalog:null,catalogLoading:false,catalogError:'',pageKey:'',arrival:null,wide:false,position:null,dragging:null};
const asQuick=[['next',L('下一步做什么')],['blocked',L('排查流程卡点')],['stage',L('解释当前阶段')],['connection',L('连接故障')]];
const asTargets=new Set(['drawing','analysis','paper','experiments','overview','work','materials','research','history','settings','help','pathways','backups','projects']);
const asProjectTargets=new Set(['drawing','analysis','paper','experiments','overview','work','materials','research','history']);
const asNavNames={drawing:L('绘图'),analysis:L('数据分析'),paper:L('论文图表'),experiments:L('实验方案'),overview:L('项目进展与记忆'),work:L('研究流程'),materials:L('文献与材料'),research:L('研究工具'),history:L('运行记录'),settings:L('模型与连接'),help:L('使用指南'),pathways:L('研究路径'),backups:L('备份与恢复'),projects:L('研究项目')};
try{const saved=JSON.parse(localStorage.getItem('yanxu-assistant-window-v1')||'null');if(saved){AS.wide=saved.wide===true;if(Number.isFinite(saved.position?.x)&&Number.isFinite(saved.position?.y))AS.position=saved.position;}}catch{}
function assistantScope(){return S.view==='project'&&S.project?S.project.id:''}
function assistantLocation(){return S.view==='project'?({work:'project',materials:'materials',research:'research',history:'history'}[S.tab]||'project'):S.view}
function assistantProject(){return S.project?.id===AS.scope?S.project:AS.project?.id===AS.scope?AS.project:null}
function assistantOptions(){return AS.options[AS.scope]||(AS.options[AS.scope]={report:false,history:true,memory:true,materials:[]})}
function assistantLastError(){return AS.errors[AS.scope??assistantScope()]||''}
function assistantData(){const same=AS.scope===assistantScope();return {project:AS.scope||'',view:same?assistantLocation():AS.scope?'project':'home',
 stage:AS.scope?(AS.stageOverrides[AS.scope]||(same?S.activeStage:AS.stages[AS.scope]))||'':'',options:{...assistantOptions(),materials:[...assistantOptions().materials]},error:assistantLastError(),draft_kind:AS.kinds[AS.scope]||'',review_of:AS.rechecks[AS.scope]||''}}
function assistantRequestId(){return crypto.randomUUID().replaceAll('-','')}
function assistantRememberError(text){AS.errors[assistantScope()]=String(text||'').slice(0,2000)}
function assistantPageKey(){return [S.view,S.project?.id||'',S.tab,S.activeStage,S.project?.updated||''].join('|')}
function assistantRecordScroll(){const list=$('#as-messages');if(list&&!AS.loading&&!AS.scopeOpen&&AS.renderedScope===AS.scope&&AS.scope!==null)AS.scrolls[AS.scope]={top:list.scrollTop,bottom:list.scrollHeight-list.clientHeight-list.scrollTop<65};}
function assistantMarkSeen(){if(!AS.open||AS.loading||AS.scopeOpen||!AS.state)return;const last=[...(AS.state.messages||[])].reverse().find(m=>m.role==='assistant'&&m.status!=='running');if(last)AS.seen[AS.scope]=last.id;delete AS.unread[AS.scope];}
async function assistantCatalogRefresh(){
 if(AS.catalogLoading)return;AS.catalogLoading=true;const requestedJob=AS.state?.job?.id;
 try{const value=await api('assistant/catalog',{});if(!Array.isArray(value.scopes))return;const first=!AS.catalog;AS.catalog=value;AS.catalogError='';
  for(const scope of value.scopes){if(first&&AS.seen[scope.id]===undefined)AS.seen[scope.id]=scope.latest;if(scope.latest&&scope.latest!==AS.seen[scope.id])AS.unread[scope.id]=true;}
  if(requestedJob&&AS.state?.job?.id===requestedJob&&!(value.jobs||[]).some(j=>j.project===AS.scope))AS.state={...AS.state,job:null};
  assistantMarkSeen();assistantPaint();
 }catch(error){AS.catalogError=error.message;}finally{AS.catalogLoading=false;assistantPoll();}
}
function assistantSync(){
 document.body.classList.toggle('assistant-open',AS.open);
 if(!AS.catalog&&!AS.catalogLoading&&typeof S!=='undefined'&&S.catalog)assistantCatalogRefresh();
 if(!AS.open){assistantPaint();return;}
 if(AS.scope===null){AS.scope=assistantScope();AS.project=AS.scope?S.project:null;AS.epoch++;}
 const key=assistantPageKey(),changed=key!==AS.pageKey;AS.pageKey=key;
 assistantPaint();if(!AS.state||changed) return assistantRefresh().catch(err=>{AS.error=err.message;AS.loading=false;assistantPaint()});
}
async function assistantRefresh(){
 if(!AS.open||AS.scope===null)return;
 const scope=AS.scope,epoch=++AS.epoch;AS.loading=!AS.state;if(AS.loading)assistantPaint();
 const state=await api('assistant/state',assistantData());
 if(!AS.open||AS.scope!==scope||epoch!==AS.epoch)return;
 AS.state=state;AS.stages[scope]=state.stage;if(AS.loading)AS.restoreScroll=true;AS.loading=false;
 const opts=assistantOptions(),ids=new Set((state.materials||[]).map(a=>a.id));opts.materials=opts.materials.filter(id=>ids.has(id));if(!state.report_available)opts.report=false;
 assistantMarkSeen();assistantPaint();assistantPoll();
}
function assistantPoll(){
 clearTimeout(AS.timer);const working=AS.state?.job||(AS.catalog?.jobs||[]).length||Object.values(AS.pending).some(Boolean);
 if(working)AS.timer=setTimeout(async()=>{if(AS.open)try{await assistantRefresh()}catch(err){AS.error=err.message;assistantPaint()}await assistantCatalogRefresh();},1800);
}
async function assistantSelectScope(scope){
 if(typeof scope!=='string'||scope&& !/^[a-f0-9]{32}$/.test(scope))return;
 assistantRecordScroll();const epoch=++AS.epoch;AS.scope=scope;AS.state=null;AS.project=null;AS.preview=null;AS.error='';AS.loading=true;AS.restoreScroll=true;AS.sending=!!AS.pending[scope];AS.scopeOpen=false;AS.pick=null;AS.contextOpen=false;assistantPaint();
 try{if(scope){const p=await api('project/'+scope);if(scope!==AS.scope||epoch!==AS.epoch)return;AS.project=p;}
  if(scope!==AS.scope||epoch!==AS.epoch)return;await assistantRefresh();
 }catch(error){if(scope===AS.scope&&epoch===AS.epoch){AS.error=error.message;AS.loading=false;assistantPaint();}}
}
function assistantScopeChoices(){
 const scopes=AS.catalog?.scopes||[{id:'',title:L('工作区帮助'),lifecycle:'active'}],q=AS.scopeQuery.trim().toLowerCase();
 return H`<section class="as-scope-picker" aria-label="切换对话环境"><p>切换对话，当前页面保留。</p><label class="as-search-label">搜索项目<input id="as-scope-search" value="${e(AS.scopeQuery)}" placeholder="项目名称"></label><div id="as-scope-results">${assistantScopeRows(scopes,q)}</div>${AS.catalogError?H`<p class="as-error">${e(AS.catalogError)}</p><button data-action="as-refresh-catalog">重新读取项目</button>`:''}</section>`;
}
function assistantScopeRows(scopes,q){return scopes.filter(s=>!q||s.title.toLowerCase().includes(q)).map(s=>`<button class="as-scope-choice" data-action="as-select-scope" data-project="${e(s.id)}" aria-current="${s.id===AS.scope?'true':'false'}"><span><strong>${e(s.id?s.title:L('工作区帮助'))}</strong><small>${s.id?(s.id===assistantScope()?L('当前页面项目 · '):'')+(s.lifecycle==='archived'?L('已归档 · 可查看与求助'):L('项目独立对话')):L('原首页对话 · 软件使用帮助')}</small></span>${AS.unread[s.id]?H`<i class="as-unread-dot" aria-label="有新回答"></i>`:''}${s.id===AS.scope?icon('check',15):''}</button>`).join('')||H`<p class="as-empty">没有匹配的项目。</p>`}
function assistantNavigationCard(m,a){
 if(!asTargets.has(a.id))return '';
 const target=a.id,label=a.label||L('打开')+asNavNames[target],pid=a.project||AS.scope||'';
 if(!asProjectTargets.has(target))return `<button data-action="as-route" data-target="${target}">${e(label)}${icon('chevron',12)}</button>`;
 const specific=['drawing','analysis','paper','experiments'].includes(target),choose=AS.pick?.message===m.id&&AS.pick?.target===target;
 if(pid&&!choose)return `<button data-action="as-route" data-target="${target}" data-project="${e(pid)}">${e(label)}${icon('chevron',12)}</button>`;
 if(!specific&&!choose)return H`<button data-action="as-pick" data-target="${target}" data-message="${e(m.id||'')}">选择项目 · ${e(label)}</button>`;
 if(m.id!==AS.latestAction&&!choose)return H`<button data-action="as-pick" data-target="${target}" data-message="${e(m.id||'')}">选择项目 · ${e(label)}</button>`;
 const projects=(AS.catalog?.scopes||[]).filter(p=>p.id&&p.lifecycle==='active'),query=choose?AS.pick.query||'':'';
 const filtered=projects.filter(p=>p.title.toLowerCase().includes(query.toLowerCase())),visible=(choose&&AS.pick.all)||query?filtered:filtered.slice(0,3);
 return H`<section class="as-nav-card" aria-label="选择项目并${e(label)}"><strong>${projects.length?L('想进入哪个项目的')+e(asNavNames[target])+'？':L('先创建一个研究项目')}</strong>${projects.length>3?H`<label class="as-search-label">搜索项目<input id="as-project-search-${e(m.id||'latest')}-${target}" data-as-project-search="${target}" data-message="${e(m.id||'')}" value="${e(query)}" placeholder="输入项目名称"></label>`:''}<div class="as-nav-projects">${!AS.catalog?H`<p>正在读取项目…</p>`:visible.map(p=>`<div><span title="${e(p.title)}">${e(p.title)}</span><button data-action="as-route" data-target="${target}" data-project="${e(p.id)}">${e(label)}</button></div>`).join('')||H`<p>没有匹配的项目。</p>`}</div>${!query&&projects.length>3&&!(choose&&AS.pick.all)?H`<button data-action="as-all-projects" data-target="${target}" data-message="${e(m.id||'')}">查看全部 ${projects.length} 个项目</button>`:''}<button class="as-new-project" data-action="as-create-project" data-target="${target}">新建项目并进入${e(asNavNames[target])}</button></section>`;
}
function assistantMessage(m){
 const user=m.role==='user',failed=['failed','stopped','interrupted'].includes(m.status);
 return `<article class="as-message ${user?'as-user':'as-answer'}"><div class="as-message-label">${user?L('你'):m.mode==='local'?L('本机指导'):L('AI 助手')}<small>${e({running:L('回答中'),completed:'',failed:L('未完成'),stopped:L('已停止'),interrupted:L('已中断')}[m.status]||'')}</small></div>${m.outdated?H`<p class="as-outdated">依据的项目内容或软件版本已更新，请重新核对这条建议。</p>`:''}<div class="as-markdown">${user?`<p class="as-user-text">${e(m.text)}</p>`:markdown(m.text)}</div>${!user&&m.sources?.length?H`<details class="as-sources"><summary>参考范围</summary><p>${m.sources.map(e).join(' · ')}</p></details>`:''}${!user&&m.status==='completed'?`<div class="as-action-links">${(m.actions||[]).filter(a=>asTargets.has(a.id)).map(a=>assistantNavigationCard(m,a)).join('')}</div>`:''}${!user&&m.outdated&&m.mode!=='local'&&m.status==='completed'&&!m.proposal?H`<button class="as-retry" data-action="as-recheck" data-message="${e(m.id)}">按最新内容复核</button>`:''}${typeof assistantProposalCard==='function'?assistantProposalCard(m):''}${failed?H`<button class="as-retry" data-action="as-retry" data-turn="${e(m.turn)}">重新提问</button>`:''}</article>`;
}
function assistantPreviewDialog(){assistantPaint();$('#as-send-confirm')?.focus({preventScroll:true})}
function assistantPreviewCard(){const p=AS.preview;if(!p)return '';return H`<section class="as-preview" aria-label="确认本次提问"><strong>本次发送范围</strong><p>${e(p.connection_label||L('当前模型连接'))} · ${p.calls??1} 次模型请求</p><details><summary>查看参考内容 · ${Number(p.context_chars||0).toLocaleString()} 字符</summary><ul>${(p.sources||[]).map(x=>`<li>${e(x)}</li>`).join('')}</ul></details><div><button data-action="as-cancel-preview">返回调整</button><button id="as-send-confirm" data-action="as-send" ${AS.sending?'disabled':''}>${AS.sending?L('正在提交…'):L('确认发送')}</button></div></section>`}
function assistantCompose(){
 const state=AS.state,opts=assistantOptions(),busy=!!state?.job||AS.sending,quick=AS.scope?asQuick:[['help',L('从哪里开始')],['connection',L('连接故障')]];
 return H`<div class="as-compose"><details class="as-quick-tools"><summary>常用问题 · 本机指导</summary><div class="as-quick">${quick.map(([id,label])=>`<button data-action="as-quick" data-intent="${id}" ${busy?'disabled':''}>${label}</button>`).join('')}${assistantLastError()?H`<button data-action="as-quick" data-intent="error">排查刚才的错误</button>`:''}</div></details><details class="as-context" id="as-context" ${AS.contextOpen?'open':''}><summary>参考范围 <span>${opts.report?L('阶段报告 · '):''}${opts.materials.length?opts.materials.length+L(' 份材料 · '):''}${opts.history?L('最近 6 轮'):L('不带历史')}</span></summary><div class="as-context-body"><p>${AS.scope?L('参考所选项目目标与流程；材料正文需主动选择。'):L('参考软件指南；不读取项目内容。')}</p><label><input type="checkbox" id="as-history" ${opts.history?'checked':''}>带上最近最多 6 轮对话</label><small>历史回答可能含此前选过的材料，可关闭历史。</small>${AS.scope?H`<label><input type="checkbox" id="as-memory" ${opts.memory!==false?'checked':''}>项目记忆（${state?.memory_count||0} 条）</label><label><input type="checkbox" id="as-report" ${opts.report?'checked':''} ${!state?.report_available?'disabled':''}>当前阶段报告${!state?.report_available?L('（尚无）'):''}</label>${(state?.materials||[]).length?H`<fieldset><legend>材料正文</legend>${state.materials.map(a=>`<label><input type="checkbox" data-as-material="${e(a.id)}" ${opts.materials.includes(a.id)?'checked':''}><span>${e(a.name)}</span></label>`).join('')}</fieldset>`:''}`:''}</div></details>
 ${AS.error?H`<div class="as-error" role="alert">${e(AS.error)} <button data-action="as-refresh">重新读取对话</button></div>`:''}${AS.rechecks[AS.scope]?H`<div class="as-recheck-note">正在复核旧建议，请检查参考范围。<button data-action="as-cancel-recheck">改为普通提问</button></div>`:''}
 ${AS.scope?H`<label class="as-mode"><span>协助方式</span><select id="as-kind" aria-label="协助方式" aria-label="协助方式" ${busy||state?.readonly?'disabled':''}><option value="">只问问题</option>${Object.entries(typeof asKinds!=='undefined'?asKinds:{}).map(([k,n])=>H`<option value="${k}" ${AS.kinds[AS.scope]===k?'selected':''}>生成${n}草稿</option>`).join('')}</select></label>`:''}
 ${AS.preview?assistantPreviewCard():H`<label class="as-input-label" for="as-input">向 AI 助手提问</label><textarea id="as-input" rows="2" maxlength="6000" placeholder="例如：我想绘图，从哪里进入？">${e(AS.drafts[AS.scope]||'')}</textarea><div class="as-send-row"><small>Ctrl + Enter 提问</small>${state?.job?H`<button class="btn secondary" data-action="as-stop">停止回答</button>`:`<button class="btn primary" data-action="as-preview" ${busy||AS.loading||!(AS.drafts[AS.scope]||'').trim()?'disabled':''}>${AS.sending?L('正在准备…'):L('提问')}${icon('arrow',15)}</button>`}</div>`}</div>`;
}
function assistantPaint(){
 const root=$('#assistant-root');if(!root||AS.dragging)return;
 if(!AS.open){const count=Object.keys(AS.unread).length,working=(AS.catalog?.jobs||[]).length||Object.values(AS.pending).some(Boolean);root.innerHTML=H`<button class="as-launcher" data-action="as-open" aria-label="打开 AI 助手${count?L('，有新回答'):''}" title="AI 助手" aria-expanded="false">${icon('spark',23)}${count?'<i class="as-unread-dot"></i>':working?'<i class="as-working-dot"></i>':''}</button>`;return;}
 const focus=document.activeElement,focused=focus&&root.contains(focus)?focus.id:'',start=focus?.selectionStart,end=focus?.selectionEnd;
 if(!AS.restoreScroll)assistantRecordScroll();AS.restoreScroll=false;
 const state=AS.state,title=(AS.scope?state?.title||assistantProject()?.title:L('工作区帮助'))||L('读取对话中'),page=assistantScope(),different=page!==AS.scope;
 AS.latestAction=[...(state?.messages||[])].reverse().find(m=>m.role==='assistant'&&m.status==='completed'&&(m.actions||[]).some(a=>asTargets.has(a.id)))?.id;
 const background=(AS.catalog?.jobs||[]).find(j=>j.project!==AS.scope),backgroundTitle=AS.catalog?.scopes.find(s=>s.id===background?.project)?.title||L('其他对话');
 root.innerHTML=H`<aside class="assistant-panel ${AS.wide?'as-wide':''}" aria-label="AI 助手"><header class="as-header" data-as-drag tabindex="0" aria-label="拖动助手窗口"><h2>${icon('spark',18)}AI 助手</h2><div><button class="icon-btn as-size-toggle" data-action="as-resize" aria-label="${AS.wide?L('标准大小'):L('舒展阅读')}" title="${AS.wide?L('标准大小'):L('舒展阅读')}">${icon(AS.wide?'layers':'expand',16)}</button><button class="icon-btn" data-action="as-close" aria-label="收起 AI 助手" title="收起">${icon('chevron',18)}</button></div></header><div class="as-scope"><button data-action="as-scopes" aria-expanded="${AS.scopeOpen}" title="${e(title)}"><span><small>当前对话</small><strong>${e(title)}</strong></span>${icon('chevron',14)}</button><details class="as-more"><summary aria-label="更多助手操作">•••</summary><div>${AS.scope?H`<button data-action="as-overview">项目进展与记忆</button>`:''}<button data-action="as-export" ${!state?.messages?.length?'disabled':''}>保存当前对话</button><button data-action="as-clear" ${state?.job||AS.sending||!state?.messages?.length?'disabled':''}>清空当前对话</button></div></details></div>
 ${different?`<div class="as-page-hint">${page?L('当前页面：')+e(S.project.title):L('当前页面：工作台')}<button data-action="as-current">${page?L('在该项目继续对话'):L('切换工作区帮助')}</button></div>`:''}${AS.arrival&&AS.arrival.pid===page?H`<div class="as-arrival" role="status">已打开${e(AS.arrival.label)}</div>`:''}${background?H`<button class="as-background" data-action="as-select-scope" data-project="${e(background.project)}">${e(backgroundTitle)} · 正在回答，查看进度</button>`:''}
 <div class="as-messages" id="as-messages" tabindex="0" aria-label="助手对话记录">${AS.scopeOpen?assistantScopeChoices():AS.loading?H`<p class="as-empty">正在读取对话…</p>`:!state?.messages?.length?H`<div class="as-welcome"><h3>需要我带你去哪里？</h3><p>问功能入口，或讨论当前项目的下一步。</p><div class="as-quick"><button data-action="as-ask-drawing">我想绘图</button><button data-action="as-quick" data-intent="next">下一步做什么</button></div></div>`:state.messages.map(assistantMessage).join('')}${!AS.scopeOpen&&state?.job?`<div class="as-progress" role="status"><span class="spinner"></span>${e(state.job.progress)}</div>`:''}</div>${AS.scopeOpen?'':assistantCompose()}</aside>`;
 const list=$('#as-messages'),position=AS.scrolls[AS.scope];if(list&&!AS.scopeOpen)list.scrollTop=position&&!position.bottom?position.top:list.scrollHeight;
 AS.renderedScope=AS.scope;assistantFit();const next=focused?document.getElementById(focused):null;if(next){next.focus({preventScroll:true});if(typeof start==='number'&&next.setSelectionRange)next.setSelectionRange(start,end)}
}
function assistantSaveWindow(){try{localStorage.setItem('yanxu-assistant-window-v1',JSON.stringify({wide:AS.wide,position:AS.position}))}catch{}}
function assistantFit(){const panel=$('.assistant-panel');if(!panel||!AS.position)return;const r=panel.getBoundingClientRect(),w=window.innerWidth,h=window.innerHeight;AS.position={x:Math.max(12,Math.min(AS.position.x,w-r.width-12)),y:Math.max(12,Math.min(AS.position.y,h-r.height-12))};Object.assign(panel.style,{left:AS.position.x+'px',top:AS.position.y+'px',right:'auto',bottom:'auto'});}
async function assistantOpen(scope){AS.opener=document.activeElement;AS.open=true;if(typeof scope==='string'&&scope!==AS.scope)await assistantSelectScope(scope);else{if(AS.scope===null){AS.scope=assistantScope();AS.project=AS.scope?S.project:null;}await assistantRefresh();}assistantCatalogRefresh();assistantPaint();}
function assistantMinimize(){AS.dragging=null;assistantRecordScroll();AS.open=false;AS.scopeOpen=false;assistantSync();if(AS.opener?.isConnected)AS.opener.focus();assistantPoll();}
async function assistantPreview(){
 const text=(AS.drafts[AS.scope]||'').trim();if(!text||AS.sending||AS.state?.job)return;
 const scope=AS.scope,epoch=AS.epoch,data=assistantData();AS.error='';AS.sending=true;AS.pending[scope]=true;assistantPaint();
 try{const preview=await api('assistant/preview',{...data,text});if(AS.scope!==scope||AS.epoch!==epoch||(AS.drafts[scope]||'').trim()!==text)return;AS.preview={...preview,text,data};
  AS.sending=false;AS.pending[scope]=false;if(preview.local){await assistantSend();return;}assistantPreviewDialog();
 }catch(err){if(AS.scope===scope&&AS.epoch===epoch)AS.error=err.message;}finally{AS.pending[scope]=false;if(AS.scope===scope)AS.sending=false;assistantPaint();}
}
async function assistantSend(intent=''){
 if(AS.sending||AS.state?.job)return;
 const scope=AS.scope,epoch=AS.epoch,preview=AS.preview;if(!intent&&(!preview||(AS.drafts[scope]||'').trim()!==preview.text))return;
 const data=intent?{...assistantData(),intent,draft_kind:'',review_of:''}:{...preview.data,text:preview.text,context_sha256:preview.sha256};
 AS.sending=true;AS.pending[scope]=true;AS.error='';assistantPaint();
 try{const state=await api('assistant/send',{...data,request_id:assistantRequestId()});
  if(!intent&&(AS.drafts[scope]||'').trim()===preview.text){AS.drafts[scope]='';delete AS.rechecks[scope];delete AS.stageOverrides[scope];}
  if(AS.scope!==scope||AS.epoch!==epoch)return;AS.state=state;AS.preview=null;AS.scrolls[scope]={top:0,bottom:true};AS.restoreScroll=true;assistantMarkSeen();
 }catch(err){if(AS.scope===scope&&AS.epoch===epoch)AS.error=err.message;}finally{AS.pending[scope]=false;if(AS.scope===scope)AS.sending=false;assistantPaint();assistantPoll();if(AS.catalog)assistantCatalogRefresh();}
}
async function assistantNavigate(target,pid=''){
 if(!asTargets.has(target))return;
 if(S.settingsDirty)throw Error(L('连接设置有未保存修改，请先保存后跳转。'));
 const epoch=++AS.navEpoch,initial=[S.view,S.project?.id,S.tab,S.activeStage].join('|');if(S.draft!==null)await saveOutput(true);
 if(initial!==[S.view,S.project?.id,S.tab,S.activeStage].join('|')||epoch!==AS.navEpoch)return;
 if(S.draft!==null)throw Error(L('仍有未保存的正文修改，请保存后再跳转。'));const page=assistantPageKey();
 const route=await api('assistant/navigate',{target,project:pid});if(epoch!==AS.navEpoch||page!==assistantPageKey())return;
 if(typeof cacheResearchDraft==='function')cacheResearchDraft();
 if(route.project){
  const project=await api('project/'+route.project_id);if(epoch!==AS.navEpoch||page!==assistantPageKey())return;
  if(typeof clearTimeout==='function')clearTimeout(pollTimer);S.project=project;S.view='project';S.activeStage=step(project);S.tab=route.tab;S.outputTab='preview';S.draft=null;
  for(const r of project.runs||[])if(r.note!==undefined)S.notes[project.id+r.stage]=r.note;
  if(route.tool)RT.tab=route.tool;if(route.mode)plotState(project).mode=route.mode;
  window.history?.pushState({},'',(projectRoute(project)==='scientific'?'/scientific':'/ars')+'?project='+encodeURIComponent(project.id));
  AS.arrival={pid:project.id,title:project.title,label:asNavNames[target]};AS.pick=null;render();window.scrollTo?.({top:0,behavior:'instant'});recoverOutputDraft();schedulePoll();
 }else{S.view=route.view;S.pathwayRoute=null;window.history?.pushState({},'','/');clearTimeout(pollTimer);S.projects=await api('projects');AS.arrival=null;render();window.scrollTo?.({top:0,behavior:'instant'});}
}
async function assistantJump(target){if(!asTargets.has(target))return;if(asProjectTargets.has(target)&&!AS.scope){AS.latestAction=[...(AS.state?.messages||[])].reverse().find(m=>(m.actions||[]).some(a=>a.id===target))?.id;AS.pick={target,message:AS.latestAction||'',query:'',all:true};assistantPaint();return;}await assistantNavigate(target,AS.scope||'');}
async function assistantAction(action,el){
 if(action.startsWith('as-proposal-'))return assistantProposalAction(action,el);
 if(action==='as-open'){await assistantOpen();return;}
 if(action==='as-close'){assistantMinimize();return;}
 if(action==='as-resize'){AS.wide=!AS.wide;assistantPaint();assistantSaveWindow();return;}
 if(action==='as-scopes'){assistantRecordScroll();AS.restoreScroll=true;AS.scopeOpen=!AS.scopeOpen;AS.scopeQuery='';assistantPaint();assistantCatalogRefresh();return;}
 if(action==='as-select-scope'||action==='as-current'){await assistantSelectScope(action==='as-current'?assistantScope():el.dataset.project);return;}
 if(action==='as-refresh-catalog'){await assistantCatalogRefresh();return;}
 if(action==='as-refresh'){await assistantRefresh();return;}
 if(action==='as-pick'||action==='as-all-projects'){AS.pick={target:el.dataset.target,message:el.dataset.message,query:'',all:action==='as-all-projects'};assistantPaint();assistantCatalogRefresh();return;}
 if(action==='as-route'){try{await assistantNavigate(el.dataset.target,el.dataset.project||'');}catch(error){AS.error=error.message;assistantPaint();assistantCatalogRefresh();}return;}
 if(action==='as-create-project'){if(!asTargets.has(el.dataset.target))return;createModal();const form=$('#create-form');if(form)form.dataset.assistantTarget=el.dataset.target;return;}
 if(action==='as-explain'||action==='as-error'){if(action==='as-error')assistantRememberError(el.dataset.error||AS.errors[assistantScope()]||'');await assistantOpen(assistantScope());await assistantSend(action==='as-explain'?'stage':'error');return;}
 if(action==='as-ask-drawing'){AS.drafts[AS.scope]=L('我想绘图，从哪里进入呢？');await assistantPreview();return;}
 if(action==='as-quick'){await assistantSend(el.dataset.intent);return;}
 if(action==='as-preview'){await assistantPreview();return;}
 if(action==='as-send'){await assistantSend();return;}
 if(action==='as-cancel-preview'){AS.preview=null;assistantPaint();$('#as-input')?.focus();return;}
 if(action==='as-cancel-recheck'){delete AS.rechecks[AS.scope];delete AS.stageOverrides[AS.scope];AS.preview=null;assistantPaint();return;}
 if(action==='as-overview'){await assistantNavigate('overview',AS.scope);return;}
 if(action==='as-recheck'){
  const pid=AS.scope,epoch=AS.epoch;const result=await api('assistant/recheck',{project:pid,message:el.dataset.message});if(AS.scope!==pid||AS.epoch!==epoch)return;
  AS.stageOverrides[pid]=result.stage;AS.drafts[pid]=result.text;AS.kinds[pid]=result.draft_kind;AS.options[pid]=result.options;AS.rechecks[pid]=result.message;AS.preview=null;AS.contextOpen=true;assistantPaint();return;
 }
 if(action==='as-stop'){const pid=AS.scope,epoch=AS.epoch,state=await api('assistant/stop',assistantData());if(AS.scope===pid&&AS.epoch===epoch){AS.state=state;assistantPaint();assistantPoll();}return;}
 if(action==='as-jump'){await assistantJump(el.dataset.target);return;}
 if(action==='as-retry'){const m=AS.state?.messages.find(x=>x.turn===el.dataset.turn&&x.role==='user');if(m){const answer=AS.state.messages.find(x=>x.turn===m.turn&&x.role==='assistant');AS.drafts[AS.scope]=m.text;AS.kinds[AS.scope]=answer?.draft_kind||'';AS.rechecks[AS.scope]=answer?.review_of||'';if(answer?.options)AS.options[AS.scope]={...answer.options,materials:[...(answer.options.materials||[])]};if(answer?.stage)AS.stageOverrides[AS.scope]=answer.stage;AS.preview=null;AS.error='';assistantPaint();$('#as-input')?.focus();}return;}
 if(action==='as-clear'){modal(H`<h2>清空当前对话？</h2><p>仅清空“`+e(AS.state?.title)+H`”的助手消息，研究成果与其他项目对话会保留。需要留存时请先保存对话。</p><div class="modal-footer">`+button('close-modal',L('取消'),'secondary')+button('as-clear-confirm',L('清空对话'),'primary')+'</div>');AS.clearScope=AS.scope;return;}
 if(action==='as-clear-confirm'){const pid=AS.clearScope,state=await api('assistant/clear',{project:pid,acknowledge:true});if(AS.scope===pid){AS.state=state;AS.preview=null;}closeModal();assistantCatalogRefresh();assistantPaint();return;}
 if(action==='as-export'){const pid=AS.scope||'';if(window.pywebview?.api?.save_assistant_export){const result=await window.pywebview.api.save_assistant_export(pid);if(result.saved)toast(L('助手对话已保存'));return;}const blob=await api('assistant/export',{project:pid},true),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=L('研序-AI助手对话.md');link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);return;}
}
document.addEventListener('input',ev=>{const t=ev.target;
 if(t.id==='as-scope-search'){AS.scopeQuery=t.value;const results=$('#as-scope-results');if(results)results.innerHTML=assistantScopeRows(AS.catalog?.scopes||[],t.value.trim().toLowerCase());return;}
 if(t.dataset?.asProjectSearch){const caret=t.selectionStart;AS.pick={target:t.dataset.asProjectSearch,message:t.dataset.message,query:t.value,all:true};const id=t.id;assistantPaint();const next=document.getElementById(id);if(next){next.focus({preventScroll:true});next.setSelectionRange?.(caret,caret);}return;}
 if(t.id!=='as-input')return;AS.drafts[AS.scope]=t.value;if(AS.preview){AS.preview=null;assistantPaint();}const b=$('[data-action="as-preview"]');if(b)b.disabled=!t.value.trim()||AS.sending||!!AS.state?.job;
});
document.addEventListener('change',ev=>{const t=ev.target;if(!['as-history','as-report','as-memory'].includes(t.id)&&!t.dataset.asMaterial)return;const o=assistantOptions();if(t.id==='as-history')o.history=t.checked;else if(t.id==='as-report')o.report=t.checked;else if(t.id==='as-memory')o.memory=t.checked;else o.materials=t.checked?[...new Set([...o.materials,t.dataset.asMaterial])]:o.materials.filter(id=>id!==t.dataset.asMaterial);AS.preview=null;assistantPaint();});
document.addEventListener('toggle',ev=>{if(ev.target.id==='as-context')AS.contextOpen=ev.target.open;},true);
document.addEventListener('keydown',ev=>{if(ev.target.id==='as-input'&&(ev.ctrlKey||ev.metaKey)&&ev.key==='Enter'&&!ev.isComposing){ev.preventDefault();assistantPreview();}if(ev.key==='Escape'&&AS.open&&!ev.isComposing&&!$('.modal')){if(AS.scopeOpen){AS.scopeOpen=false;AS.restoreScroll=true;assistantPaint();}else assistantMinimize();}});
document.addEventListener('pointerdown',ev=>{const header=ev.target.closest?.('[data-as-drag]');if(!header||ev.target.closest('button')||ev.button!==0)return;const panel=$('.assistant-panel'),r=panel.getBoundingClientRect();AS.dragging={pointer:ev.pointerId,dx:ev.clientX-r.left,dy:ev.clientY-r.top};header.setPointerCapture?.(ev.pointerId);ev.preventDefault();});
document.addEventListener('pointermove',ev=>{if(!AS.dragging||ev.pointerId!==AS.dragging.pointer)return;AS.position={x:ev.clientX-AS.dragging.dx,y:ev.clientY-AS.dragging.dy};assistantFit();});
function assistantEndDrag(ev){if(AS.dragging&&ev.pointerId===AS.dragging.pointer){AS.dragging=null;assistantFit();assistantSaveWindow();assistantPaint();}}
document.addEventListener('pointerup',assistantEndDrag);document.addEventListener('pointercancel',assistantEndDrag);
window.addEventListener?.('resize',()=>{assistantFit();});
