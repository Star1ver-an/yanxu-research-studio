const Guide={state:{status:'not_started',step:0},active:false,busy:false};
const guideSteps=[
 {title:'先选适合当前任务的路径',view:'home',target:'.route-cards',icon:'connection',body:'开题与写作适合形成开题报告、论文或审阅意见；科研工具与分析适合文献证据、实验方案和表格质控。新建项目时，也可以输入目标和材料，请应用给出推荐。'},
 {title:'连接你准备使用的模型',view:'settings',target:'.mode-options',icon:'settings',body:'在这里选择本机 Codex 或自定义 API，保存后再测试连接。两条研究路径共用这份设置；本机 Codex 需先登录，API 由对应服务商单独计费。引导本身不会检测连接或调用模型。',cta:'现在配置连接',action:'settings'},
 {title:'从一个课题开始，接着做下一步',view:'projects',target:'#guide-create',icon:'plus',body:'只填课题也能创建，应用会在本机推荐路径；可以展开更多设置自行选择。打开项目先看“继续研究”，查看下一步、待核对事项与进度简报。“项目记忆”可保存设备条件、目标和约定，供助手参考。',cta:'现在创建项目',action:'create'},
 {title:'先整理真实材料，再开始研究',view:'project',target:'.project-tabs',icon:'upload',body:'打开项目的“文献与材料”，导入有权使用的论文、笔记或数据。检索结果需要你勾选后才用于分析。扫描 PDF 需先 OCR。项目内“研究工具”可阅读材料、建立证据卡片和引用库。',cta:'打开材料页',action:'materials'},
 {title:'运行、阅读、确认，再推进下一步',view:'project',target:'.stage-panel',icon:'check',body:'点击“开始这一步”，阅读并核实报告，再确认进入下一阶段。遇到卡点可打开“AI 助手”或“解释这一步”。材料或前序内容改变后，“继续研究”会提示哪些成果和建议需要复核。复核会沿用原问题，发送前仍可检查参考范围。操作确认不等于实验真实性认证。',cta:'前往研究流程',action:'work'},
 {title:'把项目管理得清楚、有序',view:'projects',target:'#project-batch-bar',icon:'folder',body:'勾选项目即可批量改名、置顶、归档或创建独立副本。删除先进入回收站，可以恢复；永久删除另需输入确认。侧栏“备份与恢复”可保存整库并预览恢复副本。运行中的项目暂不能管理。以后可在“使用指南”或侧栏重新查看这份引导。'}
];
async function saveGuide(status,step=Guide.state.step){Guide.state=await api('preferences/onboarding',{status,step});paintGuideResume()}
function paintGuideResume(){
 let button=$('#guide-resume');
 const show=Guide.state.status==='in_progress'&&!Guide.active;
 if(!show){button?.remove();return}
 if(!button){const toolbar=$('.top-actions');if(!toolbar)return;button=document.createElement('button');button.id='guide-resume';button.className='btn secondary guide-resume';button.dataset.action='guide-resume';toolbar.prepend(button)}
 button.textContent=`继续引导 ${Guide.state.step+1}/6`;
 button.setAttribute('aria-label',`继续功能引导，第 ${Guide.state.step+1} 步，共 6 步`);
}
function clearGuideSpotlight(){document.body.classList.remove('guide-mode');document.querySelector('.guide-spotlight')?.remove()}
function positionGuide(){
 if(!Guide.active)return;
 const dialog=$('.modal'),overlay=$('.modal-overlay'),target=$(guideSteps[Guide.state.step].target);if(!dialog||!overlay)return;
 let spotlight=$('.guide-spotlight');if(!spotlight){spotlight=document.createElement('div');spotlight.className='guide-spotlight';spotlight.setAttribute('aria-hidden','true');overlay.prepend(spotlight)}
 overlay.classList.toggle('guide-centered',!target);
 if(!target){spotlight.hidden=true;dialog.style.position='relative';return}
 spotlight.hidden=false;const r=target.getBoundingClientRect(),pad=7;
 Object.assign(spotlight.style,{left:Math.max(8,r.left-pad)+'px',top:Math.max(8,r.top-pad)+'px',width:Math.min(r.width+pad*2,window.innerWidth-16)+'px',height:Math.min(r.height+pad*2,window.innerHeight-16)+'px'});
 const width=Math.min(450,window.innerWidth-32),height=Math.min(dialog.offsetHeight,window.innerHeight-40);
 let left=window.innerWidth-width-24,top=window.innerHeight-height-24;
 if(r.right+width+32<window.innerWidth){left=r.right+22;top=r.top}
 else if(r.top>height+30){top=r.top-height-22}
 else if(window.innerHeight-r.bottom>height+30){top=r.bottom+22}
 Object.assign(dialog.style,{position:'fixed',width:width+'px',left:Math.max(16,Math.min(left,window.innerWidth-width-16))+'px',top:Math.max(16,Math.min(top,window.innerHeight-height-16))+'px',margin:'0'});
}
async function showGuide(step){
 if(Guide.busy)return;Guide.busy=true;
 try{
  await saveGuide('in_progress',step);Guide.active=true;paintGuideResume();
  const info=guideSteps[step],project=activeProjects().find(p=>p.id===S.project?.id)||activeProjects()[0];
  if(info.view==='projects'){PM.state='active';PM.route='all';PM.selected.clear();S.query='';S.filter='all'}
  if(info.view==='project'&&project)await openProject(project.id,step===3?'materials':'work',false);
  else{S.view=info.view==='project'?'projects':info.view;S.pathwayRoute=null;clearTimeout(pollTimer);render()}
  modal(`<div class="guide-counter">功能引导 · ${step+1} / ${guideSteps.length}</div><div class="guide-dots" aria-hidden="true">${guideSteps.map((_,i)=>`<i class="${i===step?'current':i<step?'done':''}"></i>`).join('')}</div><span class="template-icon">${icon(info.icon,24)}</span><h2>${info.title}</h2><p class="guide-body">${info.body}</p>${info.cta?button('guide-go',info.cta,'text','arrow',`data-go="${info.action}"`):''}<div class="guide-controls">${button('guide-skip','稍后再看','text')}${step?button('guide-back','上一步','secondary'):''}${button(step===5?'guide-finish':'guide-next',step===5?'开始使用':'下一步','primary',step===5?'check':'arrow')}</div>`);
  document.body.classList.add('guide-mode');$(info.target)?.scrollIntoView({block:'center'});positionGuide();
 }finally{Guide.busy=false}
}
async function initOnboarding(state){
 Guide.state=state||{status:'not_started',step:0};paintGuideResume();
 if(Guide.state.status==='in_progress'||Guide.state.status==='not_started'&&!S.projects.length)await showGuide(Guide.state.step);
}
function onGuideModalClosed(){
 if(!Guide.active)return;
 Guide.active=false;clearGuideSpotlight();saveGuide('dismissed').catch(err=>toast(err.message,true));
}
async function guideAction(action,el){
 if(Guide.busy)return;
 if(action==='guide-start'||action==='guide-resume'){
  if(S.draft!==null||S.view==='settings'&&S.settingsDirty)throw Error('请先保存正在编辑的报告或连接设置，再打开引导。');
  return showGuide(action==='guide-start'?0:Guide.state.step);
 }
 if(action==='guide-next')return showGuide(Math.min(5,Guide.state.step+1));
 if(action==='guide-back')return showGuide(Math.max(0,Guide.state.step-1));
 if(action==='guide-finish'||action==='guide-skip'){
  await saveGuide(action==='guide-finish'?'completed':'dismissed');Guide.active=false;clearGuideSpotlight();closeModal();paintGuideResume();return;
 }
 if(action==='guide-go'){
  Guide.active=false;clearGuideSpotlight();closeModal();paintGuideResume();
  const go=el.dataset.go,project=activeProjects().find(p=>p.id===S.project?.id)||activeProjects()[0];
  if(go==='create'||!project&&['materials','work'].includes(go)){S.view='projects';render();createModal();return}
  if(go==='settings'){S.view='settings';render();return}
  if(project)await openProject(project.id,go==='materials'?'materials':'work');
 }
}
window.addEventListener?.('resize',()=>{if(Guide.active)positionGuide()});
