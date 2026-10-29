const PM = { state: 'active', route: 'all', sort: 'updated', selected: new Set(), busy: false };
let managementPollTimer;
const projectState = (p) => p.lifecycle || 'active';
const activeProjects = () =>
  S.projects
    .filter((p) => projectState(p) === 'active')
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned));
function visibleProjects() {
  const query = S.query.trim().toLowerCase();
  const projects = S.projects.filter((project) => {
    if (projectState(project) !== PM.state) return false;
    if (PM.route !== 'all' && projectRoute(project) !== PM.route) return false;
    if (S.filter !== 'all' && project.flow !== S.filter) return false;
    return (project.title + ' ' + project.topic).toLowerCase().includes(query);
  });
  return projects.sort((left, right) => {
    if (PM.state === 'active') {
      const pinnedOrder = Number(!!right.pinned) - Number(!!left.pinned);
      if (pinnedOrder) return pinnedOrder;
    }
    if (PM.sort === 'title')
      return left.title.localeCompare(right.title, window.ResearchI18n?.current() || 'zh-CN');
    return String(right[PM.sort]).localeCompare(String(left[PM.sort]));
  });
}
function projectMenu(project) {
  const state = projectState(project),
    id = `data-id="${project.id}"`,
    disabled = project.running ? 'disabled' : '';
  function item(action, label, iconName) {
    return button('pm-' + action, label, 'text', iconName, `${id} ${disabled}`);
  }
  const items = [];
  if (state === 'trash') {
    items.push(item('restore', L('恢复项目'), 'restore'), item('purge', L('永久删除'), 'trash'));
  } else {
    items.push(item('rename', L('修改名称'), 'edit'), item('duplicate', L('创建副本'), 'copy'));
    if (state === 'active') {
      items.push(
        item(
          project.pinned ? 'unpin' : 'pin',
          project.pinned ? L('取消置顶') : L('置顶项目'),
          'pin',
        ),
        item('archive', L('归档项目'), 'archive'),
      );
    } else {
      items.push(item('restore', L('取消归档'), 'restore'));
    }
    items.push(item('trash', L('移入回收站'), 'trash'));
  }
  return H`<details class="project-menu"><summary class="icon-btn" aria-label="管理项目 ${e(project.title)}">${icon('more', 18)}</summary><div class="project-menu-items">${items.join('')}</div></details>`;
}
function managedProjectCard(project) {
  const completion = progress(project),
    complete = completion === 100,
    select = S.view === 'projects',
    state = projectState(project);
  let status;
  if (project.running) status = L('任务运行中');
  else if (state === 'trash') status = L('已移入回收站');
  else if (state === 'archived') status = L('已归档 · 可查看与导出');
  else if (complete) status = L('流程已完成');
  else status = e(S.catalog.stages[step(project)].title);
  return H`<article class="project-card managed-card ${PM.selected.has(project.id) && select ? 'is-selected' : ''}"><div class="card-top">${select ? H`<label class="project-select"><input type="checkbox" data-project-check="${project.id}" aria-label="选择项目 ${e(project.title)}" ${PM.selected.has(project.id) ? 'checked' : ''} ${project.running ? 'disabled' : ''}></label>` : ''}<span class="type-label ${flow(project).color}">${e(routeName(project))} · ${e(flow(project).name)}</span>${project.pinned && state === 'active' ? H`<span class="pinned-mark" title="已置顶">${icon('pin', 13)}</span>` : ''}${projectMenu(project)}</div><button class="project-card-open" data-action="open" data-id="${project.id}" aria-label="打开项目 ${e(project.title)}"><h3>${e(project.title)}</h3><p class="card-topic">${e(project.topic || L('尚未添加研究目标'))}</p><div class="card-stage"><span class="dot ${complete ? 'complete' : ''}"></span>${status}<b>${completion}%</b></div><div class="progress"><i style="width:${completion}%"></i></div><div class="card-bottom"><span>${icon('file', 13)} ${project.asset_count} 份材料</span><span>${date(project.updated)}</span></div></button></article>`;
}
function projectManagerView() {
  return H`<div class="page-heading heading-row"><div><div class="eyebrow">RESEARCH PROJECTS</div><h1>管理你的每一项研究</h1><p>选择项目批量整理；归档保留成果，删除后可从回收站恢复。</p></div>${button('create', L('新建研究项目'), 'primary', 'plus', 'id="guide-create"')}</div><div class="management-tabs" role="tablist" aria-label="项目状态">${[
    ['active', L('工作项目')],
    ['archived', L('已归档')],
    ['trash', L('回收站')],
  ]
    .map(
      ([k, n]) =>
        `<button role="tab" aria-selected="${PM.state === k}" class="${PM.state === k ? 'selected' : ''}" data-action="pm-state" data-state="${k}">${n}<span>${S.projects.filter((p) => projectState(p) === k).length}</span></button>`,
    )
    .join(
      '',
    )}</div><div class="manager-filters"><label class="search-field">${icon('search', 17)}<input id="project-search" placeholder="搜索名称或研究目标" aria-label="搜索研究项目" value="${e(S.query)}"></label><select id="project-route" aria-label="筛选研究路径">${[
    ['all', L('全部路径')],
    ['ars', L('开题与写作')],
    ['scientific', L('科研工具与分析')],
  ]
    .map(([k, n]) => `<option value="${k}" ${PM.route === k ? 'selected' : ''}>${n}</option>`)
    .join(
      '',
    )}</select><select id="project-flow" aria-label="筛选工作流"><option value="all">全部工作流</option>${Object.entries(
    S.catalog.flows,
  )
    .map(
      ([k, f]) => `<option value="${k}" ${S.filter === k ? 'selected' : ''}>${e(f.name)}</option>`,
    )
    .join('')}</select><select id="project-sort" aria-label="项目排序">${[
    ['updated', L('最近更新')],
    ['created', L('最近创建')],
    ['title', L('名称顺序')],
  ]
    .map(([k, n]) => `<option value="${k}" ${PM.sort === k ? 'selected' : ''}>${n}</option>`)
    .join(
      '',
    )}</select></div><div id="project-batch-bar" class="project-batch-bar"></div><div id="project-list" class="project-grid">${projectResults()}</div><p class="manager-note">${PM.state === 'trash' ? L('回收站不会自动清空。永久删除会移除所选项目及其本机材料，操作前请先导出备份。') : L('运行中的项目暂不能选中或管理；每批最多选择 50 个。归档项目恢复后可继续编辑与运行。')}</p>`;
}
function projectResults() {
  const projects = visibleProjects();
  if (projects.length) return projects.map(managedProjectCard).join('');
  const filtered = S.query || PM.route !== 'all' || S.filter !== 'all';
  let title,
    action = '';
  if (filtered) {
    title = L('没有符合筛选条件的项目');
    action = button('pm-clear-filters', L('清除筛选'), 'secondary', 'refresh');
  } else if (PM.state === 'trash') {
    title = L('回收站是空的');
  } else if (PM.state === 'archived') {
    title = L('还没有归档项目');
  } else {
    title = L('从第一个研究项目开始');
  }
  if (!filtered && PM.state === 'active')
    action = button('create', L('新建研究项目'), 'primary', 'plus');
  return `<div class="manager-empty"><span>${icon(PM.state === 'trash' ? 'trash' : 'folder', 30)}</span><h3>${title}</h3><p>${PM.state === 'active' ? L('写下研究问题，或调整筛选条件。') : L('项目整理后会出现在这里。')}</p>${action}</div>`;
}
function paintProjectSelection() {
  const bar = $('#project-batch-bar');
  if (!bar) return;
  const projects = visibleProjects().filter((project) => !project.running),
    ids = new Set(projects.map((project) => project.id));
  PM.selected = new Set([...PM.selected].filter((id) => ids.has(id)));
  const count = PM.selected.size,
    disabled = !count || PM.busy ? 'disabled' : '';
  const actions = [];
  if (PM.state === 'trash') {
    actions.push(
      button('pm-restore', L('恢复'), 'secondary', 'restore', disabled),
      button('pm-purge', L('永久删除'), 'danger', 'trash', disabled),
    );
  } else {
    actions.push(
      button('pm-rename', L('批量改名'), 'secondary', 'edit', disabled),
      button('pm-duplicate', L('创建副本'), 'secondary', 'copy', disabled),
    );
    if (PM.state === 'active') {
      actions.push(
        button('pm-pin', L('置顶'), 'secondary', 'pin', disabled),
        button('pm-archive', L('归档'), 'secondary', 'archive', disabled),
      );
    } else {
      actions.push(button('pm-restore', L('取消归档'), 'secondary', 'restore', disabled));
    }
    actions.push(button('pm-trash', L('删除'), 'danger', 'trash', disabled));
  }
  if (count) actions.push(button('pm-clear', L('取消选择'), 'text', '', PM.busy ? 'disabled' : ''));
  bar.innerHTML = H`<label class="batch-check"><input id="project-select-all" type="checkbox" ${count && count === projects.length ? 'checked' : ''} ${!projects.length || PM.busy ? 'disabled' : ''}>选择当前结果</label><span class="selection-count" role="status">已选择 ${count} 项</span><div class="batch-actions">${actions.join('')}</div>`;
  const selectAll = $('#project-select-all');
  if (selectAll) selectAll.indeterminate = count > 0 && count < projects.length;
  document.querySelectorAll('[data-project-check]').forEach((input) => {
    input.checked = PM.selected.has(input.dataset.projectCheck);
    input.closest('.managed-card')?.classList.toggle('is-selected', input.checked);
  });
}
function refreshProjectResults(clear = true) {
  if (clear) PM.selected.clear();
  const el = $('#project-list');
  if (el) el.innerHTML = projectResults();
  paintProjectSelection();
}
function scheduleManagementPoll() {
  clearTimeout(managementPollTimer);
  if (S.view !== 'projects' || !S.projects.some((p) => p.running)) return;
  managementPollTimer = setTimeout(async () => {
    try {
      if (S.view !== 'projects') return;
      if (!PM.busy) {
        S.projects = await api('projects');
        refreshProjectResults(false);
      }
    } catch {
      /* Retry local status without replacing forms or showing repeated toasts. */
    } finally {
      scheduleManagementPoll();
    }
  }, 2000);
}
function selectedProjectIds(el) {
  return el?.dataset.id ? [el.dataset.id] : [...PM.selected];
}
function projectNames(ids) {
  return `<ul class="managed-names">${ids.map((id) => `<li>${e(S.projects.find((p) => p.id === id)?.title || id)}</li>`).join('')}</ul>`;
}
function managementModal(action, ids) {
  const projects = ids.map((id) => S.projects.find((p) => p.id === id));
  if (!ids.length) throw Error(L('请先选择要管理的项目。'));
  if (projects.some((p) => !p || p.running))
    throw Error(L('所选项目正在运行或已不存在，请刷新后再试。'));
  const idsField = `<input type="hidden" name="ids" value="${e(JSON.stringify(ids))}">`;
  if (action === 'rename')
    modal(
      H`<h2>${ids.length === 1 ? L('修改项目名称') : L('批量修改名称')}</h2><p class="modal-description">只修改显示名称，已有报告与确认记录继续保留。</p><form id="pm-rename-form">${idsField}${ids.length === 1 ? H`<label class="field">项目名称<input name="title" value="${e(projects[0].title)}" maxlength="140" required></label>` : H`<label class="field">添加前缀<input name="prefix" maxlength="50" placeholder="例如：2026 · "></label><label class="field">添加后缀<input name="suffix" maxlength="50" placeholder="例如：· 待复核"></label><p class="field-help">保留每个项目原名称；任一名称超过 140 字时整批不会修改。</p>${projectNames(ids)}`}<div class="modal-footer"><span>${ids.length} 个项目</span>${button('noop', L('保存名称'), 'primary', 'check', 'type="submit"')}</div></form>`,
    );
  if (action === 'duplicate')
    modal(
      H`<h2>创建独立副本</h2><p class="modal-description">材料会复制到新项目，之后两边可以分别编辑和删除。</p><form id="pm-copy-form">${idsField}${ids.length === 1 ? H`<label class="field">副本名称<input name="title" value="${e(projects[0].title.slice(0, 136) + L(' 副本'))}" maxlength="140" required></label>` : projectNames(ids)}<label class="copy-choice"><input type="radio" name="mode" value="materials" checked><span><b>复制概况与材料，重新研究</b><small>保留文献选择，清空报告与运行记录。</small></span></label><label class="copy-choice"><input type="radio" name="mode" value="reports"><span><b>同时保留报告草稿</b><small>报告标为待核实；流程从头开始，确认与运行记录清空。</small></span></label><div class="modal-footer"><span>${ids.length} 个独立副本</span>${button('noop', L('创建副本'), 'primary', 'copy', 'type="submit"')}</div></form>`,
    );
  if (['trash', 'purge'].includes(action))
    modal(
      H`<span class="template-icon">${icon('trash', 24)}</span><h2>${action === 'trash' ? L('移入回收站') : L('永久删除项目')}</h2><p class="modal-description">${action === 'trash' ? L('项目暂时从工作区移除，材料和报告仍保留，可在回收站恢复。') : L('此操作无法在软件内撤销，将删除所选项目、材料和运行文件。建议先导出 ZIP 备份。')}</p>${projectNames(ids)}<form id="pm-confirm-form">${idsField}<input type="hidden" name="operation" value="${action}">${action === 'purge' ? H`<label class="field">请输入“永久删除”确认<input name="confirmation" autocomplete="off" required placeholder="永久删除"></label>` : H`<label class="ack"><input type="checkbox" name="acknowledge" required>我确认将这些项目移入回收站</label>`}<div class="modal-footer"><span>共 ${ids.length} 个项目</span>${button('close-modal', L('取消'), 'secondary', '', 'type="button"')}${button('noop', action === 'trash' ? L('移入回收站') : L('永久删除'), 'danger', 'trash', 'type="submit"')}</div></form>`,
    );
}
async function applyProjectManagement(action, ids, extra = {}) {
  if (PM.busy) throw Error(L('正在整理项目，请稍候。'));
  PM.busy = true;
  paintProjectSelection();
  try {
    const result = await api('projects/manage', { action, ids, ...extra });
    S.projects = await api('projects');
    PM.selected.clear();
    if (S.view === 'project' && ids.includes(S.project?.id)) {
      if (action === 'purge') {
        S.view = 'projects';
        S.project = null;
        window.history?.replaceState({}, '', '/');
      } else S.project = await api('project/' + S.project.id);
    }
    closeModal();
    render();
    toast(
      result.warnings?.length
        ? result.warnings.join('；')
        : H`已${{ rename: L('修改名称'), duplicate: L('创建副本'), archive: L('归档'), trash: L('移入回收站'), restore: L('恢复'), purge: L('永久删除'), pin: L('置顶'), unpin: L('取消置顶') }[action]} ${result.count} 个项目`,
      !!result.warnings?.length,
    );
    return result;
  } finally {
    PM.busy = false;
    paintProjectSelection();
  }
}
async function projectManagementAction(action, el) {
  const op = action.slice(3);
  if (op === 'state') {
    PM.state = el.dataset.state;
    PM.selected.clear();
    render();
    return;
  }
  if (op === 'clear') {
    PM.selected.clear();
    paintProjectSelection();
    return;
  }
  if (op === 'clear-filters') {
    S.query = '';
    S.filter = 'all';
    PM.route = 'all';
    PM.selected.clear();
    render();
    return;
  }
  if (S.draft !== null) await saveOutput(true);
  const ids = selectedProjectIds(el);
  if (['rename', 'duplicate', 'trash', 'purge'].includes(op)) return managementModal(op, ids);
  await applyProjectManagement(op, ids);
}
async function submitProjectManagement(form) {
  const data = Object.fromEntries(new FormData(form)),
    ids = JSON.parse(data.ids);
  if (form.id === 'pm-rename-form') {
    if (ids.length > 1 && !data.prefix && !data.suffix)
      throw Error(L('请填写要添加的前缀或后缀。'));
    const titles = Object.fromEntries(
      ids.map((id) => [
        id,
        ids.length === 1
          ? data.title
          : (data.prefix || '') + S.projects.find((p) => p.id === id).title + (data.suffix || ''),
      ]),
    );
    return applyProjectManagement('rename', ids, { titles });
  }
  if (form.id === 'pm-copy-form')
    return applyProjectManagement('duplicate', ids, {
      mode: data.mode,
      ...(ids.length === 1 ? { title: data.title } : {}),
    });
  return applyProjectManagement(data.operation, ids, {
    acknowledge: !!form.elements.acknowledge?.checked,
    confirmation: data.confirmation,
  });
}
function inactiveProjectBanner(p) {
  const state = projectState(p);
  return state === 'active'
    ? ''
    : H`<div class="inactive-project"><div>${icon(state === 'trash' ? 'trash' : 'archive', 20)}<span>${state === 'trash' ? L('项目位于回收站') : L('项目已归档')}<small>可查看与导出，恢复后可以继续编辑和运行。</small></span></div>${button('pm-restore', state === 'trash' ? L('恢复项目') : L('取消归档'), 'secondary', 'restore', `data-id="${p.id}"`)}</div>`;
}
function protectInactiveProject() {
  if (S.view !== 'project' || !S.project || projectState(S.project) === 'active') return;
  document
    .querySelectorAll(
      '.stage-content textarea,.stage-content input,.stage-content select,.materials-layout input,.materials-layout select,.materials-layout button,#file-input,[data-action="run"],[data-action="confirm"],[data-action="project-info"],[data-action="save-output"],[data-action="upload"],[data-action="disable-literature"],[data-action="output-tab"][data-tab="edit"]',
    )
    .forEach((el) => (el.disabled = true));
  const readOnly = new Set([
    'rt-tab',
    'rt-export',
    'rt-load-page',
    'rt-read',
    'rt-pdf',
    'rt-save-pdf',
    'rt-claim-view',
    'rt-version',
    'rt-cite-token',
    'rt-goto-work',
    'rt-goto-materials',
  ]);
  document.querySelectorAll('[data-action^="rt-"]').forEach((el) => {
    if (!readOnly.has(el.dataset.action)) el.disabled = true;
  });
}
document.addEventListener('change', (event) => {
  const el = event.target;
  if (el.dataset.projectCheck) {
    if (el.checked && PM.selected.size >= 50) {
      el.checked = false;
      toast(L('每批最多选择 50 个项目。'), true);
      return;
    }
    if (el.checked) PM.selected.add(el.dataset.projectCheck);
    else PM.selected.delete(el.dataset.projectCheck);
    paintProjectSelection();
  }
  if (el.id === 'project-select-all') {
    PM.selected = new Set(
      el.checked
        ? visibleProjects()
            .filter((p) => !p.running)
            .slice(0, 50)
            .map((p) => p.id)
        : [],
    );
    paintProjectSelection();
  }
  if (['project-route', 'project-flow', 'project-sort'].includes(el.id)) {
    if (el.id === 'project-route') PM.route = el.value;
    if (el.id === 'project-flow') S.filter = el.value;
    if (el.id === 'project-sort') PM.sort = el.value;
    refreshProjectResults();
  }
});
