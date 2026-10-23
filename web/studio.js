/* Task-oriented workspace. Study plans remain drafts until explicitly reviewed. */
const Studio={plans:[],saving:false};
const studyFields={original_title:L('原始课题'),working_title:L('规范课题名称'),question:L('核心研究问题'),scope:L('研究对象与时间范围'),materials:L('已有材料与待补材料'),method:L('研究方法与比较设计'),outcomes:L('预期交付物与完成标准'),limits:L('解释边界与质量要求'),open_questions:L('需要研究者确认的问题')};
const studyLimits={original_title:500,working_title:140,question:4000,scope:4000,materials:6000,method:6000,outcomes:4000,limits:4000,open_questions:4000};
const studyProfiles={biomedical:L('医学机制研究'),finance:L('金融实证研究'),history:L('经济史比较研究'),general:L('通用研究')};

function studioHome(){
 const projects=activeProjects(),needsReview=projects.filter(p=>p.study_status==='draft');
 return H`<div class="studio-home"><div class="page-heading studio-heading"><div><div class="eyebrow">RESEARCH WORKSPACE</div><h1>继续推进你的研究</h1><p>从真实材料出发，把问题、证据和成果连接起来。</p></div>${button('create',L('新建项目'),'primary','plus')}</div>
 <section class="studio-section"><div class="section-title"><div><h2>继续上次的工作</h2><p>${projects.length?L('下一步和待核对事项随项目保存。'):L('创建项目后，材料、研究计划与成果会集中在这里。')}</p></div>${button('nav',L('查看全部'),'text','arrow','data-view="projects"')}</div>
 ${projects.length?`<div class="studio-projects">${projects.slice(0,4).map(p=>`<button class="studio-project" data-action="open" data-id="${e(p.id)}"><span class="studio-project-icon">${icon('folder',21)}</span><span class="studio-project-text"><strong>${e(p.title)}</strong><span>${e(p.followup_next?.title||L('查看项目下一步'))}</span><small>${p.asset_count} ${L('份材料')} · ${date(p.updated)}</small></span><span class="studio-status ${p.running?'running':''}">${p.running?L('运行中'):p.study_status==='draft'?L('简报待核对'):p.study_status==='confirmed'?L('研究范围已核对'):L('继续研究')}</span>${icon('arrow',16)}</button>`).join('')}</div>`:H`<div class="studio-empty"><span>${icon('folder',28)}</span><h3>让课题有一个清晰的起点</h3><p>先写下问题和已有材料，再选择这次要交付的成果。</p>${button('create',L('创建研究项目'),'primary','plus')}</div>`}</section>
 <section class="studio-section"><div class="section-title"><div><h2>你现在要完成什么？</h2><p>选择任务，再选择项目。</p></div></div><div class="studio-tasks">${[
  ['materials','book',L('整理文献与证据'),L('导入论文、阅读原文，建立有来源的证据。')],
  ['analysis','bars',L('分析数据与制作图表'),L('核对输入、运行支持的分析，保存可追溯图表。')],
  ['work','edit',L('开题、写作与返修'),L('推进研究方案和稿件，阅读并核对阶段成果。')]
 ].map(([target,ico,title,description])=>`<button class="studio-task" data-action="studio-task" data-target="${target}">${icon(ico,23)}<strong>${title}</strong><span>${description}</span><span class="studio-task-link">${L('选择项目')}${icon('arrow',15)}</span></button>`).join('')}</div></section>
 <section class="studio-start"><div><span class="eyebrow">STUDY BRIEF</span><h2>把研究构思变成可执行的计划</h2><p>医学机制、金融实证与经济史比较，各有对应的问题框架、材料清单和完成标准。</p>${needsReview.length?`<small>${needsReview.length} ${L('个项目的研究简报等待核对')}</small>`:''}</div>${button('studio-plans',L('查看课题起步方案'),'secondary','arrow')}</section>
 <details class="studio-methods"><summary>${L('浏览研究路径与专业方法')}</summary>${routeCards()}</details></div>`;
}

function studyBriefView(p){
 const brief=p.study_brief,readonly=p.lifecycle&&p.lifecycle!=='active';
 if(!brief)return H`<section class="studio-empty"><h2>先明确这项研究要解决什么</h2><p>研究简报记录问题、对象、材料、方法和交付标准，可随研究推进持续修改。</p>${button('studio-edit',L('编写研究简报'),'primary','edit',readonly?'disabled':'')}</section>`;
 return H`<div class="studio-brief"><section class="studio-brief-header"><div><span class="eyebrow">STUDY BRIEF · ${e(studyProfiles[brief.profile]||studyProfiles.general)}</span><h2>问题、材料与交付计划</h2><p>${brief.status==='confirmed'?L('已核对研究范围'):L('研究方案草稿 · 待核对')} · v${brief.revision}</p></div><div class="studio-actions">${button('studio-edit',L('编辑研究简报'),'secondary','edit',readonly?'disabled':'')}${button('studio-export',L('导出简报'),'secondary','download')}</div></section>
 <div class="studio-notice">${icon('info',18)}<p>简报记录研究计划和待确认条件。文献、数据、执行记录与结论需要分别核实。<br>${brief.context_enabled?L('已选择将简报用于后续模型研究。发送前仍会预览参考范围。'):L('简报尚未加入模型参考范围；项目名称与目标仍按原流程使用。')}</p></div>
 <div class="studio-brief-grid">${Object.entries(studyFields).map(([key,label])=>`<section class="studio-brief-field"><h3>${label}</h3><p>${e(brief.fields[key]||L('待补充'))}</p></section>`).join('')}</div>
 <div class="studio-actions studio-bottom">${button('studio-task',L('整理本项目材料'),'primary','upload','data-target="materials" data-project="'+e(p.id)+'"')}${button('studio-tasks',L('查看研究任务'),'secondary','check')}${button('project-tab',L('进入研究流程'),'secondary','arrow','data-tab="work"')}</div></div>`;
}

async function studioPlans(){
 if(!Studio.plans.length)Studio.plans=(await api('studies')).blueprints;
 modal(H`<div class="eyebrow">STUDY STARTERS</div><h2>从课题构思到研究计划</h2><p class="modal-description">以下名称与范围是可编辑的起步建议，创建后先核对研究简报。已有同一方案项目时直接打开，不重复创建。</p><div class="studio-plan-list">${Studio.plans.map(plan=>`<section class="studio-plan"><span class="badge">${e(plan.label)}</span><h3>${e(plan.title)}</h3><p>${e(plan.fields.question)}</p><details><summary>${L('查看材料缺口与研究边界')}</summary><p>${e(plan.fields.materials)}</p><p>${e(plan.fields.limits)}</p><ul>${plan.sources.map(s=>`<li><button class="link" data-action="external" data-url="${e(s.url)}">${e(s.title)} ${icon('external',12)}</button><small>${e(s.use)}</small></li>`).join('')}</ul></details>${button('studio-create',L('创建或打开研究项目'),'primary','arrow','data-blueprint="'+e(plan.id)+'"')}</section>`).join('')}</div>`,true);
}

function studyDraftKey(pid){return 'yanxu-study-draft:'+pid}
function cacheStudyDraft(){
 const f=$('#studio-brief-form');if(!f)return;
 try{localStorage.setItem(studyDraftKey(f.dataset.project),JSON.stringify({revision:Number(f.dataset.revision),fields:Object.fromEntries(Object.keys(studyFields).map(key=>[key,f.elements[key].value])),context_enabled:f.elements.context_enabled.checked}));return true}catch{return false}
}
function studioEdit(){
 const p=S.project,brief=p.study_brief||{revision:0,fields:{original_title:p.title,working_title:p.title,question:p.topic||''}};
 let draft=null;try{draft=JSON.parse(localStorage.getItem(studyDraftKey(p.id))||'null')}catch{}
 const canRecover=draft&&draft.revision===brief.revision&&draft.fields&&Object.keys(studyFields).every(key=>typeof draft.fields[key]==='string'&&draft.fields[key].length<=studyLimits[key]);
 const fields=canRecover?draft.fields:brief.fields;
 modal(H`<h2>编辑研究简报</h2><p class="modal-description">保留原始构思，逐步核定名称、范围与方法。保存草稿后可以继续补充。</p><form id="studio-brief-form" data-project="${e(p.id)}" data-revision="${brief.revision}">
 ${canRecover?H`<p class="studio-notice">已恢复此版本尚未提交的本机草稿。</p>`:''}${draft&&!canRecover?H`<p class="studio-notice">检测到其他版本的本机草稿，未覆盖当前内容。可先复制旧草稿后继续编辑。</p><button type="button" class="btn secondary" data-action="studio-old-draft">查看旧草稿</button>`:''}
 <div class="studio-edit-fields">${Object.entries(studyFields).map(([key,label])=>`<label class="field">${label}<textarea name="${key}" rows="${['working_title','original_title'].includes(key)?2:4}" maxlength="${studyLimits[key]}" ${['working_title','question'].includes(key)?'required':''}>${e(fields[key]||'')}</textarea></label>`).join('')}</div>
 <label class="ack"><input type="checkbox" name="context_enabled" ${canRecover?draft.context_enabled?'checked':'':brief.context_enabled?'checked':''}>${L('将简报用于后续模型研究；运行前可预览参考范围')}</label>
 <label class="ack"><input type="checkbox" name="acknowledge">${L('我已核对研究范围；该确认不代表结果已验证')}</label>
 <p class="field-help">${L('修改简报后重新核对。用于模型的简报发生变化时，已有阶段成果需要重核。')}</p>
 <div class="modal-footer"><span id="studio-draft-status">${L('编辑内容在当前设备保留草稿。')}</span><div class="studio-actions"><button class="btn secondary" type="submit" value="draft">${L('保存草稿')}</button><button class="btn primary" type="submit" value="confirm">${L('保存并确认范围')}</button></div></div></form>`,true);
}

async function studioSave(f,confirm=false){
 if(Studio.saving)return;
 if(confirm&&!f.elements.acknowledge.checked)throw new Error(L('请先勾选已核对研究范围。'));
 Studio.saving=true;const pid=f.dataset.project;cacheStudyDraft();
 f.querySelectorAll('button[type="submit"]').forEach(b=>b.disabled=true);
 try{
  const result=await api(`project/${pid}/study-save`,{revision:Number(f.dataset.revision),fields:Object.fromEntries(Object.keys(studyFields).map(key=>[key,f.elements[key].value])),context_enabled:f.elements.context_enabled.checked,confirm,acknowledge:f.elements.acknowledge.checked});
  closeModal();try{localStorage.removeItem(studyDraftKey(pid))}catch{}
  if(S.project?.id===pid){S.project=result;S.tab='brief'}
  S.projects=await api('projects');render();toast(confirm?L('研究范围已记录为核对完成。'):L('研究简报草稿已保存。'));
 }finally{Studio.saving=false;if(f.isConnected)f.querySelectorAll('button[type="submit"]').forEach(b=>b.disabled=false)}
}

async function studioTask(target,pid='',general=false){
 if(pid){
  if(target==='analysis'&&!general){
   const p=S.project?.id===pid?S.project:await api('project/'+pid),profile=p.study_brief?.profile;
   if(['finance','history'].includes(profile)){
    modal(H`<div class="eyebrow">${e(studyProfiles[profile])}</div><h2>${profile==='finance'?L('先准备策略评估的数据与协议'):L('先准备历史比较的资料与口径')}</h2><p class="modal-description">${e(p.title)}</p><div class="studio-notice"><p>${profile==='finance'?L('专用金融回测引擎尚未接入。先核对信息时间、基准、样本外划分、交易约束与成本，再导入外部回测或前向评估的真实记录。'):L('历史资料需要先核对分期、统计口径、来源版本与跨国可比性。现有统计工具不会自动识别政策因果效果。')}</p></div><div class="studio-actions">${button('studio-task',L('整理本项目材料'),'primary','upload','data-target="materials" data-project="'+e(pid)+'"')}${button('studio-task',L('核对研究简报'),'secondary','file','data-target="brief" data-project="'+e(pid)+'"')}</div><details class="studio-methods"><summary>${L('按需使用通用统计工具')}</summary><p class="field-help">${L('请逐项核对方法适用条件。模型概率评估等通用分析不能代替策略回测或政策因果识别。')}</p>${button('studio-task',L('打开通用统计工具'),'secondary','bars','data-target="analysis" data-project="'+e(pid)+'" data-general="1"')}</details>`,true);return;
   }
  }
  await assistantNavigate(target,pid);closeModal();return;
 }
 const projects=activeProjects();
 modal(H`<h2>选择要继续的项目</h2><p class="modal-description">任务会使用所选项目的材料与记录。</p><div class="studio-picker">${projects.map(p=>`<button class="studio-project" data-action="studio-task" data-target="${e(target)}" data-project="${e(p.id)}"><span class="studio-project-text"><strong>${e(p.title)}</strong><small>${p.asset_count} ${L('份材料')}</small></span>${icon('arrow',16)}</button>`).join('')||H`<p>先为这项任务创建一个项目。</p>`}</div>${button('studio-task-new',L('新建项目'),'primary','plus','data-target="'+e(target)+'"')}`,true);
}

async function studioAction(action,el){
 if(action==='studio-plans')return studioPlans();
 if(action==='studio-create'){
  el.disabled=true;try{const p=await api('studies/create',{blueprint:el.dataset.blueprint});closeModal();S.projects=await api('projects');await openProject(p.id,'brief');toast(L('研究计划已就绪，请先核对简报与材料缺口。'))}finally{if(el.isConnected)el.disabled=false}return;
 }
 if(action==='studio-edit')return studioEdit();
 if(action==='studio-tasks'){S.tab='research';RT.tab='navigator';render();return}
 if(action==='studio-task')return studioTask(el.dataset.target,el.dataset.project||'',el.dataset.general==='1');
 if(action==='studio-task-new'){newProjectModal(el.dataset.target==='analysis'?'scientific_data':'full');$('#create-form').dataset.assistantTarget=el.dataset.target;return}
 if(action==='studio-old-draft'){
  const raw=localStorage.getItem(studyDraftKey(S.project.id));
  // Separate dialog retains the older version before any new input is typed.
  modal(H`<h2>旧版本的本机草稿</h2><pre class="studio-raw-draft">${e(raw||L('未找到旧草稿'))}</pre>`,true);return;
 }
 if(action==='studio-export'){
  const blob=await api(`project/${S.project.id}/study-export`,{},true),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='研究简报.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
 }
}
document.addEventListener('input',ev=>{if(ev.target.closest('#studio-brief-form')){const saved=cacheStudyDraft(),status=$('#studio-draft-status');if(status)status.textContent=saved?L('已保留本机草稿；尚未保存到项目。'):L('本机草稿空间不可用，请及时保存到项目。')}});
document.addEventListener('change',ev=>{if(ev.target.closest('#studio-brief-form'))cacheStudyDraft()});
