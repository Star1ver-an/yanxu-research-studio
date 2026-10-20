"""Cell QC/embedding and sample-aware descriptive single-cell/spatial summaries."""
import math
from collections import Counter, defaultdict

from .analysis_special import (checkpoint, dependency, field, figure, input_file, method,
                               numbers, palette, param, read_matrix, read_table, result,
                               select, table, validate_config)
from .analysis_omics import MATRIX_INPUT, MATRIX_FIELDS, METADATA_FIELDS, ORIENTATION, metadata, pca_matrix


def catalog():
    return [
        method('cell_embedding', '单细胞 QC 与降维', 'single_cell', '真实表达矩阵经显式 QC、总量归一化、log1p、PCA，再选择 t-SNE 或 UMAP。',
               [MATRIX_INPUT, input_file('metadata', '已有细胞注释（可选）', required=False)], MATRIX_FIELDS + METADATA_FIELDS,
               [ORIENTATION, select('data_scale', '输入数据类型', [('raw_counts', '非负整数原始 counts'), ('normalized', '已归一化非负表达')], 'raw_counts'),
                param('min_genes', '每细胞最少表达基因数', 'integer', 1, min=1, max=30000),
                param('min_counts', '每细胞最少总 counts/表达', 'number', 1, min=0, max=1e12),
                param('min_cells', '每基因最少检出细胞数', 'integer', 1, min=1, max=50000),
                param('mitochondrial_prefix', '线粒体基因 ID 前缀（按数据真实 ID）', 'text', 'MT-'),
                param('max_mito_percent', '线粒体表达百分比上限', 'number', 100, min=0, max=100),
                param('normalize_total', '每细胞总量归一化（只适用原始 counts）', 'boolean', True),
                param('target_sum', '归一化目标总量', 'number', 10000, min=1, max=1e9),
                param('log1p', '归一化后 log1p', 'boolean', True), param('scale', '按基因方差缩放', 'boolean', False),
                param('max_features', '最多保留高方差基因数', 'integer', 2000, min=2, max=5000),
                param('n_pcs', '降维 / 邻居图使用 PCA 分量数（受数据尺寸限制）', 'integer', 20, min=2, max=50),
                select('embedding', '降维方法', [('pca', 'PCA'), ('tsne', 't-SNE（最多 5,000 细胞）'), ('umap', 'UMAP（可选组件）')], 'pca'),
                param('perplexity', 't-SNE perplexity', 'number', 30, min=1, max=100),
                param('n_neighbors', 'UMAP / Leiden 邻居数', 'integer', 15, min=2, max=100),
                param('cluster', '可选 Scanpy Leiden 聚类（不是细胞类型注释）', 'boolean', False),
                param('resolution', 'Leiden 分辨率', 'number', 1, min=.01, max=10),
                param('seed', '随机种子', 'integer', 0, min=0, max=2147483647)],
               ['PCA/t-SNE 可在基础环境运行；UMAP/Scanpy 需安装可选组件。', '不自动识别细胞类型，不执行差异表达或批次校正。',
                '高方差排序为透明方差筛选，不冒充 Seurat 的 highly_variable_genes。', '线粒体筛选须使用文件真实基因 ID 前缀；跨物种/Ensembl 不自动猜测。']),
        method('cell_expression', '细胞表达摘要（按独立样本）', 'single_cell', '从带真实样本/细胞类型注释的长表汇总每样本平均表达与阳性比例。',
               [input_file()], [field('cell_id', '细胞 ID 列', required=True), field('sample', '独立样本 ID 列', required=True),
                field('cell_type', '已有细胞类型注释列', required=True), field('gene', '基因 ID 列', required=True),
                field('value', '已有归一化表达列', 'numeric', required=True)],
               [param('positive_threshold', '表达阳性阈值（严格大于）', 'number', 0, min=-1e12, max=1e12)],
               ['仅描述汇总，不将细胞当作独立生物重复做统计检验。', '跨样本图中每个独立样本等权；不推断细胞类型。']),
        method('cell_composition', '细胞组成（逐独立样本）', 'single_cell', '按每个真实样本计算已有细胞类型注释的计数与比例。',
               [input_file()], [field('cell_id', '细胞 ID 列', required=True), field('sample', '独立样本 ID 列', required=True),
                field('cell_type', '已有细胞类型注释列', required=True), field('group', '样本分组（可选）')], [],
               ['每样本分母为本表该样本全部已注释细胞；缺失注释会拒绝运行。', '不将 pooled cell fraction 当作样本重复；不进行组间显著性推断。']),
        method('spatial_expression', '空间表达（真实坐标）', 'spatial', '将已有表达量映射至用户提供的真实空间点坐标。',
               [input_file()], [field('id', '空间点 ID 列', required=True), field('x', '真实 X 坐标', 'numeric', required=True),
                field('y', '真实 Y 坐标', 'numeric', required=True), field('value', '已有表达量', 'numeric', required=True),
                field('section', '切片 ID（多切片时必须指定）')],
               [param('section', '本次切片 ID（多切片时填写）'), param('invert_y', 'Y 轴向下（图像像素坐标）', 'boolean', False),
                param('point_size', '空间点大小', 'number', 15, min=1, max=200)],
               ['只使用真实坐标，不生成空间位置或组织背景。', '输入为已有表达；不做空间配准、解卷积或空间统计推断。']),
        method('variant_summary', '已有变异 / CNV 结果图', 'genomics', '绘制已有基因变异的样本矩阵、位点 lollipop 或已有 CNV 分段。',
               [input_file(formats=['csv', 'tsv', 'xlsx', 'vcf'])],
               [field('sample', '样本 ID（矩阵图必须）'), field('gene', '已有基因注释（矩阵图必须）'),
                field('variant_type', '已有变异/CNV 类型（可选）'), field('chromosome', '染色体列'),
                field('position', '真实位点坐标', 'numeric'), field('end', 'CNV 终点坐标', 'numeric'),
                field('value', '已有 CNV log2 ratio', 'numeric')],
               [select('view', '绘图视图', [('oncoplot', '基因 × 样本变异矩阵'), ('lollipop', '真实位点 lollipop'), ('cnv', '已有 CNV 分段')], 'oncoplot'),
                param('chromosome', '单一染色体（多个时必须填写）'), param('sample', '单一样本（CNV 多样本时必须填写）'),
                param('top_genes', '最多显示基因数', 'integer', 30, min=1, max=60)],
               ['仅映射已有结果，不进行变异 calling、致病性推断或 CNV 检测。', '未注释 VCF 仅支持位点图；不会根据坐标臆测基因或样本携带状态。']),
    ]


def validate(config):
    config = validate_config(config, catalog())
    p, cols = config['parameters'], config['columns']
    if config['method'] == 'cell_embedding' and p['data_scale'] == 'normalized' and p['normalize_total']:
        raise ValueError('已归一化表达请关闭再次总量归一化；确认是否仍需要 log1p。')
    if config['method'] == 'variant_summary':
        needed = ['sample', 'gene'] if p['view'] == 'oncoplot' else ['position'] + (['end', 'value'] if p['view'] == 'cnv' else [])
        if any(not cols.get(k) for k in needed):
            raise ValueError('请为选择的变异视图配置所需列：' + '、'.join(needed))
    return config


def _embedding(config, sources, cancel, progress):
    import numpy as np
    from scipy import sparse
    matrix, ids, genes, embedded = read_matrix(config, sources)
    p, cols = config['parameters'], config['columns']
    aligned = metadata(config, sources, ids, embedded)
    x = matrix.astype(float).copy(); issparse = sparse.issparse(x)
    values = x.data if issparse else x
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('单细胞 QC 需要有限非负表达；负值的 scaled 数据不能用于 counts QC。')
    if p['data_scale'] == 'raw_counts' and not np.equal(values, np.floor(values)).all():
        raise ValueError('原始 counts 必须是非负整数；已归一化表达请选择相应输入类型。')
    totals = np.asarray(x.sum(axis=1)).ravel()
    detected = np.asarray((x > 0).sum(axis=1)).ravel()
    mito = np.asarray([g.upper().startswith(p['mitochondrial_prefix'].upper()) if p['mitochondrial_prefix'] else False for g in genes])
    mito_total = np.asarray(x[:, mito].sum(axis=1)).ravel() if mito.any() else np.zeros(len(ids))
    percent = np.divide(mito_total * 100, totals, out=np.zeros_like(totals), where=totals != 0)
    if not mito.any() and p['max_mito_percent'] < 100:
        raise ValueError('真实基因 ID 没有匹配线粒体前缀；无法应用线粒体 QC，请更正 ID 前缀或关闭该过滤。')
    keep = (detected >= p['min_genes']) & (totals >= p['min_counts']) & (totals > 0) & (percent <= p['max_mito_percent'])
    if keep.sum() < 3:
        raise ValueError('细胞 QC 后至少保留 3 个细胞；请检查输入类型与阈值。')
    qc = [dict(cell_id=ident, total=float(totals[i]), detected_genes=int(detected[i]),
               mito_percent=float(percent[i]), retained=bool(keep[i])) for i, ident in enumerate(ids)]
    retained_indices = np.flatnonzero(keep); x = x[keep]
    gene_keep = np.asarray((x > 0).sum(axis=0)).ravel() >= p['min_cells']
    if gene_keep.sum() < 2:
        raise ValueError('基因 QC 后至少保留 2 个非恒定基因。')
    # Denominator is all input genes in a retained cell, before gene filtering.
    if p['normalize_total']:
        factors = p['target_sum'] / totals[keep]
        x = sparse.diags(factors) @ x if issparse else x * factors[:, None]
    x = x[:, gene_keep]
    checkpoint(cancel, progress, '计算 QC 后表达矩阵 PCA')
    coords, variance, chosen, _ = pca_matrix(x, log1p=p['log1p'], scale=p['scale'], max_features=p['max_features'], n_components=p['n_pcs'])
    pc_coords = coords.copy()
    # Record the actual number of components; never claim batch correction or
    # biological annotation from an embedding.
    if p['embedding'] == 'tsne':
        from sklearn.manifold import TSNE
        if len(coords) > 5000 or p['perplexity'] >= len(coords):
            raise ValueError('t-SNE 最多 5,000 个细胞，且 perplexity 必须小于保留细胞数。')
        checkpoint(cancel, progress, '运行可重复 t-SNE（基于已记录的 PCA 分量）')
        coords = TSNE(n_components=2, perplexity=p['perplexity'], init='pca', random_state=p['seed'],
                      learning_rate='auto', max_iter=1000, n_jobs=1).fit_transform(pc_coords)
    elif p['embedding'] == 'umap':
        umap = dependency('umap', 'umap-learn')
        if p['n_neighbors'] >= len(coords):
            raise ValueError('UMAP 邻居数必须小于保留细胞数。')
        checkpoint(cancel, progress, '运行可重复 UMAP（基于已记录的 PCA 分量）')
        coords = umap.UMAP(n_components=2, n_neighbors=p['n_neighbors'], random_state=p['seed'], n_jobs=1).fit_transform(pc_coords)
    else:
        coords = pc_coords[:, :2]
    clusters = None
    if p['cluster']:
        sc = dependency('scanpy'); dependency('igraph'); dependency('leidenalg')
        if p['n_neighbors'] >= len(coords):
            raise ValueError('Leiden 邻居数必须小于保留细胞数。')
        checkpoint(cancel, progress, '计算可选 Leiden 簇（不推断细胞类型）')
        ad = sc.AnnData(X=pc_coords)
        sc.pp.neighbors(ad, n_neighbors=p['n_neighbors'], n_pcs=None, use_rep='X', random_state=p['seed'])
        sc.tl.leiden(ad, resolution=p['resolution'], random_state=p['seed'], flavor='leidenalg', directed=False)
        clusters = list(map(str, ad.obs['leiden']))
    group_col = cols.get('group')
    if group_col and (aligned is None or any(group_col not in aligned[i] or aligned[i][group_col] is None for i in retained_indices)):
        raise ValueError('注释列不存在或缺失；不自动推断细胞类型。')
    if group_col and len({str(aligned[i][group_col]) for i in retained_indices}) > 60:
        raise ValueError('降维注释最多 60 类；请使用已有细胞类型等注释，或关闭注释显示。')
    if clusters is not None and len(set(clusters)) > 60:
        raise ValueError('Leiden 结果超过 60 个簇，请降低分辨率或选取关注的细胞子集后绘图。')
    rows = []
    for j, i in enumerate(retained_indices):
        row = dict(cell_id=ids[i], x=float(coords[j, 0]), y=float(coords[j, 1]),
                   annotation=str(aligned[i][group_col]) if group_col else '未注释',
                   PC1=float(pc_coords[j, 0]), PC2=float(pc_coords[j, 1]))
        if clusters is not None:
            row['leiden_cluster'] = clusters[j]
        rows.append(row)
    warnings = ['细胞降维仅探索性描述；本方法不进行细胞类型识别、批次校正或差异表达。']
    if not mito.any():
        warnings.append('基因 ID 无匹配线粒体前缀；线粒体比例记为 0，未应用线粒体过滤。')
    notes = ['归一化分母为保留细胞的全部输入基因总表达，基因过滤后再 PCA。',
             '按方差排序保留特征；PCA 严格中心化，非 TruncatedSVD。',
             f't-SNE/UMAP/Leiden 使用 {pc_coords.shape[1]} 个 PCA 分量；维度选择需研究者审核。']
    packages = ['numpy', 'scipy', 'scikit-learn'] + (['umap-learn'] if p['embedding'] == 'umap' else []) + (['scanpy', 'anndata', 'igraph', 'leidenalg'] if p['cluster'] else [])
    return result(config['method'], {'coordinates': table(rows), 'cell_qc': table(qc),
                  'retained_features': table([dict(feature=gene) for i, gene in enumerate(genes) if gene_keep[i]])},
                  dict(input_cells=len(ids), retained_cells=len(rows), input_genes=len(genes), qc_retained_genes=int(gene_keep.sum()),
                       pca_features=len(chosen), pca_components=int(pc_coords.shape[1]), explained_variance_ratio=list(map(float, variance)),
                       embedding=p['embedding'], seed=p['seed'], normalization=p['normalize_total'], target_sum=p['target_sum'], log1p=p['log1p']),
                  warnings, notes, packages)


def _required_rows(config, sources, keys):
    data = read_table(config, sources)
    cols = config['columns']; rows = data['rows']
    if any(cols[k] not in rows[0] for k in keys):
        raise ValueError('选择的分析列不存在。')
    if any(r[cols[k]] is None for r in rows for k in keys):
        raise ValueError('分析必需字段含缺失；请补齐真实注释，不能自动猜测。')
    return rows, cols


def _expression(config, sources):
    rows, cols = _required_rows(config, sources, ['cell_id', 'sample', 'cell_type', 'gene', 'value'])
    numbers([r[cols['value']] for r in rows], '表达量')
    observed = set(); annotations = {}; grouped = defaultdict(list)
    for r in rows:
        sample, cell, kind, gene = (str(r[cols[k]]) for k in ['sample', 'cell_id', 'cell_type', 'gene'])
        identity = (sample, cell, gene)
        if identity in observed:
            raise ValueError('相同样本/细胞/基因重复，不能重复计算表达。')
        observed.add(identity)
        if (sample, cell) in annotations and annotations[(sample, cell)] != kind:
            raise ValueError('同一细胞存在矛盾的细胞类型注释。')
        annotations[(sample, cell)] = kind
        grouped[(sample, kind, gene)].append(float(r[cols['value']]))
    output = []; threshold = config['parameters']['positive_threshold']
    for (sample, kind, gene), values in sorted(grouped.items()):
        output.append(dict(sample=sample, cell_type=kind, gene=gene, cells=len(values),
                           mean_expression=sum(values) / len(values), positive_fraction=sum(v > threshold for v in values) / len(values)))
    aggregated = defaultdict(list)
    for r in output:
        aggregated[(r['cell_type'], r['gene'])].append(r)
    summary = [dict(cell_type=kind, gene=gene, independent_samples=len(rs),
                    mean_expression=sum(r['mean_expression'] for r in rs) / len(rs),
                    positive_fraction=sum(r['positive_fraction'] for r in rs) / len(rs)) for (kind, gene), rs in sorted(aggregated.items())]
    if len({r['gene'] for r in summary}) > 40 or len({r['cell_type'] for r in summary}) > 40:
        raise ValueError('表达点图最多 40 个基因和 40 种细胞类型，请先选取关注的结果。')
    return result(config['method'], {'per_sample': table(output), 'summary': table(summary)},
                  dict(independent_samples=len({r['sample'] for r in output}), cells=len(annotations), positive_threshold=threshold),
                  notes=['每个样本先汇总；跨样本描述平均时每个样本等权，没有执行显著性检验。', '阳性比例分母为该样本/类型中提供该基因表达的细胞；不同基因缺行会改变分母。'])


def _composition(config, sources):
    rows, cols = _required_rows(config, sources, ['cell_id', 'sample', 'cell_type'])
    observed = set(); counts = Counter(); totals = Counter(); groups = {}
    for r in rows:
        sample, cell, kind = (str(r[cols[k]]) for k in ['sample', 'cell_id', 'cell_type'])
        if (sample, cell) in observed:
            raise ValueError('相同样本中的细胞 ID 重复；组成分母不能重复计数。')
        observed.add((sample, cell)); counts[(sample, kind)] += 1; totals[sample] += 1
        if cols.get('group') and (cols['group'] not in r or r[cols['group']] is None):
            raise ValueError('已选择的样本分组列不存在或缺失。')
        group = str(r.get(cols.get('group'), '全部样本'))
        if sample in groups and groups[sample] != group:
            raise ValueError('同一独立样本对应多个分组。')
        groups[sample] = group
    types = sorted({kind for _, kind in counts})
    if len(types) > 40 or len(totals) > 80:
        raise ValueError('组成图最多 80 个独立样本、40 种细胞类型；请选取子集。')
    output = [dict(sample=sample, group=groups[sample], cell_type=kind, count=counts[(sample, kind)],
                   total=totals[sample], fraction=counts[(sample, kind)] / totals[sample]) for sample in sorted(totals) for kind in types]
    return result(config['method'], {'composition': table(output)}, dict(samples=len(totals), cells=len(observed), cell_types=len(types)),
                  notes=['分母为输入表内每个样本的全部已注释细胞；零计数类型显式保留。', '不将细胞数作为生物重复数，不进行组间统计检验。'])


def _spatial(config, sources):
    rows, cols = _required_rows(config, sources, ['id', 'x', 'y', 'value'])
    section = cols.get('section'); selected = config['parameters']['section']
    if section:
        if section not in rows[0] or any(r[section] is None for r in rows):
            raise ValueError('切片列不存在或缺失。')
        sections = {str(r[section]) for r in rows}
        if len(sections) > 1 and not selected:
            raise ValueError('输入含多个切片，请明确选择本次切片 ID，不能叠加坐标。')
        selected = selected or next(iter(sections))
        rows = [r for r in rows if str(r[section]) == selected]
        if not rows:
            raise ValueError('选择的切片 ID 不存在。')
    if len({str(r[cols['id']]) for r in rows}) != len(rows):
        raise ValueError('同一切片的空间点 ID 必须唯一。')
    for k in ['x', 'y', 'value']:
        numbers([r[cols[k]] for r in rows], k)
    output = [dict(id=str(r[cols['id']]), x=float(r[cols['x']]), y=float(r[cols['y']]), value=float(r[cols['value']])) for r in rows]
    return result(config['method'], {'spatial': table(output)}, dict(spots=len(output), section=selected or 'single'),
                  notes=['坐标与表达均来自用户原始表；未生成空间位置、组织背景或推断性分析。'])


def _variants(config, sources):
    import numpy as np
    rows = read_table(config, sources)['rows']; cols = config['columns']; p = config['parameters']
    if p['view'] == 'oncoplot':
        needed = ['sample', 'gene']
        if any(cols[k] not in rows[0] for k in needed) or any(r[cols[k]] is None for r in rows for k in needed):
            raise ValueError('变异矩阵需要明确样本携带记录与已有基因注释；未注释 VCF 请用位点图。')
        gene_counts = Counter(str(r[cols['gene']]) for r in rows)
        genes = sorted(gene_counts, key=lambda x: (-gene_counts[x], x))[:p['top_genes']]
        samples = sorted({str(r[cols['sample']]) for r in rows})
        if len(samples) > 100:
            raise ValueError('变异矩阵最多 100 个样本。')
        grouped = defaultdict(set)
        for r in rows:
            gene, sample = str(r[cols['gene']]), str(r[cols['sample']])
            if gene in genes:
                grouped[(gene, sample)].add(str(r.get(cols.get('variant_type'), '已有变异')))
        output = [dict(gene=gene, sample=sample, mutated=int((gene, sample) in grouped),
                       variant_types=';'.join(sorted(grouped[(gene, sample)]))) for gene in genes for sample in samples]
        return result(config['method'], {'variants': table(output)}, dict(view='oncoplot', genes=genes, samples=samples),
                      notes=['二值矩阵表示表内已有变异记录；未提供的样本/基因不代表测序确认阴性。', '仅按已有注释汇总；未进行变异 calling。'])
    chromosome = cols.get('chromosome')
    if chromosome:
        if chromosome not in rows[0] or any(r[chromosome] is None for r in rows):
            raise ValueError('染色体列不存在或缺失。')
        chromosomes = {str(r[chromosome]) for r in rows}
        chosen = p['chromosome']
        if len(chromosomes) > 1 and not chosen:
            raise ValueError('有多个染色体，请明确选择一个，不能将不同染色体坐标混合。')
        chosen = chosen or next(iter(chromosomes))
        rows = [r for r in rows if str(r[chromosome]) == chosen]
        if not rows:
            raise ValueError('选择的染色体不存在。')
    else:
        chosen = p['chromosome'] or '未指定（用户确认同一坐标系）'
    needed = ['position'] + (['end', 'value'] if p['view'] == 'cnv' else [])
    if p['view'] == 'cnv' and cols.get('sample'):
        sample_col = cols['sample']
        if sample_col not in rows[0] or any(r[sample_col] is None for r in rows):
            raise ValueError('CNV 样本列不存在或缺失。')
        samples = {str(r[sample_col]) for r in rows}
        if len(samples) > 1 and not p['sample']:
            raise ValueError('CNV 包含多个样本，请明确选择单一样本，不能重叠展示。')
        sample = p['sample'] or next(iter(samples))
        rows = [r for r in rows if str(r[sample_col]) == sample]
        if not rows:
            raise ValueError('选择的 CNV 样本不存在。')
    if any(cols[k] not in rows[0] for k in needed):
        raise ValueError('选择的变异列不存在。')
    for k in needed:
        numbers([r[cols[k]] for r in rows], k)
    if any(r[cols['position']] < 0 or float(r[cols['position']]).is_integer() is False for r in rows):
        raise ValueError('真实位点坐标必须为非负整数。')
    if p['view'] == 'cnv':
        if len(rows) > 5000:
            raise ValueError('单图最多 5,000 个已有 CNV 分段，请先选择关注染色体或样本。')
        if any(r[cols['end']] < r[cols['position']] or not float(r[cols['end']]).is_integer() for r in rows):
            raise ValueError('CNV 终点须为整数且不得小于起点。')
        output = [dict(start=int(r[cols['position']]), end=int(r[cols['end']]), value=float(r[cols['value']])) for r in rows]
    else:
        counts = Counter(int(r[cols['position']]) for r in rows)
        output = [dict(position=pos, records=count) for pos, count in sorted(counts.items())]
    return result(config['method'], {'variants': table(output)}, dict(view=p['view'], chromosome=chosen, input_records=len(rows)),
                  notes=['仅展示已有变异记录或 CNV 分段；计数不是突变携带率，未进行变异 calling。'])


def compute(config, sources, cancel=None, progress=None):
    config = validate(config)
    checkpoint(cancel, progress, '校验单细胞 / 空间 / 已有变异输入')
    ident = config['method']
    if ident == 'cell_embedding':
        out = _embedding(config, sources, cancel, progress)
    elif ident == 'cell_expression':
        out = _expression(config, sources)
    elif ident == 'cell_composition':
        out = _composition(config, sources)
    elif ident == 'spatial_expression':
        out = _spatial(config, sources)
    else:
        out = _variants(config, sources)
    checkpoint(cancel, progress, '分析完成')
    return out


def draw(result, config, style, title=''):
    import numpy as np
    from matplotlib.colors import ListedColormap
    fig, axes = figure(style, title); ax = axes[0, 0]; colors = palette(style)
    ident = result['method']
    if ident == 'cell_embedding':
        rows = result['tables']['coordinates']['rows']
        key = 'leiden_cluster' if config['parameters'].get('cluster') else 'annotation'
        groups = list(dict.fromkeys(r[key] for r in rows))
        for i, group in enumerate(groups):
            subset = [r for r in rows if r[key] == group]
            ax.scatter([r['x'] for r in subset], [r['y'] for r in subset], s=6, alpha=.7, color=colors[i % len(colors)], label=group, rasterized=len(rows) > 3000)
        embedding = result['statistics']['embedding'].upper()
        ax.set(xlabel=embedding + ' 1', ylabel=embedding + ' 2')
        if len(groups) <= 12:
            ax.legend(fontsize='small', markerscale=2)
    elif ident == 'cell_expression':
        rows = result['tables']['summary']['rows']
        genes = sorted({r['gene'] for r in rows}); types = sorted({r['cell_type'] for r in rows})
        fig.set_size_inches(fig.get_size_inches()[0], max(fig.get_size_inches()[1], min(16, 2 + len(types) * .28)))
        artist = ax.scatter([genes.index(r['gene']) for r in rows], [types.index(r['cell_type']) for r in rows],
                            s=[15 + 180 * r['positive_fraction'] for r in rows], c=[r['mean_expression'] for r in rows], cmap='viridis')
        ax.set_xticks(range(len(genes)), genes, rotation=90 if len(genes) > 15 else 45, ha='right', fontsize='small'); ax.set_yticks(range(len(types)), types)
        fig.colorbar(artist, ax=ax, label='Mean expression (equal sample weight)')
    elif ident == 'cell_composition':
        rows = result['tables']['composition']['rows']; samples = sorted({r['sample'] for r in rows}); types = sorted({r['cell_type'] for r in rows})
        bottom = np.zeros(len(samples))
        lookup = {(r['sample'], r['cell_type']): r['fraction'] for r in rows}
        for i, kind in enumerate(types):
            vals = [lookup[(sample, kind)] for sample in samples]
            ax.bar(samples, vals, bottom=bottom, label=kind, color=colors[i % len(colors)]); bottom += vals
        ax.set_ylim(0, 1); ax.set_ylabel('Fraction within each independent sample'); ax.tick_params(axis='x', rotation=45)
        ax.legend(fontsize='small', bbox_to_anchor=(1.02, 1), loc='upper left')
    elif ident == 'spatial_expression':
        rows = result['tables']['spatial']['rows']
        artist = ax.scatter([r['x'] for r in rows], [r['y'] for r in rows], c=[r['value'] for r in rows],
                            s=config['parameters'].get('point_size', 15), cmap='viridis', rasterized=len(rows) > 3000)
        ax.set_aspect('equal'); ax.set(xlabel='Real spatial X', ylabel='Real spatial Y'); fig.colorbar(artist, ax=ax, label='Provided expression')
        if config['parameters'].get('invert_y'):
            ax.invert_yaxis()
    else:
        rows = result['tables']['variants']['rows']; view = result['statistics']['view']
        if view == 'oncoplot':
            genes, samples = result['statistics']['genes'], result['statistics']['samples']
            fig.set_size_inches(fig.get_size_inches()[0], max(fig.get_size_inches()[1], min(16, 1.8 + len(genes) * .25)))
            lookup = {(r['gene'], r['sample']): r['mutated'] for r in rows}
            ax.imshow([[lookup[(g, s)] for s in samples] for g in genes], aspect='auto', interpolation='nearest', cmap=ListedColormap(['#EDF0F3', colors[0]]), vmin=0, vmax=1)
            ax.set_yticks(range(len(genes)), genes, fontsize='small'); ax.set_xticks(range(len(samples)), samples, rotation=90, fontsize='x-small'); ax.set_xlabel('Samples (provided records)')
        elif view == 'lollipop':
            ax.vlines([r['position'] for r in rows], 0, [r['records'] for r in rows], color=colors[0], linewidth=1)
            ax.scatter([r['position'] for r in rows], [r['records'] for r in rows], color=colors[0], s=20)
            ax.set(xlabel='Provided genomic position', ylabel='Variant record count')
        else:
            for r in rows:
                ax.plot([r['start'], r['end']], [r['value'], r['value']], color=colors[0] if r['value'] >= 0 else colors[1], lw=2)
            ax.axhline(0, color='#788591', lw=.7); ax.set(xlabel='Provided genomic position', ylabel='Provided CNV log2 ratio')
    return fig
