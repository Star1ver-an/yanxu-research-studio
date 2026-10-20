"""Declarative local research plotting. No generated code, network or inference.

Specialist curves consume existing analysis results. Statistical tests, interval
estimation, survival fitting and differential-expression analysis are not done.
"""
import copy
import hashlib
import io
import json
import math
import numbers
import os
from pathlib import Path
import threading
import unicodedata
import warnings as python_warnings
from functools import lru_cache

from .plot_data import load_table, load_image

ENGINE_VERSION = '1.0'
LOCK = threading.RLock()  # Matplotlib font/cache state is process-global.
PALETTES = {
    'muted': ['#385E85', '#BC7052', '#54836B', '#9B769E', '#B29C50', '#557F8B', '#6C738C', '#A57B67'],
    'accessible': ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#56B4E9', '#000000', '#8B6F35'],
    'gray': ['#3E4A57', '#788591', '#A0ABB5', '#BCC5CD', '#525E69', '#D1D7DD'],
}
_CATALOG = [
    ('line', '折线 / 训练曲线', 'general', ['x', 'y'], 'raw', '按提供的观测顺序绘制，不自动平滑；支持分组、多数值列。'),
    ('scatter', '散点 / 准确率与时延', 'general', ['x', 'y'], 'raw', '展示真实观测点；不自动拟合或推断相关性。'),
    ('bar', '柱状 / 消融比较', 'general', ['x', 'y'], 'raw', '默认逐行展示；显式均值汇总时同时展示原始点，不自动生成误差线。'),
    ('box', '箱线 + 原始点', 'general', ['y'], 'raw', '箱体为 Q1–Q3，中线为中位数，须为 1.5 IQR；叠加全部原始点。'),
    ('violin', '小提琴 + 原始点', 'general', ['y'], 'raw', '展示描述分布和原始点；每组至少两个不同数值。'),
    ('histogram', '直方图', 'general', ['x'], 'raw', '仅展示频数分布，不执行假设检验。'),
    ('heatmap', '热图', 'general', [], 'raw', '数值矩阵原样显示；不进行聚类、标准化或缺失值填补。'),
    ('confusion', '混淆矩阵', 'computer', [], 'results', '导入已有非负计数矩阵；不从模型预测重新计算。'),
    ('volcano', '火山图（已有结果）', 'biomedical', ['x', 'y'], 'results', 'x 为 log2FC，y 为已有 p 或校正 p；不计算差异表达。'),
    ('ma', 'MA 图（已有结果）', 'biomedical', ['x', 'y'], 'results', '横轴为已计算的平均表达，纵轴为已计算 log2FC。'),
    ('forest', '森林图（已有区间）', 'biomedical', ['y', 'lower', 'upper', 'label'], 'results', '原样绘制效应值与已有上下界；不估计区间或合并效应。'),
    ('roc', 'ROC 曲线（已有结果）', 'biomedical', ['x', 'y'], 'results', 'x 为 FPR，y 为 TPR；不计算 AUC、最佳阈值或模型性能。'),
    ('pr', 'PR 曲线（已有结果）', 'biomedical', ['x', 'y'], 'results', 'x 为 recall，y 为 precision；不计算 AP。'),
    ('km', '生存曲线（已有结果）', 'biomedical', ['x', 'y'], 'results', '导入已有时间与生存概率；不拟合、不估计在险人数、不做 log-rank 检验。'),
    ('enrichment', '富集点图（已有结果）', 'biomedical', ['x', 'y'], 'results', '展示已有富集统计；可选 size/count 与 color，不运行 GO/KEGG 富集分析。'),
    ('diagram', '技术路线 / 模型框图', 'diagram', [], 'raw', '用可编辑节点与箭头构图；概念/机制假设需由研究者确认。'),
    ('panel', '原图面板拼版', 'image', [], 'raw', '保留原始图片、等比显示并在图外添加 A/B 标签；不修饰实验内容。'),
]


def catalog():
    return [dict(id=k, label=label, family=family, requirements=fields, support=support, description=description)
            for k, label, family, fields, support, description in _CATALOG]


def _text(value, name, limit=300):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(name + '必须为长度合理的文本。')
    return value


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name}必须在 {low:g} 至 {high:g} 之间。')
    return value


def validate_spec(spec):
    if not isinstance(spec, dict) or any(not isinstance(k, str) for k in spec):
        raise ValueError('绘图配置必须是结构化对象。')
    try:
        encoded = json.dumps(spec, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError('绘图配置包含无效或非有限数值。') from None
    if len(encoded) > 100000:
        raise ValueError('绘图配置过大，请简化标签或节点。')
    allowed = {'version', 'kind', 'title', 'source_ids', 'mapping', 'style', 'options', 'diagram', 'panels'}
    if set(spec) - allowed:
        raise ValueError('绘图配置含不支持的字段：' + ', '.join(sorted(set(spec) - allowed)))
    spec = copy.deepcopy(spec)
    if type(spec.get('version', 1)) is not int or spec.get('version', 1) != 1:
        raise ValueError('暂不支持此绘图配置版本。')
    spec['version'] = 1
    kind = spec.get('kind')
    if not isinstance(kind, str) or kind not in {x[0] for x in _CATALOG}:
        raise ValueError('请选择受支持的图型。')
    spec['title'] = _text(spec.get('title', ''), '图题', 300)
    ids = spec.setdefault('source_ids', [])
    if not isinstance(ids, list) or len(ids) > 24 or any(not isinstance(x, str) or not x or len(x) > 80 for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('绘图来源 ID 无效或重复（最多 24 个）。')
    for field, fields in [('mapping', {'x', 'y', 'group', 'label', 'lower', 'upper', 'size', 'color'}),
                          ('style', {'xlabel', 'ylabel', 'width', 'height', 'dpi', 'palette', 'font_size', 'legend', 'xlim', 'ylim', 'xscale', 'yscale'}),
                          ('options', {'aggregate', 'bins', 'annotate', 'y_is_neglog10', 'p_threshold', 'fc_threshold', 'reference', 'columns', 'orientation', 'label_points', 'sort_x'})]:
        obj = spec.setdefault(field, {})
        if not isinstance(obj, dict) or any(not isinstance(k, str) for k in obj) or set(obj) - fields:
            raise ValueError(field + ' 中有不支持的选项。')
    for key, value in spec['mapping'].items():
        if key == 'y' and isinstance(value, list):
            if not 1 <= len(value) <= 30 or any(not isinstance(v, str) or not v or len(v) > 200 for v in value):
                raise ValueError('y 列列表无效（最多 30 列）。')
        else:
            _text(value, '字段映射', 200)
    style = spec['style']
    for key in ['xlabel', 'ylabel']:
        if key in style:
            _text(style[key], '坐标轴标题')
    for key, default, low, high in [('width', 7.2, 3, 16), ('height', 4.8, 2.5, 16), ('dpi', 180, 72, 600), ('font_size', 10, 6, 24)]:
        style[key] = _number(style.get(key, default), key, low, high)
    if style['width'] * style['dpi'] * style['height'] * style['dpi'] > 24_000_000:
        raise ValueError('导出尺寸超过 2,400 万像素，请降低画布或 DPI。')
    style.setdefault('palette', 'muted')
    if not isinstance(style['palette'], str) or style['palette'] not in PALETTES:
        raise ValueError('请选择柔和、色觉友好或灰阶配色。')
    if 'legend' in style and not isinstance(style['legend'], bool):
        raise ValueError('图例开关必须为布尔值。')
    for key in ['xlim', 'ylim']:
        if key in style:
            values = style[key]
            if not isinstance(values, list) or len(values) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values) or values[0] >= values[1]:
                raise ValueError('坐标范围必须为递增的两个有限数值。')
    for key in ['xscale', 'yscale']:
        if not isinstance(style.get(key, 'linear'), str) or style.get(key, 'linear') not in {'linear', 'log'}:
            raise ValueError('坐标轴只支持 linear 或 log。')
        if style.get(key) == 'log' and any(v <= 0 for v in style.get(key[0] + 'lim', [])):
            raise ValueError('对数轴显示范围必须大于零。')
    opts = spec['options']
    for key in ['annotate', 'y_is_neglog10', 'label_points', 'sort_x']:
        if key in opts and not isinstance(opts[key], bool):
            raise ValueError(key + ' 必须为布尔值。')
    if not isinstance(opts.get('aggregate', 'none'), str) or opts.get('aggregate', 'none') not in {'none', 'mean'}:
        raise ValueError('柱状图只支持逐行展示或显式 mean 汇总。')
    if 'bins' in opts and (isinstance(opts['bins'], bool) or not isinstance(opts['bins'], int) or not 2 <= opts['bins'] <= 100):
        raise ValueError('直方图分箱数必须为 2–100 的整数。')
    if 'columns' in opts and (isinstance(opts['columns'], bool) or not isinstance(opts['columns'], int) or not 1 <= opts['columns'] <= 6):
        raise ValueError('布局列数必须为 1–6 的整数。')
    if not isinstance(opts.get('orientation', 'vertical'), str) or opts.get('orientation', 'vertical') not in {'vertical', 'horizontal'}:
        raise ValueError('方向必须为 vertical 或 horizontal。')
    for key, low, high in [('p_threshold', 1e-300, 1), ('fc_threshold', 0, 1000), ('reference', -1e12, 1e12)]:
        if key in opts:
            _number(opts[key], key, low, high)
    _validate_diagram(spec.get('diagram', {}), required=kind == 'diagram')
    _validate_panels(spec.get('panels', []), required=kind == 'panel')
    if kind == 'panel' and any(p['asset_id'] not in ids for p in spec['panels']):
        raise ValueError('面板来源必须包含在所选 source_ids 中。')
    return spec


def _validate_diagram(diagram, required=False):
    if not isinstance(diagram, dict) or set(diagram) - {'nodes', 'edges'}:
        raise ValueError('框图配置只支持 nodes 与 edges。')
    nodes, edges = diagram.get('nodes', []), diagram.get('edges', [])
    if not isinstance(nodes, list) or not (1 if required else 0) <= len(nodes) <= 24 or not isinstance(edges, list) or len(edges) > 48:
        raise ValueError('框图需要 1–24 个节点，最多 48 条连线。')
    ids = []
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {'id', 'label'}:
            raise ValueError('框图节点只支持 id 与 label。')
        _text(node.get('id'), '节点 ID', 80)
        _text(node.get('label'), '节点标签', 200)
        if not node['id']:
            raise ValueError('框图节点 ID 不能为空。')
        ids.append(node['id'])
    if len(set(ids)) != len(ids):
        raise ValueError('框图节点 ID 不能重复。')
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) - {'from', 'to', 'label'}:
            raise ValueError('框图连线只支持 from、to、label。')
        _text(edge.get('from'), '连线起点', 80)
        _text(edge.get('to'), '连线终点', 80)
        _text(edge.get('label', ''), '连线标签', 100)
        if edge['from'] not in ids or edge['to'] not in ids or edge['from'] == edge['to']:
            raise ValueError('连线应引用两个不同且存在的节点。')


def _validate_panels(panels, required=False):
    if not isinstance(panels, list) or not (1 if required else 0) <= len(panels) <= 12:
        raise ValueError('原图拼版需要 1–12 张 PNG/JPEG 图片。')
    for i, panel in enumerate(panels):
        if not isinstance(panel, dict) or set(panel) - {'asset_id', 'label'}:
            raise ValueError('面板只支持 asset_id 与 label。')
        _text(panel.get('asset_id'), '面板来源 ID', 80)
        if not panel['asset_id']:
            raise ValueError('面板来源 ID 不能为空。')
        _text(panel.get('label', chr(65 + i)), '面板标签', 100)


@lru_cache(maxsize=1)
def _fonts():
    from matplotlib import font_manager
    windir = Path(os.environ.get('WINDIR', 'C:/Windows'))
    for name in ['msyh.ttc', 'msyh.ttf', 'simsun.ttc', 'simhei.ttf']:
        path = windir / 'Fonts' / name
        if path.is_file():
            try:
                font_manager.fontManager.addfont(str(path))
                return font_manager.FontProperties(fname=str(path)).get_name()
            except (OSError, RuntimeError):
                continue
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ['Noto Sans CJK SC', 'Noto Sans SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']:
        if name in available:
            return name
    return 'DejaVu Sans'


def _numeric(value):
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value)


def _wrap(value, units):
    """Wrap labels by display width so Chinese and Latin titles both fit."""
    lines, current, width = [], '', 0
    for ch in str(value):
        weight = 2 if unicodedata.east_asian_width(ch) in {'W', 'F'} else 1
        if ch == '\n' or width + weight > max(8, units):
            lines.append(current.rstrip())
            current, width = '', 0
        if ch != '\n':
            current += ch
            width += weight
    if current:
        lines.append(current.rstrip())
    return '\n'.join(lines)


def _check_item_layout(spec, count):
    minimum = 1.2 + count * spec['style']['font_size'] * 1.3 / 72
    if spec['style']['height'] < minimum:
        raise ValueError(f'当前有 {count} 个条目，画布高度过小；请将 height 调至至少 {minimum:.1f} 英寸，或减少条目以保证标签可读。')


def _selected_rows(table, mapping, required, warnings):
    fields = {c['name'] for c in table['columns']}
    for key in required:
        field = mapping.get(key)
        if not isinstance(field, str) or not field:
            raise ValueError('请先选择 ' + key + ' 对应的数据列。')
    chosen = []
    for key, value in mapping.items():
        values = value if isinstance(value, list) else [value]
        chosen.extend(v for v in values if v)
    unknown = set(chosen) - fields
    if unknown:
        raise ValueError('找不到数据列：' + ', '.join(sorted(unknown)))
    chosen = list(dict.fromkeys(chosen))
    rows = [r for r in table['rows'] if all(r[f] is not None for f in chosen)]
    removed = table['row_count'] - len(rows)
    if removed:
        warnings.append(f'排除了所选字段缺失或非有限的 {removed} 行；未填补数据。')
    if not rows:
        raise ValueError('所选字段没有完整观测值，请核对缺失值或更换字段。')
    return rows


def _values(rows, field, numeric=False):
    values = [r[field] for r in rows]
    if numeric and not all(_numeric(v) for v in values):
        raise ValueError('“' + field + '”需为有限数值列，请检查混合文本或缺失标记。')
    return values


def _groups(rows, field):
    groups = {}
    for r in rows:
        label = str(r[field]) if field else ''
        if len(label) > 200:
            raise ValueError('分组标签过长，请选择简短的类别/标识列（最多 200 字）。')
        groups.setdefault(label, []).append(r)
    if len(groups) > 30:
        raise ValueError('分组最多支持 30 组，请筛选或拆分数据。')
    return groups


def _table_plot(ax, spec, table, warnings, colors, metadata):
    import numpy as np
    kind, mapping, options = spec['kind'], spec['mapping'], spec['options']
    requirements = next(x[3] for x in _CATALOG if x[0] == kind)
    if kind in {'heatmap', 'confusion'}:
        return _heatmap(ax, spec, table, warnings, metadata)
    y_fields = mapping.get('y')
    if isinstance(y_fields, list) and kind != 'line':
        raise ValueError('只有折线图或热图支持多个 y 列，请选择单个 y 列。')
    if kind == 'line' and isinstance(y_fields, list):
        requirements = ['x']
    rows = _selected_rows(table, mapping, requirements, warnings)
    metadata['rows_used'] = len(rows)
    x, y, group = mapping.get('x'), mapping.get('y'), mapping.get('group')
    if kind in {'line', 'scatter', 'roc', 'pr', 'km', 'ma', 'volcano'}:
        xs = _values(rows, x)
        if kind == 'line' and any(not _numeric(v) for v in xs):
            if len(set(xs)) > 80 or any(len(str(v)) > 200 for v in xs):
                raise ValueError('分类折线图最多 80 个简短类别；连续训练曲线请使用数值型横轴。')
        if kind in {'scatter', 'roc', 'pr', 'km', 'ma', 'volcano'}:
            _values(rows, x, True)
        ys_fields = y if isinstance(y, list) else [y]
        for field in ys_fields:
            _values(rows, field, True)
        if kind in {'roc', 'pr', 'km'}:
            if any(not 0 <= r[field] <= 1 for r in rows for field in ys_fields) or (kind != 'km' and any(not 0 <= r[x] <= 1 for r in rows)):
                raise ValueError('ROC/PR 概率坐标须在 0–1 内，生存概率也须在 0–1 内。')
            if kind == 'km' and any(r[x] < 0 for r in rows):
                raise ValueError('生存时间不能为负数。')
        if kind == 'volcano':
            values = _values(rows, y, True)
            if options.get('y_is_neglog10', False):
                if any(v < 0 for v in values):
                    raise ValueError('−log10(p) 不能为负数。')
                ys = values
            else:
                if any(not 0 < v <= 1 for v in values):
                    raise ValueError('p/校正 p 值必须大于 0 且不超过 1；p=0 请回到分析工具保留精度，不自动替换。')
                ys = [-math.log10(v) for v in values]
            # Thresholds are explicit visual guides, never newly calculated significance.
            threshold = options.get('p_threshold', .05)
            fold = options.get('fc_threshold', 1)
            cutoff = -math.log10(threshold)
            shades = [colors[1] if a >= fold and b >= cutoff else colors[0] if a <= -fold and b >= cutoff else '#AEB5BC' for a, b in zip(xs, ys)]
            ax.scatter(xs, ys, s=24, c=shades, alpha=.85, linewidths=.3, edgecolors='white')
            ax.axhline(cutoff, color='#8A9198', lw=.8, ls='--')
            ax.axvline(fold, color='#8A9198', lw=.8, ls='--')
            ax.axvline(-fold, color='#8A9198', lw=.8, ls='--')
            if options.get('label_points') and mapping.get('label'):
                if len(rows) > 50:
                    raise ValueError('点标签最多 50 行，请筛选后再标注。')
                for r, a, b in zip(rows, xs, ys):
                    ax.annotate(str(r[mapping['label']]), (a, b), xytext=(3, 3), textcoords='offset points', fontsize=8)
            ax.set_xlabel('log2 fold change')
            ax.set_ylabel('−log10(' + y + ')' if not options.get('y_is_neglog10') else y)
            metadata['transforms'] = [] if options.get('y_is_neglog10') else [{'column': y, 'operation': '-log10', 'zero_replacement': False}]
            metadata['visual_thresholds'] = {'p': threshold, 'absolute_log2fc': fold}
            warnings.append('火山图阈值仅作视觉参考；p 值类型、比较设计与多重校正需研究者核对。')
        else:
            groups = _groups(rows, group)
            for gi, (label, selected) in enumerate(groups.items()):
                if options.get('sort_x', False):
                    selected = sorted(selected, key=lambda r: r[x])
                xx = _values(selected, x)
                if kind in {'roc', 'pr', 'km'} and any(xx[i] > xx[i + 1] for i in range(len(xx) - 1)):
                    raise ValueError('曲线的 x 值需按组递增；请先排序，或启用 sort_x。')
                if kind == 'km' and any(selected[i][y] < selected[i + 1][y] - 1e-12 for i in range(len(selected) - 1)):
                    raise ValueError('已有生存曲线的生存概率应不随时间增加；请核对字段。')
                if kind == 'roc' and any(selected[i][y] > selected[i + 1][y] + 1e-12 for i in range(len(selected) - 1)):
                    raise ValueError('已有 ROC 曲线的 TPR 应随 FPR 不减；请核对字段与排序。')
                for yi, field in enumerate(ys_fields):
                    yy = _values(selected, field, True)
                    color = colors[(gi * len(ys_fields) + yi) % len(colors)]
                    legend_label = ' · '.join(v for v in [label, field if len(ys_fields) > 1 else ''] if v)
                    if kind in {'scatter', 'ma'}:
                        ax.scatter(xx, yy, s=28, color=color, alpha=.8, label=legend_label or None, linewidths=.3, edgecolors='white')
                    elif kind == 'km':
                        ax.step(xx, yy, where='post', color=color, lw=1.7, label=legend_label or None)
                    else:
                        ax.plot(xx, yy, color=color, lw=1.6, marker='o' if len(xx) < 40 and kind == 'line' else None,
                                markersize=3.4, label=legend_label or None)
            if kind == 'roc':
                ax.plot([0, 1], [0, 1], ls='--', color='#A3AAB1', lw=.8)
            if kind in {'roc', 'pr'}:
                ax.set(xlim=(0, 1), ylim=(0, 1), xlabel='False positive rate' if kind == 'roc' else 'Recall', ylabel='True positive rate' if kind == 'roc' else 'Precision')
            elif kind == 'km':
                ax.set(ylim=(0, 1.03), xlabel=x, ylabel='Survival probability')
            if kind == 'ma':
                ax.axhline(0, color='#A3AAB1', lw=.8, ls='--')
            if kind in {'roc', 'pr', 'km'}:
                warnings.append('直接绘制已有结果；未计算模型指标、置信区间、在险人数或假设检验。')
    elif kind == 'bar':
        _values(rows, y, True)
        labels = list(dict.fromkeys(str(r[x]) for r in rows))
        if len(labels) > 80:
            raise ValueError('柱状图最多 80 个类别，请筛选数据。')
        groups = _groups(rows, group)
        width = .8 / len(groups)
        aggregate = options.get('aggregate', 'none')
        metadata['bar_values'] = []
        for gi, (label, selected) in enumerate(groups.items()):
            buckets = {lab: [r[y] for r in selected if str(r[x]) == lab] for lab in labels}
            if aggregate != 'mean' and any(len(values) > 1 for values in buckets.values()):
                raise ValueError('同一类别/组有多行数据，请明确选择 mean 汇总（同时显示原始点），或使用箱线/散点图。')
            positions, values = [], []
            for index, lab in enumerate(labels):
                ys = buckets[lab]
                if not ys:
                    continue
                pos = index - .4 + width * (gi + .5)
                positions.append(pos)
                values.append(math.fsum(ys) / len(ys))
                metadata['bar_values'].append({'category': lab, 'group': label, 'value': values[-1], 'record_count': len(ys)})
                if aggregate == 'mean':
                    jitter = np.linspace(-width * .16, width * .16, len(ys)) if len(ys) > 1 else [0]
                    if options.get('orientation') == 'horizontal':
                        ax.scatter(ys, [pos + j for j in jitter], s=14, color='#25313C', zorder=4, alpha=.75)
                    else:
                        ax.scatter([pos + j for j in jitter], ys, s=14, color='#25313C', zorder=4, alpha=.75)
            if options.get('orientation') == 'horizontal':
                ax.barh(positions, values, height=width * .9, color=colors[gi % len(colors)], label=label or None)
                ax.set_yticks(range(len(labels)), labels)
                ax.set_xlabel(y)
                ax.set_ylabel(x)
            else:
                ax.bar(positions, values, width=width * .9, color=colors[gi % len(colors)], label=label or None)
                ax.set_xticks(range(len(labels)), labels, rotation=25 if max(map(len, labels)) > 10 else 0, ha='right' if max(map(len, labels)) > 10 else 'center')
        metadata['aggregation'] = aggregate
        if aggregate == 'mean':
            warnings.append('柱高为本机计算的算术均值，并显示原始点；未生成误差线或显著性。实验单位/重复测量需另行确认。')
    elif kind in {'box', 'violin'}:
        _values(rows, y, True)
        field = group or x
        grouped = _groups(rows, field)
        values = [[r[y] for r in selected] for selected in grouped.values()]
        labels = list(grouped) if field else [y]
        if kind == 'box':
            result = ax.boxplot(values, patch_artist=True, widths=.5, showfliers=False, whis=1.5,
                                medianprops={'color': '#243445', 'linewidth': 1.4}, zorder=2)
            for index, patch in enumerate(result['boxes']):
                patch.set(facecolor=colors[index % len(colors)], alpha=.35)
        else:
            if any(len(v) < 2 or len(set(v)) < 2 for v in values):
                raise ValueError('小提琴图每组至少需要两个不同数值；单值组可改用散点图或箱线图。')
            result = ax.violinplot(values, showmeans=False, showmedians=True, showextrema=True)
            for index, body in enumerate(result['bodies']):
                body.set(facecolor=colors[index % len(colors)], edgecolor=colors[index % len(colors)], alpha=.3)
        for index, vals in enumerate(values, 1):
            jitter = np.linspace(-.10, .10, len(vals)) if len(vals) > 1 else [0]
            ax.scatter([index + j for j in jitter], vals, color=colors[(index - 1) % len(colors)], s=16, alpha=.7, zorder=3)
        ax.set_xticks(range(1, len(labels) + 1), labels)
        if field:
            ax.set_xlabel(field)
        metadata['group_counts'] = {label: len(vals) for label, vals in zip(labels, values)}
    elif kind == 'histogram':
        _values(rows, x, True)
        grouped = _groups(rows, group)
        all_values = _values(rows, x, True)
        edges = np.histogram_bin_edges(all_values, bins=options.get('bins', 15))
        for index, (label, selected) in enumerate(grouped.items()):
            ax.hist([r[x] for r in selected], bins=edges, color=colors[index % len(colors)], alpha=.55, label=label or None, edgecolor='white', linewidth=.5)
        ax.set_ylabel('Count')
        metadata['bin_edges'] = edges.tolist()
    elif kind == 'forest':
        effects = _values(rows, y, True)
        lower = _values(rows, mapping['lower'], True)
        upper = _values(rows, mapping['upper'], True)
        if any(not lo <= eff <= hi for lo, eff, hi in zip(lower, effects, upper)):
            raise ValueError('森林图区间应满足下界 ≤ 效应值 ≤ 上界，请核对映射。')
        if len(rows) > 80:
            raise ValueError('森林图最多 80 个条目，请筛选数据。')
        if any(len(str(r[mapping['label']])) > 200 for r in rows):
            raise ValueError('森林图条目标签过长，请使用简短名称（最多 200 字）。')
        _check_item_layout(spec, len(rows))
        positions = list(range(len(rows)))
        ax.errorbar(effects, positions, xerr=[[e - lo for e, lo in zip(effects, lower)], [hi - e for hi, e in zip(upper, effects)]],
                    fmt='s', color=colors[0], ecolor=colors[0], capsize=3, markersize=4, lw=1.1)
        ax.set_yticks(positions, [_wrap(r[mapping['label']], 40) for r in rows])
        ax.invert_yaxis()
        if 'reference' in options:
            ax.axvline(options['reference'], ls='--', lw=.8, color='#8A9198')
        ax.set_xlabel(y)
        warnings.append('上下界来自所选数据列；本图未推断区间置信水平，未拟合 Cox/回归或执行 Meta 合并。')
    elif kind == 'enrichment':
        # Conventional dot plot: numeric enrichment x, category label y.
        xx = _values(rows, x, True)
        if len(rows) > 80:
            raise ValueError('富集点图最多 80 个条目，请先筛选已有结果。')
        if any(len(str(r[y])) > 200 for r in rows):
            raise ValueError('富集术语标签过长，请使用简短名称（最多 200 字）。')
        _check_item_layout(spec, len(rows))
        sizes = _values(rows, mapping['size'], True) if mapping.get('size') else [1] * len(rows)
        if any(v < 0 for v in sizes):
            raise ValueError('点大小对应的计数/权重不能为负数。')
        maximum = max(sizes) or 1
        sizes = [24 + 160 * v / maximum for v in sizes]
        c = _values(rows, mapping['color'], True) if mapping.get('color') else colors[0]
        points = ax.scatter(xx, list(range(len(rows))), s=sizes, c=c, cmap='viridis' if mapping.get('color') else None, alpha=.85)
        if mapping.get('color'):
            ax.figure.colorbar(points, ax=ax, label=mapping['color'], fraction=.04, pad=.03)
        ax.set_yticks(range(len(rows)), [_wrap(r[y], 40) for r in rows])
        ax.invert_yaxis()
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        warnings.append('富集指标、术语筛选和背景基因集由上游分析决定；本图未重新运行富集分析。')
    if kind not in {'forest', 'enrichment'}:
        if not ax.get_xlabel() and x:
            ax.set_xlabel(x)
        if not ax.get_ylabel() and isinstance(y, str):
            ax.set_ylabel(y)


def _heatmap(ax, spec, table, warnings, metadata):
    import numpy as np
    mapping, opts = spec['mapping'], spec['options']
    rows = _selected_rows(table, mapping, [], warnings)
    x, y, color = mapping.get('x'), mapping.get('y'), mapping.get('color')
    if isinstance(y, str) and x and color:
        # Explicit long-form matrix; duplicates must never silently aggregate.
        xs = list(dict.fromkeys(str(r[x]) for r in rows))
        ys = list(dict.fromkeys(str(r[y]) for r in rows))
        matrix = np.full((len(ys), len(xs)), np.nan)
        seen = set()
        for r in rows:
            key = (str(r[x]), str(r[y]))
            if key in seen:
                raise ValueError('热图同一行列位置有多个值，请先明确汇总规则。')
            seen.add(key)
            if not _numeric(r[color]):
                raise ValueError('热图 color 字段必须为数值。')
            matrix[ys.index(key[1]), xs.index(key[0])] = r[color]
    else:
        if isinstance(y, str) and y:
            fields = [y]
        else:
            fields = y or [c['name'] for c in table['columns'] if c['name'] != x and c['type'] == 'numeric']
        if not fields:
            raise ValueError('热图需要至少一个数值列；可用 x 指定行标签列。')
        if len(fields) > 80 or len(rows) > 120:
            raise ValueError('热图最多 120 行、80 个数值列，请先筛选。')
        for r in rows:
            if any(r[f] is not None and not _numeric(r[f]) for f in fields):
                raise ValueError('热图数值区域包含文本，请核对所选列。')
        matrix = np.array([[r[f] if r[f] is not None else np.nan for f in fields] for r in rows], dtype=float)
        xs = fields
        ys = [str(r[x]) for r in rows] if x else [str(i + 1) for i in range(len(rows))]
    if len(xs) > 80 or len(ys) > 120:
        raise ValueError('热图最多 120 行、80 列。')
    if spec['kind'] == 'confusion':
        if matrix.shape[0] != matrix.shape[1] or np.isnan(matrix).any() or (matrix < 0).any():
            raise ValueError('混淆矩阵需为完整、非负的方阵，且不自动归一化。')
        if not np.equal(matrix, np.floor(matrix)).all():
            warnings.append('矩阵含小数；按提供的数值绘制，请确认其为计数还是已归一化结果。')
    missing = int(np.isnan(matrix).sum())
    if missing:
        warnings.append(f'矩阵有 {missing} 个未提供的位置，以空白显示，未补零。')
    cmap = __import__('matplotlib').colormaps['Blues' if spec['kind'] == 'confusion' else 'viridis'].with_extremes(bad='#F2F3F4')
    image = ax.imshow(np.ma.masked_invalid(matrix), aspect='auto', interpolation='nearest', cmap=cmap)
    ax.set_xticks(range(len(xs)), xs, rotation=35, ha='right')
    ax.set_yticks(range(len(ys)), ys)
    if opts.get('annotate', matrix.size <= 100):
        if matrix.size > 400:
            raise ValueError('热图逐格标注最多 400 格，请关闭 annotate。')
        finite = matrix[np.isfinite(matrix)]
        threshold = (float(finite.max()) + float(finite.min())) / 2 if finite.size else 0
        for (i, j), v in np.ndenumerate(matrix):
            if math.isfinite(v):
                ax.text(j, i, format(v, '.4g'), ha='center', va='center', fontsize=8,
                        color='white' if v > threshold else '#26313B')
    ax.figure.colorbar(image, ax=ax, fraction=.04, pad=.03)
    if spec['kind'] == 'confusion':
        ax.set_xlabel('Predicted class')
        ax.set_ylabel('True class')
    metadata['rows_used'] = len(rows)
    metadata['matrix_shape'] = list(matrix.shape)
    metadata['transforms'] = []


def _diagram(ax, spec, warnings, metadata):
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    diagram = spec.get('diagram', {})
    if not isinstance(diagram, dict) or set(diagram) - {'nodes', 'edges'}:
        raise ValueError('框图配置只支持 nodes 与 edges。')
    nodes, edges = diagram.get('nodes', []), diagram.get('edges', [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 24 or not isinstance(edges, list) or len(edges) > 48:
        raise ValueError('框图需要 1–24 个节点，最多 48 条连线。')
    ids = []
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {'id', 'label'}:
            raise ValueError('框图节点只支持 id 与 label。')
        _text(node.get('id'), '节点 ID', 80)
        if not node['id']:
            raise ValueError('框图节点 ID 不能为空。')
        _text(node.get('label'), '节点标签', 200)
        ids.append(node['id'])
    if len(set(ids)) != len(ids):
        raise ValueError('框图节点 ID 不能重复。')
    columns = min(spec['options'].get('columns', 3), len(nodes))
    row_count = math.ceil(len(nodes) / columns)
    positions = {}
    box_width = .82 / columns
    box_height = min(.55 / row_count, .22)
    for i, node in enumerate(nodes):
        col, row = i % columns, i // columns
        px, py = (col + .5) / columns, 1 - (row + .5) / row_count
        positions[node['id']] = (px, py)
        patch = FancyBboxPatch((px - box_width / 2, py - box_height / 2), box_width, box_height,
                              boxstyle='round,pad=.008,rounding_size=.015', linewidth=1,
                              edgecolor='#668098', facecolor='#F1F5F8', zorder=3)
        ax.add_patch(patch)
        label = _wrap(node['label'], max(12, int(72 / columns)))
        if label.count('\n') > max(2, math.floor(box_height * spec['style']['height'] * 72 / spec['style']['font_size']) - 1):
            raise ValueError('框图节点标签过长；请缩短标签、增加画布高度或减少布局行数。')
        ax.text(px, py, label, ha='center', va='center', fontsize=min(spec['style']['font_size'], 11), zorder=4, clip_on=False)
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) - {'from', 'to', 'label'} or edge.get('from') not in positions or edge.get('to') not in positions:
            raise ValueError('框图连线必须引用存在的 from/to 节点。')
        _text(edge.get('label', ''), '连线标签', 100)
        if edge['from'] == edge['to']:
            raise ValueError('基础框图暂不支持指向自身的连线。')
        start, end = positions[edge['from']], positions[edge['to']]
        # Shrink out of boxes according to direction; draw arrows beneath nodes.
        dx, dy = end[0] - start[0], end[1] - start[1]
        t = min(box_width / (2 * abs(dx)) if dx else 1, box_height / (2 * abs(dy)) if dy else 1)
        a = (start[0] + dx * (t + .025), start[1] + dy * (t + .025))
        b = (end[0] - dx * (t + .025), end[1] - dy * (t + .025))
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle='-|>', mutation_scale=10, color='#6D7D8B', lw=1, zorder=2))
        if edge.get('label'):
            ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + .018, edge['label'], ha='center', va='bottom', fontsize=8,
                    bbox={'facecolor': 'white', 'edgecolor': 'none', 'pad': 1}, zorder=5)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis('off')
    warnings.append('这是可编辑的技术路线/概念框图；箭头本身不构成实验或因果证据。')
    metadata['node_count'] = len(nodes)
    metadata['edge_count'] = len(edges)


def _panel(fig, spec, sources, warnings, metadata):
    panels = spec.get('panels', [])
    if not isinstance(panels, list) or not 1 <= len(panels) <= 12:
        raise ValueError('原图拼版需要 1–12 张 PNG/JPEG 图片。')
    columns = min(spec['options'].get('columns', 2), len(panels))
    rows = math.ceil(len(panels) / columns)
    total_pixels = 0
    for i, panel in enumerate(panels):
        if not isinstance(panel, dict) or set(panel) - {'asset_id', 'label'} or panel.get('asset_id') not in sources:
            raise ValueError('面板必须选择当前项目内有效的图片材料。')
        label = _text(panel.get('label', chr(65 + i)), '面板标签', 100)
        source = sources[panel['asset_id']]
        image = load_image(source['raw'], source['name'])
        total_pixels += image.width * image.height
        if total_pixels > 32_000_000:
            raise ValueError('一次拼版的原图合计最多 3,200 万像素，请拆为多张组图。')
        ax = fig.add_subplot(rows, columns, i + 1)
        ax.imshow(image, interpolation='none', aspect='equal')
        ax.set_title(_wrap(label, max(16, int(spec['style']['width'] * 9 / columns))), loc='left', fontsize=spec['style']['font_size'] + 1, fontweight='bold', pad=8)
        ax.axis('off')
    warnings.append('图片仅等比排版与图外加标签；显示尺寸会缩放，原始文件和像素来源保持不变。未新增比例尺或修改实验内容。')
    metadata['image_processing'] = ['display_exif_orientation', 'fit_preserve_aspect', 'label_outside_image']
    metadata['panel_count'] = len(panels)


def render_figure(spec, sources, fmt='png'):
    """Render validated config with local source bytes and reproducible provenance."""
    if fmt not in {'png', 'svg', 'pdf', 'tiff'}:
        raise ValueError('导出格式支持 PNG、SVG、PDF、TIFF。')
    spec = validate_spec(spec)
    if not isinstance(sources, list) or len(sources) > 24:
        raise ValueError('绘图材料来源无效（最多 24 个）。')
    source_map = {}
    total_bytes = 0
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('id'), str) or not isinstance(source.get('name'), str) or not isinstance(source.get('raw'), bytes):
            raise ValueError('绘图材料缺少 id、名称或原始文件。')
        if source['id'] in source_map:
            raise ValueError('绘图材料 ID 重复。')
        total_bytes += len(source['raw'])
        if len(source['raw']) > 20 * 1024 * 1024 or total_bytes > 60 * 1024 * 1024:
            raise ValueError('单个绘图材料最多 20 MB，一次绘图原始材料合计最多 60 MB。')
        source_map[source['id']] = source
    requested = spec['source_ids']
    if any(i not in source_map for i in requested):
        raise ValueError('绘图来源材料已不存在，请重新选择。')
    # Only selected IDs can be used, even when the caller has extra assets.
    selected = {i: source_map[i] for i in requested}
    warnings = []
    metadata = {'engine': 'local-matplotlib', 'engine_version': ENGINE_VERSION, 'spec_version': 1,
                'model_called': False, 'analysis_performed': False, 'transforms': [], 'sources': []}
    for source in selected.values():
        metadata['sources'].append({'id': source['id'], 'name': source['name'], 'sha256': hashlib.sha256(source['raw']).hexdigest()})
    with LOCK:
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        font = _fonts()
        style = spec['style']
        rc = {'font.family': font, 'font.size': style['font_size'], 'axes.unicode_minus': False,
              'text.usetex': False, 'text.parse_math': False, 'figure.facecolor': 'white', 'axes.facecolor': 'white',
              'svg.fonttype': 'path', 'pdf.fonttype': 42, 'axes.spines.top': False, 'axes.spines.right': False,
              'axes.edgecolor': '#87939F', 'axes.labelcolor': '#293641', 'xtick.color': '#465461', 'ytick.color': '#465461',
              'savefig.facecolor': 'white', 'axes.titleweight': 'normal', 'svg.hashsalt': 'yanxu-plot-v1'}
        with matplotlib.rc_context(rc):
            fig = Figure(figsize=(style['width'], style['height']), dpi=style['dpi'], layout='constrained')
            FigureCanvasAgg(fig)
            try:
                if spec['kind'] == 'panel':
                    _panel(fig, spec, selected, warnings, metadata)
                    if spec['title']:
                        fig.suptitle(_wrap(spec['title'], style['width'] * 10), fontsize=style['font_size'] + 2)
                else:
                    ax = fig.add_subplot(111)
                    if spec['kind'] == 'diagram':
                        _diagram(ax, spec, warnings, metadata)
                    else:
                        if not selected:
                            raise ValueError('请先选择一个本机表格材料。')
                        if len(selected) != 1:
                            raise ValueError('单图一次使用一个表格；请先合并数据，或分别生成后拼版。')
                        source = next(iter(selected.values()))
                        table = load_table(source['raw'], source['name'])
                        warnings.extend(table['warnings'])
                        _table_plot(ax, spec, table, warnings, PALETTES[style['palette']], metadata)
                        for key, setter in [('xlabel', ax.set_xlabel), ('ylabel', ax.set_ylabel), ('xlim', ax.set_xlim), ('ylim', ax.set_ylim)]:
                            if key in style:
                                if key in {'xlabel', 'ylabel'} and not style[key].strip():
                                    continue
                                setter(style[key])
                        for key, axis in [('xscale', 'x'), ('yscale', 'y')]:
                            if style.get(key) == 'log':
                                # Never let Matplotlib silently hide nonpositive observations.
                                for line in ax.lines:
                                    vals = line.get_xdata() if axis == 'x' else line.get_ydata()
                                    if any(_numeric(v) and v <= 0 for v in vals):
                                        raise ValueError('对数轴不能包含零或负值，请先核对数据。')
                                if spec['kind'] in {'bar', 'box', 'violin', 'histogram', 'heatmap', 'confusion', 'forest', 'enrichment'}:
                                    raise ValueError('此图型的基础版本暂不支持对数轴，请使用线性轴。')
                                field = spec['mapping'].get(axis)
                                if isinstance(field, str) and any(_numeric(r[field]) and r[field] <= 0 for r in table['rows']):
                                    raise ValueError('对数轴不能包含零或负值，请先核对数据。')
                                getattr(ax, 'set_' + axis + 'scale')('log')
                        handles, labels = ax.get_legend_handles_labels()
                        if handles and style.get('legend', True):
                            ax.legend(handles, labels, frameon=False, loc='best', fontsize=max(7, style['font_size'] - 1))
                        if spec['kind'] not in {'heatmap', 'confusion'}:
                            ax.grid(axis='y', alpha=.16, lw=.6, zorder=0)
                    if spec['title']:
                        ax.set_title(_wrap(spec['title'], style['width'] * 10), fontsize=style['font_size'] + 2, pad=12)
                output = io.BytesIO()
                kwargs = {'format': fmt, 'dpi': style['dpi'], 'facecolor': 'white'}
                if fmt == 'svg':
                    kwargs['metadata'] = {'Date': None, 'Creator': 'Yanxu Research Studio local plotting'}
                elif fmt == 'pdf':
                    kwargs['metadata'] = {'CreationDate': None, 'ModDate': None, 'Creator': 'Yanxu Research Studio local plotting'}
                elif fmt == 'png':
                    kwargs['metadata'] = {'Software': 'Yanxu Research Studio local plotting'}
                with python_warnings.catch_warnings(record=True) as render_warnings:
                    python_warnings.simplefilter('always')
                    fig.savefig(output, **kwargs)
                if any('Glyph' in str(w.message) and 'missing' in str(w.message) for w in render_warnings):
                    warnings.append('部分字符在当前字体中缺失，请替换特殊符号或选择系统支持的标签后重新导出。')
                if any('constrained_layout' in str(w.message) for w in render_warnings):
                    warnings.append('标签较多导致画布空间不足，请增大画布或缩短标签，并检查导出文件。')
                metadata.update({'matplotlib': matplotlib.__version__, 'font': font, 'width_inches': style['width'],
                                 'height_inches': style['height'], 'dpi': style['dpi'], 'format': fmt,
                                 'spec_sha256': hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()})
                if font == 'DejaVu Sans':
                    warnings.append('系统未找到中文字体；含中文标签时请安装微软雅黑或 Noto Sans CJK 后重新导出。')
                family = next(x for x in _CATALOG if x[0] == spec['kind'])
                caption = spec['title'] or family[1]
                caption += '。' + ('展示导入的已有分析结果；' if family[4] == 'results' else '由本机绘图工具绘制；') + '方法、实验单位和结论请由研究者核对。'
                if metadata.get('rows_used') is not None:
                    caption += f"本图使用 {metadata['rows_used']} 行完整记录。"
                return {'data': output.getvalue(), 'mime': {'png': 'image/png', 'svg': 'image/svg+xml', 'pdf': 'application/pdf', 'tiff': 'image/tiff'}[fmt],
                        'warnings': list(dict.fromkeys(warnings)), 'caption': caption, 'metadata': metadata}
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                if isinstance(exc, ValueError):
                    raise
                raise ValueError('绘图配置或数值不适合当前图型，请核对字段与范围。') from None
            finally:
                fig.clear()
