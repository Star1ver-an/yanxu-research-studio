"""Explicit parent-gate cytometry analysis. Never infer biological gates."""
import io
import json
from pathlib import Path
import re

from .analysis_special import (checkpoint, dependency, field, figure, input_file,
                               method, numbers, palette, param, read_table, result,
                               select, source, table, validate_config)

MAX_EVENTS = 200_000
MAX_CHANNELS = 128
MAX_ELEMENTS = 5_000_000


def catalog():
    return [method('flow_gating', '流式事件与明确门控', 'flow', '读取真实 FCS/事件表，按指定补偿、变换和父门计算比例。',
                   [input_file(formats=['fcs', 'csv', 'tsv', 'xlsx']), input_file('compensation', '用户 spillover 矩阵（可选）', required=False)],
                   [field('x', '绘图 X 通道', 'numeric', required=True), field('y', '绘图 Y 通道', 'numeric', required=True)],
                   [select('compensation', '补偿方式（必须明确选择）', [('none', '不补偿（用户确认）'), ('file', '用户 spillover 矩阵'), ('fcs', 'FCS 中已有 SPILL/SPILLOVER')], 'none'),
                    select('transform', '显示与门坐标变换', [('linear', '线性'), ('arcsinh', 'arcsinh(x/cofactor)'), ('log10', 'log10（仅正值）')], 'linear'),
                    param('cofactor', 'arcsinh cofactor', 'number', 150, min=.000001, max=1e12),
                    param('gates', '按父门顺序配置门 JSON（门坐标为变换后坐标）', 'text', '[]'),
                    param('display_gate', '显示父门名称（all 或已有门名）', 'text', 'all'),
                    select('view', '视图', [('scatter', '散点 + 门'), ('density', '二维密度 + 门'), ('histogram', 'X 通道直方图')], 'scatter'),
                    param('display_events', '最多显示事件（计数仍用全部事件）', 'integer', 5000, min=100, max=10000),
                    param('seed', '显示抽样种子', 'integer', 0, min=0, max=2147483647)],
                   ['门和补偿必须来自用户/实验对照；不自动推断生物学群体。',
                    '门比例分母为指定父门事件数；全部样本比例另列。门边界计入内部。',
                    'CSV 表示用户提供的线性事件值；FCS 先按仪器 gain/log/time 元数据预处理，再补偿、变换。',
                    '矩形门：{"name":"P1","type":"rectangle","parent":"all","bounds":[xmin,xmax,ymin,ymax]}；多边形门使用 type=polygon、vertices=[[x,y],...]。'])]


def validate(config):
    config = validate_config(config, catalog())
    p = config['parameters']
    if p['compensation'] == 'file' and not config['inputs'].get('compensation'):
        raise ValueError('请选择用户提供的 spillover 矩阵材料。')
    if config['columns']['x'] == config['columns']['y']:
        raise ValueError('门控 X 和 Y 必须选择不同的通道。')
    try:
        gates = json.loads(p['gates'])
    except (ValueError, TypeError):
        raise ValueError('门配置必须为有效 JSON 数组，不执行代码。') from None
    if not isinstance(gates, list) or not 1 <= len(gates) <= 24:
        raise ValueError('请明确配置 1–24 个门；不会自动猜测门限。')
    known = {'all'}
    for gate in gates:
        if not isinstance(gate, dict) or set(gate) - {'name', 'parent', 'type', 'bounds', 'vertices', 'x', 'y'}:
            raise ValueError('门含未知字段。')
        name = gate.get('name')
        if not isinstance(name, str) or not name or len(name) > 80 or name in known:
            raise ValueError('门名称不能为空、重复或使用保留名称 all。')
        if gate.get('parent', 'all') not in known:
            raise ValueError('父门必须为 all 或已经声明的门；不能循环引用。')
        for key in ['x', 'y']:
            if key in gate and (not isinstance(gate[key], str) or not gate[key] or len(gate[key]) > 200):
                raise ValueError('门通道名称无效。')
        if gate.get('type') == 'rectangle':
            bounds = gate.get('bounds')
            if not isinstance(bounds, list) or len(bounds) != 4:
                raise ValueError('矩形门 bounds 必须是 [xmin,xmax,ymin,ymax]。')
            numbers(bounds, '矩形门边界')
            if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
                raise ValueError('矩形门上界必须大于下界。')
        elif gate.get('type') == 'polygon':
            vertices = gate.get('vertices')
            if not isinstance(vertices, list) or not 3 <= len(vertices) <= 100 or any(not isinstance(v, list) or len(v) != 2 for v in vertices):
                raise ValueError('多边形门至少 3 点、最多 100 点，每点为 [x,y]。')
            for v in vertices:
                numbers(v, '多边形顶点')
            if len({tuple(v) for v in vertices}) != len(vertices):
                raise ValueError('多边形顶点不能重复（不必重复第一个点闭合）。')
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(vertices, vertices[1:] + vertices[:1]))
            if abs(area) < 1e-12:
                raise ValueError('多边形门面积必须大于 0。')
            edges = list(zip(vertices, vertices[1:] + vertices[:1]))
            for i, (a, b) in enumerate(edges):
                for j in range(i + 1, len(edges)):
                    if j == i + 1 or (i == 0 and j == len(edges) - 1):
                        continue
                    if _intersects(a, b, *edges[j]):
                        raise ValueError('多边形门不能自相交。')
        else:
            raise ValueError('门类型支持 rectangle 或 polygon。')
        known.add(name)
    if p['display_gate'] not in known:
        raise ValueError('显示父门不存在。')
    return config


def _intersects(a, b, c, d):
    def cross(x, y, z):
        return (y[0] - x[0]) * (z[1] - x[1]) - (y[1] - x[1]) * (z[0] - x[0])
    u, v, w, z = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    return u * v <= 0 and w * z <= 0 and max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0])) and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))


def _fcs_preflight(raw):
    if len(raw) < 58 or raw[:6] not in {b'FCS2.0', b'FCS3.0', b'FCS3.1'}:
        raise ValueError('FCS 文件头无效。')
    try:
        start = int(raw[10:18]); end = int(raw[18:26])
    except ValueError:
        raise ValueError('FCS TEXT 偏移无效。') from None
    if start < 58 or end < start or end >= len(raw) or end - start > 1024 * 1024:
        raise ValueError('FCS TEXT 区越界或超过 1 MB。')
    segment = raw[start:end + 1]
    delimiter = re.escape(segment[:1])
    def keyword(key, default=None):
        match = re.search(delimiter + re.escape(key.encode()) + delimiter + b'([^' + delimiter + b']*)' + delimiter, segment, re.I)
        if match is None:
            if default is not None:
                return default
            raise ValueError('FCS 缺少必需字段 ' + key)
        return match.group(1).decode('ascii', errors='strict')
    try:
        count, channels = int(keyword('$TOT')), int(keyword('$PAR'))
        mode = keyword('$MODE'); datatype = keyword('$DATATYPE')
        nextdata = int(keyword('$NEXTDATA', '0'))
    except (ValueError, UnicodeDecodeError):
        raise ValueError('FCS 事件数、通道数或数据类型元数据无效。') from None
    if not 1 <= count <= MAX_EVENTS or not 2 <= channels <= MAX_CHANNELS or count * channels > MAX_ELEMENTS:
        raise ValueError('FCS 最多 200,000 个事件、128 个通道、5,000,000 个数值。')
    if mode.upper() != 'L' or datatype.upper() not in {'I', 'F', 'D'} or nextdata != 0:
        raise ValueError('本期只支持单数据集 list-mode I/F/D FCS；多数据集请先拆分。')
    return count, channels


def _flow_data(raw, only_text=False):
    _fcs_preflight(raw)
    flowio = dependency('flowio')
    try:
        return flowio.FlowData(io.BytesIO(raw), only_text=only_text)
    except Exception as e:
        raise ValueError('FCS 解析失败；请检查数据区偏移、位宽和文件完整性（' + type(e).__name__ + '）。') from None


def _channels(fd):
    names = [str(v.get('PnN', v.get('pnn', key))) for key, v in fd.channels.items()]
    if len(set(names)) != len(names):
        raise ValueError('FCS 通道名重复，请在原始导出中修正。')
    return names


def inspect_fcs(raw):
    count, _ = _fcs_preflight(raw)
    fd = _flow_data(raw, only_text=True)
    names = _channels(fd)
    spill = fd.text.get('spill', fd.text.get('spillover'))
    return dict(row_count=count, channels=len(names), columns=[dict(name=x, type='numeric') for x in names],
                has_compensation=bool(spill), warnings=['读取 FCS 仪器元数据，不自动应用补偿或推断门。'])


def _load_events(config, sources):
    import numpy as np
    s = source(config, sources)
    if Path(s['name']).suffix.lower() == '.fcs':
        fd = _flow_data(s['raw'])
        names = _channels(fd)
        try:
            events = fd.as_array(preprocess=True).astype(float)
        except Exception:
            raise ValueError('FCS 仪器 gain/log/time 预处理失败，请检查元数据。') from None
        spill = fd.text.get('spill', fd.text.get('spillover'))
        notes = ['FCS 由 FlowIO 按仪器 gain、log、time 元数据预处理为线性事件，然后才应用用户指定补偿/显示变换。']
    else:
        data = read_table(config, sources)
        names = [x['name'] for x in data['columns'] if x['type'] == 'numeric']
        if len(names) < 2:
            raise ValueError('事件表至少需要 2 个完整数值通道。')
        events = np.column_stack([numbers([r[x] for r in data['rows']], x) for x in names]); spill = None
        notes = ['CSV/XLSX 按用户提供的线性事件值读取，不猜测仪器变换或补偿状态。']
    if len(events) > MAX_EVENTS or len(names) > MAX_CHANNELS or events.size > MAX_ELEMENTS or not np.isfinite(events).all():
        raise ValueError('事件矩阵超限或含非有限值。')
    return events, names, spill, notes


def _compensation(config, sources, spill):
    import numpy as np
    if config['parameters']['compensation'] == 'fcs':
        if not spill:
            raise ValueError('FCS 没有已有 SPILL/SPILLOVER，无法自动构造补偿矩阵。')
        parts = spill.split(',')
        try:
            n = int(parts[0]); names = parts[1:n + 1]
            if not 1 <= n <= MAX_CHANNELS or len(parts) != 1 + n + n * n:
                raise ValueError()
            matrix = np.asarray(list(map(float, parts[n + 1:])), dtype=float).reshape(n, n)
        except (ValueError, IndexError):
            raise ValueError('FCS 已有 spillover 矩阵结构无效。') from None
    else:
        data = read_table(config, sources, 'compensation')
        fields = [x['name'] for x in data['columns']]; label = fields[0]; names = fields[1:]
        if not 1 <= len(names) <= MAX_CHANNELS or len(data['rows']) != len(names):
            raise ValueError('补偿 CSV 第一列为通道 ID，剩余列与行必须构成方阵。')
        if [str(r[label]) for r in data['rows']] != names:
            raise ValueError('spillover 行标签必须与列名按同一通道顺序排列。')
        matrix = np.column_stack([numbers([r[x] for r in data['rows']], x) for x in names])
    if len(set(names)) != len(names) or not np.isfinite(matrix).all() or (matrix < 0).any() or not np.allclose(np.diag(matrix), 1, atol=1e-6):
        raise ValueError('spillover 矩阵须通道唯一、有限、非负、对角线为 1（比例单位，不是百分数）。')
    if np.linalg.cond(matrix) > 1e8:
        raise ValueError('spillover 矩阵接近奇异，拒绝进行不稳定补偿。')
    return names, matrix


def _polygon_mask(x, y, vertices):
    import numpy as np
    from matplotlib.path import Path as MplPath
    points = np.column_stack([x, y]); inside = MplPath(vertices).contains_points(points)
    # Matplotlib leaves exact-boundary results undefined. Explicitly include
    # points on every segment, with a tolerance proportional to coordinates.
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        delta = np.asarray(b) - a
        cross = (x - a[0]) * delta[1] - (y - a[1]) * delta[0]
        scale = max(1, abs(a[0]), abs(a[1]), abs(b[0]), abs(b[1]))
        tol = 1e-10 * scale
        boundary = (np.abs(cross) <= tol * max(1, np.linalg.norm(delta))) & (x >= min(a[0], b[0]) - tol) & (x <= max(a[0], b[0]) + tol) & (y >= min(a[1], b[1]) - tol) & (y <= max(a[1], b[1]) + tol)
        inside |= boundary
    return inside


def compute(config, sources, cancel=None, progress=None):
    import numpy as np
    config = validate(config)
    checkpoint(cancel, progress, '读取真实流式事件并校验补偿')
    events, names, spill, notes = _load_events(config, sources)
    p, cols = config['parameters'], config['columns']; warnings = []
    if p['compensation'] != 'none':
        comp_names, matrix = _compensation(config, sources, spill)
        if any(x not in names for x in comp_names):
            raise ValueError('补偿通道未在事件表中找到；不会按位置猜测匹配。')
        indices = [names.index(x) for x in comp_names]
        # Standard spillover convention: measured = true @ spillover.
        events[:, indices] = np.linalg.solve(matrix.T, events[:, indices].T).T
        notes.append('补偿约定 measured = true @ spillover；按通道名匹配，先补偿后显示变换。')
    else:
        warnings.append('本次明确选择不补偿；不表示数据已通过补偿质量检查。')
    gates = json.loads(p['gates']); used_channels = {cols['x'], cols['y']}
    for gate in gates:
        used_channels.update([gate.get('x', cols['x']), gate.get('y', cols['y'])])
    if any(channel not in names for channel in used_channels):
        raise ValueError('门控或绘图通道不存在；请按真实通道名配置。')
    transformed = {}
    for channel in used_channels:
        values = events[:, names.index(channel)]
        if p['transform'] == 'arcsinh':
            values = np.arcsinh(values / p['cofactor'])
        elif p['transform'] == 'log10':
            if (values <= 0).any():
                raise ValueError('log10 变换包含零/负事件（补偿后也可能出现），请选择 arcsinh 或线性；不会丢弃这些事件。')
            values = np.log10(values)
        transformed[channel] = values
    masks = {'all': np.ones(len(events), dtype=bool)}; gate_rows = []
    for gate in gates:
        checkpoint(cancel, progress, '计算明确门：' + gate['name'])
        x = transformed[gate.get('x', cols['x'])]; y = transformed[gate.get('y', cols['y'])]
        parent = gate.get('parent', 'all'); parent_mask = masks[parent]
        if gate['type'] == 'rectangle':
            xmin, xmax, ymin, ymax = gate['bounds']
            member = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
        else:
            member = _polygon_mask(x, y, gate['vertices'])
        masks[gate['name']] = member & parent_mask
        count = int(masks[gate['name']].sum()); denominator = int(parent_mask.sum())
        gate_rows.append(dict(gate=gate['name'], parent=parent, count=count, parent_count=denominator,
                              fraction_parent=count / denominator if denominator else None, fraction_all=count / len(events)))
        if denominator == 0:
            warnings.append('父门 ' + parent + ' 没有事件；其子门比例保留为空，不记为 0%。')
    selected = np.flatnonzero(masks[p['display_gate']]); rng = np.random.default_rng(p['seed'])
    if len(selected) > p['display_events']:
        selected = np.sort(rng.choice(selected, size=p['display_events'], replace=False))
        notes.append('仅绘图事件按固定种子抽样；所有门的计数/比例仍使用完整事件。')
    shown = [dict(event_index=int(i), x=float(transformed[cols['x']][i]), y=float(transformed[cols['y']][i]),
                  gates=';'.join(g['name'] for g in gates if masks[g['name']][i])) for i in selected]
    # Full-data histogram/density bins provide exact bin counts despite a
    # downsampled scatter preview.
    parent_indices = np.flatnonzero(masks[p['display_gate']])
    xp, yp = transformed[cols['x']][parent_indices], transformed[cols['y']][parent_indices]
    hist, edges = np.histogram(xp, bins=50)
    histogram = [dict(left=float(edges[i]), right=float(edges[i + 1]), count=int(n)) for i, n in enumerate(hist)]
    density, xe, ye = np.histogram2d(xp, yp, bins=50)
    density_rows = [dict(x_left=float(xe[i]), x_right=float(xe[i + 1]), y_bottom=float(ye[j]), y_top=float(ye[j + 1]), count=int(density[i, j])) for i, j in zip(*np.nonzero(density))]
    notes.append('门坐标均为所选变换后的坐标，边界事件计入门内；比例以明确父门为分母。')
    return result('flow_gating', {'gates': table(gate_rows), 'display_events': table(shown, ['event_index', 'x', 'y', 'gates']),
                                'histogram': table(histogram), 'density': table(density_rows)},
                  dict(events=len(events), channels=names, displayed_events=len(shown), display_parent_events=len(parent_indices),
                       display_gate=p['display_gate'], compensation=p['compensation'], transform=p['transform'], cofactor=p['cofactor'], seed=p['seed']),
                  warnings, notes, ['numpy', 'flowio'])


def draw(result, config, style, title=''):
    from matplotlib.patches import Rectangle, Polygon
    from matplotlib.colors import LogNorm
    fig, axes = figure(style, title); ax = axes[0, 0]; colors = palette(style)
    p, cols = config['parameters'], config['columns']
    if p['view'] == 'histogram':
        rows = result['tables']['histogram']['rows']
        ax.bar([r['left'] for r in rows], [r['count'] for r in rows], width=[r['right'] - r['left'] for r in rows], align='edge', color=colors[0], alpha=.8)
        ax.set_ylabel('Full parent-gate event count')
    elif p['view'] == 'density':
        rows = result['tables']['density']['rows']
        if rows:
            artist = ax.scatter([(r['x_left'] + r['x_right']) / 2 for r in rows], [(r['y_bottom'] + r['y_top']) / 2 for r in rows],
                                c=[r['count'] for r in rows], marker='s', s=24, cmap='viridis', norm=LogNorm())
            fig.colorbar(artist, ax=ax, label='Full parent-gate event count / bin')
    else:
        rows = result['tables']['display_events']['rows']
        ax.scatter([r['x'] for r in rows], [r['y'] for r in rows], s=4, alpha=.45, color=colors[0], rasterized=len(rows) > 3000)
    if p['view'] != 'histogram':
        for i, gate in enumerate(json.loads(p['gates'])):
            if gate.get('x', cols['x']) != cols['x'] or gate.get('y', cols['y']) != cols['y']:
                continue
            color = colors[(i + 1) % len(colors)]
            if gate['type'] == 'rectangle':
                a, b, c, d = gate['bounds']; patch = Rectangle((a, c), b - a, d - c, fill=False, color=color, lw=1.2)
                point = (a, d)
            else:
                patch = Polygon(gate['vertices'], fill=False, color=color, lw=1.2); point = gate['vertices'][0]
            ax.add_patch(patch)
            ax.annotate(gate['name'], point, xytext=(3,3),textcoords='offset points',color=color,fontsize='small',
                        bbox={'facecolor':'white','edgecolor':'none','alpha':.9,'pad':1.5})
        ax.set_ylabel(cols['y'] + ' (' + p['transform'] + ')')
    ax.set_xlabel(cols['x'] + ' (' + p['transform'] + ')')
    return fig
