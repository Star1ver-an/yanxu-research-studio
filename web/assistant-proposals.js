/* The model only produces editable drafts. All writes use reviewed server previews. */
const asKinds = {
  tasks: L('研究待办'),
  experiments: L('实验方案'),
  issues: L('修改意见'),
  report: L('报告修改'),
};
const asProposalFields = {
  tasks: ['title', 'purpose', 'materials', 'criterion', 'due', 'stage'],
  experiments: [
    'title',
    'hypothesis',
    'baseline',
    'variables',
    'controls',
    'dataset',
    'split',
    'metric',
    'unit',
    'seeds',
    'sample_reason',
    'analysis',
    'compute',
    'license',
  ],
  issues: ['title', 'priority', 'location', 'stage'],
};
const asProposalEdits = new Map();
function assistantProposalCard(m) {
  const p = m.proposal;
  if (!p) return '';
  const saved = p.status === 'saved',
    discarded = p.status === 'discarded';
  return `<section class="as-proposal-card"><strong>${icon(saved ? 'check' : 'edit', 16)}${e(asKinds[p.kind] || L('协助'))} · ${saved ? L('已保存') : discarded ? L('已舍弃') : L('待核对草稿')}</strong><p>${saved ? H`已保存 ${p.kind === 'report' ? L('报告修改') : p.receipt?.count + L(' 条记录')}，${p.receipt?.affected?.length ? L('相关成果需要重核') : L('未推进研究阶段')}。` : discarded ? L('未写入研究内容。') : p.kind === 'report' ? L('先编辑完整报告，查看差异和受影响阶段，再决定保存。') : L('可编辑条目并选择要保存的内容，所有条目会一起保存。')}</p>${!saved && !discarded && m.outdated ? H`<p class="as-outdated">项目已变化，请保留需要的文字并重新生成草稿。</p>` : ''}<div>${saved ? H`<button data-action="as-proposal-result" data-message="${e(m.id)}">查看保存位置${icon('chevron', 12)}</button>` : discarded ? '' : H`<button data-action="as-proposal-review" data-message="${e(m.id)}">编辑与核对</button><button data-action="as-proposal-discard" data-message="${e(m.id)}">舍弃草稿</button>`}</div></section>`;
}
function proposalField(key, value, index) {
  const label = rtLabels[key] || key,
    attrs = `data-proposal-index="${index}" data-proposal-field="${key}"`;
  let input;
  if (key === 'stage')
    input = `<select ${attrs}>${flow(AS.review?.project || assistantProject() || S.project)
      .stages.map(
        (s) =>
          `<option value="${s}" ${s === value ? 'selected' : ''}>${e(S.catalog.stages[s].title)}</option>`,
      )
      .join('')}</select>`;
  else if (key === 'priority')
    input = `<select ${attrs}>${[
      ['high', L('高')],
      ['medium', L('中')],
      ['low', L('低')],
    ]
      .map(([id, n]) => `<option value="${id}" ${id === value ? 'selected' : ''}>${n}</option>`)
      .join('')}</select>`;
  else if (['title', 'due'].includes(key))
    input = `<input ${attrs} type="${key === 'due' ? 'date' : 'text'}" value="${e(value)}" ${key === 'title' ? 'required maxlength="500"' : ''}>`;
  else input = `<textarea ${attrs} rows="3" maxlength="6000">${e(value)}</textarea>`;
  return `<label class="field ${key === 'title' ? 'as-field-full' : ''}"><span>${e(label)}</span>${input}</label>`;
}
function cacheAssistantProposal() {
  const form = $('#as-proposal-form'),
    r = AS.review;
  if (!form || !r) return;
  if (r.kind === 'report') {
    r.draft = { text: $('#as-report-draft')?.value || '' };
  } else {
    const records = r.draft.records.map((x) => ({ ...x }));
    form
      .querySelectorAll('[data-proposal-field]')
      .forEach(
        (t) => (records[Number(t.dataset.proposalIndex)][t.dataset.proposalField] = t.value),
      );
    r.draft = { records };
    r.selected = [...form.querySelectorAll('[data-proposal-selected]')]
      .filter((t) => t.checked)
      .map((t) => Number(t.dataset.proposalSelected));
  }
  asProposalEdits.set(r.pid + ':' + r.message, {
    draft: structuredClone(r.draft),
    selected: r.selected,
  });
}
function assistantReviewForm() {
  const r = AS.review;
  if (!r) return;
  const body =
    r.kind === 'report'
      ? H`<label class="field"><span>修改后的完整报告</span><textarea id="as-report-draft" rows="20" maxlength="80000" required>${e(r.draft.text)}</textarea></label>`
      : r.draft.records
          .map(
            (record, index) =>
              H`<section class="as-record-editor"><label class="ack"><input type="checkbox" data-proposal-selected="${index}" ${r.selected.includes(index) ? 'checked' : ''}>保存第 ${index + 1} 条${r.kind === 'experiments' ? L('研究计划') : ''}</label><div class="as-proposal-grid">${asProposalFields[r.kind].map((key) => proposalField(key, record[key] || '', index)).join('')}</div></section>`,
          )
          .join('');
  modal(
    H`<h2>核对${e(asKinds[r.kind])}草稿</h2><p>项目：${e(r.project?.title || assistantProject()?.title || '')} · 草稿尚未写入研究内容。</p><form id="as-proposal-form"><div class="as-proposal-scroll">${r.kind === 'experiments' ? H`<p class="notice">保存状态固定为“研究计划”，不会标记实验已执行。</p>` : r.kind === 'issues' ? H`<p class="notice">来源固定为“AI 建议（待研究者核对）”，保存后为未处理。</p>` : ''}${body}<p id="as-proposal-error" class="as-error" role="alert"></p></div><div class="modal-footer"><small>先查看保存预览与影响范围；取消会保留本窗口中的编辑。</small><div class="rt-actions">${button('close-modal', L('稍后处理'), 'secondary')}${button('noop', L('预览保存'), 'primary', '', 'type="submit"')}</div></div></form>`,
    true,
  );
  $('.modal').classList.add('as-proposal-modal');
}
async function assistantProposalSubmit() {
  const r = AS.review;
  if (!r) return;
  cacheAssistantProposal();
  const draft =
    r.kind === 'report'
      ? structuredClone(r.draft)
      : {
          records: r.draft.records.filter((_, i) => r.selected.includes(i)).map((x) => ({ ...x })),
        };
  const data = { project: r.pid, message: r.message, draft };
  try {
    const preview = await api('assistant/proposal-preview', data);
    if (AS.review !== r || !$('#as-proposal-form')) return;
    r.preview = preview;
    r.data = data;
    assistantProposalPreviewView();
  } catch (err) {
    if (AS.review === r && $('#as-proposal-error'))
      $('#as-proposal-error').textContent = err.message;
    else throw err;
  }
}
function assistantProposalPreviewView() {
  const r = AS.review,
    p = r.preview;
  modal(
    H`<h2>确认保存${e(asKinds[r.kind])}</h2><p>保存到项目：${e(r.project?.title || assistantProject()?.title || r.pid)}。仅保存已核对草稿，不执行实验或确认阶段成果。</p><div class="as-proposal-scroll"><dl><dt>保存内容</dt><dd>${r.kind === 'report' ? L('替换当前阶段报告，保留修改前快照') : H`新增 ${p.count} 条${e(asKinds[r.kind])}`}</dd><dt>需要重新核对的成果</dt><dd>${p.affected.map((s) => e(S.catalog.stages[s].title)).join('、') || L('无')}</dd><dt>流程位置</dt><dd>${p.cursor_before} → ${p.cursor_after}${p.cursor_before !== p.cursor_after ? L('（回到需核对阶段）') : L('（不推进阶段）')}</dd></dl>${p.warnings.map((w) => `<p class="notice">${e(w)}</p>`).join('')}${r.kind === 'report' ? H`<h3>报告差异</h3><pre class="as-report-diff">${e(p.diff)}</pre>` : H`<h3>将创建的条目</h3><ol>${p.draft.records.map((x) => `<li>${e(x.title)}</li>`).join('')}</ol>`}<p id="as-proposal-error" class="as-error" role="alert"></p></div><div class="modal-footer"><small>保存前会再次校验草稿与项目是否变化。</small><div class="rt-actions">${button('as-proposal-back', L('返回编辑'), 'secondary')}${button('as-proposal-apply', L('确认保存'), 'primary', 'check')}</div></div>`,
    true,
  );
  $('.modal').classList.add('as-proposal-modal');
}
async function assistantProposalAction(action, el) {
  if (action === 'as-proposal-review') {
    const m = AS.state?.messages.find(
      (m) => m.id === el.dataset.message && m.proposal?.status === 'draft',
    );
    if (!m || !AS.scope) return;
    if (S.draft !== null) await saveOutput(true);
    const pid = AS.scope,
      epoch = AS.epoch,
      project = await api('project/' + pid);
    if (pid !== AS.scope || epoch !== AS.epoch) return;
    const saved = asProposalEdits.get(pid + ':' + m.id),
      draft = structuredClone(saved?.draft || m.proposal.draft);
    AS.review = {
      pid,
      project,
      message: m.id,
      kind: m.proposal.kind,
      stage: m.proposal.stage,
      draft,
      selected: saved?.selected || draft.records?.map((_, i) => i) || [],
    };
    assistantReviewForm();
    return;
  }
  if (action === 'as-proposal-back') {
    assistantReviewForm();
    return;
  }
  if (action === 'as-proposal-apply') {
    const r = AS.review;
    if (!r?.preview || r.saving) return;
    r.saving = true;
    el.disabled = true;
    try {
      const result = await api('assistant/proposal-apply', {
        ...r.data,
        acknowledge: true,
        preview_sha256: r.preview.sha256,
      });
      if (AS.scope === r.pid) {
        AS.state = result.state;
        AS.project = result.project;
      }
      if (S.project?.id === r.pid) {
        S.project = result.project;
        S.activeStage = step(S.project);
        S.outputTab = 'preview';
      }
      S.projects = await api('projects');
      asProposalEdits.delete(r.pid + ':' + r.message);
      closeModal();
      AS.review = null;
      render();
      toast(
        r.kind === 'report'
          ? L('报告修改已保存，原版本可在版本恢复查看。')
          : H`${result.receipt.count} 条${asKinds[r.kind]}已保存，可以在研究工具中查看。`,
      );
    } catch (err) {
      if ($('#as-proposal-error')) $('#as-proposal-error').textContent = err.message;
      else throw err;
    } finally {
      r.saving = false;
      if (el.isConnected) el.disabled = false;
    }
    return;
  }
  if (action === 'as-proposal-discard') {
    const m = AS.state?.messages.find(
      (m) => m.id === el.dataset.message && m.proposal?.status === 'draft',
    );
    if (!m) return;
    AS.discard = { pid: AS.scope, message: m.id };
    modal(
      H`<h2>舍弃这份协助草稿？</h2><p>尚未保存的内容不会写入研究工具或报告，对话回答会保留。</p><div class="modal-footer">` +
        button('close-modal', L('取消'), 'secondary') +
        button('as-proposal-discard-confirm', L('舍弃草稿'), 'primary') +
        '</div>',
    );
    return;
  }
  if (action === 'as-proposal-discard-confirm') {
    const d = AS.discard;
    if (!d) return;
    const state = await api('assistant/proposal-discard', {
      project: d.pid,
      message: d.message,
      acknowledge: true,
    });
    if (AS.scope === d.pid) AS.state = state;
    asProposalEdits.delete(d.pid + ':' + d.message);
    closeModal();
    assistantPaint();
    return;
  }
  if (action === 'as-proposal-result') {
    const m = AS.state?.messages.find(
      (m) => m.id === el.dataset.message && m.proposal?.status === 'saved',
    );
    if (!m) return;
    const p = m.proposal,
      pid = AS.scope;
    await assistantNavigate(
      p.kind === 'report' ? 'work' : p.kind === 'experiments' ? 'experiments' : 'research',
      pid,
    );
    if (S.project?.id === pid) {
      if (p.kind === 'report') S.activeStage = p.stage;
      else RT.tab = p.kind === 'tasks' ? 'navigator' : p.kind;
      render();
    }
  }
}
document.addEventListener('change', (ev) => {
  if (ev.target.id === 'as-kind') {
    AS.kinds[AS.scope] = ev.target.value;
    const starter = (k) => L('请结合当前课题生成') + k + L('草稿，未知条件保留待确认。'),
      draft = AS.drafts[AS.scope] || '',
      automatic = Object.values(asKinds).some((k) => draft === starter(k));
    if (ev.target.value && (!draft.trim() || automatic))
      AS.drafts[AS.scope] = starter(asKinds[ev.target.value]);
    else if (!ev.target.value && automatic) AS.drafts[AS.scope] = '';
    if (ev.target.value === 'report' && AS.state?.report_available)
      assistantOptions().report = true;
    AS.preview = null;
    assistantPaint();
  }
});
