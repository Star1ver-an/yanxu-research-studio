/* Read-only guidance: model text never supplies navigation or executable actions. */
const AS={open:false,scope:null,state:null,loading:false,sending:false,error:'',preview:null,
 drafts:{},kinds:{},rechecks:{},options:{},errors:{},contextOpen:false,epoch:0,timer:null,opener:null};
const asQuick=[['next','下一步做什么'],['blocked','排查流程卡点'],['stage','解释当前阶段'],['connection','连接故障']];
const asTargets=new Set(['work','materials','research','history','settings','help','pathways','backups','projects']);
function assistantScope(){return S.view==='project'&&S.project?S.project.id:''}
function assistantLocation(){return S.view==='project'?({work:'project',materials:'materials',research:'research',history:'history'}[S.tab]||'project'):S.view}
function assistantOptions(){return AS.options[AS.scope]||(AS.options[AS.scope]={report:false,history:true,memory:true,materials:[]})}
function assistantLastError(){return AS.errors[AS.scope??assistantScope()]||''}
function assistantData(){return {project:AS.scope||'',view:assistantLocation(),stage:AS.scope?S.activeStage:'',options:{...assistantOptions(),materials:[...assistantOptions().materials]},error:assistantLastError(),draft_kind:AS.kinds[AS.scope]||'',review_of:AS.rechecks[AS.scope]||''}}
function assistantRequestId(){return crypto.randomUUID().replaceAll('-','')}
function assistantRememberError(text){AS.errors[assistantScope()]=String(text||'').slice(0,2000)}
function assistantSync(){
 document.body.classList.toggle('assistant-open',AS.open);
 if(!AS.open){clearTimeout(AS.timer);const root=$('#assistant-root');if(root)root.innerHTML='';return}
 const scope=assistantScope();
 if(scope!==AS.scope){AS.scope=scope;AS.state=null;AS.preview=null;AS.error='';AS.contextOpen=false;AS.epoch++;clearTimeout(AS.timer)}
 return assistantRefresh().catch(err=>{AS.error=err.message;AS.loading=false;assistantPaint()});
}
async function assistantRefresh(){
 if(!AS.open)return;
 const scope=AS.scope,epoch=++AS.epoch;AS.loading=!AS.state;
 if(AS.loading)assistantPaint();
 const state=await api('assistant/state',assistantData());
 if(!AS.open||AS.scope!==scope||epoch!==AS.epoch)return;
 AS.state=state;AS.loading=false;
 const opts=assistantOptions(),ids=new Set(state.materials.map(a=>a.id));
 opts.materials=opts.materials.filter(id=>ids.has(id));if(!state.report_available)opts.report=false;
 assistantPaint();assistantPoll();
}
function assistantPoll(){clearTimeout(AS.timer);if(AS.open&&AS.state?.job)AS.timer=setTimeout(()=>assistantRefresh().catch(err=>{AS.error=err.message;assistantPaint();AS.timer=setTimeout(()=>assistantRefresh().catch(()=>{}),4000)}),1500)}
function assistantPaint(){
 const root=$('#assistant-root');if(!root||!AS.open)return;
 const focus=document.activeElement,focused=focus&&root.contains(focus)?focus.id:'',start=focus?.selectionStart,end=focus?.selectionEnd;
 const old=$('#as-messages'),scroll=old?.scrollTop||0,bottom=!old||old.scrollHeight-old.clientHeight-scroll<65;
 const state=AS.state,opts=assistantOptions(),busy=!!state?.job||AS.sending;
 const draft=AS.drafts[AS.scope]||'',title=state?.title||(AS.scope?S.project?.title:'工作区帮助');
 const quick=AS.scope?asQuick:[['help','从哪里开始'],['connection','连接故障']];
 root.innerHTML=`<aside class="assistant-panel" aria-label="AI 助手"><div class="as-header"><div><span class="as-eyebrow">RESEARCH GUIDANCE</span><h2>${icon('spark',19)}AI 助手</h2></div><button class="icon-btn" data-action="as-close" aria-label="收起 AI 助手">${icon('close',20)}</button></div>
 <div class="as-scope"><strong title="${e(title)}">${e(title)}</strong><span>${AS.scope?'项目独立对话':'不读取任何项目内容'} · 本机保存</span></div>
 <div class="as-toolbar">${AS.scope?'<button class="as-follow-link" data-action="as-overview">项目进展与记忆</button>':''}<button data-action="as-export" ${!state?.messages.length?'disabled':''}>保存对话</button><button data-action="as-clear" ${busy||!state?.messages.length?'disabled':''}>清空对话</button><span>${state?.connection==='api'?'API 连接':'本机 Codex'}</span></div>
 <div class="as-messages" id="as-messages" tabindex="0" aria-label="助手对话记录">${AS.loading?'<p class="as-empty">正在读取对话…</p>':!state?.messages.length?`<div class="as-welcome"><span class="as-welcome-icon">${icon('spark',26)}</span><h3>从你卡住的地方开始。</h3><p>可以问软件怎么用，也可以结合当前研究阶段讨论下一步。下方快捷指导由本机提供，不调用模型。</p><p class="as-quiet">普通问答只给建议；协助模式先生成草稿，由你核对后保存。</p></div>`:state.messages.map(assistantMessage).join('')}
 ${state?.job?`<div class="as-progress" role="status"><span class="spinner"></span><span>${e(state.job.progress)}</span></div>`:''}</div>
 <div class="as-compose"><details class="as-quick-tools" ${!state?.messages.length?'open':''}><summary>常用问题 · 本机指导</summary><div class="as-quick" aria-label="本机快捷指导">${quick.map(([id,label])=>`<button data-action="as-quick" data-intent="${id}" ${busy?'disabled':''}>${label}</button>`).join('')}${assistantLastError()?`<button data-action="as-quick" data-intent="error" ${busy?'disabled':''}>排查刚才的错误</button>`:''}</div></details>
 <details class="as-context" id="as-context" ${AS.contextOpen?'open':''}><summary>本次参考范围 <span>${opts.report?'阶段报告 · ':''}${opts.materials.length?opts.materials.length+' 份材料 · ':''}${opts.history?'最近 6 轮':'不带历史'}</span></summary><div class="as-context-body"><p>${AS.scope?'默认参考项目目标、流程状态与软件指南；不自动发送材料正文。':'参考软件指南与当前页面；不读取项目。'}</p>
 <label><input type="checkbox" id="as-history" ${opts.history?'checked':''}>带上最近最多 6 轮对话</label><small>历史回答中可能含此前选过的材料内容；可关闭历史。</small>
 ${AS.scope?`<label><input type="checkbox" id="as-memory" ${opts.memory!==false?'checked':''}>用户确认的项目记忆（${state?.memory_count||0} 条启用）</label><label><input type="checkbox" id="as-report" ${opts.report?'checked':''} ${!state?.report_available?'disabled':''}>当前阶段报告${!state?.report_available?'（尚无报告）':''}</label><fieldset><legend>材料正文（仅显示已启用材料）</legend>${state?.materials.length?state.materials.map(a=>`<label><input type="checkbox" data-as-material="${a.id}" ${opts.materials.includes(a.id)?'checked':''}><span>${e(a.name)}</span></label>`).join(''):'<small>暂无启用材料</small>'}</fieldset>`:''}
 <small>只参考本项目中明确选择的内容；项目记忆可在“继续研究”编辑。</small></div></details>

 ${AS.error?`<div class="as-error" role="alert">${e(AS.error)} <button data-action="as-jump" data-target="settings">检查连接</button></div>`:''}
 ${AS.rechecks[AS.scope]?'<div class="as-recheck-note">正在复核旧建议。请查看参考范围，新增材料需主动勾选。<button data-action="as-cancel-recheck">改为普通提问</button></div>':''}
 ${AS.scope?`<label class="as-mode"><span>本次协助</span><select id="as-kind" ${busy||state?.readonly?'disabled':''}><option value="">只问问题</option>${Object.entries(asKinds).map(([k,n])=>`<option value="${k}" ${(AS.kinds[AS.scope]||'')===k?'selected':''}>生成${n}草稿</option>`).join('')}</select></label>${AS.kinds[AS.scope]?'<p class="as-mode-note">只生成可编辑草稿，核对后再预览保存。修改报告时需选中当前报告。</p>':''}`:''}
 <label class="as-input-label" for="as-input">向 AI 助手提问</label><textarea id="as-input" rows="3" maxlength="6000" placeholder="例如：我还没有实验数据，这一步该如何准备？">${e(draft)}</textarea><div class="as-send-row"><small>Enter 换行 · Ctrl + Enter 提问</small>${state?.job?'<button class="btn secondary" data-action="as-stop">停止回答</button>':`<button class="btn primary" data-action="as-preview" ${busy||AS.loading||!draft.trim()?'disabled':''}>${AS.sending?'正在提交…':'查看范围并提问'}${icon('arrow',16)}</button>`}</div></div></aside>`;
 const list=$('#as-messages');if(list)list.scrollTop=bottom?list.scrollHeight:scroll;
 const next=focused?document.getElementById(focused):null;if(next){next.focus({preventScroll:true});if(typeof start==='number'&&next.setSelectionRange)next.setSelectionRange(start,end)}
}
function assistantMessage(m){
 const user=m.role==='user',failed=['failed','stopped','interrupted'].includes(m.status);
 return `<article class="as-message ${user?'as-user':'as-answer'}"><div class="as-message-label">${user?'你':m.mode==='local'?'本机指导':'AI 助手'}<small>${e({running:'回答中',completed:'',failed:'未完成',stopped:'已停止',interrupted:'已中断'}[m.status]||'')}</small></div>${m.outdated?'<p class="as-outdated">依据的项目内容或软件版本已更新，请重新核对这条建议。</p>':''}<div class="as-markdown">${user?`<p class="as-user-text">${e(m.text)}</p>`:markdown(m.text)}</div>${!user&&m.sources?.length?`<details class="as-sources"><summary>参考范围</summary><p>${m.sources.map(e).join(' · ')}</p></details>`:''}${!user&&m.status==='completed'?`<div class="as-action-links">${(m.actions||[]).filter(a=>asTargets.has(a.id)).map(a=>`<button data-action="as-jump" data-target="${a.id}">${e(a.label)}${icon('chevron',12)}</button>`).join('')}</div>`:''}${!user&&m.outdated&&m.mode!=='local'&&m.status==='completed'&&!m.proposal?`<button class="as-retry" data-action="as-recheck" data-message="${e(m.id)}">按最新内容复核</button>`:''}${typeof assistantProposalCard==='function'?assistantProposalCard(m):''}${failed?`<button class="as-retry" data-action="as-retry" data-turn="${e(m.turn)}">重新提问</button>`:''}</article>`;
}
async function assistantOpen(){AS.opener=document.activeElement;if(S.draft!==null)await saveOutput(true);AS.open=true;await assistantSync()}
function assistantPreviewDialog(){
 const p=AS.preview;if(!p||!$('#modal-root'))return;
 modal(`<h2>确认本次提问</h2><p>发送给 ${e(p.connection_label||'当前模型连接')} · 1 次模型请求</p><div class="as-proposal-scroll"><h3>本次参考</h3><ul>${p.sources.map(x=>`<li>${e(x)}</li>`).join('')}</ul><p class="field-help">${p.context_chars.toLocaleString()} 字符上下文，加上本次问题。</p><h3>你的问题</h3><p class="as-user-text">${e(p.text)}</p></div><div class="modal-footer">${button('as-cancel-preview','返回调整','secondary')}${button('as-send','确认发送','primary','arrow')}</div>`,true);
 $('.modal').classList.add('as-proposal-modal','as-preview-modal');
}
async function assistantPreview(){
 const text=(AS.drafts[AS.scope]||'').trim();if(!text||AS.sending||AS.state?.job)return;
 const scope=AS.scope,data=assistantData();AS.error='';AS.sending=true;assistantPaint();
 try{const preview=await api('assistant/preview',data);if(AS.scope!==scope)return;if((AS.drafts[scope]||'').trim()!==text)return;AS.preview={...preview,text,data};assistantPreviewDialog()}
 catch(err){if(AS.scope===scope)AS.error=err.message}finally{AS.sending=false;assistantPaint()}
}
async function assistantSend(intent=''){
 if(AS.sending||AS.state?.job)return;
 const scope=AS.scope,preview=AS.preview;
 if(!intent&&(!preview||(AS.drafts[scope]||'').trim()!==preview.text))return;
 const data=intent?{...assistantData(),intent,draft_kind:'',review_of:''}:{...preview.data,text:preview.text,context_sha256:preview.sha256};
 AS.sending=true;AS.error='';assistantPaint();
 try{
  const state=await api('assistant/send',{...data,request_id:assistantRequestId()});
  if(AS.scope!==scope)return;
  AS.state=state;AS.preview=null;if(!intent){AS.drafts[scope]='';delete AS.rechecks[scope]}
 }catch(err){if(AS.scope===scope)AS.error=err.message}finally{AS.sending=false;assistantPaint();assistantPoll()}
}
async function assistantJump(target){
 if(!asTargets.has(target))return;
 const pid=AS.scope;AS.open=false;assistantSync();if(S.draft!==null)await saveOutput(true);
 if(['work','materials','research','history'].includes(target)){
  if(!pid)return;
  await openProject(pid,target==='work'?'work':target);
 }else{S.view=target;S.pathwayRoute=null;window.history?.pushState({},'','/');clearTimeout(pollTimer);S.projects=await api('projects');render()}
}
async function assistantAction(action,el){
 if(action.startsWith('as-proposal-'))return assistantProposalAction(action,el);
 if(action==='as-open'){await assistantOpen();return}
 if(action==='as-close'){AS.open=false;assistantSync();if(AS.opener?.isConnected)AS.opener.focus();return}
 if(action==='as-explain'){await assistantOpen();await assistantSend('stage');return}
 if(action==='as-error'){assistantRememberError(el.dataset.error||AS.errors[assistantScope()]||'');await assistantOpen();await assistantSend('error');return}
 if(action==='as-quick'){await assistantSend(el.dataset.intent);return}
 if(action==='as-preview'){await assistantPreview();return}
 if(action==='as-send'){closeModal();await assistantSend();return}
 if(action==='as-cancel-recheck'){delete AS.rechecks[AS.scope];AS.preview=null;assistantPaint();return}
 if(action==='as-overview'){await fuNavigate({tab:'overview'});return}
 if(action==='as-recheck'){await fuRecheck(el.dataset.message);return}
 if(action==='as-cancel-preview'){AS.preview=null;closeModal();assistantPaint();return}
 if(action==='as-stop'){AS.state=await api('assistant/stop',assistantData());assistantPaint();assistantPoll();return}
 if(action==='as-jump'){await assistantJump(el.dataset.target);return}
 if(action==='as-retry'){
  const m=AS.state?.messages.find(x=>x.turn===el.dataset.turn&&x.role==='user');if(m){const answer=AS.state.messages.find(x=>x.turn===m.turn&&x.role==='assistant');AS.drafts[AS.scope]=m.text;AS.kinds[AS.scope]=answer?.draft_kind||'';AS.rechecks[AS.scope]=answer?.review_of||'';if(answer?.options)AS.options[AS.scope]={...answer.options,materials:[...(answer.options.materials||[])]};if(answer?.stage&&S.project&&flow(S.project).stages.indexOf(answer.stage)<=S.project.cursor)S.activeStage=answer.stage;AS.preview=null;AS.error='';assistantPaint();$('#as-input')?.focus()}return;
 }
 if(action==='as-clear'){modal('<h2>清空当前对话？</h2><p>仅清空“'+e(AS.state?.title)+'”的助手消息，研究成果与其他项目对话会保留。需要留存时请先保存对话。</p><div class="modal-footer">'+button('close-modal','取消','secondary')+button('as-clear-confirm','清空对话','primary')+'</div>');return}
 if(action==='as-clear-confirm'){AS.state=await api('assistant/clear',{...assistantData(),acknowledge:true});AS.preview=null;closeModal();assistantPaint();return}
 if(action==='as-export'){
  if(window.pywebview?.api?.save_assistant_export){const result=await window.pywebview.api.save_assistant_export(AS.scope||'');if(result.saved)toast('助手对话已保存');return}
  const blob=await api('assistant/export',assistantData(),true),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='研序-AI助手对话.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);return;
 }
}
document.addEventListener('input',ev=>{if(ev.target.id!=='as-input')return;AS.drafts[AS.scope]=ev.target.value;if(AS.preview){AS.preview=null;assistantPaint()}const b=$('[data-action="as-preview"]');if(b)b.disabled=!ev.target.value.trim()||AS.sending||!!AS.state?.job});
document.addEventListener('change',ev=>{
 const t=ev.target;if(!['as-history','as-report','as-memory'].includes(t.id)&&!t.dataset.asMaterial)return;
 const o=assistantOptions();if(t.id==='as-history')o.history=t.checked;else if(t.id==='as-report')o.report=t.checked;else if(t.id==='as-memory')o.memory=t.checked;else o.materials=t.checked?[...new Set([...o.materials,t.dataset.asMaterial])]:o.materials.filter(id=>id!==t.dataset.asMaterial);
 AS.preview=null;assistantPaint();
});
document.addEventListener('toggle',ev=>{if(ev.target.id==='as-context')AS.contextOpen=ev.target.open},true);
document.addEventListener('keydown',ev=>{
 if(ev.target.id==='as-input'&&(ev.ctrlKey||ev.metaKey)&&ev.key==='Enter'&&!ev.isComposing){ev.preventDefault();assistantPreview()}
 if(ev.key==='Escape'&&AS.open&&!$('.modal')){AS.open=false;assistantSync();AS.opener?.isConnected&&AS.opener.focus()}
});
