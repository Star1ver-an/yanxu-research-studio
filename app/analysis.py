"""Versioned dispatch to reviewed local kernels; never executes generated code."""
import importlib
import importlib.util
import json
import math

VERSION = '1'
MODULES = ('analysis_clinical', 'analysis_meta', 'analysis_omics', 'analysis_cell', 'analysis_flow')
DEPENDENCIES = {
    'km': ['lifelines'], 'cif': ['lifelines'], 'cox': ['lifelines'],
    'binary_evaluation': ['sklearn', 'scipy'], 'logistic_model': ['sklearn', 'scipy'],
    'meta_forest': ['statsmodels', 'scipy'], 'meta_funnel': ['statsmodels', 'scipy'],
    'meta_sensitivity': ['statsmodels', 'scipy'], 'meta_baujat': ['statsmodels', 'scipy'],
    'expression_pca': ['sklearn', 'scipy'], 'differential_expression': ['scipy'],
    'deseq2': ['pydeseq2'], 'enrichment_ora': ['scipy'], 'gsea_prerank': ['gseapy'],
    'cell_embedding': ['sklearn', 'scipy'], 'cell_expression': ['scipy'],
    'cell_composition': ['scipy'], 'spatial_expression': ['scipy'],
    'variant_summary': [], 'flow_gating': ['flowio'],
}


def modules():
    return [importlib.import_module('.' + name, __package__) for name in MODULES]


def catalog(capabilities=None):
    result = []
    for module in modules():
        for item in module.catalog():
            required = DEPENDENCIES.get(item['id'], []) + item.get('optional_dependencies', [])
            missing = [name for name in dict.fromkeys(required)
                       if not (capabilities.get(name, False) if capabilities is not None else importlib.util.find_spec(name) is not None)]
            result.append(dict(item, available=not missing, missing_dependencies=missing))
    return result


def kernel(config):
    for module in modules():
        if any(x['id'] == config.get('method') for x in module.catalog()):
            return module
    raise ValueError('请选择受支持的数据分析方法。')


def validate(config):
    if not isinstance(config, dict):
        raise ValueError('分析配置必须为结构化对象。')
    try:
        encoded = json.dumps(config, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('分析配置含无效值。') from None
    if len(encoded) > 80000:
        raise ValueError('分析参数过长，请使用材料文件提供大量输入。')
    return kernel(config).validate(config)


def checked_result(result):
    """Reject non-finite/nonportable kernel outputs before disk or UI exposure."""
    def walk(value, depth=0):
        if depth > 30:
            raise ValueError('分析结果嵌套过深。')
        if value is None or isinstance(value, (str, bool, int)):
            return
        if isinstance(value, float) and math.isfinite(value):
            return
        if isinstance(value, list):
            for v in value: walk(v, depth+1)
            return
        if isinstance(value, dict) and all(isinstance(k, str) for k in value):
            for v in value.values(): walk(v, depth+1)
            return
        raise ValueError('分析返回了无效或非有限结果；未保存结果。')
    walk(result)
    if not isinstance(result, dict) or not isinstance(result.get('tables'), dict):
        raise ValueError('分析结果结构不完整。')
    if not isinstance(result.get('method'),str) or len(result['method'])>80 or len(result['tables'])>60:
        raise ValueError('分析方法或结果表数量无效。')
    for name,table in result['tables'].items():
        if len(name)>200 or not isinstance(table,dict) or set(table)-{'columns','rows'}:
            raise ValueError('分析结果表结构无效。')
        columns,rows=table.get('columns'),table.get('rows')
        if not isinstance(columns,list) or len(columns)>300 or any(not isinstance(c,str) or not c or len(c)>300 for c in columns) or len(set(columns))!=len(columns):
            raise ValueError('分析结果列名无效。')
        if not isinstance(rows,list) or len(rows)>200000 or any(not isinstance(r,dict) or set(r)-set(columns) for r in rows):
            raise ValueError('分析结果行结构无效。')
    for field in ('warnings','notes'):
        if not isinstance(result.get(field,[]),list) or any(not isinstance(s,str) or len(s)>5000 for s in result.get(field,[])):
            raise ValueError('分析结果说明无效。')
    if not isinstance(result.get('statistics',{}),dict) or not isinstance(result.get('software',{}),dict):
        raise ValueError('分析统计摘要或软件版本无效。')
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError('分析结果超过 20 MB，请缩小数据或导出的结果数量。')
    return raw


def compute(config, sources, cancel=None, progress=None):
    config = validate(config)
    result = kernel(config).compute(config, sources, cancel=cancel, progress=progress)
    checked_result(result)
    return result


def draw(result, config, style, title=''):
    return kernel(config).draw(result, config, style, title)


def render(result, config, style, title='', fmt='png'):
    import io
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from .plot_engine import LOCK, _fonts
    if fmt not in {'png', 'svg', 'pdf', 'tiff'}: raise ValueError('分析图导出格式无效。')
    with LOCK, matplotlib.rc_context({'font.family': _fonts(), 'font.size': style['font_size'],
            'axes.unicode_minus': False, 'text.usetex': False, 'text.parse_math': False,
            'svg.fonttype': 'path', 'pdf.fonttype': 42, 'figure.facecolor': 'white',
            'savefig.facecolor': 'white', 'axes.spines.top': False, 'axes.spines.right': False}):
        fig = draw(result, config, style, title)
        FigureCanvasAgg(fig)
        try:
            fig.set_size_inches(style['width'], min(16,max(style['height'],fig.get_size_inches()[1])))
            fig.set_dpi(style['dpi'])
            if fig.axes:
                ax=fig.axes[0]
                if style.get('xlabel'): ax.set_xlabel(style['xlabel'])
                if style.get('ylabel'): ax.set_ylabel(style['ylabel'])
                if style.get('xlim'): ax.set_xlim(*style['xlim'])
                if style.get('ylim'): ax.set_ylim(*style['ylim'])
            if fig.get_size_inches().prod()*style['dpi']**2>24_000_000:
                raise ValueError('扩展后的分析图超过 2,400 万像素，请降低 DPI 或缩小画布宽度。')
            output = io.BytesIO()
            options = {'metadata': {'Creator': 'Yanxu local analysis'}} if fmt in {'pdf', 'svg'} else {}
            fig.savefig(output, format=fmt, dpi=style['dpi'], **options)
            return output.getvalue()
        finally:
            fig.clear()
