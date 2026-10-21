"""Whole-paper figure planning, atomic batch edits and local delivery jobs."""
import base64
import copy
import hashlib
import io
import json
import re
import threading
import time
import uuid
import zipfile
from .i18n import output_language
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .storage import now
from .plotting import digest, figures, plot_stale
from .paper_engine import walk_specs, spec_digest, remap_spec

ID = re.compile(r'[0-9a-f]{32}')
MARKER = re.compile(r'\[figure:([A-Za-z0-9_-]{1,80})\]')
DEFAULT_REQUIREMENTS = {'max_width_mm': 180, 'max_height_mm': 230, 'min_font_pt': 7, 'min_dpi': 300,
                        'formats': ['png', 'svg', 'pdf']}


def validate_plan(value):
    from .plot_engine import _text, _number
    if not isinstance(value, dict) or set(value) - {'title', 'entries', 'stages', 'requirements'}:
        raise ValueError('论文图表清单格式无效。')
    value = copy.deepcopy(value)
    _text(value.setdefault('title', '论文图表交付'), '交付名称', 160)
    entries = value.setdefault('entries', []); stages = value.setdefault('stages', [])
    if not isinstance(entries, list) or len(entries) > 80: raise ValueError('论文清单最多 80 张图表。')
    if not isinstance(stages, list) or len(stages) > 30 or any(not isinstance(s, str) or not 1 <= len(s) <= 80 for s in stages) or len(set(stages)) != len(stages):
        raise ValueError('稿件阶段列表无效。')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {'figure_id', 'role', 'section', 'caption_reviewed', 'caption_basis', 'include'}:
            raise ValueError('论文清单条目格式无效。')
        identity = entry.get('figure_id')
        if not isinstance(identity, str) or not ID.fullmatch(identity) or identity in seen: raise ValueError('清单图表标识无效或重复。')
        seen.add(identity)
        if entry.setdefault('role', 'main') not in {'main', 'supplement', 'abstract'}: raise ValueError('图表用途无效。')
        _text(entry.setdefault('section', ''), '正文位置', 300)
        for key, default in [('caption_reviewed', False), ('include', True)]:
            if type(entry.setdefault(key, default)) is not bool: raise ValueError('图表清单开关无效。')
        basis = entry.setdefault('caption_basis', '')
        if not isinstance(basis,str) or basis and not re.fullmatch('[0-9a-f]{64}',basis): raise ValueError('图注核对摘要无效。')
    req = value.setdefault('requirements', copy.deepcopy(DEFAULT_REQUIREMENTS))
    if not isinstance(req, dict) or set(req) - set(DEFAULT_REQUIREMENTS): raise ValueError('投稿规格字段无效。')
    for key, low, high in [('max_width_mm', 76.2, 406.4), ('max_height_mm', 63.5, 406.4), ('min_font_pt', 6, 24), ('min_dpi', 72, 600)]:
        _number(req.setdefault(key, DEFAULT_REQUIREMENTS[key]), key, low, high)
    formats = req.setdefault('formats', ['png', 'svg', 'pdf'])
    if not isinstance(formats, list) or not 1 <= len(formats) <= 4 or len(set(formats)) != len(formats) or any(f not in {'png', 'svg', 'pdf', 'tiff'} for f in formats):
        raise ValueError('请选择 PNG、SVG、PDF、TIFF 中的交付格式。')
    return value


def default_plan(project):
    from .workflows import FLOWS
    stages = [s for s in FLOWS[project['flow']]['stages'] if project.get('outputs', {}).get(s, {}).get('text')]
    return validate_plan({'title': project['title'][:140] + ' 图表', 'entries': [
        {'figure_id': row['id'], 'role': 'abstract' if row['spec']['kind'] == 'graphical_abstract' else 'main',
         'section': row.get('location', ''), 'caption_reviewed': False, 'include': True} for row in figures(project)],
        'stages': stages[-1:]})


def paper_basis(project):
    """Exclude preferences and read timestamps; bind all research edits relevant to a delivery."""
    return digest({'figures': figures(project), 'paper': project.get('plotting', {}).get('paper', {}),
        'assets': [{k: a.get(k) for k in ('id', 'name', 'sha256', 'size', 'file')} for a in project['assets']],
        'outputs': project.get('outputs', {}), 'experiments': project.get('research', {}).get('experiments', [])})


def plan_record(project):
    record = project.get('plotting', {}).get('paper') or {}
    plan = validate_plan(record.get('plan') or default_plan(project));rows = {r['id']:r for r in figures(project)}
    for entry in plan['entries']:
        row = rows.get(entry['figure_id'])
        if entry['caption_reviewed'] and entry['caption_basis'] and (not row or entry['caption_basis'] != caption_basis(row)):
            entry['caption_reviewed'] = False
    return {'revision': record.get('revision', 0), 'plan': plan,
            'review': record.get('review', {})}


def caption_basis(row):
    spec=copy.deepcopy(row['spec'])
    for child in walk_specs(spec): child.pop('style',None)
    for child in spec.get('composition',{}).get('items',[]): child.pop('revision',None)
    return digest({'spec':spec,'caption':row.get('caption',''),'sources':row.get('source_hashes',{})})


def paper_audit(project):
    from .workflows import STAGES
    record = plan_record(project); plan = record['plan']; rows = {f['id']: f for f in figures(project)}
    entries = [e for e in plan['entries'] if e['include']]; selected = {e['figure_id'] for e in entries}
    issues = []; links = []; affected = set(); stage_hashes = {}; review = record['review']; req = plan['requirements']
    def issue(level, kind, text, identity='', stage=''):
        issues.append({'level': level, 'kind': kind, 'message': text, 'figure_id': identity, 'stage': stage})
        if identity: affected.add(identity)
    if not entries: issue('error', 'empty', '请先把图表加入论文清单。')
    for sid in plan['stages']:
        output = project.get('outputs', {}).get(sid, {})
        text = output.get('text', ''); refs = list(dict.fromkeys(MARKER.findall(text)))
        sha = hashlib.sha256(text.encode()).hexdigest(); stage_hashes[sid] = {'sha256': sha, 'figure_ids': refs}
        if not text: issue('warning', 'missing_manuscript', '所选稿件阶段还没有正文。', stage=sid)
        if output.get('stale'): issue('warning', 'stale_manuscript', '此稿件阶段已标记需重新核对。', stage=sid)
        old = review.get('outputs', {}).get(sid)
        if old and old.get('sha256') != sha:
            for identity in set(refs) | set(old.get('figure_ids', [])):
                if identity in selected: issue('warning', 'manuscript_changed', '正文变化，请核对该图与论述是否仍一致。', identity, sid)
        for identity in refs:
            links.append({'stage': sid, 'stage_title': STAGES.get(sid, {}).get('title', sid), 'figure_id': identity})
            if identity not in rows: issue('error', 'missing_reference', '正文引用了已删除或不存在的图表。', identity, sid)
            elif identity not in selected: issue('error', 'excluded_reference', '正文引用的图未加入当前交付清单。', identity, sid)
    checks = []
    for number, entry in enumerate(entries, 1):
        identity = entry['figure_id']; row = rows.get(identity)
        if row is None:
            issue('error', 'missing_figure', '清单中的图表已不存在，请移除或重新加入。', identity); continue
        style = row['spec']['style']; width = style['width'] * 25.4
        height = row.get('metadata', {}).get('height_inches', style['height']) * 25.4
        actual_font = row.get('metadata', {}).get('effective_min_font_pt', style['font_size'])
        word_font = actual_font * min(1, 166 / width, 185 / height)
        checks.append({'figure_id': identity, 'number': number, 'title': row['title'], 'role': entry['role'],
            'width_mm': round(width, 2), 'height_mm': round(height, 2), 'dpi': style['dpi'],
            'minimum_font_pt': round(actual_font, 2), 'estimated_word_font_pt': round(word_font, 2)})
        if plot_stale(project, row): issue('error', 'stale_source', '来源或关联实验已变化，需核对并保存新版本。', identity)
        if not row.get('caption', '').strip(): issue('warning', 'missing_caption', '图注为空。', identity)
        if not entry['caption_reviewed']: issue('warning', 'caption_review', '图注尚未标记为人工核对。', identity)
        if width > req['max_width_mm'] + .01 or height > req['max_height_mm'] + .01:
            issue('warning', 'size', '画布尺寸超过当前投稿规格，请调整尺寸后检查字体。', identity)
        if style['dpi'] < req['min_dpi']: issue('warning', 'dpi', '位图导出 DPI 低于当前规格。SVG 矢量元素不受此值影响。', identity)
        if actual_font < req['min_font_pt'] or word_font < req['min_font_pt']:
            issue('warning', 'font', '原图或缩放到 Word 后的估算字号低于当前规格，请检查成品。', identity)
        if row['spec']['kind'] in {'panel', 'composition'}:
            issue('warning', 'raster', '含原图或栅格面板，需人工核对有效分辨率。组合图 SVG 保留各子图矢量元素。', identity)
        if row['spec']['kind'] == 'composition':
            for child in row['spec']['composition']['items']:
                current = rows.get(child['figure_id'])
                if not current or current['revision'] != child['revision']:
                    issue('warning', 'child_changed', '组合中的子图已有新版本或已删除；当前组合仍保留原快照。', identity)
        if review.get('figures', {}).get(identity) not in (None, spec_digest(row['spec'])):
            issue('warning', 'figure_changed', '图稿自上次关联核对后已修改，请检查正文引用和图注。', identity)
        if not any(link['figure_id'] == identity for link in links):
            issue('info', 'unplaced', '所选正文中未发现插图标记；Word 会按清单顺序附在文末。', identity)
    return {'issues': issues, 'links': links, 'checks': checks, 'affected_figures': sorted(affected),
        'errors': sum(i['level'] == 'error' for i in issues), 'warnings': sum(i['level'] == 'warning' for i in issues),
        'outputs': stage_hashes, 'note': '这是可配置的技术规格检查，不代表期刊认可或科学结论已验证。'}


class PaperFigures:
    def init_paper(self):
        self.paper_jobs = {}; self.paper_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='paper-figures')
        self.paper_delivery_dir = self.store.root / 'paper-deliveries'
        self.paper_delivery_dir.mkdir(exist_ok=True)
        if self.paper_delivery_dir.is_symlink() or self.paper_delivery_dir.resolve().parent != self.store.root.resolve():
            raise ValueError('图表交付缓存目录不安全。')
        # Only application-owned delivery filenames may be expired; never touch materials.
        for path in self.paper_delivery_dir.glob('*.zip'):
            if re.fullmatch(r'(?:[0-9a-f]{32}-)?[0-9a-f]{32}\.zip', path.name) and not path.is_symlink() and time.time() - path.stat().st_mtime > 86400:
                path.unlink()

    def close_paper(self):
        for job in self.paper_jobs.values(): job['cancel'].set()
        self.paper_worker.shutdown(wait=False, cancel_futures=True)

    def paper_busy(self, pid=None):
        return any(j['status'] in {'queued', 'running', 'cancelling'} and (pid is None or j['pid'] == pid)
                   for j in list(self.paper_jobs.values()))

    def paper_state(self, pid, data=None):
        with self.lock:
            p = self.store.get(pid)
            jobs = [j for j in self.paper_jobs.values() if j['pid'] == pid]
            active = [j for j in jobs if j['status'] in {'queued','running','cancelling'}]
            latest = (active or jobs)[-1] if jobs else None
            return {**plan_record(p), 'etag': paper_basis(p), 'audit': paper_audit(p),
                    'job': self.paper_job(pid, {'id': latest['id']}) if latest else None}

    def paper_save(self, pid, data):
        from .workflows import FLOWS
        clean = validate_plan(data.get('plan'))
        with self.lock:
            p = self.store.get(pid); self.assert_idle(p)
            if self.paper_busy(pid): raise ValueError('请等待批量操作完成后再修改清单。')
            current = plan_record(p)
            if data.get('expected_revision') != current['revision']: raise ValueError('清单已在其他操作中变化，请刷新后保存。')
            if any(s not in FLOWS[p['flow']]['stages'] for s in clean['stages']): raise ValueError('所选稿件阶段不属于当前研究流程。')
            rows={r['id']:r for r in figures(p)}
            for entry in clean['entries']:
                if entry['caption_reviewed']:
                    row=rows.get(entry['figure_id'])
                    if not row: raise ValueError('无法确认已删除图表的图注。')
                    actual=caption_basis(row)
                    if entry['caption_basis'] and entry['caption_basis']!=actual:
                        raise ValueError('图稿内容已变化，请刷新后重新核对图注。')
                    entry['caption_basis']=actual
            p.setdefault('plotting', {'figures': []})['paper'] = {'revision': current['revision'] + 1,
                'plan': clean, 'review': current['review'], 'updated': now()}
            self.store.put(p)
        return self.paper_state(pid)

    def paper_review(self, pid, data):
        with self.lock:
            p = self.store.get(pid); self.assert_idle(p)
            if self.paper_busy(pid): raise ValueError('请等待批量操作完成后核对关联。')
            if data.get('etag') != paper_basis(p): raise ValueError('图表或正文已变化，请刷新检查后核对。')
            record = plan_record(p); audit = paper_audit(p)
            if audit['errors']: raise ValueError('请先处理缺失来源、图表或正文引用。')
            record['review'] = {'outputs': audit['outputs'], 'figures': {r['id']: spec_digest(r['spec']) for r in figures(p)}, 'at': now()}
            record['revision'] += 1
            p.setdefault('plotting', {'figures': []})['paper'] = record; self.store.put(p)
        return self.paper_state(pid)

    def _paper_render_composition(self, pid, spec, sources, fmt):
        from .paper_engine import render_composition
        from .plot_engine import render_figure
        def render_child(child, selected, format):
            return self._analysis_render(pid, child, selected, format) if child['kind'] == 'analysis' else render_figure(child, selected, format)
        cancel = next((j['cancel'] for j in self.paper_jobs.values() if j['pid'] == pid and j['status'] in {'running', 'cancelling'}), None)
        return render_composition(spec, sources, render_child, fmt, cancel)

    def paper_compose(self, pid, data):
        import math
        from .plot_engine import validate_spec
        ids = data.get('figure_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 6 or len(set(ids)) != len(ids): raise ValueError('请选择 1–6 张不同的单图。')
        with self.lock:
            p = self.store.get(pid); self.assert_idle(p)
            rows = [self._plot_row(p, identity) for identity in ids]
            if any(r['spec']['kind'] == 'composition' for r in rows): raise ValueError('请选择单图，不能再次嵌套组合图。')
            if any(plot_stale(p, r) for r in rows): raise ValueError('所选单图来源需复核，请先更新单图。')
            columns=data.get('columns',2)
            if type(columns) is not int or not 1<=columns<=3: raise ValueError('组合图列数应为1–3。')
            actual_columns=min(columns,len(rows));panel_width=(7.2-.24-.22*(actual_columns-1))/actual_columns
            row_heights=[max(r.get('metadata',{}).get('height_inches',r['spec']['style']['height'])/r['spec']['style']['width']*panel_width+.17 for r in rows[i:i+actual_columns]) for i in range(0,len(rows),actual_columns)]
            height=min(16,max(2.5,sum(row_heights)+.22*(len(row_heights)-1)+.5+(.5 if data.get('shared_legend') else .12)))
            spec = {'version': 1, 'kind': 'composition', 'title': data.get('title', '论文组合图'),
                'source_ids': list(dict.fromkeys(a for row in rows for a in row['spec']['source_ids'])),
                'style': data.get('style') or {'width': 7.2, 'height': height, 'dpi': 300, 'font_size': 10, 'palette': 'accessible'},
                'composition': {'columns': data.get('columns', 2), 'shared_legend': data.get('shared_legend', False),
                    'items': [{'figure_id': r['id'], 'revision': r['revision'], 'label': chr(65 + i), 'spec': copy.deepcopy(r['spec'])} for i, r in enumerate(rows)]}}
            return {'spec': validate_spec(spec), 'note': '子图以固定版本加入组合。之后修改单图时，会提醒刷新组合。'}

    def paper_abstract(self, pid, data):
        from .plot_engine import validate_spec
        self.store.get(pid)
        spec = {'version': 1, 'kind': 'graphical_abstract', 'title': '研究方案图形摘要', 'source_ids': [],
            'style': {'width': 9, 'height': 4.8, 'dpi': 300, 'font_size': 10, 'palette': 'accessible'},
            'diagram': {'nodes': [{'id': 'q', 'label': '待填写研究问题'}, {'id': 'm', 'label': '待填写方法与对照'}, {'id': 'v', 'label': '待填写评估与验证'}],
                'edges': [{'from': 'q', 'to': 'm'}, {'from': 'm', 'to': 'v'}]},
            'abstract': {'status': 'plan', 'summary': '研究方案示意，尚无实验结论。',
                'sections': [{'title': '研究问题', 'nodes': ['q']}, {'title': '方法设计', 'nodes': ['m']}, {'title': '评估与验证', 'nodes': ['v']}]}}
        return {'spec': validate_spec(spec)}

    def _paper_candidates(self, p, data):
        from .plot_engine import validate_spec
        ids = data.get('figure_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 30 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('每次批量操作请选择 1–30 张不同的图表。')
        action = data.get('action')
        if action not in {'style', 'refresh', 'sources'}: raise ValueError('批量操作类型无效。')
        patch = data.get('style', {})
        if not isinstance(patch, dict) or set(patch) - {'palette', 'font_size', 'dpi', 'width'}: raise ValueError('统一样式仅支持配色、字号、DPI 与可选宽度。')
        if action == 'style' and not patch: raise ValueError('请至少选择一个要统一的样式字段。')
        source_map = data.get('source_map', {})
        if not isinstance(source_map, dict) or len(source_map) > 1 or any(not isinstance(k, str) or not isinstance(v, str) or not ID.fullmatch(k) or not ID.fullmatch(v) for k, v in source_map.items()):
            raise ValueError('每次请选择一份旧材料与一份新的替代材料。')
        if action == 'sources' and (len(source_map) != 1 or any(k == v for k, v in source_map.items())): raise ValueError('请选择不同的旧、新来源材料。')
        rows = [copy.deepcopy(self._plot_row(p, identity)) for identity in ids]; prepared = []; skipped = []
        for row in rows:
            spec = copy.deepcopy(row['spec']); before = copy.deepcopy(spec)
            if action == 'sources':
                if not any(a in source_map for a in spec['source_ids']):
                    skipped.append({'id': row['id'], 'reason': '未使用所选旧材料。'}); continue
                if spec['kind'] in {'analysis', 'composition'}:
                    skipped.append({'id': row['id'], 'reason': '需重新分析或刷新子图；请打开单图明确确认，原结果保留。'}); continue
                spec = remap_spec(spec, source_map)
            elif action == 'style':
                for child in walk_specs(spec):
                    old_width = child['style']['width']
                    changes = patch if child is spec else {k: v for k, v in patch.items() if k != 'width'}
                    child['style'].update(changes)
                    if child is spec and 'width' in patch:
                        child['style']['height'] *= patch['width'] / old_width
            elif spec['kind'] == 'composition':
                for child in spec['composition']['items']:
                    latest = self._plot_row(p, child['figure_id'])
                    if latest['spec']['kind'] == 'composition': raise ValueError('组合图的来源已变成组合，请重新选择单图。')
                    child.update(revision=latest['revision'], spec=copy.deepcopy(latest['spec']))
                spec['source_ids'] = list(dict.fromkeys(a for child in spec['composition']['items'] for a in child['spec']['source_ids']))
            spec = validate_spec(spec)
            self._plot_sources(p, spec['source_ids'])
            prepared.append({'row': row, 'spec': spec, 'changed_fields': [key for key in set(before) | set(spec) if before.get(key) != spec.get(key)]})
        staged = {c['row']['id']: c for c in prepared}
        for candidate in prepared:
            if candidate['spec']['kind'] != 'composition': continue
            for child in candidate['spec']['composition']['items']:
                next_version = staged.get(child['figure_id'])
                if next_version:
                    child.update(spec=copy.deepcopy(next_version['spec']), revision=next_version['row']['revision'] + 1)
            candidate['spec'] = validate_spec(candidate['spec'])
            if candidate['spec'].get('composition') != candidate['row']['spec'].get('composition') and 'composition' not in candidate['changed_fields']:
                candidate['changed_fields'].append('composition')
        if not prepared: raise ValueError('没有可批量更新的图表；分析图更换材料后需要重新运行分析，组合图请在子图更新后刷新。')
        return prepared, skipped

    def paper_batch_preview(self, pid, data):
        with self.lock:
            p = self.store.get(pid); self.assert_idle(p)
            candidates, skipped = self._paper_candidates(p, data)
            return {'etag': paper_basis(p), 'updates': [{'id': c['row']['id'], 'title': c['row']['title'],
                'revision': c['row']['revision'], 'changed_fields': c['changed_fields'], 'before_style': c['row']['spec']['style'],
                'after_style': c['spec']['style']} for c in candidates], 'skipped': skipped,
                'note': '预览仅列出配置变化。明确开始后逐图渲染，全部成功才一次保存；取消或失败不保存部分修改。'}

    def paper_source_draft(self, pid, data):
        with self.lock:
            p = self.store.get(pid); row = self._plot_row(p, data.get('id'))
            old, new = data.get('old_source'), data.get('new_source')
            if old not in row['spec']['source_ids'] or not isinstance(new, str) or old == new: raise ValueError('请选择当前图中的旧来源与不同的新材料。')
            self._plot_sources(p, [new])
            if row['spec']['kind'] == 'composition': raise ValueError('请先替换单图材料并核对，再刷新组合图。')
            spec = remap_spec(row['spec'], {old: new}); spec.pop('analysis_result', None)
            if spec['kind'] == 'analysis':
                for key in ('independent_units', 'evaluation_independent', 'independent_replicates', 'normalized_log2'):
                    if key in spec['analysis']['parameters']: spec['analysis']['parameters'][key] = False
            return {'spec': spec, 'figure_id': row['id'], 'revision': row['revision'],
                'requires_analysis': spec['kind'] == 'analysis', 'note': '仅创建草稿，原图未改动；需要核对字段与统计条件后重新计算和保存。'}

    def _paper_new_job(self, pid, kind, data):
        with self.lock:
            p = self.store.get(pid)
            request = data.get('request_id')
            if not isinstance(request,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}',request):
                raise ValueError('任务缺少有效请求编号，请重新点击开始。')
            fingerprint = digest({'kind':kind,'data':data})
            old = next((j for j in self.paper_jobs.values() if j['pid']==pid and j.get('request_id')==request),None)
            if old:
                if old['fingerprint']!=fingerprint: raise ValueError('请求编号已用于不同内容，请重新预览。')
                return self.paper_job(pid,{'id':old['id']})
            self.assert_idle(p)
            if self.plotting_busy(pid): raise ValueError('此项目正在绘图、分析或批量处理，请等待完成。')
            if sum(j['status'] in {'queued', 'running', 'cancelling'} for j in self.paper_jobs.values()) >= 2:
                raise ValueError('论文图表队列已满，请稍后再试。')
            basis = paper_basis(p)
            if data.get('etag') != basis: raise ValueError('图表、材料或正文已变化，请重新预览后开始。')
            candidates, skipped = self._paper_candidates(p, data) if kind == 'batch' else ([], [])
            if kind == 'export':
                audit = paper_audit(p)
                if not any(e['include'] for e in plan_record(p)['plan']['entries']):
                    raise ValueError('请先将至少一张图表纳入交付清单。')
                if audit['errors']: raise ValueError('请先处理检查报告中的缺失来源或正文引用错误。')
                if audit['warnings'] and data.get('allow_warnings') is not True: raise ValueError('尚有待核对项；处理后导出，或明确选择带检查报告的草稿交付包。')
                if type(data.get('include_sources', False)) is not bool: raise ValueError('来源包开关无效。')
            for identity in [i for i, j in self.paper_jobs.items() if j['status'] not in {'queued', 'running', 'cancelling'}][:-10]:
                old = self.paper_jobs.pop(identity)
                if old.get('path'): Path(old['path']).unlink(missing_ok=True)
            identity = uuid.uuid4().hex
            job = {'id': identity, 'pid': pid, 'kind': kind, 'status': 'queued', 'progress': '已排队',
                'request_id':request,'fingerprint':fingerprint,
                'cancel': threading.Event(), 'started': time.monotonic(), 'basis': basis,
                'snapshot': copy.deepcopy(p), 'data': copy.deepcopy(data), 'candidates': candidates, 'skipped': skipped,
                'output_language':output_language(p,self.store)}
            self.paper_jobs[identity] = job
            self.paper_worker.submit(self._paper_run_job, job)
            return self.paper_job(pid, {'id': identity})

    def paper_batch_start(self, pid, data): return self._paper_new_job(pid, 'batch', data)
    def paper_export_start(self, pid, data): return self._paper_new_job(pid, 'export', data)

    def paper_job(self, pid, data):
        with self.lock:
            self.store.get(pid); job = self.paper_jobs.get(data.get('id'))
            if not job or job['pid'] != pid: raise ValueError('任务不存在或不属于当前项目；软件重启后请重新发起。')
            value = {k: copy.deepcopy(job[k]) for k in ('id', 'kind', 'status', 'progress', 'error', 'summary', 'skipped') if k in job}
            if job['kind'] == 'export' and job['status'] == 'completed':
                value['stale'] = job['basis'] != paper_basis(self.store.get(pid))
            return value

    def paper_cancel(self, pid, data):
        with self.lock:
            self.paper_job(pid, data); job = self.paper_jobs[data['id']]
            if job['status'] in {'queued', 'running', 'cancelling'}:
                job['cancel'].set(); job['status'] = 'cancelling'; job['progress'] = '正在取消，已有保存版本不变'
            return self.paper_job(pid, data)

    @staticmethod
    def _paper_check_cancel(job):
        if job['cancel'].is_set(): raise ValueError('论文图表操作已取消。')
        if time.monotonic() - job['started'] > 1200: raise ValueError('论文图表操作超过 20 分钟，请减少图表数量。')

    def _paper_run_job(self, job):
        try:
            self._paper_check_cancel(job); job['status'] = 'running'
            if job['kind'] == 'export': self._paper_delivery(job)
            else:
                updates = []; total = len(job['candidates'])
                for index, candidate in enumerate(job['candidates']):
                    self._paper_check_cancel(job); job['progress'] = f'正在核对与绘制 {index+1}/{total}：' + candidate['row']['title'][:70]
                    spec, rendered = self._plot_render(job['pid'], candidate['spec'])
                    row = copy.deepcopy(candidate['row'])
                    history = {k: copy.deepcopy(row.get(k)) for k in ('title', 'spec', 'revision', 'created', 'source_hashes', 'caption', 'caption_manual', 'experiment_id', 'experiment_digest', 'stage', 'location', 'metadata')}
                    history['id'] = uuid.uuid4().hex
                    row['history'] = [*row.get('history', []), history][-10:]
                    row.update(spec=spec, revision=row['revision']+1, updated=now(), source_hashes=rendered['source_hashes'],
                               metadata=rendered['metadata'], warnings=rendered['warnings'])
                    updates.append(row)
                with self.lock:
                    self._paper_check_cancel(job); current = self.store.get(job['pid']); self.assert_idle(current)
                    if paper_basis(current) != job['basis']: raise ValueError('绘制期间项目内容已变化，未覆盖任何图表，请刷新后重新预览。')
                    for row in updates:
                        source_hashes = {s['id']: hashlib.sha256(s['raw']).hexdigest() for s in self._plot_sources(current, row['spec']['source_ids'])}
                        if source_hashes != row['source_hashes']: raise ValueError('绘制期间原始材料校验变化，未保存任何图表。')
                    by_id = {r['id']: r for r in updates}
                    current['plotting']['figures'] = [by_id.get(r['id'], r) for r in figures(current)]
                    self.store.put(current)
                    job['summary'] = {'updated': len(updates), 'history_preserved': True, 'model_requests': 0}
                    job['status'] = 'completed'; job['progress'] = '全部成功，已一次保存；旧版本可从图表历史恢复。'
        except Exception as exc:
            job['status'] = 'cancelled' if job['cancel'].is_set() else 'failed'
            job['error'] = str(exc)[:1600] if isinstance(exc, ValueError) else '操作未完成，原图与正文已保留。请检查配置后主动重试。'
            job['progress'] = job['error']
            if job.get('path'): Path(job['path']).unlink(missing_ok=True)
        finally:
            for key in ('snapshot', 'data', 'candidates'): job.pop(key, None)

    def _paper_delivery(self, job):
        from .word_export import make_docx
        from .workflows import VERSION, STAGES
        from .i18n import tr
        locale=job.get('output_language','zh-CN')
        p = job['snapshot']; record = plan_record(p); plan = record['plan']; audit = paper_audit(p)
        selected = [e for e in plan['entries'] if e['include']]; rows = {r['id']: r for r in figures(p)}
        output = io.BytesIO(); total_bytes = 0; images = []; manifest_rows = []; included_sources = {}
        def write(archive, name, raw):
            nonlocal total_bytes
            if isinstance(raw, str): raw = raw.encode('utf-8')
            total_bytes += len(raw)
            if total_bytes > 200 * 1024 * 1024: raise ValueError('交付内容展开后超过 200 MB，请减少格式、降低 DPI 或拆分交付。')
            archive.writestr(name, raw)
            if output.tell() > 180 * 1024 * 1024: raise ValueError('交付压缩包超过 180 MB，请减少图表或导出格式。')
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for index, entry in enumerate(selected, 1):
                row = rows[entry['figure_id']]; number = f'Fig{index:02}'; results = {}
                for fmt in dict.fromkeys([*plan['requirements']['formats'], 'png']):
                    self._paper_check_cancel(job); job['progress'] = f'正在生成 {index}/{len(selected)} · {fmt.upper()}：' + row['title'][:60]
                    _, result = self._plot_render(p['id'], row['spec'], fmt)
                    if result['source_hashes'] != row['source_hashes']: raise ValueError('保存版本的来源摘要不匹配，请先更新图表。')
                    results[fmt] = result
                    if fmt in plan['requirements']['formats']: write(archive, f'figures/{number}.{fmt}', result['data'])
                images.append({'id': row['id'], 'title': row['title'], 'caption': row.get('caption', ''),
                    'png': results['png']['data'], 'provenance': f"清单图 {index}；图稿版本 {row['revision']}；来源 SHA-256 " + json.dumps(row['source_hashes'])})
                manifest_rows.append({'number': index, 'filename_prefix': number, 'figure_id': row['id'], 'revision': row['revision'],
                    'role': entry['role'], 'section': entry['section'], 'title': row['title'], 'source_hashes': row['source_hashes'],
                    'spec_sha256': spec_digest(row['spec']), 'warnings': results['png']['warnings']})
                write(archive, f'recipes/{number}.json', json.dumps({'format': 'yanxu-figure-v1', 'figure': row}, ensure_ascii=False, indent=2))
                if row['spec']['kind'] == 'composition':
                    for panel_number, panel in enumerate(row['spec']['composition']['items']):
                        for panel_format in ['svg', 'pdf']:
                            self._paper_check_cancel(job)
                            _, original = self._plot_render(p['id'], panel['spec'], panel_format)
                            write(archive, f'panels/{number}-{chr(65+panel_number)}.{panel_format}', original['data'])
                if job['data'].get('include_sources'):
                    for source in self._plot_sources(p, row['spec']['source_ids']): included_sources[source['id']] = source
            self._paper_check_cancel(job)
            parts = ['# '+tr('论文图表交付',locale), plan['title'], tr('图表按本清单编号。本文与图注来自当前已保存版本，研究结论由作者核对。',locale)]
            for sid in plan['stages']:
                text = p.get('outputs', {}).get(sid, {}).get('text', '')
                if text: parts.extend(['## ' + tr(STAGES.get(sid, {}).get('title', sid),locale), text])
            if not plan['stages']: parts.append(tr('未选择稿件正文。以下附上已保存图表与图注。',locale))
            md = '\n\n'.join(parts)
            docx = make_docx(md, images,locale=locale)
            write(archive, '论文与图表.docx', docx); write(archive, '正文与插图标记.md', md)
            figure_label={'zh-CN':'图','zh-TW':'圖','en':'Figure','ja':'図','ko':'그림'}[locale]
            write(archive, '图注清单.txt', '\n\n'.join(f"{figure_label} {i}  {r['title']}\n{r.get('caption','')}" for i, r in enumerate([rows[e['figure_id']] for e in selected], 1)))
            write(archive, '检查报告.json', json.dumps(audit, ensure_ascii=False, indent=2))
            for key, raw in self.analysis_bundle({'plotting': {'figures': [rows[e['figure_id']] for e in selected]}, 'assets': p['assets'], 'id': p['id']}).items():
                write(archive, 'analysis-results/' + key + '.json', raw)
            for identity, source in included_sources.items(): write(archive, 'sources/' + identity + Path(source['name']).suffix.lower(), source['raw'])
            manifest = {'format': 'yanxu-paper-delivery-v1', 'software_version': VERSION, 'created': now(),
                'title': plan['title'], 'plan': plan, 'project_id': p['id'], 'figures': manifest_rows,
                'source_files_included': bool(job['data'].get('include_sources')), 'review_status': 'needs_review' if audit['warnings'] else 'technical_checks_passed',
                'word_numbering': 'Figures follow manifest order; repeated markers become cross references.',
                'composition_vector_note': 'Composition SVG embeds vector children. Composition PNG, TIFF and PDF use raster panels; individual vector files and editable recipes remain available.'}
            write(archive, 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            write(archive, '使用与重绘说明.txt', '本包用于论文图表交付，不是完整项目备份。图形文件、图注、正文与 manifest 编号一致。\n'
                'recipes 保留可编辑配置和固定子图版本；完整工作区迁移请使用项目 ZIP 或整库备份。\n'
                'sources 仅在明确选择时包含原始材料；配方、分析结果和正文也可能包含研究内容，分享前请核对授权。\n'
                'SVG 组合保留子图矢量元素；组合 PDF/PNG/TIFF 使用栅格面板。位图与字体需检查最终物理尺寸。\n'
                '技术检查不是期刊认可或科学审查。无模型密钥、登录信息或系统路径。')
        with self.lock:
            self._paper_check_cancel(job); current = self.store.get(p['id']); self.assert_idle(current)
            if paper_basis(current) != job['basis']: raise ValueError('导出期间图表或正文已变化，未生成混合版本交付包；请刷新后重新导出。')
            existing = sum(f.stat().st_size for f in self.paper_delivery_dir.glob('*.zip') if f.is_file() and not f.is_symlink())
            if existing + output.tell() > 512 * 1024 * 1024: raise ValueError('交付缓存超过 512 MB，请下载已有交付并清理缓存后重试。')
            path = self.paper_delivery_dir / (p['id'] + '-' + job['id'] + '.zip')
            path.write_bytes(output.getvalue()); job['path'] = str(path)
            job['summary'] = {'figures': len(selected), 'bytes': path.stat().st_size, 'warnings': audit['warnings'], 'model_requests': 0}
            job['status'] = 'completed'; job['progress'] = '图形、图注、正文 Word 与检查报告已完成，可以下载。'

    def paper_download(self, pid, data):
        with self.lock:
            self.paper_job(pid, data); job = self.paper_jobs[data['id']]
            if job['kind'] != 'export' or job['status'] != 'completed': raise ValueError('交付包尚未完成。')
            if job['basis'] != paper_basis(self.store.get(pid)):
                raise ValueError('图稿、清单或正文已变化，请重新生成当前版本交付包。')
            path = Path(job.get('path', ''))
            if path.is_symlink() or path.resolve().parent != self.paper_delivery_dir.resolve() or not path.is_file(): raise ValueError('交付缓存已清理，请重新导出。')
            return '论文图表交付包.zip', path.read_bytes(), 'application/zip'

    def paper_clean_deliveries(self, pid, data):
        with self.lock:
            self.store.get(pid)
            if self.paper_busy(): raise ValueError('请等待论文图表任务完成后清理交付缓存。')
            count = freed = 0
            for path in self.paper_delivery_dir.glob(pid+'-*.zip'):
                if not re.fullmatch(pid+r'-[0-9a-f]{32}\.zip',path.name): continue
                if path.resolve().parent == self.paper_delivery_dir.resolve() and not path.is_symlink() and path.is_file():
                    freed += path.stat().st_size; path.unlink(); count += 1
            return {'files_removed': count, 'bytes_freed': freed}
