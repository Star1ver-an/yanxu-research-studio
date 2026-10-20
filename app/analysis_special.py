"""Bounded scientific-file readers and shared, declarative analysis primitives.

No archive is extracted, no user expression/formula is evaluated, and sparse
matrices are never implicitly converted to dense matrices.
"""
import copy
import gzip
import hashlib
import importlib.metadata
import io
import json
import math
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

from .plot_data import load_table, MAX_BYTES

MAX_DENSE = 5_000_000
MAX_NNZ = 5_000_000
MAX_OBS = 50_000
MAX_VAR = 30_000
MAX_EXPANDED = 128 * 1024 * 1024
SPECIAL_SUFFIXES = {'.h5ad', '.zip', '.gmt', '.fcs', '.vcf'}


def dependency(module, distribution=None):
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError:
        raise ValueError('此方法需要可选分析组件 ' + (distribution or module) + '；请在分析环境中安装后运行。') from None


def software(*packages):
    result = {}
    for name in packages:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def checkpoint(cancel=None, progress=None, message='正在分析'):
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise ValueError('分析已取消。')
    if progress is not None:
        progress(message)


def table(rows, columns=None):
    return {'columns': columns or (list(rows[0]) if rows else []), 'rows': rows}


def result(method, tables, statistics=None, warnings=None, notes=None, packages=()):
    out = {'method': method, 'tables': tables, 'statistics': statistics or {},
           'warnings': warnings or [], 'notes': notes or [], 'software': software(*packages)}
    try:
        encoded = json.dumps(out, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('分析结果含无效数值，请检查输入和方法适用条件。') from None
    if len(encoded.encode('utf-8')) > 20 * 1024 * 1024:
        raise ValueError('分析结果超过 20 MB，请减少观测数量或选择较少特征。')
    return out


def field(key, label, type='any', required=False, multiple=False, source='data'):
    return dict(key=key, label=label, type=type, required=required, multiple=multiple, source=source)


def param(key, label, type='text', default='', **extra):
    return dict(key=key, label=label, type=type, default=default, **extra)


def select(key, label, values, default):
    return param(key, label, 'select', default, options=[{'value': a, 'label': b} for a, b in values])


def input_file(key='data', label='数据材料', formats=None, required=True):
    return dict(key=key, label=label, formats=formats or ['csv', 'tsv', 'xlsx'], required=required)


def method(ident, label, family, description, inputs, columns=(), parameters=(), limitations=(), dependencies=()):
    return dict(id=ident, label=label, family=family, description=description, inputs=list(inputs),
                columns=list(columns), parameters=list(parameters), limitations=list(limitations),
                optional_dependencies=list(dependencies))


def validate_config(config, catalog):
    if not isinstance(config, dict) or set(config) - {'method', 'inputs', 'columns', 'parameters'}:
        raise ValueError('分析配置必须是含 method、inputs、columns、parameters 的结构化对象。')
    try:
        encoded = json.dumps(config, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('分析配置含无效数值或结构。') from None
    if len(encoded) > 100000:
        raise ValueError('分析配置过大。')
    entry = next((x for x in catalog if x['id'] == config.get('method')), None)
    if entry is None:
        raise ValueError('请选择支持的分析方法。')
    out = copy.deepcopy(config)
    for key in ['inputs', 'columns', 'parameters']:
        out.setdefault(key, {})
        if not isinstance(out[key], dict):
            raise ValueError(key + ' 必须是对象。')
    for key, definitions in [('inputs', entry['inputs']), ('columns', entry['columns']), ('parameters', entry['parameters'])]:
        defs = {d['key']: d for d in definitions}
        if set(out[key]) - set(defs):
            raise ValueError(key + ' 含未知字段。')
        for name, d in defs.items():
            value = out[key].get(name)
            if key == 'parameters' and value is None:
                value = copy.deepcopy(d['default']); out[key][name] = value
            if value is None or value == '' or value == []:
                if d.get('required'):
                    raise ValueError('请配置：' + d['label'])
                if key in {'inputs', 'columns'}:
                    out[key].pop(name, None)
                continue
            if key == 'columns' and d.get('multiple'):
                if not isinstance(value, list) or len(value) > 200 or any(not isinstance(x, str) or not x or len(x) > 200 for x in value) or len(set(value)) != len(value):
                    raise ValueError(d['label'] + ' 列选择无效。')
            elif key in {'inputs', 'columns'}:
                if not isinstance(value, str) or len(value) > 200:
                    raise ValueError(d['label'] + ' 必须为有效文本。')
            elif d['type'] in {'number', 'integer'}:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (d['type'] == 'integer' and type(value) is not int):
                    raise ValueError(d['label'] + ' 必须为有限数值。')
                if value < d.get('min', -math.inf) or value > d.get('max', math.inf):
                    raise ValueError(d['label'] + ' 超出允许范围。')
            elif d['type'] == 'boolean':
                if type(value) is not bool:
                    raise ValueError(d['label'] + ' 必须为开关。')
            elif d['type'] == 'select':
                if value not in [v['value'] for v in d['options']]:
                    raise ValueError(d['label'] + ' 选项无效。')
            elif not isinstance(value, str) or len(value) > 50000:
                raise ValueError(d['label'] + ' 必须为长度合理的文本。')
    return out


def source(config, sources, key='data'):
    ident = config['inputs'].get(key)
    matches = [x for x in sources if x.get('id') == ident]
    if len(matches) != 1:
        raise ValueError('未找到唯一的 ' + key + ' 数据材料，请重新选择。')
    item = matches[0]
    raw, name = item.get('raw'), item.get('name')
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES or not isinstance(name, str):
        raise ValueError('分析材料无效，单文件最多 20 MB。')
    return item


def read_table(config, sources, key='data'):
    s = source(config, sources, key)
    if Path(s['name']).suffix.lower() == '.vcf':
        return read_vcf(s['raw'])
    return load_table(s['raw'], s['name'])


def numbers(values, label='数据'):
    import numpy as np
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError(label + ' 含缺失、非数值或非有限值；请先清洗，不会自动填补。')
    if any(abs(x) > 1e100 for x in values):
        raise ValueError(label + ' 的数值量级过大，可能导致溢出；请检查单位与数据。')
    return np.asarray(values, dtype=float)


def bh(pvalues):
    import numpy as np
    p = np.asarray(pvalues, dtype=float)
    if p.size == 0:
        return []
    order = np.argsort(p, kind='stable')
    adj = np.minimum.accumulate((p[order] * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    out = np.empty_like(p); out[order] = np.minimum(adj, 1)
    return out.tolist()


def _text(raw):
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('科研专用格式请保存为 UTF-8 编码。') from None


def read_gmt(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('GMT 文件为空或超过 20 MB。')
    sets = {}
    memberships = 0
    for line in _text(raw).splitlines():
        if not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) < 3 or not fields[0] or fields[0] in sets:
            raise ValueError('GMT 每行需要唯一基因集名称、描述和至少一个基因。')
        if any(len(x) > 500 for x in fields) or len(sets) >= 10000:
            raise ValueError('GMT 名称过长或基因集超过 10,000。')
        genes = set(x.strip() for x in fields[2:] if x.strip())
        memberships += len(genes)
        if memberships > 1_000_000:
            raise ValueError('GMT 总基因成员超过 1,000,000，请减少基因集。')
        sets[fields[0]] = genes
    if not sets:
        raise ValueError('GMT 没有可用的基因集。')
    return sets


def _bounded_gzip(raw):
    if raw[:2] != b'\x1f\x8b':
        return raw
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            out = stream.read(MAX_EXPANDED + 1)
        if len(out) > MAX_EXPANDED:
            raise ValueError('压缩矩阵展开后超过 128 MB。')
        return out
    except (OSError, EOFError):
        raise ValueError('矩阵中的 gzip 文件损坏。') from None


def _10x_parts(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('10x ZIP 文件超过 20 MB。')
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError('10x ZIP 文件无效。') from None
    with z:
        infos = z.infolist()
        if len(infos) > 30 or sum(x.file_size for x in infos) > MAX_EXPANDED:
            raise ValueError('10x ZIP 内容过多或解压后超过 128 MB。')
        files = {}
        for item in infos:
            path = PurePosixPath(item.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or ':' in item.filename or item.flag_bits & 1:
                raise ValueError('10x ZIP 含不安全路径或加密条目。')
            if item.is_dir():
                continue
            name = path.name
            base = name[:-3] if name.endswith('.gz') else name
            if base not in {'matrix.mtx', 'features.tsv', 'genes.tsv', 'barcodes.tsv'}:
                raise ValueError('10x ZIP 只允许 matrix.mtx、features.tsv/genes.tsv、barcodes.tsv（可 gzip）。')
            if base in files:
                raise ValueError('10x ZIP 包含重复矩阵文件。')
            files[base] = _bounded_gzip(z.read(item))
            if sum(map(len, files.values())) > MAX_EXPANDED:
                raise ValueError('10x ZIP 所有内容展开后超过 128 MB。')
        features = files.get('features.tsv', files.get('genes.tsv'))
        if features is None or 'matrix.mtx' not in files or 'barcodes.tsv' not in files:
            raise ValueError('10x ZIP 缺少矩阵、特征或条形码文件。')
        matrix = files['matrix.mtx']
        lines = matrix.splitlines()
        if not lines or not lines[0].lower().startswith(b'%%matrixmarket matrix coordinate'):
            raise ValueError('10x 需要稀疏 coordinate MatrixMarket 格式。')
        if b' general' not in lines[0].lower() or not any(t in lines[0].lower() for t in [b' integer ', b' real ']):
            raise ValueError('10x 矩阵须为 general integer/real coordinate 格式。')
        dimensions = next((x for x in lines[1:] if x.strip() and not x.startswith(b'%')), b'')
        try:
            nvar, nobs, nnz = map(int, dimensions.split())
        except ValueError:
            raise ValueError('MatrixMarket 尺寸无效。') from None
        if not (1 <= nobs <= MAX_OBS and 1 <= nvar <= MAX_VAR and 0 <= nnz <= MAX_NNZ):
            raise ValueError('稀疏矩阵最多 50,000 个细胞、30,000 个特征、5,000,000 个非零值。')
        genes = [x.split('\t')[0] for x in _text(features).splitlines() if x.strip()]
        cells = [x.strip() for x in _text(files['barcodes.tsv']).splitlines() if x.strip()]
        if len(genes) != nvar or len(cells) != nobs or len(set(genes)) != len(genes) or len(set(cells)) != len(cells):
            raise ValueError('10x 特征/条形码数量与矩阵不一致，或 ID 重复。')
        return matrix, genes, cells, nnz


def read_10x(raw):
    import numpy as np
    from scipy.io import mmread
    matrix_raw, genes, cells, nnz = _10x_parts(raw)
    matrix = mmread(io.BytesIO(matrix_raw)).T.tocsr()
    if matrix.nnz > MAX_NNZ or not np.isfinite(matrix.data).all() or (matrix.data < 0).any():
        raise ValueError('10x 计数须为有限非负值，且稀疏元素数不能超限。')
    if matrix.shape != (len(cells), len(genes)):
        raise ValueError('10x 矩阵尺寸不一致。')
    return matrix, cells, genes, None


def _h5ad_preflight(path):
    import numpy as np
    h5py = dependency('h5py', 'anndata')
    total = [0, 0]
    def walk(group, depth=0):
        if depth > 20:
            raise ValueError('H5AD 嵌套过深或存在循环组链接。')
        for key in group:
            link = group.get(key, getlink=True)
            if not isinstance(link, h5py.HardLink):
                raise ValueError('H5AD 不允许外部或软链接。')
            item = group[key]
            total[1] += 1
            if total[1] > 2000:
                raise ValueError('H5AD 数据集过多。')
            if isinstance(item, h5py.Dataset):
                logical = math.prod(item.shape) * max(item.dtype.itemsize, 8)
                total[0] += logical
                if item.is_virtual or item.external or logical > MAX_EXPANDED or total[0] > MAX_EXPANDED:
                    raise ValueError('H5AD 展开尺寸超过 128 MB 或包含外部/虚拟数据集。')
                vlen = h5py.check_vlen_dtype(item.dtype)
                if vlen is not None and vlen not in {str, bytes}:
                    raise ValueError('H5AD 不支持任意变长数值对象，请使用标准稀疏矩阵和注释。')
                if vlen in {str, bytes}:
                    # Reading one entry at a time bounds vlen-heap allocation;
                    # repeated references cannot evade the logical byte budget.
                    if item.ndim != 1 or len(item) > MAX_OBS:
                        raise ValueError('H5AD 变长文本注释过多或维度无效。')
                    for i in range(len(item)):
                        value = item[i]
                        size = len(value.encode('utf-8')) if isinstance(value, str) else len(value)
                        total[0] += size
                        if size > 10000 or total[0] > MAX_EXPANDED:
                            raise ValueError('H5AD 文本单元过长或展开内容超过 128 MB。')
            else:
                walk(item, depth + 1)
    with h5py.File(path, 'r') as file:
        walk(file)
        if 'X' not in file:
            raise ValueError('H5AD 缺少表达矩阵 X。')
        x = file['X']
        shape = tuple(x.attrs.get('shape', ())) if isinstance(x, h5py.Group) else x.shape
        if len(shape) != 2 or not (1 <= shape[0] <= MAX_OBS and 1 <= shape[1] <= MAX_VAR):
            raise ValueError('H5AD 最多 50,000 个观测和 30,000 个特征。')
        if isinstance(x, h5py.Group):
            encoding = x.attrs.get('encoding-type')
            if encoding not in {'csr_matrix', 'csc_matrix'} or not {'data', 'indices', 'indptr'} <= set(x) or len(x['data']) > MAX_NNZ:
                raise ValueError('H5AD 稀疏格式无效或非零值超过 5,000,000。')
            if any(x[k].ndim != 1 for k in ['data', 'indices', 'indptr']) or len(x['data']) != len(x['indices']):
                raise ValueError('H5AD 稀疏数组结构无效。')
            size, bound = (shape[0], shape[1]) if encoding == 'csr_matrix' else (shape[1], shape[0])
            if len(x['indptr']) != size + 1 or x['indices'].dtype.kind not in 'iu' or x['indptr'].dtype.kind not in 'iu' or x['data'].dtype.kind not in 'iuf':
                raise ValueError('H5AD 稀疏指针或数值类型无效。')
            pointers, indices = x['indptr'][:], x['indices'][:]
            if pointers[0] != 0 or pointers[-1] != len(x['data']) or np.any(pointers[1:] < pointers[:-1]) or np.any(indices < 0) or np.any(indices >= bound):
                raise ValueError('H5AD 稀疏索引越界或指针不单调。')
        elif math.prod(shape) > MAX_DENSE:
            raise ValueError('H5AD 稠密矩阵超过 5,000,000 个元素，请使用稀疏矩阵。')
        elif x.dtype.kind not in 'iuf':
            raise ValueError('H5AD 表达矩阵必须为数值。')
        return shape


def read_h5ad(raw):
    import numpy as np
    anndata = dependency('anndata')
    with tempfile.TemporaryDirectory(prefix='yanxu-h5ad-') as directory:
        path = Path(directory) / 'source.h5ad'; path.write_bytes(raw)
        _h5ad_preflight(path)
        data = anndata.read_h5ad(path, backed='r')
        try:
            matrix = data.X.to_memory() if hasattr(data.X, 'to_memory') else data.X[:]
            values = matrix.data if hasattr(matrix, 'tocsr') else matrix
            if not np.isfinite(values).all():
                raise ValueError('H5AD X 含非有限数值。')
            if not data.obs_names.is_unique or not data.var_names.is_unique:
                raise ValueError('H5AD 观测和特征 ID 必须唯一。')
            obs = data.obs.copy()
            if '_observation_id' in obs.columns:
                raise ValueError('H5AD obs 已使用保留列 _observation_id，请先重命名该注释列。')
            obs.insert(0, '_observation_id', list(map(str, data.obs_names)))
            return matrix, list(map(str, data.obs_names)), list(map(str, data.var_names)), obs
        finally:
            data.file.close()


def read_matrix(config, sources):
    import numpy as np
    s = source(config, sources)
    suffix = Path(s['name']).suffix.lower()
    if suffix == '.zip':
        return read_10x(s['raw'])
    if suffix == '.h5ad':
        return read_h5ad(s['raw'])
    data = read_table(config, sources)
    columns = config['columns']
    ident = columns.get('id') or data['columns'][0]['name']
    if ident not in data['rows'][0]:
        raise ValueError('矩阵 ID 列不存在。')
    features = columns.get('features') or [x['name'] for x in data['columns'] if x['name'] != ident and x['type'] == 'numeric']
    if not features or any(x not in data['rows'][0] for x in features):
        raise ValueError('请至少选择一个存在的数值特征列。')
    if len(data['rows']) * len(features) > MAX_DENSE:
        raise ValueError('矩阵最多 5,000,000 个元素，请选取特征或使用稀疏格式。')
    ids = [str(x[ident]) for x in data['rows']]
    if any(x[ident] is None for x in data['rows']) or len(set(ids)) != len(ids):
        raise ValueError('矩阵观测/基因 ID 不能为空或重复。')
    matrix = np.column_stack([numbers([r[x] for r in data['rows']], x) for x in features])
    if config['parameters'].get('orientation', 'samples_rows') == 'genes_rows':
        return matrix.T, features, ids, None
    return matrix, ids, features, None


def read_vcf(raw):
    rows, header = [], None
    for line in _text(raw).splitlines():
        if line.startswith('##') or not line:
            continue
        if line.startswith('#CHROM'):
            header = line[1:].split('\t'); continue
        if line.startswith('#'):
            continue
        if header is None:
            raise ValueError('VCF 缺少 #CHROM 列名。')
        values = line.split('\t')
        if len(values) != len(header) or len(rows) >= 50000:
            raise ValueError('VCF 行列不一致或记录超过 50,000。')
        record = dict(zip(header, values))
        try:
            record['POS'] = int(record['POS'])
        except (ValueError, KeyError):
            raise ValueError('VCF POS 必须是正整数。') from None
        if record['POS'] < 1:
            raise ValueError('VCF POS 必须为正整数。')
        # Never infer gene annotations or affected genotype from unannotated VCF.
        rows.append(record)
    if not rows:
        raise ValueError('VCF 没有变异记录。')
    return {'rows': rows, 'columns': [{'name': x, 'type': 'numeric' if x == 'POS' else 'text'} for x in header],
            'row_count': len(rows), 'warnings': ['VCF 按已有位点读取；不会推断基因注释、致病性或进行变异 calling。']}


def inspect_special(raw, name):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ValueError('科研数据文件为空或超过 20 MB。')
    suffix = Path(name).suffix.lower()
    out = dict(kind='scientific', format=suffix.lstrip('.'), name=name,
               sha256=hashlib.sha256(raw).hexdigest(), parsed=True, warnings=[], columns=[], preview=[])
    if suffix == '.gmt':
        sets = read_gmt(raw)
        out.update(gene_sets=len(sets), memberships=sum(map(len, sets.values())),
                   preview=[{'set': k, 'genes': len(v)} for k, v in list(sets.items())[:8]])
    elif suffix == '.zip':
        _, genes, cells, nnz = _10x_parts(raw)
        out.update(row_count=len(cells), features=len(genes), nonzero=nnz, orientation='cells_by_genes')
    elif suffix == '.h5ad':
        try:
            with tempfile.TemporaryDirectory(prefix='yanxu-inspect-') as directory:
                path = Path(directory) / 'source.h5ad'; path.write_bytes(raw)
                shape = _h5ad_preflight(path)
            out.update(row_count=int(shape[0]), features=int(shape[1]))
        except ValueError as e:
            if '可选分析组件' not in str(e):
                raise
            out.update(parsed=False, warnings=[str(e), '文件已保存；尚未解析，安装组件后仍需校验。'])
    elif suffix == '.vcf':
        data = read_vcf(raw)
        out.update(columns=data['columns'], row_count=data['row_count'], preview=data['rows'][:8], warnings=data['warnings'])
    elif suffix == '.fcs':
        from .analysis_flow import inspect_fcs
        try:
            out.update(inspect_fcs(raw))
        except ValueError as e:
            if '可选分析组件' not in str(e):
                raise
            out.update(parsed=False, warnings=[str(e), '文件已保存；尚未解析，安装组件后仍需校验。'])
    else:
        raise ValueError('此科研数据格式暂不支持。')
    return out


def figure(style, title='', rows=1, cols=1):
    from matplotlib.figure import Figure
    # Parent renderer validates style and manages font state and serialization.
    fig = Figure(figsize=(style.get('width', 7), style.get('height', 4.8)),
                 dpi=style.get('dpi', 120), layout='constrained')
    if title:
        fig.suptitle(title)
    return fig, fig.subplots(rows, cols, squeeze=False)


def palette(style):
    colors = {'muted': ['#385E85', '#BC7052', '#54836B', '#9B769E', '#B29C50', '#557F8B'],
              'accessible': ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#56B4E9'],
              'gray': ['#3E4A57', '#788591', '#A0ABB5', '#BCC5CD']}
    return colors.get(style.get('palette'), colors['muted'])
