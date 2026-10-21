/* Program-generated follow-up destinations; model replies cannot provide actions. */
const FU={expanded:false,deferred:false,memory:null};
function fuButton(label,target,kind='secondary'){
 return `<button class="btn ${kind}" data-action="fu-go" data-destination="${e(JSON.stringify(target))}">${e(label)}${icon('arrow',15)}</button>`;
}
function followupView(p){
 const f=p.followup;if(!f)return '<p>正在整理项目进展…</p>';
 const c=f.counts,active=f.checks.filter(x=>!x.deferred),deferred=f.checks.filter(x=>x.deferred);
 const card=x=>`<article class="fu-check"><div><span class="fu-check-label">${x.deferred?'已暂缓':x.level==='blocking'?'流程条件':x.level==='info'?'准备建议':'需要核对'}</span><h3>${e(x.title)}</h3><p>${e(x.detail)}</p></div><div class="fu-check-actions">${fuButton(x.target.action==='recheck'?'复核建议':'去处理',x.target)}<button class="btn text" data-action="fu-defer" data-check="${e(x.id)}" ${f.readonly||f.running?'disabled':''}>${x.deferred?'恢复提醒':'稍后处理'}</button></div></article>`;
 return `<section class="panel fu-next"><div><span class="eyebrow">CONTINUE YOUR RESEARCH</span><h2>${e(f.next.title)}</h2><p>${e(f.next.detail)}</p>${p.routing_recommendation?`<details class="fu-route"><summary>为什么推荐这条路径？</summary><p>${p.routing_recommendation.reasons.map(e).join(' ')}</p></details>`:''}</div>${fuButton('继续',f.next.target,'primary')}</section>
 <div class="fu-metrics"><span><strong>${c.stages_done}<small> / ${c.stages_total}</small></strong>阶段已推进</span><span><strong>${c.materials}</strong>份材料</span><span><strong>${c.tasks_done}<small> / ${c.tasks_total}</small></strong>任务已完成</span><span><strong>${c.checks}</strong>项待留意</span></div>
 <div class="fu-columns"><section class="panel fu-checks"><div class="section-title"><div><h2>现在需要留意</h2><p>检查已登记的流程、材料与研究记录。</p></div><button class="btn text" data-action="fu-refresh">${icon('refresh',16)}刷新</button></div>
 ${active.length?active.slice(0,FU.expanded?active.length:3).map(card).join(''):'<div class="fu-clear">'+icon('check',22)+'<div><strong>目前没有待提醒事项</strong><p>可以继续下一步；这不代表研究结论已经验证。</p></div></div>'}
 ${active.length>3?`<button class="btn text fu-expand" data-action="fu-expand">${FU.expanded?'收起更多事项':`查看其余 ${active.length-3} 项`}</button>`:''}
 ${deferred.length?`<details class="fu-deferred" ${FU.deferred?'open':''}><summary>已暂缓 ${deferred.length} 项 · 内容变化后会再次提醒</summary>${deferred.map(card).join('')}</details>`:''}
 <p class="field-help">${e(f.meaning)}</p></section>
 <aside class="fu-side"><section class="panel fu-memory"><div class="section-title"><div><h2>项目记忆</h2><p>记住你确认的条件与约定。</p></div><button class="btn text" data-action="fu-memory" ${f.readonly?'disabled':''}>${icon('plus',15)}添加</button></div>
 ${f.memory.items.length?f.memory.items.map(x=>`<button class="fu-memory-item" data-action="fu-memory" data-id="${e(x.id)}"><span><strong>${e(x.title)}</strong><small>${x.enabled?'供助手参考':'已停用'}</small></span><p>${e(x.text)}</p>${icon('edit',14)}</button>`).join(''):`<p class="fu-empty">例如：只有一张 8 GB 显卡，先做可行性方案。保存后，助手无需反复询问。</p><button class="btn secondary" data-action="fu-memory" ${f.readonly?'disabled':''}>记录一条研究约定</button>`}
 <p class="field-help">由你填写并保存；可随时修改或停用。仅供助手参考，不会改写报告。</p></section>
 <section class="panel fu-summary"><h2>进度与交接</h2><p>把阶段状态、待办和约定整理成简报，方便下次接着做。</p><button class="btn secondary" data-action="fu-summary">查看进度简报${icon('file',15)}</button><details><summary>查看全部阶段</summary><ol>${f.timeline.map(x=>`<li><span>${e(x.title)}</span><small>${e(x.status)}</small></li>`).join('')}</ol></details></section></aside></div>`;
}
async function fuNavigate(target){
 if(!S.project||!target||typeof target!=='object')return;
 if(S.draft!==null)await saveOutput(true);
 if(target.action==='project-info'){infoModal();return}
 if(target.action==='export'){exportModal();return}
 if(target.action==='recheck'){await fuRecheck(target.message);return}
 if(!['work','materials','research','history','overview'].includes(target.tab))return;
 if(target.tab==='overview'){const pid=S.project.id;const fresh=await api('project/'+pid);if(S.project?.id!==pid)return;S.project=fresh}
 S.tab=target.tab;
 if(target.tab==='work'){
  const stages=flow(S.project).stages;
  if(stages.includes(target.stage)&&stages.indexOf(target.stage)<=S.project.cursor)S.activeStage=target.stage;
  S.outputTab='preview';
 }
 if(target.tab==='research'&&Object.keys(rtFields).includes(target.group))RT.tab=target.group==='tasks'?'navigator':target.group;
 render();window.scrollTo?.({top:0,behavior:'instant'});
 if(target.tab==='work')$('.stage-content')?.scrollIntoView?.({block:'start',behavior:'instant'});
 if(target.tab==='research'&&target.record&&Object.keys(rtFields).includes(target.group)){
  const record=rtRows(target.group).find(x=>x.id===target.record);if(record&&projectState(S.project)==='active')rtEditor(target.group,record);
 }
}
async function fuRecheck(message){
 const pid=S.project?.id;if(!pid)return;
 const result=await api('assistant/recheck',{project:pid,message});if(S.project?.id!==pid)return;
 S.activeStage=result.stage;await assistantOpen(pid);
 AS.drafts[pid]=result.text;AS.kinds[pid]=result.draft_kind;AS.options[pid]=result.options;
 AS.stageOverrides[pid]=result.stage;AS.rechecks[pid]=result.message;AS.preview=null;AS.contextOpen=true;AS.error='';assistantPaint();
 toast('已带回原问题，请检查参考范围。');
}
function fuMemoryEditor(id=''){
 const p=S.project,f=p.followup,old=f.memory.items.find(x=>x.id===id);
 FU.memory={pid:p.id,revision:f.memory.revision,id:old?.id||''};
 modal(`<h2>${old?'编辑':'添加'}项目记忆</h2><p>保存你确认的研究条件、目标或决定，供本项目的助手参考。</p><form id="fu-memory-form"><div class="as-proposal-scroll"><label class="field">名称<input name="title" value="${e(old?.title||'')}" maxlength="100" required placeholder="例如：设备与时间预算"></label><label class="field">内容<textarea name="text" rows="6" maxlength="4000" required placeholder="例如：只有一张 8 GB 显卡，本周先完成实验方案，尚无实验结果。">${e(old?.text||'')}</textarea></label><label class="ack"><input type="checkbox" name="enabled" ${old?.enabled!==false?'checked':''}>在助手参考范围中启用</label><p class="field-help">助手不会把建议自动写成记忆。启用的记忆会在你提问时发送给所选模型，可在“参考范围”中取消。</p><p class="as-error" id="fu-memory-error" role="alert"></p></div><div class="modal-footer">${button('close-modal','取消','secondary')}${button('noop','确认保存','primary','check','type="submit"')}</div></form>`,true);
 $('.modal').classList.add('as-proposal-modal');
 if(f.readonly)$('#fu-memory-form').querySelectorAll('input,textarea,button[type=submit]').forEach(x=>x.disabled=true);
}
async function fuMemorySubmit(form){
 const edit=FU.memory;if(!edit)return;
 const record={title:form.elements.title.value,text:form.elements.text.value,enabled:form.elements.enabled.checked};
 if(edit.id)record.id=edit.id;
 try{
  const p=await api('assistant/memory-save',{project:edit.pid,revision:edit.revision,record});
  if(S.project?.id===edit.pid){S.project=p;S.projects=await api('projects');closeModal();render();toast('项目记忆已保存，之后提问时可在参考范围中调整。')}
 }catch(err){if($('#fu-memory-error'))$('#fu-memory-error').textContent=err.message;else throw err}
}
function fuExportNotice(){
 const f=S.project?.followup;if(!f)return '';
 return `<div class="fu-export-note"><strong>${f.counts.checks?`还有 ${f.counts.checks} 项待留意`:'已检查项目中的登记记录'}</strong><p>${f.checks.filter(x=>!x.deferred).slice(0,3).map(x=>e(x.title)).join('；')||'导出不会确认成果或验证科学结论。'}${f.counts.deferred?` · 另有 ${f.counts.deferred} 项已暂缓`:''}</p>${f.counts.checks?button('fu-export-check','先查看待留意事项','text'):''}</div>`;
}
async function followupAction(action,el){
 if(action==='fu-go'){await fuNavigate(JSON.parse(el.dataset.destination));return}
 if(action==='fu-open'){FU.expanded=false;await openProject(el.dataset.id,'overview');return}
 if(action==='fu-expand'){FU.expanded=!FU.expanded;render();return}
 if(action==='fu-refresh'){S.project=await api('project/'+S.project.id);render();toast('已根据最新记录重新检查，不调用模型。');return}
 if(action==='fu-memory'){fuMemoryEditor(el.dataset.id||'');return}
 if(action==='fu-export-check'){closeModal();await fuNavigate({tab:'overview'});return}
 if(action==='fu-defer'){
  const pid=S.project.id,item=S.project.followup.checks.find(x=>x.id===el.dataset.check);if(!item)return;el.disabled=true;
  const p=await api('assistant/defer',{project:pid,id:item.id,signature:item.signature,defer:!item.deferred});
  if(S.project?.id===pid){S.project=p;render();toast(item.deferred?'已恢复提醒':'已暂缓；只有依据变化时才会再次提醒。')}return;
 }
 if(action==='fu-summary'){
  const pid=S.project.id,blob=await api('assistant/progress-export',{project:pid},true);if(S.project?.id!==pid)return;
  FU.summary=await blob.text();FU.summaryPid=pid;
  modal(`<h2>项目进度简报</h2><p>从本机记录整理，不调用模型。</p><div class="as-proposal-scroll"><article class="markdown">${markdown(FU.summary)}</article></div><div class="modal-footer">${button('fu-copy-summary','复制简报','secondary','copy')}${button('fu-save-summary','保存简报','primary','download')}</div>`,true);$('.modal').classList.add('as-proposal-modal');return;
 }
 if(action==='fu-copy-summary'){await navigator.clipboard.writeText(FU.summary);toast('进度简报已复制。');return}
 if(action==='fu-save-summary'){
  if(window.pywebview?.api?.save_progress_export){const r=await window.pywebview.api.save_progress_export(FU.summaryPid);if(r.saved)toast('进度简报已保存。')}
  else rtDownload(new Blob([FU.summary],{type:'text/markdown;charset=utf-8'}),'研序-项目进度简报.md');return;
 }
}
document.addEventListener('toggle',ev=>{if(ev.target.classList?.contains('fu-deferred'))FU.deferred=ev.target.open},true);
