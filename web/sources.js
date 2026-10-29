/* Source records share the existing validated, recoverable research forms. */
const SourceUI = { section: 'sources', search: null, audit: null, busy: false };
Object.assign(rtNames, { sources: L('来源与筛选'), searches: L('检索台账') });
Object.assign(rtLabels, {
  query: L('检索式'),
  database: L('检索平台'),
  criteria: L('纳入与排除标准'),
  task_id: L('关联研究任务'),
  brief_section: L('关联简报部分'),
  coverage: L('可用内容层级'),
  version: L('材料版本'),
  language: L('原文语言'),
  decision: L('筛选决定'),
  domain_notes: L('领域条件与不可外推之处'),
});
rtFields.sources = [
  'title',
  'asset_id',
  'reference_id',
  'url',
  'source_date',
  'coverage',
  'license',
  'version',
  'language',
  'decision',
  'reason',
  'domain_notes',
  'task_id',
  'brief_section',
];
rtFields.searches = [
  'title',
  'query',
  'database',
  'source_date',
  'url',
  'criteria',
  'reason',
  'task_id',
  'brief_section',
];
rtEnums.sources = {
  coverage: [
    ['metadata', L('仅书目')],
    ['abstract', L('仅摘要')],
    ['excerpt', L('节选')],
    ['fulltext', L('全文（登记声明）')],
  ],
  decision: [
    ['pending', L('待筛选')],
    ['included', L('纳入')],
    ['excluded', L('排除')],
  ],
};
rtHints.sources = L(
  '登记原始链接、获取日期、使用条件、内容层级与筛选理由。全文状态是登记声明；科学支持关系仍需阅读原文判断。',
);
rtHints.searches = L(
  '保留检索平台、日期、原始检索式和筛选依据。有限检索未命中，不能证明相关研究不存在。',
);
const sourceSections = [
  ['question', L('核心研究问题')],
  ['scope', L('研究范围')],
  ['materials', L('材料需求')],
  ['method', L('研究方法')],
  ['outcomes', L('预期交付物')],
  ['limits', L('解释边界')],
  ['open_questions', L('待确认问题')],
];
function sourceStamp(p) {
  return JSON.stringify([p.research, p.assets, p.outputs]);
}
function sourceCoverage(value) {
  return rtEnums.sources.coverage.find(([id]) => id === value)?.[1] || L('尚未登记来源');
}
function sourceSearchView(p) {
  const result =
    SourceUI.search?.pid === p.id && SourceUI.search.stamp === JSON.stringify(p.assets)
      ? SourceUI.search.result
      : null;
  return H`<section class="panel source-search"><h2>搜索项目材料</h2><p>搜索已导入材料的提取文字，打开命中页后可选取原文建立证据卡片。</p><form id="rt-source-search-form" data-project="${p.id}"><label class="field">检索词<input name="query" maxlength="120" required value="${e(result?.query || '')}" placeholder="例如：TMEM43 或 transaction costs"></label><label class="ack"><input type="checkbox" name="enabled_only" checked>只搜索启用材料</label>${button('noop', L('搜索材料'), 'primary', 'search', 'type="submit"')}</form>${result ? H`<p class="field-help">${e(result.meaning)}</p><p>${result.matched_pages} ${L('个命中页，显示最多 60 页。')}</p><div class="source-results">${result.results.map((row) => H`<article class="rt-record"><div class="rt-record-head"><h3>${e(row.name)}</h3><span class="badge">${e(sourceCoverage(row.coverage))}</span></div><p>${row.pdf ? L('PDF物理页') : L('文字分段')} ${row.page}${row.enabled ? '' : L(' · 材料已停用')}</p><pre class="source-snippet">${e(row.snippet)}</pre>${rtAction('read', L('打开命中位置'), `data-id="${row.asset_id}" data-page="${row.page}"`)}</article>`).join('') || H`<p class="rt-empty">提取文字中未找到匹配项。可换词搜索，或打开原件核对。</p>`}</div>` : ''}</section>`;
}
function sourceView(p) {
  const group = SourceUI.section;
  return H`<section class="panel"><div class="section-title"><div><h2>检索与来源</h2><p>从找到材料到登记证据，保留每一步的出处与判断。</p></div>${rtAction('source-audit', L('核对引用与出处'))}</div><div class="rt-actions">${rtAction('source-section', L('来源与筛选'), `data-section="sources" aria-pressed="${group === 'sources'}"`)}${rtAction('source-section', L('检索台账'), `data-section="searches" aria-pressed="${group === 'searches'}"`)}${rtAction('new', L('新建记录'), `data-group="${group}"`, 'primary')}${rtAction('source-export', L('导出来源核对包'))}</div><p class="field-help">${e(rtHints[group])}</p>${group === 'sources' ? sourceCards(p) : rtCards(p, 'searches')}</section>${sourceSearchView(p)}${sourceAuditView(p)}`;
}
function sourceCards(p) {
  const rows = rtRows('sources', p);
  return rows.length
    ? `<div class="rt-records">${rows.map((row) => `<article class="rt-record"><div class="rt-record-head"><h3>${e(row.title)}</h3><span class="badge">${e(sourceCoverage(row.coverage))}</span></div><p>${e(rtEnums.sources.decision.find(([v]) => v === row.decision)?.[1] || L('待筛选'))} · ${e(row.source_date || L('获取日期未填'))}</p><p>${e(row.reason || L('筛选理由未填'))}</p>${row.url ? `<a href="${e(row.url)}" target="_blank" rel="noopener noreferrer">${L('查看原始来源')}</a>` : ''}<details><summary>${L('来源、许可与领域条件')}</summary>${rtRecordDetails(p, 'sources', row)}</details><div class="rt-actions">${rtAction('edit', L('编辑'), `data-group="sources" data-id="${row.id}"`)}${row.asset_id ? rtAction('read', L('打开材料'), `data-id="${row.asset_id}" data-page="1"`) : ''}${rtAction('delete', L('删除'), `data-group="sources" data-id="${row.id}"`, 'text')}</div></article>`).join('')}</div>`
    : H`<p class="rt-empty">导入材料后，在这里记录来源；尚无全文时可以先登记书目或摘要。</p>`;
}
function sourceAuditView(p) {
  const item = SourceUI.audit?.pid === p.id ? SourceUI.audit : null;
  if (!item) return '';
  if (item.stamp !== sourceStamp(p))
    return H`<section class="panel"><p>记录已变更，请重新核对引用与出处。</p>${rtAction('source-audit', L('重新核对'))}</section>`;
  const a = item.result;
  return H`<section class="panel source-audit"><h2>引用与出处核对</h2><p>${e(a.meaning)}</p><div class="rt-summary"><span>${L('已登记来源')} ${a.counts.sources}</span><span>${L('已登记论断')} ${a.counts.claims}</span><span>${L('可定位记录')} ${a.counts.locatable}</span><span>${L('待核对事项')} ${a.counts.issues}</span></div>${a.counts.claims ? '' : H`<p class="notice">尚未登记论断，不能据此判断稿件已获证据支持。</p>`}${a.issues.length ? `<ul class="source-issues">${a.issues.map((i) => `<li><strong>${e(i.title)}</strong><p>${e(L(i.message))}</p></li>`).join('')}</ul>` : H`<p>已登记记录未发现关联缺项；科学内容仍需研究者核读。</p>`}${a.claims.map((row) => H`<article class="rt-record"><h3>${e(row.claim.title)}</h3><p>${e(row.claim.reason || L('尚未填写判断依据'))}</p>${row.evidence ? H`<blockquote>${e(row.evidence.quote)}</blockquote><p>${e(row.evidence.limitations)}</p><div class="rt-actions">${rtAction('read', L('回到原文'), `data-id="${row.evidence.asset_id}" data-page="${e(row.evidence.page)}"`)}${rtAction('edit', L('编辑论断'), `data-group="claims" data-id="${row.claim.id}"`)}</div>` : ''}</article>`).join('')}</section>`;
}
async function sourceAction(action, el) {
  if (!action.startsWith('rt-source-')) return false;
  const p = S.project,
    pid = p.id;
  if (action === 'rt-source-section') {
    SourceUI.section = el.dataset.section === 'searches' ? 'searches' : 'sources';
    render();
  } else if (action === 'rt-source-audit') {
    el.disabled = true;
    try {
      const stamp = sourceStamp(p),
        result = await api(`project/${pid}/source-audit`, {});
      if (S.project?.id === pid) {
        SourceUI.audit = { pid, stamp, result };
        RT.tab = 'sources';
        render();
        $('.source-audit')?.scrollIntoView?.({ block: 'start', behavior: 'auto' });
      }
    } finally {
      el.disabled = false;
    }
  } else if (action === 'rt-source-export') {
    el.disabled = true;
    try {
      const blob = await api(`project/${pid}/source-export`, {}, true);
      rtDownload(blob, p.title + '-' + L('来源核对包') + '.md');
    } finally {
      el.disabled = false;
    }
  }
  return true;
}
async function sourceSearch(form) {
  const pid = form.dataset.project;
  if (S.project?.id !== pid) return;
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const stamp = JSON.stringify(S.project.assets),
      result = await api(`project/${pid}/material-search`, {
        query: form.elements.query.value,
        enabled_only: form.elements.enabled_only.checked,
      });
    if (S.project?.id === pid) {
      SourceUI.search = { pid, stamp, result };
      render();
    }
  } finally {
    submit.disabled = false;
  }
}
