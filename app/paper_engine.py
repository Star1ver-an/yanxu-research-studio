"""Bounded, declarative whole-paper compositions; never run generated code."""
import copy
import hashlib
import io
import json
import math
import re
import xml.etree.ElementTree as ET


def spec_digest(spec):
    return hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def walk_specs(spec):
    yield spec
    if spec.get('kind') == 'composition':
        for item in spec.get('composition', {}).get('items', []):
            yield item['spec']


def validate_composition(value):
    from .plot_engine import validate_spec, _text
    if not isinstance(value, dict) or set(value) - {'items', 'columns', 'shared_legend', 'fit_fonts'}:
        raise ValueError('组合图配置只支持面板、列数与共享图例。')
    items, columns = value.get('items'), value.get('columns', 2)
    if not isinstance(items, list) or not 1 <= len(items) <= 6:
        raise ValueError('每张组合图需要 1–6 张已保存的图表。')
    if type(columns) is not int or not 1 <= columns <= 3:
        raise ValueError('组合图列数应为 1–3。')
    if type(value.get('shared_legend', False)) is not bool:
        raise ValueError('共享图例开关无效。')
    if type(value.get('fit_fonts', True)) is not bool: raise ValueError('组合字号适配开关无效。')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {'figure_id', 'revision', 'label', 'spec'}:
            raise ValueError('组合图面板格式无效。')
        identity = item.get('figure_id')
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity) or identity in seen:
            raise ValueError('组合图子图标识无效或重复。')
        seen.add(identity)
        if type(item.get('revision')) is not int or item['revision'] < 1:
            raise ValueError('组合图子图版本无效。')
        _text(item.get('label', ''), '面板标签', 12)
        child = item.get('spec')
        if not isinstance(child, dict) or child.get('kind') == 'composition':
            raise ValueError('组合图不能再次嵌套组合图，请选择原始单图。')
        item['spec'] = validate_spec(child)
    return copy.deepcopy(value)


def remap_spec(spec, mapping):
    """Remap inputs including frozen child recipes; result hashes stay immutable."""
    value = copy.deepcopy(spec)
    for child in walk_specs(value):
        child['source_ids'] = [mapping.get(a, a) for a in child.get('source_ids', [])]
        if child.get('kind') == 'analysis':
            child['analysis']['inputs'] = {k: mapping.get(v, v) for k, v in child['analysis']['inputs'].items()}
        for panel in child.get('panels', []):
            panel['asset_id'] = mapping.get(panel['asset_id'], panel['asset_id'])
    return value


def validate_abstract(value, diagram):
    from .plot_engine import _text
    if not isinstance(value, dict) or set(value) - {'sections', 'status', 'summary'}:
        raise ValueError('图形摘要需要分区、研究状态与简短说明。')
    sections = value.get('sections')
    if not isinstance(sections, list) or not 2 <= len(sections) <= 3:
        raise ValueError('图形摘要需要 2–3 个分区。')
    if value.get('status', 'plan') not in {'plan', 'results'}: raise ValueError('请明确是研究方案还是结果示意。')
    _text(value.get('summary', ''), '图形摘要说明', 220)
    selected = []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {'title', 'nodes'}:
            raise ValueError('摘要分区需要标题与节点 ID 列表。')
        _text(section['title'], '摘要分区标题', 36)
        if not isinstance(section['nodes'], list) or not 1 <= len(section['nodes']) <= 3:
            raise ValueError('每个摘要分区需要 1–3 个节点。')
        selected.extend(section['nodes'])
    expected = [n['id'] for n in diagram['nodes']]
    if any(not isinstance(n, str) for n in selected) or len(set(selected)) != len(selected) or set(selected) != set(expected):
        raise ValueError('每个摘要节点必须恰好属于一个分区。')
    if len(diagram['edges']) > 12: raise ValueError('图形摘要最多 12 条连线，请简化结构。')
    return copy.deepcopy(value)


def draw_abstract(ax, spec, warnings, metadata):
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    from .plot_engine import PALETTES, _wrap
    content = spec['abstract']; sections = content['sections']; count = len(sections)
    palette = PALETTES[spec['style']['palette']]; positions = {}; nodes = {n['id']: n for n in spec['diagram']['nodes']}
    fs = spec['style']['font_size']
    if spec['style']['height'] < 3.5: raise ValueError('图形摘要高度至少 3.5 英寸，避免分区和说明拥挤。')
    for index, section in enumerate(sections):
        x = index / count + .015; width = 1 / count - .03
        ax.add_patch(FancyBboxPatch((x, .16), width, .72, boxstyle='round,pad=.005,rounding_size=.02',
            facecolor='#F6F7F8', edgecolor=palette[index % len(palette)], lw=1, zorder=1))
        ax.text(x + width / 2, .83, _wrap(section['title'], int(spec['style']['width'] * 9 / count)),
            ha='center', va='top', fontsize=fs, color=palette[index % len(palette)], fontweight='bold', zorder=4)
        for j, identity in enumerate(section['nodes']):
            y = .69 - j * (.43 / max(1, len(section['nodes']) - 1)) if len(section['nodes']) > 1 else .50
            px = x + width / 2; label = _wrap(nodes[identity]['label'], max(10, int(spec['style']['width'] * 8 / count)))
            if len(label.splitlines()) > 3: raise ValueError('摘要节点文字过长，请缩短文字或增大画布宽度。')
            ax.add_patch(FancyBboxPatch((x + .02, y - .060), width - .04, .12,
                boxstyle='round,pad=.005,rounding_size=.012', facecolor='white', edgecolor='#A4ADB5', lw=.7, zorder=3))
            ax.text(px, y, label, ha='center', va='center', fontsize=fs, zorder=4)
            positions[identity] = (px, y, (width - .04) / 2, .06)
    for edge in spec['diagram']['edges']:
        a, b = positions[edge['from']], positions[edge['to']]
        dx, dy = b[0]-a[0], b[1]-a[1]
        def boundary(box, sign):
            factor = min(box[2]/abs(dx) if dx else float('inf'), box[3]/abs(dy) if dy else float('inf'))
            return (box[0]+sign*dx*factor, box[1]+sign*dy*factor)
        ax.add_patch(FancyArrowPatch(boundary(a, 1), boundary(b, -1), arrowstyle='-|>', mutation_scale=9, color='#677784',
            lw=.9, linestyle='--' if content.get('status', 'plan') == 'plan' else '-', zorder=2,
            connectionstyle='arc3,rad=.02', shrinkA=2, shrinkB=2))
        if edge.get('label'):
            ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + .025,
                _wrap(edge['label'], 18), fontsize=max(6, fs - 2), ha='center', va='bottom', zorder=5,
                bbox={'facecolor': 'white', 'edgecolor': 'none', 'pad': 1})
    summary = _wrap(content.get('summary', ''), int(spec['style']['width'] * 11))
    if len(summary.splitlines()) > 2: raise ValueError('图形摘要说明过长，请精简到两行。')
    ax.text(.5, .08, summary, ha='center', va='center', fontsize=max(7, fs - 1))
    ax.text(0, .99, '研究方案示意 · 待验证' if content.get('status', 'plan') == 'plan' else '研究结果示意 · 作者核对',
        fontsize=max(7, fs - 1), color='#64717E', ha='left', va='top')
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis('off')
    warnings.append('图形摘要是结构化矢量示意，不生成实验原图；节点、箭头与结果表述仍需作者核对。')
    metadata.update(abstract_status=content.get('status', 'plan'), section_count=len(sections), node_count=len(nodes))


def shared_legend(spec, sources):
    """Share only legends whose actual series, order, meaning and colors match."""
    from .plot_engine import _selected_rows, _groups
    from .plot_data import load_table
    reference = None
    for item in spec['composition']['items']:
        child = item['spec']
        if child['kind'] not in {'line', 'scatter'} or not child['mapping'].get('group') or isinstance(child['mapping'].get('y'), list) and len(child['mapping']['y']) != 1:
            raise ValueError('共享图例仅支持使用同一分组字段的折线/散点单序列图；其他图保留各自图例。')
        source = next((s for s in sources if s['id'] in child['source_ids']), None)
        if source is None: raise ValueError('共享图例缺少真实表格来源。')
        mapping = copy.deepcopy(child['mapping'])
        if isinstance(mapping.get('y'),list): mapping['y']=mapping['y'][0]
        rows = _selected_rows(load_table(source['raw'], source['name']), mapping, ['x', 'y'], [])
        labels = list(_groups(rows, child['mapping']['group']))
        signature = (child['kind'], child['mapping']['group'], labels, child['style']['palette'])
        if reference is not None and reference != signature:
            raise ValueError('各子图的图例含义、分组顺序或配色不同，不能合并；请关闭共享图例。')
        reference = signature
    if reference is None or not 1 <= len(reference[2]) <= 8:
        raise ValueError('共享图例需要 1–8 个真实分组。')
    return reference


def _svg_overlay(frame, children):
    """Embed only application-rendered SVG; namespace IDs to avoid clip conflicts."""
    ns = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', ns)
    root = ET.fromstring(frame)
    for index, (raw, rect) in enumerate(children):
        if len(raw) > 20 * 1024 * 1024: raise ValueError('子图 SVG 超过 20 MB，请简化图形。')
        child = ET.fromstring(raw)
        ids = {e.attrib['id']: f'panel{index}_{e.attrib["id"]}' for e in child.iter() if 'id' in e.attrib}
        for element in child.iter():
            if 'id' in element.attrib: element.set('id', ids[element.attrib['id']])
            for key, text in list(element.attrib.items()):
                if key == 'id': continue
                if text.startswith('#') and text[1:] in ids: text = '#' + ids[text[1:]]
                text = re.sub(r'url\(#([^)]*)\)', lambda m: 'url(#' + ids.get(m[1], m[1]) + ')', text)
                element.set(key, text)
        x, y, w, h = rect
        child.set('x', str(x)); child.set('y', str(y))
        child.set('width', str(w)); child.set('height', str(h))
        child.set('preserveAspectRatio', 'xMidYMin meet')
        root.append(child)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def render_composition(spec, sources, render_child, fmt='png', cancel=None):
    from .plot_engine import LOCK, _fonts, _wrap, PALETTES
    from PIL import Image
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.lines import Line2D
    style = spec['style']; items = spec['composition']['items']
    columns = min(spec['composition']['columns'], len(items)); rows = math.ceil(len(items) / columns)
    legend = shared_legend(spec, sources) if spec['composition'].get('shared_legend') else None
    title_space = .5 if spec.get('title') else .12; bottom = .50 if legend else .12; gap = .22; left = .12
    cellw = (style['width'] - 2 * left - gap * (columns - 1)) / columns
    cellh = (style['height'] - title_space - bottom - gap * (rows - 1)) / rows
    if cellw < 1.4 or cellh < 1: raise ValueError('组合图面板空间过小，请减少列数或增加画布尺寸。')
    warnings = []; rendered = []; applied_fonts = []; child_pixels = 0
    for item in items:
        if cancel is not None and cancel.is_set(): raise ValueError('组合图已取消。')
        child = copy.deepcopy(item['spec'])
        if legend: child['style']['legend'] = False
        factor = min(cellw / child['style']['width'], (cellh - .17) / child['style']['height'])
        if spec['composition'].get('fit_fonts', True):
            child['style']['font_size'] = min(24, max(child['style']['font_size'], style['font_size'] / factor))
        applied_fonts.append(child['style']['font_size'])
        selected = [s for s in sources if s['id'] in child['source_ids']]
        result = render_child(child, selected, 'svg' if fmt == 'svg' else 'png')
        if fmt != 'svg':
            with Image.open(io.BytesIO(result['data'])) as image:
                child_pixels += image.width * image.height
                if child_pixels > 48_000_000: raise ValueError('组合子图合计超过 4,800 万像素，请降低单图 DPI 或减少面板。')
                result.setdefault('metadata', {}).update(width_inches=image.width/child['style']['dpi'], height_inches=image.height/child['style']['dpi'])
        rendered.append(result); warnings.extend(result.get('warnings', []))
    with LOCK, matplotlib.rc_context({'font.family': _fonts(), 'font.size': style['font_size'],
            'text.usetex': False, 'text.parse_math': False, 'svg.fonttype': 'path', 'pdf.fonttype': 42}):
        fig = Figure(figsize=(style['width'], style['height']), dpi=style['dpi'])
        FigureCanvasAgg(fig)
        svg_children = []; effective_fonts = []
        try:
            if spec.get('title'):
                fig.text(.5, 1 - .12 / style['height'], _wrap(spec['title'], style['width'] * 11),
                         ha='center', va='top', fontsize=style['font_size'] + 1)
            for index, (item, result) in enumerate(zip(items, rendered)):
                x = left + (index % columns) * (cellw + gap)
                y = style['height'] - title_space - (index // columns + 1) * cellh - (index // columns) * gap
                labelh = .17
                fig.text(x / style['width'], (y + cellh) / style['height'], item.get('label') or chr(65 + index),
                         fontsize=style['font_size'] + 1, fontweight='bold', va='top')
                availh = cellh - labelh
                child_style = item['spec']['style']
                cw = result.get('metadata', {}).get('width_inches', child_style['width'])
                ch = result.get('metadata', {}).get('height_inches', child_style['height'])
                factor = min(cellw / cw, availh / ch)
                effective_fonts.append(round(applied_fonts[index] * factor, 2))
                if fmt == 'svg':
                    svg_children.append((result['data'], (x * 72, (style['height'] - y - availh) * 72, cellw * 72, availh * 72)))
                else:
                    image = Image.open(io.BytesIO(result['data'])); image.load()
                    ax = fig.add_axes([x / style['width'], y / style['height'], cellw / style['width'], availh / style['height']])
                    ax.imshow(image, aspect='equal', interpolation='none'); ax.set_anchor('N'); ax.axis('off')
            if legend:
                handles = [Line2D([], [], color=PALETTES[legend[3]][i % len(PALETTES[legend[3]])],
                                  marker='o' if legend[0] == 'scatter' else None,
                                  linestyle='none' if legend[0] == 'scatter' else '-', label=name) for i, name in enumerate(legend[2])]
                fig.legend(handles=handles, loc='lower center', ncol=min(4, len(handles)), frameon=False,
                           fontsize=style['font_size'], bbox_to_anchor=(.5, .01))
            out = io.BytesIO(); fig.savefig(out, format=fmt, dpi=style['dpi'], facecolor='white',
                **({'metadata': {'Creator': 'Yanxu paper composition'}} if fmt in {'svg', 'pdf'} else {}))
            raw = _svg_overlay(out.getvalue(), svg_children) if fmt == 'svg' else out.getvalue()
        finally: fig.clear()
    if min(effective_fonts) < 7:
        warnings.append('组合后部分子图字号小于 7 pt，请减少面板、增加尺寸或先放大子图字体。')
    if fmt in {'pdf', 'tiff', 'png'}:
        warnings.append('组合图的当前格式采用原样栅格面板；需要矢量组合请导出 SVG，交付包同时保留各单图矢量与配方。')
    return {'data': raw, 'mime': {'png': 'image/png', 'svg': 'image/svg+xml', 'pdf': 'application/pdf', 'tiff': 'image/tiff'}[fmt],
        'warnings': list(dict.fromkeys(warnings)), 'caption': '组合图。各面板保留所选子图的固定版本，面板标记位于图外；请核对图注。',
        'metadata': {'engine': 'yanxu-paper-composition', 'engine_version':'1.0','model_called': False, 'analysis_performed': False,
            'width_inches': style['width'], 'height_inches': style['height'], 'dpi': style['dpi'],
            'effective_min_font_pt': min(effective_fonts), 'panel_count': len(items), 'format': fmt,
            'vector_panels': fmt == 'svg', 'children': [{'figure_id': i['figure_id'], 'revision': i['revision'],
                'spec_sha256': spec_digest(i['spec']), 'render_font_pt':round(applied_fonts[index],2)} for index,i in enumerate(items)]}}
