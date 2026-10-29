"""Reproducible bulk/feature analysis, with explicit data-scale contracts."""
import math
import warnings as pywarnings

from .analysis_special import (bh, checkpoint, dependency, field, figure, input_file,
                               method, numbers, palette, param, read_gmt, read_matrix,
                               read_table, result, select, source, table, validate_config,
                               MAX_DENSE)

MATRIX_INPUT = input_file(formats=['csv', 'tsv', 'xlsx', 'h5ad', 'zip'])
MATRIX_FIELDS = [field('id', '矩阵行 ID 列'), field('features', '数值矩阵列（不选则全部数值列）', 'numeric', multiple=True)]
ORIENTATION = select('orientation', '表格矩阵方向', [('samples_rows', '样本/细胞为行'), ('genes_rows', '基因/特征为行')], 'samples_rows')
METADATA_FIELDS = [field('metadata_id', '元数据样本 ID 列', source='metadata'),
                   field('group', '元数据分组列', 'categorical', source='metadata')]


def catalog():
    return [
        method('expression_pca', '表达 / 特征矩阵 PCA', 'omics', '中心化主成分分析；明确选择 log1p、方差缩放及特征筛选。',
               [MATRIX_INPUT, input_file('metadata', '样本元数据（可选）', required=False)], MATRIX_FIELDS + METADATA_FIELDS,
               [ORIENTATION, param('log1p', '先 log1p（仅非负值）', 'boolean', False), param('scale', '按特征标准差缩放', 'boolean', False),
                param('max_features', '最多保留高方差特征数', 'integer', 2000, min=2, max=5000)],
               ['PCA 为描述分析，不证明分组差异或因果关系。', '特征筛选、缩放和 log1p 会记录在结果中。']),
        method('differential_expression', '差异表达：已归一化 log2 表达', 'omics', '独立生物样本间逐基因 Welch t 检验与 BH 校正。',
               [MATRIX_INPUT, input_file('metadata', '独立生物样本元数据')], MATRIX_FIELDS + METADATA_FIELDS +
               [field('replicate', '独立生物样本 ID 列', source='metadata', required=True)],
               [ORIENTATION, param('reference', '对照组名称'), param('comparison', '比较组名称'),
                param('independent_replicates', '确认每行样本为独立生物重复（非细胞或技术重复）', 'boolean', False),
                param('normalized_log2', '确认输入是已归一化 log2 表达（不是原始 counts）', 'boolean', False)],
               ['只接受已归一化 log2 表达；不将 Welch 检验称为 DESeq2。', '不支持配对/批次设计；细胞不是独立生物重复。']),
        method('deseq2', '差异表达：原始 counts / PyDESeq2', 'omics', '对原始整数 counts 拟合 PyDESeq2；明确对照与比较组。',
               [MATRIX_INPUT, input_file('metadata', '独立生物样本元数据')], MATRIX_FIELDS + METADATA_FIELDS +
               [field('replicate', '独立生物样本 ID 列', source='metadata', required=True)],
               [ORIENTATION, param('reference', '对照组名称'), param('comparison', '比较组名称'),
                param('independent_replicates', '确认每行样本为独立生物重复', 'boolean', False),
                param('min_total_count', '基因最低总计数', 'integer', 10, min=1, max=1000000)],
               ['需要可选 PyDESeq2；不接受 TPM、归一化值或 log 表达。', '本期仅单一两组独立设计；不进行批次、交互项或配对推断。'], ['pydeseq2']),
        method('enrichment_ora', '过度富集 ORA（自备 GMT 与背景）', 'omics', '使用完整实验背景与选中基因，计算超几何右尾 p 和 BH 校正。',
               [input_file(), input_file('background', '完整可检验基因背景'), input_file('genesets', '基因集 GMT', ['gmt'])],
               [field('gene', '选中基因列', required=True), field('background_gene', '背景基因列', required=True, source='background')],
               [param('min_set_size', '背景内最小基因集大小', 'integer', 5, min=1, max=10000),
                param('max_set_size', '背景内最大基因集大小', 'integer', 5000, min=1, max=30000),
                param('top_n', '显示前 N 个基因集', 'integer', 20, min=1, max=40)],
               ['背景必须为本实验全部可检验基因，不能仅使用显著基因。', '基因集由用户提供；不会联网猜测物种、ID 或 GO/KEGG 版本。']),
        method('gsea_prerank', 'GSEA 预排序（自备 GMT）', 'omics', '对唯一、完整、有符号的基因统计量排序执行本地 GSEApy prerank。',
               [input_file(), input_file('genesets', '基因集 GMT', ['gmt'])],
               [field('gene', '唯一基因 ID 列', required=True), field('rank', '有符号排序统计量列', 'numeric', required=True)],
               [param('min_set_size', '最小基因集大小', 'integer', 15, min=2, max=10000),
                param('max_set_size', '最大基因集大小', 'integer', 500, min=2, max=30000),
                param('permutations', '基因集置换次数', 'integer', 1000, min=100, max=10000),
                param('seed', '随机种子', 'integer', 0, min=0, max=2147483647),
                param('top_n', '显示前 N 个基因集', 'integer', 20, min=1, max=40)],
               ['需要可选 GSEApy；只接受完整排序，不能仅提供显著基因。', '置换单位为基因集，不是样本；结果依赖 GMT 版本及排序指标。'], ['gseapy']),
    ]


def validate(config):
    config = validate_config(config, catalog())
    p = config['parameters']
    if config['method'] in {'differential_expression', 'deseq2'}:
        if not p['independent_replicates']:
            raise ValueError('必须确认独立生物重复；不可把单个细胞或技术重复当作独立样本。')
        if not p['reference'] or not p['comparison'] or p['reference'] == p['comparison']:
            raise ValueError('必须明确不同的对照组与比较组名称。')
        if not config['columns'].get('metadata_id') or not config['columns'].get('group'):
            raise ValueError('请配置元数据样本 ID 和分组列。')
        if config['method'] == 'differential_expression' and not p['normalized_log2']:
            raise ValueError('Welch 方法仅适用于已归一化 log2 表达，原始 counts 请选择 PyDESeq2。')
    if 'min_set_size' in p and p['min_set_size'] > p['max_set_size']:
        raise ValueError('最小基因集大小不能大于最大大小。')
    return config


def metadata(config, sources, ids, embedded=None):
    """Align sample annotations by exact ID; no positional matching."""
    cols = config['columns']
    if config['inputs'].get('metadata'):
        rows = read_table(config, sources, 'metadata')['rows']
        ident = cols.get('metadata_id')
        if not ident or ident not in rows[0]:
            raise ValueError('请选择元数据中的样本 ID 列。')
    elif embedded is not None:
        rows = embedded.to_dict('records'); ident = '_observation_id'
    else:
        return None
    keys = [str(r[ident]) for r in rows]
    if len(set(keys)) != len(keys) or any(r[ident] is None for r in rows):
        raise ValueError('元数据样本 ID 不能为空或重复。')
    by_id = dict(zip(keys, rows))
    if any(x not in by_id for x in ids):
        raise ValueError('部分矩阵样本在元数据中不存在；不会按行序猜测匹配。')
    return [by_id[x] for x in ids]


def pca_matrix(matrix, *, log1p=False, scale=False, max_features=2000, n_components=2):
    import numpy as np
    from scipy import sparse
    from sklearn.decomposition import PCA
    x = matrix.astype(float).copy()
    if x.shape[0] < 3 or x.shape[1] < 2:
        raise ValueError('PCA 至少需要 3 个样本和 2 个数值特征。')
    is_sparse = sparse.issparse(x)
    values = x.data if is_sparse else x
    if not np.isfinite(values).all() or (np.abs(values) > 1e100).any():
        raise ValueError('PCA 矩阵含非有限值。')
    if log1p:
        if (values < 0).any():
            raise ValueError('log1p 仅接受非负表达；已 log/中心化表达请关闭此开关。')
        if is_sparse:
            x.data = np.log1p(x.data)
        else:
            x = np.log1p(x)
    if is_sparse:
        mean = np.asarray(x.mean(axis=0)).ravel()
        variance = np.maximum(np.asarray(x.multiply(x).mean(axis=0)).ravel() - mean ** 2, 0)
    else:
        variance = np.var(x, axis=0)
    usable = np.flatnonzero(variance > 1e-15)
    if len(usable) < 2:
        raise ValueError('PCA 至少需要 2 个非恒定特征。')
    chosen = usable[np.argsort(-variance[usable], kind='stable')[:max_features]]
    x = x[:, chosen]
    if scale:
        sd = np.sqrt(variance[chosen])
        x = x.multiply(1 / sd).tocsr() if is_sparse else x / sd
    # ARPACK centers a sparse matrix through a LinearOperator; no densification.
    if is_sparse and min(x.shape) > 2:
        components = min(n_components, min(x.shape) - 1)
        model = PCA(n_components=components, svd_solver='arpack', random_state=0)
    else:
        if x.shape[0] * x.shape[1] > MAX_DENSE:
            raise ValueError('PCA 稠密矩阵超限，请减少特征。')
        x = x.toarray() if is_sparse else x
        model = PCA(n_components=min(n_components, min(x.shape)), svd_solver='full')
    coordinates = model.fit_transform(x)
    return coordinates, model.explained_variance_ratio_, chosen, model.components_


def _pca(config, sources, cancel, progress):
    matrix, ids, genes, embedded = read_matrix(config, sources)
    p = config['parameters']
    aligned = metadata(config, sources, ids, embedded)
    checkpoint(cancel, progress, '计算中心化 PCA')
    coords, variance, chosen, loadings = pca_matrix(matrix, log1p=p['log1p'], scale=p['scale'], max_features=p['max_features'])
    group_col = config['columns'].get('group')
    if group_col and (aligned is None or any(group_col not in r or r[group_col] is None for r in aligned)):
        raise ValueError('分组列缺失，请提供与样本 ID 匹配的元数据。')
    if group_col and len({str(r[group_col]) for r in aligned}) > 30:
        raise ValueError('PCA 分组最多 30 类；请使用真实分组列，不要将唯一的样本 ID 当作分组。')
    rows = [dict(sample=ident, PC1=float(coords[i, 0]), PC2=float(coords[i, 1]),
                 group=str(aligned[i][group_col]) if group_col else '全部样本') for i, ident in enumerate(ids)]
    return result(config['method'], {'coordinates': table(rows),
                  'loadings': table([dict(feature=genes[j], PC1=float(loadings[0, i]), PC2=float(loadings[1, i])) for i, j in enumerate(chosen)])},
                  dict(samples=len(ids), input_features=len(genes), retained_features=len(chosen),
                       explained_variance_ratio=list(map(float, variance)), log1p=p['log1p'], scale=p['scale']),
                  notes=['按真实样本 ID 匹配元数据；PCA 坐标不能替代分组差异检验。'], packages=['numpy', 'scipy', 'scikit-learn'])


def _diff(config, sources, cancel, progress):
    import numpy as np
    from scipy import sparse, stats
    matrix, ids, genes, _ = read_matrix(config, sources)
    aligned = metadata(config, sources, ids)
    cols, p = config['columns'], config['parameters']
    group = cols['group']; replicate = cols['replicate']
    if any(group not in r or replicate not in r or r[group] is None or r[replicate] is None for r in aligned):
        raise ValueError('元数据中的分组或生物样本 ID 缺失。')
    selected = [i for i, r in enumerate(aligned) if str(r[group]) in {p['reference'], p['comparison']}]
    biological_ids = [str(aligned[i][replicate]) for i in selected]
    if len(set(biological_ids)) != len(biological_ids):
        raise ValueError('同一生物样本重复出现，请先按生物样本汇总；细胞或技术重复不可作为独立重复。')
    controls = [i for i in selected if str(aligned[i][group]) == p['reference']]
    tests = [i for i in selected if str(aligned[i][group]) == p['comparison']]
    if min(len(controls), len(tests)) < 2:
        raise ValueError('每组至少需要 2 个独立生物重复；建议每组至少 3 个。')
    if len(selected) * len(genes) > MAX_DENSE:
        raise ValueError('差异分析最多 5,000,000 个元素，请减少样本或基因。')
    x = matrix[selected]
    x = x.toarray() if sparse.issparse(x) else x
    if not np.isfinite(x).all():
        raise ValueError('差异表达数据含缺失或非有限值。')
    a = [selected.index(i) for i in controls]; b = [selected.index(i) for i in tests]
    warnings = []
    if min(len(a), len(b)) < 3:
        warnings.append('至少一组仅 2 个独立生物重复，结果稳定性有限，建议补充重复。')
    if len(selected) != len(ids):
        warnings.append(f'明确排除非目标组的 {len(ids) - len(selected)} 个样本。')
    checkpoint(cancel, progress, '计算逐基因差异与多重检验校正')
    model_stats = {}
    if config['method'] == 'deseq2':
        dependency('pydeseq2')
        import pandas as pd
        from pydeseq2.dds import DeseqDataSet
        from pydeseq2.ds import DeseqStats
        if (x < 0).any() or not np.equal(x, np.floor(x)).all() or (x > 2**31 - 1).any():
            raise ValueError('PyDESeq2 只接受非负整数原始 counts，不能使用归一化或 log 表达。')
        keep = x.sum(axis=0) >= p['min_total_count']
        if keep.sum() < 10 or np.any(x[:, keep].sum(axis=1) == 0):
            raise ValueError('过滤后至少需要 10 个基因，且每个样本的总计数必须大于 0。')
        chosen_genes = [g for g, k in zip(genes, keep) if k]
        frame = pd.DataFrame(x[:, keep].astype(np.int64), index=[ids[i] for i in selected], columns=chosen_genes)
        meta = pd.DataFrame({'condition': [str(aligned[i][group]) for i in selected]}, index=frame.index)
        dds = DeseqDataSet(counts=frame, metadata=meta, design='~condition', n_cpus=1, quiet=True)
        with pywarnings.catch_warnings(record=True) as fitting_warnings:
            pywarnings.simplefilter('always')
            dds.deseq2()
            checkpoint(cancel, progress, '提取 PyDESeq2 Wald 检验结果')
            ds = DeseqStats(dds, contrast=['condition', p['comparison'], p['reference']], n_cpus=1, quiet=True)
            ds.summary()
        for message in list(dict.fromkeys(str(w.message) for w in fitting_warnings))[:10]:
            warnings.append('PyDESeq2 拟合提示：' + message[:500])
        model_stats = dict(test='PyDESeq2 Wald', dispersion_fit_type=str(dds.uns.get('disp_function_type', 'unknown')),
                           min_total_count=p['min_total_count'], lfc_shrinkage=False)
        rows = []
        for gene, row in ds.results_df.iterrows():
            rows.append(dict(gene=str(gene), base_mean=float(row['baseMean']),
                             log2FC=_finite(row['log2FoldChange']), pvalue=_finite(row['pvalue']), padj=_finite(row['padj'])))
        warnings.append('PyDESeq2 的 Cook 过滤/独立过滤可能产生缺失 p 或 padj；保留为空，未替换为显著结果。')
        notes = ['原始 counts；PyDESeq2 负二项模型/Wald 检验；未进行 LFC shrinkage。']
        packages = ['numpy', 'pydeseq2', 'pandas']
    else:
        with pywarnings.catch_warnings():
            pywarnings.simplefilter('ignore', RuntimeWarning)
            _, ps = stats.ttest_ind(x[b], x[a], axis=0, equal_var=False)
        effects = np.mean(x[b], axis=0) - np.mean(x[a], axis=0)
        invalid = ~np.isfinite(ps)
        ps[invalid] = 1.0
        qs = bh(ps)
        rows = [dict(gene=g, mean_reference=float(np.mean(x[a, j])), mean_comparison=float(np.mean(x[b, j])),
                     log2FC=float(effects[j]), pvalue=float(ps[j]), padj=float(qs[j])) for j, g in enumerate(genes)]
        if invalid.any():
            warnings.append(f'{int(invalid.sum())} 个恒定/退化基因无法估计 Welch 检验，p 记为 1 并保留。')
        notes = ['输入由用户确认已归一化 log2 表达；log2FC 为两组平均 log2 表达之差。', '逐基因双侧 Welch t 检验；BH 对全部保留基因校正。']
        packages = ['numpy', 'scipy']
        model_stats = dict(test='Welch t (two-sided)', adjustment='BH', input_scale='normalized_log2')
    return result(config['method'], {'differential': table(rows)},
                  dict(reference=p['reference'], comparison=p['comparison'], n_reference=len(a), n_comparison=len(b),
                       genes_tested=len(rows), input_genes=len(genes), independent_biological_replicates=True, **model_stats), warnings, notes, packages)


def _finite(x):
    return float(x) if math.isfinite(x) else None


def _ora(config, sources):
    from scipy.stats import hypergeom
    cols, p = config['columns'], config['parameters']
    def genes(key, field):
        rows = read_table(config, sources, key)['rows']
        if field not in rows[0] or any(r[field] is None for r in rows):
            raise ValueError('基因 ID 列不存在或含缺失。')
        return {str(r[field]).strip() for r in rows if str(r[field]).strip()}
    selected = genes('data', cols['gene'])
    background = genes('background', cols['background_gene'])
    if not selected or not background or not selected <= background:
        raise ValueError('选中基因必须非空并全部属于完整实验背景；请先统一基因 ID。')
    if selected == background:
        raise ValueError('选中基因与背景完全相同，无法进行有意义的过度富集。')
    sets = read_gmt(source(config, sources, 'genesets')['raw'])
    rows = []
    for label, geneset in sets.items():
        usable = geneset & background
        if not p['min_set_size'] <= len(usable) <= p['max_set_size']:
            continue
        overlap = selected & usable
        pv = float(hypergeom.sf(len(overlap) - 1, len(background), len(usable), len(selected)))
        rows.append(dict(gene_set=label, overlap=len(overlap), selected=len(selected), set_size=len(usable),
                         background=len(background), gene_ratio=len(overlap) / len(selected),
                         fold_enrichment=(len(overlap) / len(selected)) / (len(usable) / len(background)),
                         pvalue=pv, genes=';'.join(sorted(overlap))))
    if not rows:
        raise ValueError('没有基因集满足背景交集和大小过滤，请检查 ID、物种和阈值。')
    for row, q in zip(rows, bh([r['pvalue'] for r in rows])):
        row['padj'] = q
    rows.sort(key=lambda r: (r['padj'], r['pvalue'], r['gene_set']))
    return result(config['method'], {'enrichment': table(rows)},
                  dict(background=len(background), selected=len(selected), tested_gene_sets=len(rows), adjustment='BH'),
                  notes=['右尾超几何检验；BH 包括所有满足大小条件的基因集（含零重叠）。', '背景与 GMT 由用户提供；结果不自动解释为通路激活或抑制。'], packages=['scipy'])


def _gsea(config, sources, cancel, progress):
    import pandas as pd
    gp = dependency('gseapy')
    rows = read_table(config, sources)['rows']; cols, p = config['columns'], config['parameters']
    if any(cols[k] not in rows[0] for k in ['gene', 'rank']):
        raise ValueError('基因或排序统计量列不存在。')
    ids = [str(r[cols['gene']]) for r in rows]
    if any(r[cols['gene']] is None for r in rows) or len(set(ids)) != len(ids):
        raise ValueError('GSEA 基因 ID 必须唯一且非缺失；不会自动合并重复基因。')
    ranks = numbers([r[cols['rank']] for r in rows], 'GSEA 排序统计量')
    if len(rows) < 10 or not (ranks < 0).any() or not (ranks > 0).any():
        raise ValueError('GSEA 需完整、至少 10 个基因、同时有正负值的有符号排序统计量。')
    sets = read_gmt(source(config, sources, 'genesets')['raw'])
    aligned = sorted(zip(ids, ranks), key=lambda x: (-x[1], x[0]))
    warnings = []
    if len(set(ranks)) != len(ranks):
        warnings.append('排序统计量含并列值；以基因 ID 稳定排序，并列顺序会影响富集。')
    checkpoint(cancel, progress, '运行本地基因集置换 GSEA')
    fit = gp.prerank(rnk=pd.DataFrame(aligned), gene_sets={k: sorted(v) for k, v in sets.items()},
                     min_size=p['min_set_size'], max_size=p['max_set_size'], permutation_num=p['permutations'],
                     threads=1, seed=p['seed'], outdir=None, no_plot=True, ascending=False, verbose=False)
    output = []
    for _, r in fit.res2d.iterrows():
        output.append(dict(gene_set=str(r['Term']), ES=_finite(float(r['ES'])), NES=_finite(float(r['NES'])),
                           pvalue=_finite(float(r['NOM p-val'])), fdr=_finite(float(r['FDR q-val'])),
                           lead_genes=str(r.get('Lead_genes', ''))))
    output.sort(key=lambda r: (r['fdr'] if r['fdr'] is not None else 1, r['gene_set']))
    return result(config['method'], {'gsea': table(output)}, dict(ranked_genes=len(ids), seed=p['seed'], permutations=p['permutations']), warnings,
                  ['GSEApy prerank；基因集置换；FDR 为 GSEA 估计，不是 ORA 的 BH。', '未联网下载基因集。'], ['gseapy', 'pandas'])


def compute(config, sources, cancel=None, progress=None):
    config = validate(config)
    checkpoint(cancel, progress, '校验组学输入')
    ident = config['method']
    if ident == 'expression_pca':
        out = _pca(config, sources, cancel, progress)
    elif ident in {'differential_expression', 'deseq2'}:
        out = _diff(config, sources, cancel, progress)
    elif ident == 'enrichment_ora':
        out = _ora(config, sources)
    else:
        out = _gsea(config, sources, cancel, progress)
    checkpoint(cancel, progress, '组学分析完成')
    return out


def draw(result, config, style, title=''):
    fig, axes = figure(style, title); ax = axes[0, 0]; colors = palette(style)
    ident = result['method']
    if ident == 'expression_pca':
        rows = result['tables']['coordinates']['rows']
        for i, group in enumerate(dict.fromkeys(r['group'] for r in rows)):
            subset = [r for r in rows if r['group'] == group]
            ax.scatter([r['PC1'] for r in subset], [r['PC2'] for r in subset], label=group, color=colors[i % len(colors)], s=25, alpha=.8)
        variance = result['statistics']['explained_variance_ratio']
        ax.set(xlabel=f'PC1 ({variance[0]:.1%})', ylabel=f'PC2 ({variance[1]:.1%})'); ax.legend(fontsize='small')
    elif ident in {'differential_expression', 'deseq2'}:
        rows = [r for r in result['tables']['differential']['rows'] if r['log2FC'] is not None and r['padj'] is not None]
        for significant, color, label in [(False, '#A0ABB5', 'BH ≥ 0.05'), (True, colors[1], 'BH < 0.05')]:
            subset = [r for r in rows if (r['padj'] < .05) == significant]
            ax.scatter([r['log2FC'] for r in subset], [-math.log10(max(r['padj'], 1e-300)) for r in subset], s=12, alpha=.65, color=color, label=label)
        ax.axhline(-math.log10(.05), color='#788591', lw=.8, ls='--'); ax.set(xlabel='log2FC (comparison − reference)', ylabel='−log10(adjusted p)'); ax.legend(fontsize='small')
    elif ident == 'enrichment_ora':
        rows = result['tables']['enrichment']['rows'][:config['parameters'].get('top_n', 20)][::-1]
        fig.set_size_inches(fig.get_size_inches()[0], max(fig.get_size_inches()[1], min(16, 1.8 + len(rows) * .28)))
        artist = ax.scatter([r['gene_ratio'] for r in rows], range(len(rows)), s=[max(20, min(r['overlap'] * 15, 300)) for r in rows],
                            c=[-math.log10(max(r['padj'], 1e-300)) for r in rows], cmap='viridis')
        ax.set_yticks(range(len(rows)), [r['gene_set'] for r in rows]); ax.set_xlabel('Gene ratio (overlap / selected)'); fig.colorbar(artist, ax=ax, label='−log10(BH adjusted p)')
    else:
        rows = result['tables']['gsea']['rows'][:config['parameters'].get('top_n', 20)][::-1]
        fig.set_size_inches(fig.get_size_inches()[0], max(fig.get_size_inches()[1], min(16, 1.8 + len(rows) * .28)))
        ax.barh(range(len(rows)), [r['NES'] or 0 for r in rows], color=[colors[0] if (r['NES'] or 0) >= 0 else colors[1] for r in rows])
        ax.set_yticks(range(len(rows)), [r['gene_set'] for r in rows]); ax.set_xlabel('GSEA normalized enrichment score (NES)'); ax.axvline(0, lw=.7, color='#788591')
    return fig
