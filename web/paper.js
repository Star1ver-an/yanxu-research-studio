/* Whole-paper management: local configuration, explicit commits and scoped jobs. */
const PaperSessions = new Map();
function paperButton(action, label, attrs='', kind='secondary') { return button('pf-'+action,e(label),kind,'',attrs); }
function paperState(p=S.project) {
  if(!PaperSessions.has(p.id)) PaperSessions.set(p.id,{pid:p.id,value:null,plan:null,base:'',loading:false,attempted:false,busy:'',error:'',section:'list',selected:[],generation:0,
    style:{font_size:10,dpi:300,palette:'accessible'},resize:false,width:7.0866,batchAction:'style',oldSource:'',newSource:'',preview:null,job:null,timer:null,pending:null,
    composeTitle:L('论文组合图'),columns:2,shared:false,includeSources:false,allowWarnings:false});
  return PaperSessions.get(p.id);
}
function paperHere(state) { return S.view==='project'&&S.project?.id===state.pid&&S.tab==='research'&&RT.tab==='figures'&&plotState().mode==='paper'; }
function paperPaint(state) { if(paperHere(state)) render(); }
function paperBusy(state) { return !!state.busy||['queued','running','cancelling'].includes(state.job?.status); }
function paperDirty(state) { return !!state.plan&&(state.value?.revision===0||JSON.stringify(state.plan)!==state.base); }
function paperInvalidate(state) { state.generation++;state.preview=null;state.pending=null;state.error='';if(paperHere(state)){const row=$('.pf-save-row');if(row){const btn=row.querySelector('button');if(btn)btn.disabled=!paperDirty(state);const label=row.querySelector('span');if(label)label.textContent=paperDirty(state)?L('有未保存的清单或规格修改'):L('清单与规格已保存');}} }
function paperAdopt(state,value) { state.value=value;state.plan=plotClone(value.plan);state.base=JSON.stringify(value.plan);state.error='';if(value.job)state.job=value.job; }
async function paperLoad(state) {
  if(state.loading)return; state.loading=true;state.attempted=true;state.error='';const generation=state.generation;
  try { const value=await api(`project/${state.pid}/paper-state`,{});if(state.generation===generation)paperAdopt(state,value); }
  catch(error) { state.error=error.message; }
  finally {state.loading=false;paperPaint(state);if(['queued','running','cancelling'].includes(state.job?.status)&&!state.polling)queueMicrotask(()=>paperPoll(state));}
}
async function paperEnsureSaved(state) {
  if(!paperDirty(state))return;
  const sent=plotClone(state.plan),generation=state.generation;state.busy='saving';paperPaint(state);
  try{const value=await api(`project/${state.pid}/paper-save`,{plan:sent,expected_revision:state.value.revision});
    if(state.generation===generation)paperAdopt(state,value);else{state.value=value;state.base=JSON.stringify(sent);}
  }finally{state.busy='';paperPaint(state);}
}
function paperSelected(state) { const ids=state.plan?.entries.map(e=>e.figure_id)||state.selected;return ids.filter(id=>state.selected.includes(id)&&plotFigures().some(f=>f.id===id)); }
function paperIssues(state) { return state.value?.audit?.issues||[]; }
function paperEntryRows(state,p) {
  const entries=state.plan.entries,rows=plotFigures(p),byId=new Map(rows.map(f=>[f.id,f]));
  const list=entries.map((entry,index)=>{
    const row=byId.get(entry.figure_id),issues=paperIssues(state).filter(i=>i.figure_id===entry.figure_id&&i.level!=='info');
    return H`<article class="pf-row ${!row?'pf-missing':''}"><div class="pf-row-head"><label class="pf-select"><input type="checkbox" data-paper-select="${e(entry.figure_id)}" ${state.selected.includes(entry.figure_id)?'checked':''} ${!row?'disabled':''} aria-label="选择图表 ${index+1}"><span class="pf-number">${index+1}</span></label><div class="pf-row-title"><h4>${e(row?.title||L('图表已删除'))}</h4><p>${e(row?plotKindName(row.spec.kind):entry.figure_id)}${row?L(' · 版本 ')+e(row.revision):''}${issues.length?' · '+issues.length+L(' 项待核对'):''}</p></div><div class="pf-order">${paperButton('move',L('上移'),`data-id="${e(entry.figure_id)}" data-direction="-1" ${index===0?'disabled':''}`)}${paperButton('move',L('下移'),`data-id="${e(entry.figure_id)}" data-direction="1" ${index===entries.length-1?'disabled':''}`)}</div></div>
      <div class="pf-row-bottom"><label class="pl-check"><input type="checkbox" data-paper-entry="include" data-id="${e(entry.figure_id)}" ${entry.include?'checked':''}>纳入交付</label><label class="field pf-role"><span class="sr-only">图 ${index+1} 用途</span><select data-paper-entry="role" data-id="${e(entry.figure_id)}">${[['main',L('正文图')],['supplement',L('补充图')],['abstract',L('图形摘要')]].map(([v,l])=>`<option value="${v}" ${entry.role===v?'selected':''}>${l}</option>`).join('')}</select></label>${row?paperButton('edit',L('打开图稿'),`data-id="${e(row.id)}"`):''}${paperButton('remove',L('移出清单'),`data-id="${e(entry.figure_id)}"`,'text')}</div>
      <details><summary>正文位置与图注核对</summary><label class="field">正文位置<input data-paper-entry="section" data-id="${e(entry.figure_id)}" value="${e(entry.section)}" maxlength="300" placeholder="例如：实验结果 3.2 节"></label><label class="pl-check"><input type="checkbox" data-paper-entry="caption_reviewed" data-id="${e(entry.figure_id)}" ${entry.caption_reviewed?'checked':''}>已人工核对图注、单位和适用范围</label><p class="pf-caption">${e(row?.caption||L('尚无图注，请打开图稿补充。'))}</p>${row?paperButton('copy-token',L('复制正文插图标记'),`data-id="${e(row.id)}"`):''}</details></article>`;
  }).join('');
  const available=rows.filter(row=>!entries.some(entry=>entry.figure_id===row.id));
  return list+(available.length?H`<details class="pf-additions" open><summary>图表库中还有 ${available.length} 张图</summary>${available.map(row=>`<div><span>${e(row.title)}</span>${paperButton('add',L('加入清单'),`data-id="${e(row.id)}"`)}</div>`).join('')}</details>`:'');
}
function paperListView(state,p) {
  return H`<div class="pf-section-heading"><div><h3>整理这一篇论文的图表</h3><p>清单顺序决定文件编号和 Word 图号。移出清单不会删除图表。</p></div><div class="pl-actions">${paperButton('abstract',L('新建图形摘要'))}${paperButton('select-all',L('选择已纳入图表'))}${paperButton('select-none',L('取消选择'))}</div></div>
    
    ${state.plan.entries.length?`<div class="pf-list">${paperEntryRows(state,p)}</div>`:H`<div class="pf-empty"><h3>先保存几张图，再整理成论文图表</h3><p>可以先创建研究方案的图形摘要，也可以回到绘图或分析流程。</p>${paperEntryRows(state,p)}</div>`}
    <details class="pf-name"><summary>清单名称</summary><label class="field">交付名称<input data-paper-plan="title" value="${e(state.plan.title)}" maxlength="160"></label></details>
    <details class="pf-composer" ${state.selected.length?'open':''}><summary>将所选单图组合排版 · ${state.selected.length} 张</summary><p>最多 6 张。保留固定子图版本与原始来源；子图变化后可定位并刷新组合。SVG 保留子图矢量；PNG、TIFF、PDF 使用栅格面板，交付包另存各子图矢量。</p><div class="pf-fields"><label class="field">组合图标题<input data-paper-local="composeTitle" value="${e(state.composeTitle)}" maxlength="160"></label><label class="field">布局列数<select data-paper-local="columns">${[1,2,3].map(n=>H`<option value="${n}" ${state.columns===n?'selected':''}>${n} 列</option>`).join('')}</select></label></div><label class="pl-check"><input type="checkbox" data-paper-local="shared" ${state.shared?'checked':''}>合并相同图例（仅支持分组与顺序一致的折线或散点）</label><div class="pl-actions">${paperButton('compose',L('创建组合图草稿'),!state.selected.length?'disabled':'','primary')}</div></details>`;
}
function paperStyleView(state,p) {
  return H`<div class="pf-section-heading"><div><h3>统一风格与批量更新</h3><p>先在“图表清单”选择图表。样式更新不运行分析、不调用 AI，旧版本可恢复。</p></div><span class="badge">已选 ${state.selected.length} 张</span></div>
    <label class="field">这次要做什么？<select data-paper-local="batchAction"><option value="style" ${state.batchAction==='style'?'selected':''}>统一配色、字号与分辨率</option><option value="refresh" ${state.batchAction==='refresh'?'selected':''}>重新绘制并刷新组合中的子图版本</option><option value="sources" ${state.batchAction==='sources'?'selected':''}>使用新材料更新普通数据图</option></select></label>
    ${state.batchAction==='style'?H`<div class="pl-actions pf-presets">${paperButton('preset',L('克制彩色'),`data-preset="color"`)}${paperButton('preset',L('黑白打印'),`data-preset="gray"`)}${paperButton('preset',L('双栏版式'),`data-preset="double"`)}</div><div class="pf-fields"><label class="field">字号（pt）<input data-paper-style="font_size" type="number" value="${state.style.font_size}" min="6" max="24"></label><label class="field">位图分辨率（DPI）<input data-paper-style="dpi" type="number" value="${state.style.dpi}" min="72" max="600"></label><label class="field">配色<select data-paper-style="palette">${[['accessible',L('色觉友好')],['muted',L('克制多色')],['gray',L('灰阶')]].map(([v,l])=>`<option value="${v}" ${state.style.palette===v?'selected':''}>${l}</option>`).join('')}</select></label></div><label class="pl-check"><input data-paper-local="resize" type="checkbox" ${state.resize?'checked':''}>同时统一宽度，按比例调整画布高度</label>${state.resize?H`<label class="field">目标宽度（mm）<input data-paper-local="width" type="number" min="76.2" max="406.4" step="0.1" value="${(state.width*25.4).toFixed(1)}"></label>`:''}`:
    state.batchAction==='sources'?H`<p class="notice">普通数据图可以批量替换来源并重新绘制。分析图需重新核对字段和统计条件后运行；组合图在子图更新后刷新。当前原图均保留。</p><div class="pf-fields"><label class="field">旧材料<select data-paper-local="oldSource"><option value="">请选择</option>${p.assets.map(a=>`<option value="${e(a.id)}" ${state.oldSource===a.id?'selected':''}>${e(a.name)}</option>`).join('')}</select></label><label class="field">新材料<select data-paper-local="newSource"><option value="">请选择</option>${p.assets.map(a=>`<option value="${e(a.id)}" ${state.newSource===a.id?'selected':''}>${e(a.name)}</option>`).join('')}</select></label></div>${state.selected.length?H`<details><summary>分析图需要单独确认时</summary>${state.selected.filter(id=>plotFigures(p).find(f=>f.id===id)?.spec.kind==='analysis').map(id=>`<div class="pf-link-row"><span>${e(plotFigures(p).find(f=>f.id===id).title)}</span>${paperButton('source-draft',L('打开替换来源草稿'),`data-id="${e(id)}"`)}</div>`).join('')||H`<p>所选图表没有分析图。</p>`}</details>`:L('预览后将检查实际字段；没有匹配字段时整批保留原版本。')}`:
    H`<p class="notice">重新使用已保存配方绘图；组合图会载入最新单图。分析图复用冻结结果，需要重算的请先回到分析向导。</p>`}
    <div class="pl-actions">${paperButton('batch-preview',L('查看将要修改的内容'),state.selected.length?'':'disabled','primary')}</div>
    ${state.preview?H`<section class="pf-preview"><h4>待开始的批量操作 · ${state.preview.updates.length} 张</h4><p>${e(state.preview.note)}</p><ul>${state.preview.updates.map(row=>H`<li><strong>${e(row.title)}</strong><span>版本 ${row.revision} → ${row.revision+1} · ${e(row.changed_fields.map(k=>({style:L('样式'),source_ids:L('来源材料'),composition:L('组合子图版本'),panels:L('原图面板')})[k]||k).join('、')||L('按现有配置重新绘制'))}</span></li>`).join('')}</ul>${state.preview.skipped.length?H`<p>以下图表本次跳过：</p><ul>${state.preview.skipped.map(r=>`<li>${e(plotFigures(p).find(f=>f.id===r.id)?.title||r.id)}：${e(r.reason)}</li>`).join('')}</ul>`:''}<div class="pl-actions">${paperButton('batch-start',L('明确开始并保存整批更新'),'','primary')}${paperButton('discard-preview',L('取消这次预览'))}</div></section>`:''}`;
}
function paperCheckView(state,p) {
  const audit=state.value.audit,stages=flow(p).stages;
  return H`<div class="pf-section-heading"><div><h3>正文关联与投稿规格</h3><p>按你填写的规格检查。预设是通用尺寸参考，不代表任何期刊的正式要求。</p></div>${paperButton('check',L('保存并重新检查'),'','primary')}</div>
    <details open><summary>选择参与联动的正文</summary><div class="pf-stage-list">${stages.map(s=>`<label class="pl-check"><input type="checkbox" data-paper-stage="${e(s)}" ${state.plan.stages.includes(s)?'checked':''}>${e(S.catalog.stages[s].title)}${p.outputs?.[s]?.text?'':L(' · 尚无正文')}</label>`).join('')}</div><p class="pf-note">在稿件中粘贴 [figure:图表ID] 定位插图。重复标记会显示交叉引用；未放置的图按清单顺序附在文末。</p></details>
    <details><summary>技术规格与交付格式</summary><div class="pf-fields">${[['max_width_mm',L('最大宽度（mm）'),76.2,406.4],['max_height_mm',L('最大高度（mm）'),63.5,406.4],['min_font_pt',L('最小字号（pt）'),6,24],['min_dpi',L('最低位图 DPI'),72,600]].map(([key,label,min,max])=>`<label class="field">${label}<input type="number" data-paper-requirement="${key}" value="${state.plan.requirements[key]}" min="${min}" max="${max}" step="0.1"></label>`).join('')}</div><div class="pf-formats">${['png','svg','pdf','tiff'].map(fmt=>`<label class="pl-check"><input type="checkbox" data-paper-format="${fmt}" ${state.plan.requirements.formats.includes(fmt)?'checked':''}>${fmt.toUpperCase()}</label>`).join('')}</div></details>
    <div class="pf-audit-heading"><h4>${audit.errors} 项阻止交付 · ${audit.warnings} 项待核对</h4>${paperDirty(state)?H`<span class="badge">有未保存设置，请重新检查</span>`:''}</div>
    ${audit.issues.length?`<div class="pf-issues">${audit.issues.map(item=>`<article class="pf-issue ${e(item.level)}"><span class="badge">${({error:L('需修复'),warning:L('待核对'),info:L('说明')})[item.level]}</span><div><strong>${e(plotFigures(p).find(f=>f.id===item.figure_id)?.title||item.stage&&S.catalog.stages[item.stage]?.title||L('论文图表'))}</strong><p>${e(item.message)}</p>${item.stage?H`<small>正文阶段：${e(S.catalog.stages[item.stage]?.title||item.stage)}</small>`:''}</div><div class="pl-actions">${item.figure_id&&plotFigures(p).some(f=>f.id===item.figure_id)?paperButton('edit',L('查看图稿'),`data-id="${e(item.figure_id)}"`):''}${item.stage?paperButton('stage',L('查看正文'),`data-stage="${e(item.stage)}"`):''}</div></article>`).join('')}</div>`:H`<p class="notice">当前登记内容通过技术检查。研究结论、图注含义与期刊要求仍需作者核对。</p>`}
    <details><summary>查看实际尺寸与正文引用 · ${audit.checks.length} 张</summary><div class="pf-check-table"><table><thead><tr><th>图表</th><th>尺寸 mm</th><th>DPI</th><th>字号 pt</th><th>Word 估算</th></tr></thead><tbody>${audit.checks.map(c=>H`<tr><td>图 ${c.number} ${e(c.title)}</td><td>${c.width_mm} × ${c.height_mm}</td><td>${c.dpi}</td><td>${c.minimum_font_pt}</td><td>${c.estimated_word_font_pt}</td></tr>`).join('')}</tbody></table></div>${audit.links.map(link=>`<p class="pf-note">${e(link.stage_title)} → ${e(plotFigures(p).find(f=>f.id===link.figure_id)?.title||L('缺失图表'))}</p>`).join('')}</details>
    <p class="pf-note">估算字号不替代成品检查，原始图片有效分辨率、图形含义、颜色可辨性和投稿要求需人工核对。${e(audit.note)}</p><div class="pl-actions">${paperButton('review',L('已核对当前正文与图表关联'),audit.errors?'disabled':'')}${paperButton('to-deliver',L('前往交付包'),'','primary')}</div>`;
}
function paperDeliverView(state) {
  const count=state.plan.entries.filter(e=>e.include).length,audit=state.value.audit;
  return H`<h3>生成一致的论文图表交付包</h3><p>按清单顺序生成 ${count} 张图的 ${state.plan.requirements.formats.map(x=>x.toUpperCase()).join(' / ')}、图注、可编辑配方、分析结果、正文 Word 和检查报告。组合图另含各子图 SVG/PDF。</p>
    <div class="pf-delivery-summary"><span>图号与文件名一致</span><span>正文标记定位插图</span><span>旧版本与来源可追溯</span></div>
    <label class="pl-check"><input type="checkbox" data-paper-local="includeSources" ${state.includeSources?'checked':''}>同时包含原始材料</label><p class="pf-note">默认不打包原始文件。配方、分析结果及正文也可能包含研究内容，分享前请核对授权。完整项目迁移请使用项目 ZIP 或整库备份。</p>
    ${audit.warnings?H`<label class="pl-check"><input type="checkbox" data-paper-local="allowWarnings" ${state.allowWarnings?'checked':''}>保留 ${audit.warnings} 项待核对记录，导出带检查报告的草稿交付包</label>`:''}
    ${audit.errors?H`<p class="notice error">还有 ${audit.errors} 项来源或正文引用错误，请先在“关联与检查”中修复。</p>`:''}
    <div class="pl-actions">${paperButton('export',L('生成交付包'),!count||audit.errors?'disabled':'','primary')}${paperButton('to-check',L('返回检查'))}</div><p class="pf-note">生成交付不调用模型。进行中可取消；正文或图稿若发生变化，会停止生成混合版本包。</p>
    ${state.job?.kind==='export'&&state.job.status==='completed'&&!state.job.stale?H`<section class="pf-ready"><h4>交付包已准备好</h4><p>${state.job.summary?.figures||count} 张图 · ${((state.job.summary?.bytes||0)/1024/1024).toFixed(1)} MB</p>${paperButton('download',L('下载论文图表交付包'),'','primary')}</section>`:''}
    ${state.job?.stale?H`<p class="notice">图稿、清单或正文已变化，请重新生成当前版本交付包。</p>`:''}<details><summary>交付缓存</summary><p>交付缓存临时保留；软件重启后请重新生成。下载后可清理缓存，项目、原图与保存版本不受影响。</p>${paperButton('clean',L('清理本项目交付缓存'))}</details>`;
}
function paperJobView(state) {
  const job=state.job;if(!job)return '';
  const active=['queued','running','cancelling'].includes(job.status);
  return `<section class="panel pf-job" role="status"><div><strong>${job.stale?L('交付需重新生成'):({queued:L('已排队'),running:L('正在处理'),cancelling:L('正在取消'),completed:L('操作完成'),failed:L('操作未完成'),cancelled:L('已取消')})[job.status]||L('任务状态')}</strong><p>${e(job.stale?L('图稿、清单或正文已变化，请在交付包中重新生成。'):job.progress||'')}</p>${job.summary?.updated?H`<p>已更新 ${job.summary.updated} 张图表，历史版本保留。</p>`:''}</div>${active?paperButton('cancel',L('取消本次任务'),job.status==='cancelling'?'disabled':''):paperButton('dismiss-job',L('收起状态'))}</section>`;
}
function paperView(p) {
  const state=paperState(p);state.selected=paperSelected(state);
  if(!state.value&&!state.loading&&!state.attempted)queueMicrotask(()=>paperLoad(state));
  const audit=state.value?.audit||{},count=state.plan?.entries.filter(e=>e.include).length||0,busy=paperBusy(state);
  return H`<section class="pf-workspace" aria-label="整篇论文图表管理"><section class="panel pf-header"><div class="section-title"><div><span class="eyebrow">PAPER FIGURES</span><h2>整篇论文图表</h2><p>整理、统一、核对和交付，让正文、图稿与图注保持一致。</p></div><div class="pf-header-actions">${paperButton('other-tools',L('其他研究工具'))}${paperButton('refresh',L('刷新检查'),busy?'disabled':'')}</div></div>${analysisModeSwitch('paper')}${state.value?H`<div class="pf-metrics"><div><strong>${count}</strong><span>纳入交付</span></div><div><strong>${audit.links?.length||0}</strong><span>正文关联</span></div><div><strong>${audit.affected_figures?.length||0}</strong><span>需关注的图</span></div><div><strong>${audit.warnings||0}</strong><span>待核对项</span></div></div>`:''}</section>${state.error?`<p class="notice error" role="alert">${e(state.error)}</p>`:''}${paperJobView(state)}
    ${!state.plan?H`<section class="panel"><p role="status">正在读取论文图表清单…</p></section>`:H`<nav class="pf-tabs" aria-label="论文图表步骤">${[['list',L('图表清单')],['style',L('统一与更新')],['check',L('关联与检查')],['deliver',L('交付包')]].map(([v,l])=>paperButton('section',l,`data-section="${v}" aria-current="${state.section===v?'step':'false'}"`,state.section===v?'primary':'secondary')).join('')}</nav><section class="panel pf-body" aria-busy="${busy}"><fieldset class="pf-controls" ${busy?'disabled':''}>${state.section==='list'?paperListView(state,p):state.section==='style'?paperStyleView(state,p):state.section==='check'?paperCheckView(state,p):paperDeliverView(state)}<div class="pf-save-row"><span>${paperDirty(state)?L('有未保存的清单或规格修改'):L('清单与规格已保存')}</span>${paperButton('save',L('保存清单'),paperDirty(state)?'':'disabled')}</div></fieldset></section>`}</section>`;
}
function paperAbstractEditor(state) {
  const spec=state.spec;
  if(spec.kind==='composition')return H`<p class="pf-note">子图和顺序在“整篇论文图表”中管理。这里可以调整组合图标题、画布和图注。</p><ol>${spec.composition.items.map(c=>H`<li>${e(c.label)} · ${e(c.spec.title||L('未命名单图'))} · 版本 ${c.revision}</li>`).join('')}</ol>${paperButton('mode',L('管理论文清单'))}`;
  return H`<p class="pf-note">用节点、箭头与分区编辑图形摘要。方案使用虚线箭头并标明待验证，不生成实验原图。</p><label class="field">研究状态<select data-paper-abstract="status"><option value="plan" ${spec.abstract.status==='plan'?'selected':''}>研究方案 · 待验证</option><option value="results" ${spec.abstract.status==='results'?'selected':''}>结果示意 · 作者核对</option></select></label><label class="field">节点（每行 编号 | 内容）<textarea id="pl-nodes" rows="4">${e(spec.diagram.nodes.map(n=>n.id+' | '+n.label).join('\n'))}</textarea></label><label class="field">箭头（每行 起点 → 终点 | 标签）<textarea id="pl-edges" rows="3">${e(spec.diagram.edges.map(n=>n.from+' → '+n.to+(n.label?' | '+n.label:'')).join('\n'))}</textarea></label>${spec.abstract.sections.map((section,index)=>H`<fieldset class="pf-abstract-section"><legend>分区 ${index+1}</legend><label class="field">分区标题<input data-paper-section="title" data-index="${index}" value="${e(section.title)}" maxlength="36"></label><label class="field">本分区节点编号（逗号分隔）<input data-paper-section="nodes" data-index="${index}" value="${e(section.nodes.join(', '))}"></label></fieldset>`).join('')}<label class="field">摘要说明<textarea data-paper-abstract="summary" rows="2" maxlength="220">${e(spec.abstract.summary||'')}</textarea></label>`;
}
async function paperAdoptDraft(state,spec,id='') {
  const pl=plotState();
  if(pl.busy)throw Error(L('请先等待当前绘图完成。'));
  if(plotDirty(pl))throw Error(L('绘图页还有未保存修改，请先保存或放弃该草稿，再创建新的图。'));
  pl.mode='draw';pl.id=id;pl.sources=[...(spec.source_ids||[])];pl.editSession++;pl.undo=[];pl.redo=[];pl.preview=null;pl.previewKey='';pl.caption='';pl.captionEdited=false;
  const row=plotFigures().find(r=>r.id===id);pl.meta={caption:row?.caption||'',experiment_id:row?.experiment_id||'',stage:row?.stage||'',location:row?.location||''};
  plotAdopt(spec,pl,false);pl.fieldsExpanded=true;pl.styleExpanded=false;pl.sourcesExpanded=false;await plotInspectSources(pl);render();
  $('.pl-editor')?.scrollIntoView?.({block:'start',behavior:'smooth'});
}
function paperBatchData(state) {
  return {figure_ids:[...state.selected],action:state.batchAction,...(state.batchAction==='style'?{style:{...state.style,...(state.resize?{width:state.width}:{})}}:{}),
    ...(state.batchAction==='sources'?{source_map:state.oldSource&&state.newSource?{[state.oldSource]:state.newSource}:{}}:{})};
}
async function paperPoll(state) {
  if(state.polling)return;state.polling=true;
  clearTimeout(state.timer);const id=state.job?.id;if(!id){state.polling=false;return;}
  try {
    const value=await api(`project/${state.pid}/paper-job`,{id});if(state.job?.id!==id)return;state.job=value;
    if(value.status==='completed'&&value.kind==='batch'){
      const project=await api(`project/${state.pid}`);if(S.project?.id===state.pid)S.project=project;
      state.preview=null;state.pending=null;await paperLoad(state);
    }else if(['completed','failed','cancelled'].includes(value.status))state.pending=null;
    paperPaint(state);
  }catch(error){state.error=L('读取任务状态失败：')+error.message+L('。可主动刷新进度，任务不会自动重跑。');paperPaint(state);}finally{state.polling=false;}
  if(['queued','running','cancelling'].includes(state.job?.status))state.timer=setTimeout(()=>paperPoll(state),1300);
}
async function paperAction(action,el) {
  const state=paperState();
  if(action==='pf-mode'){plotState().mode='paper';render();if(state.value&&!paperDirty(state))await paperLoad(state);return;}
  if(action==='pf-other-tools'){RT.tab='navigator';render();return;}
  if(action==='pf-cancel'){if(state.job?.id){state.job=await api(`project/${state.pid}/paper-cancel`,{id:state.job.id});paperPaint(state);await paperPoll(state);}return;}
  if(action==='pf-dismiss-job'){state.job=null;paperPaint(state);return;}
  if(action==='pf-section'){state.section=el.dataset.section;paperPaint(state);return;}
  if(paperBusy(state)){toast(L('请等待当前任务完成，或取消任务。'));return;}
  state.error='';
  try {
    if(action==='pf-refresh'){
      if(paperDirty(state)){await paperEnsureSaved(state);}await paperLoad(state);return;
    }
    if(action==='pf-save'||action==='pf-check'){await paperEnsureSaved(state);await paperLoad(state);toast(action==='pf-save'?L('论文清单已保存。'):L('关联与规格检查已更新。'));return;}
    if(action==='pf-select-all'){const ids=state.plan.entries.filter(e=>e.include&&plotFigures().some(r=>r.id===e.figure_id)).map(e=>e.figure_id);if(ids.length>30)throw Error(L('每批最多选择 30 张，请分批处理。'));state.selected=ids;paperInvalidate(state);paperPaint(state);return;}
    if(action==='pf-select-none'){state.selected=[];paperInvalidate(state);paperPaint(state);return;}
    if(action==='pf-move'){const i=state.plan.entries.findIndex(e=>e.figure_id===el.dataset.id),target=i+Number(el.dataset.direction);if(i>=0&&target>=0&&target<state.plan.entries.length)[state.plan.entries[i],state.plan.entries[target]]=[state.plan.entries[target],state.plan.entries[i]];paperInvalidate(state);paperPaint(state);return;}
    if(action==='pf-remove'){state.plan.entries=state.plan.entries.filter(e=>e.figure_id!==el.dataset.id);state.selected=state.selected.filter(id=>id!==el.dataset.id);paperInvalidate(state);paperPaint(state);return;}
    if(action==='pf-add'){const row=plotFigures().find(r=>r.id===el.dataset.id);if(row&&!state.plan.entries.some(e=>e.figure_id===row.id))state.plan.entries.push({figure_id:row.id,role:row.spec.kind==='graphical_abstract'?'abstract':'main',section:row.location||'',caption_reviewed:false,include:true});paperInvalidate(state);paperPaint(state);return;}
    if(action==='pf-copy-token'){await navigator.clipboard.writeText('[figure:'+el.dataset.id+']');toast(L('正文插图标记已复制。'));return;}
    if(action==='pf-edit'){await plottingAction('pl-open',{dataset:{id:el.dataset.id}});return;}
    if(action==='pf-stage'){await paperEnsureSaved(state);S.tab='work';S.activeStage=el.dataset.stage;S.outputTab='preview';S.draft=null;render();return;}
    if(action==='pf-to-deliver'||action==='pf-to-check'){await paperEnsureSaved(state);await paperLoad(state);state.section=action==='pf-to-deliver'?'deliver':'check';paperPaint(state);return;}
    if(action==='pf-preset'){state.style={font_size:10,dpi:300,palette:el.dataset.preset==='gray'?'gray':'accessible'};if(el.dataset.preset==='double'){state.resize=true;state.width=180/25.4;}paperInvalidate(state);paperPaint(state);return;}
    if(action==='pf-abstract'||action==='pf-compose'){
      const request=action==='pf-abstract'?{}:{figure_ids:state.selected,title:state.composeTitle,columns:state.columns,shared_legend:state.shared};
      const result=await api(`project/${state.pid}/paper-${action==='pf-abstract'?'abstract':'compose'}`,request);await paperAdoptDraft(state,result.spec);return;
    }
    if(action==='pf-source-draft'){
      const value=await api(`project/${state.pid}/paper-source-draft`,{id:el.dataset.id,old_source:state.oldSource,new_source:state.newSource});
      if(value.requires_analysis){const an=analysisState();if(analysisBusy(an))throw Error(L('请先等待当前分析完成。'));an.spec=plotClone(value.spec);an.step=1;an.result=null;an.resultSpec=null;an.job=null;an.savedId=value.figure_id;an.originId=value.figure_id;an.preflight=null;analysisInvalidated(an);plotState().mode='analysis';render();if(!an.methods)await analysisLoadCatalog(an);for(const id of value.spec.source_ids)await analysisInspect(an,id);analysisPaint(an);}else await paperAdoptDraft(state,value.spec,value.figure_id);
      return;
    }
    if(action==='pf-batch-preview'){
      await paperEnsureSaved(state);const generation=state.generation,data=paperBatchData(state);state.busy='preview';paperPaint(state);
      try{const preview=await api(`project/${state.pid}/paper-batch-preview`,data);if(generation===state.generation)state.preview={...preview,request:plotClone(data)};}finally{state.busy='';paperPaint(state);}return;
    }
    if(action==='pf-discard-preview'){paperInvalidate(state);paperPaint(state);return;}
    if(action==='pf-batch-start'||action==='pf-export'){
      await paperEnsureSaved(state);
      if(action==='pf-batch-start'&&!state.preview)throw Error(L('请先查看本次将要修改的内容。'));
      const data=action==='pf-batch-start'?{...state.preview.request,etag:state.preview.etag}:{etag:state.value.etag,include_sources:state.includeSources,allow_warnings:state.allowWarnings};
      const key=JSON.stringify(data);if(!state.pending||state.pending.key!==key)state.pending={key,id:globalThis.crypto?.randomUUID?.()||'paper-'+Date.now()+'-'+Math.random().toString(36).slice(2)};
      state.busy='starting';paperPaint(state);
      try{state.job=await api(`project/${state.pid}/paper-${action==='pf-batch-start'?'batch-start':'export-start'}`,{...data,request_id:state.pending.id});}finally{state.busy='';paperPaint(state);}
      await paperPoll(state);return;
    }
    if(action==='pf-review'){await paperEnsureSaved(state);const value=await api(`project/${state.pid}/paper-review`,{etag:state.value.etag});paperAdopt(state,value);paperPaint(state);return;}
    if(action==='pf-download'){if(!state.job?.id)return;const blob=await api(`project/${state.pid}/paper-download`,{id:state.job.id},true);rtDownload(blob,L('论文图表交付包.zip'));return;}
    if(action==='pf-clean'){modal(H`<h2>清理本项目交付缓存？</h2><p>请先下载需要的交付包。仅清理本项目生成的 ZIP 缓存；项目、材料与图稿保留。</p><div class="modal-footer">${paperButton('clean-confirm',L('确认清理'),'','primary')}${button('close-modal',L('返回'),'secondary')}</div>`);return;}
    if(action==='pf-clean-confirm'){closeModal();const result=await api(`project/${state.pid}/paper-clean`,{});if(state.job?.kind==='export')state.job=null;state.pending=null;toast(H`已清理 ${result.files_removed} 个交付缓存。`);paperPaint(state);return;}
  }catch(error){state.error=error.message;paperPaint(state);throw error;}
}
function paperFormInput(input) {
  if(input.dataset.paperAbstract||input.dataset.paperSection){const pl=plotState();if(!pl.spec||pl.spec.kind!=='graphical_abstract')return;plotRemember(pl);if(input.dataset.paperAbstract)pl.spec.abstract[input.dataset.paperAbstract]=input.value;else{const section=pl.spec.abstract.sections[Number(input.dataset.index)];if(section)section[input.dataset.paperSection]=input.dataset.paperSection==='nodes'?input.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean):input.value;}return;}
  if(!input.closest?.('.pf-workspace')||!S.project)return;const state=paperState();if(paperBusy(state)||!state.plan)return;
  const value=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;
  if(input.dataset.paperPlan)state.plan[input.dataset.paperPlan]=value;
  if(input.dataset.paperEntry){const entry=state.plan.entries.find(e=>e.figure_id===input.dataset.id);if(entry){entry[input.dataset.paperEntry]=value;if(input.dataset.paperEntry==='caption_reviewed')entry.caption_basis='';}}
  if(input.dataset.paperLocal){state[input.dataset.paperLocal]=input.dataset.paperLocal==='width'?Number(value)/25.4:input.dataset.paperLocal==='columns'?Number(value):value;}
  if(input.dataset.paperStyle)state.style[input.dataset.paperStyle]=value;
  if(input.dataset.paperRequirement)state.plan.requirements[input.dataset.paperRequirement]=value;
  paperInvalidate(state);
}
document.addEventListener('input',event=>{if(event.target.tagName!=='SELECT'&&event.target.type!=='checkbox')paperFormInput(event.target);});
document.addEventListener('change',event=>{
  const input=event.target;
  if(input.dataset.paperAbstract||input.dataset.paperSection){paperFormInput(input);return;}
  if(!input.closest?.('.pf-workspace')||!S.project)return;const state=paperState();if(paperBusy(state)||!state.plan)return;
  if(input.dataset.paperSelect){if(input.checked&&!state.selected.includes(input.dataset.paperSelect)){if(state.selected.length>=30){input.checked=false;toast(L('每批最多 30 张图表。'));return;}state.selected.push(input.dataset.paperSelect);}else state.selected=state.selected.filter(id=>id!==input.dataset.paperSelect);paperInvalidate(state);paperPaint(state);return;}
  if(input.dataset.paperStage){state.plan.stages=input.checked?[...new Set([...state.plan.stages,input.dataset.paperStage])]:state.plan.stages.filter(id=>id!==input.dataset.paperStage);paperInvalidate(state);paperPaint(state);return;}
  if(input.dataset.paperFormat){state.plan.requirements.formats=input.checked?[...new Set([...state.plan.requirements.formats,input.dataset.paperFormat])]:state.plan.requirements.formats.filter(f=>f!==input.dataset.paperFormat);paperInvalidate(state);return;}
  if(input.tagName==='SELECT'||input.type==='checkbox')paperFormInput(input);
  if(input.dataset.paperLocal||input.dataset.paperStyle||input.dataset.paperEntry)paperPaint(state);
});
