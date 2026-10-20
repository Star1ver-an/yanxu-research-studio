"""Numerical/replicate/FDR acceptance tests with synthetic, offline data."""
import copy
import io
import importlib.util
import json
import math
import unittest
import zipfile
import tempfile
from pathlib import Path

from app import analysis_omics as omics
from app import analysis_cell as cell
from app.analysis_special import (bh, inspect_special, read_10x, read_gmt,
                                  read_matrix, read_vcf, validate_config)


def src(text, ident='data', name='test.csv'):
    return dict(id=ident, name=name, raw=text.encode('utf-8') if isinstance(text, str) else text)


def conf(method, columns=None, parameters=None, **inputs):
    return dict(method=method, columns=columns or {}, parameters=parameters or {}, inputs={'data': 'data', **inputs})


def zip10x(matrix=b'%%MatrixMarket matrix coordinate integer general\n% synthetic\n3 3 5\n1 1 2\n2 1 3\n1 2 5\n3 2 7\n3 3 1\n', **extra):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('synthetic/matrix.mtx', matrix)
        archive.writestr('synthetic/features.tsv', 'g1\tG1\tGene Expression\ng2\tG2\tGene Expression\ng3\tG3\tGene Expression\n')
        archive.writestr('synthetic/barcodes.tsv', 'c1\nc2\nc3\n')
        for path, value in extra.items():
            archive.writestr(path, value)
    return stream.getvalue()


class ScientificReaderTests(unittest.TestCase):
    def test_bh_known_values_and_zero(self):
        expected = [.02, .04, .04, .008]
        for actual, value in zip(bh([.01, .04, .03, .002]), expected):
            self.assertAlmostEqual(actual, value)
        self.assertEqual(bh([0, 1]), [0, 1])

    def test_gmt_deduplicates_members_and_rejects_duplicate_terms(self):
        self.assertEqual(read_gmt(b'set\tdescription\tg1\tg2\tg1'), {'set': {'g1', 'g2'}})
        with self.assertRaisesRegex(ValueError, '唯一'):
            read_gmt(b'set\tdescription\tg1\nset\tdescription\tg2')

    def test_10x_sparse_values_and_inspection(self):
        raw = zip10x(); before = bytes(raw)
        info = inspect_special(raw, 'counts.zip')
        self.assertEqual((info['row_count'], info['features'], info['nonzero']), (3, 3, 5))
        matrix, obs, genes, _ = read_10x(raw)
        self.assertTrue(hasattr(matrix, 'tocsr'))
        self.assertEqual(matrix.toarray().tolist(), [[2, 3, 0], [5, 0, 7], [0, 0, 1]])
        self.assertEqual(obs, ['c1', 'c2', 'c3']); self.assertEqual(genes, ['g1', 'g2', 'g3'])
        self.assertEqual(raw, before)

    def test_10x_rejects_traversal_oversize_shape_and_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, '不安全'):
            inspect_special(zip10x(**{'../escape': 'bad'}), 'counts.zip')
        oversized = b'%%MatrixMarket matrix coordinate integer general\n30001 3 1\n1 1 2\n'
        with self.assertRaisesRegex(ValueError, '稀疏矩阵'):
            inspect_special(zip10x(oversized), 'counts.zip')
        with self.assertRaisesRegex(ValueError, '只允许'):
            inspect_special(zip10x(**{'execute.py': 'print(1)'}), 'counts.zip')

    def test_vcf_does_not_invent_genes_or_sample_carriers(self):
        text = b'##fileformat=VCFv4.3\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n1\t123\t.\tA\tG\t.\tPASS\t.\n'
        data = read_vcf(text)
        self.assertEqual(data['rows'][0]['POS'], 123)
        self.assertNotIn('gene', data['rows'][0])
        self.assertNotIn('sample', data['rows'][0])
        self.assertEqual(inspect_special(text, 'variants.vcf')['row_count'], 1)

    def test_matrix_orientation_and_missing_fail(self):
        cfg = conf('expression_pca', {'id': 'gene'}, {'orientation': 'genes_rows'})
        matrix, obs, genes, _ = read_matrix(cfg, [src('gene,s1,s2\ng1,1,2\ng2,3,4')])
        self.assertEqual(matrix.tolist(), [[1, 3], [2, 4]])
        self.assertEqual(obs, ['s1', 's2']); self.assertEqual(genes, ['g1', 'g2'])
        cfg['columns']['features'] = ['s1', 's2']
        with self.assertRaisesRegex(ValueError, '缺失'):
            read_matrix(cfg, [src('gene,s1,s2\ng1,,2\ng2,3,4')])

    def test_schema_rejects_unknown_code_and_nonfinite(self):
        with self.assertRaisesRegex(ValueError, '未知'):
            omics.validate(conf('expression_pca', parameters={'python': 'exec(1)'}))
        with self.assertRaisesRegex(ValueError, '无效数值'):
            omics.validate(conf('expression_pca', parameters={'max_features': math.inf}))
        with self.assertRaisesRegex(ValueError, '无效'):
            omics.validate(conf('expression_pca', columns={'features': [['x']]}))


class OmicsAnalysisTests(unittest.TestCase):
    def test_pca_matches_svd_reconstruction_and_exact_metadata_id(self):
        import numpy as np
        data = src('id,f1,f2,f3\ns1,1,4,3\ns2,2,5,2\ns3,3,8,1\ns4,4,9,0')
        meta = src('sample,cohort\ns4,B\ns2,A\ns1,A\ns3,B', 'meta')
        cfg = conf('expression_pca', {'id': 'id', 'metadata_id': 'sample', 'group': 'cohort'}, metadata='meta')
        out = omics.compute(cfg, [data, meta]); rows = out['tables']['coordinates']['rows']
        self.assertEqual([r['group'] for r in rows], ['A', 'A', 'B', 'B'])
        x = np.array([[1, 4, 3], [2, 5, 2], [3, 8, 1], [4, 9, 0]], float)
        expected = np.linalg.svd(x - x.mean(axis=0), compute_uv=False) ** 2
        self.assertAlmostEqual(out['statistics']['explained_variance_ratio'][0], expected[0] / expected.sum(), places=10)
        self.assertAlmostEqual(sum(r['PC1'] for r in rows), 0, places=10)
        self.assertEqual(cfg['parameters'], {})
        json.dumps(out, allow_nan=False)

    def test_pca_log_rejects_negative_and_sparse_not_densified(self):
        import numpy as np
        from scipy import sparse
        from unittest.mock import patch
        with self.assertRaisesRegex(ValueError, '非负'):
            omics.pca_matrix(np.array([[1, -1], [2, 2], [3, 1]]), log1p=True)
        x = sparse.csr_matrix([[1, 0, 4, 0], [2, 1, 0, 1], [4, 0, 1, 2], [0, 2, 3, 1]])
        with patch.object(sparse.csr_matrix, 'toarray', side_effect=AssertionError('implicit dense')):
            coords, _, _, _ = omics.pca_matrix(x, scale=True)
        self.assertEqual(coords.shape, (4, 2))

    def differential_fixture(self):
        data = src('sample,g1,g2,g3\na1,1,5,1\na2,2,4,1\na3,3,6,1\nb1,4,5,1\nb2,5,6,1\nb3,6,4,1')
        meta = src('sample,group,bio\na1,A,a1\na2,A,a2\na3,A,a3\nb1,B,b1\nb2,B,b2\nb3,B,b3', 'meta')
        cfg = conf('differential_expression', {'id': 'sample', 'metadata_id': 'sample', 'group': 'group', 'replicate': 'bio'},
                   {'reference': 'A', 'comparison': 'B', 'independent_replicates': True, 'normalized_log2': True}, metadata='meta')
        return cfg, [data, meta]

    def test_welch_and_bh_match_scipy_and_constant_gene(self):
        from scipy.stats import ttest_ind
        cfg, sources = self.differential_fixture(); out = omics.compute(cfg, sources)
        rows = out['tables']['differential']['rows']
        self.assertEqual(rows[0]['log2FC'], 3)
        p = float(ttest_ind([4, 5, 6], [1, 2, 3], equal_var=False).pvalue)
        self.assertAlmostEqual(rows[0]['pvalue'], p, places=12)
        self.assertAlmostEqual(rows[0]['padj'], min(3 * p, 1), places=12)
        self.assertEqual(rows[2]['pvalue'], 1)
        self.assertEqual(out['statistics']['n_reference'], 3)
        self.assertTrue(out['warnings'])

    def test_no_raw_count_welch_or_cell_pseudoreplication(self):
        cfg, sources = self.differential_fixture()
        cfg['parameters']['normalized_log2'] = False
        with self.assertRaisesRegex(ValueError, '原始 counts'):
            omics.compute(cfg, sources)
        cfg['parameters']['normalized_log2'] = True
        sources[1] = src('sample,group,bio\na1,A,donor1\na2,A,donor1\na3,A,donor1\nb1,B,donor2\nb2,B,donor2\nb3,B,donor2', 'meta')
        with self.assertRaisesRegex(ValueError, '独立重复'):
            omics.compute(cfg, sources)

    def test_group_labels_and_missing_metadata_are_explicit(self):
        cfg, sources = self.differential_fixture()
        cfg['parameters']['comparison'] = 'unknown'
        with self.assertRaisesRegex(ValueError, '每组'):
            omics.compute(cfg, sources)
        cfg['parameters']['comparison'] = 'B'
        sources[1] = src('sample,group,bio\na1,A,a1\nb1,B,b1', 'meta')
        with self.assertRaisesRegex(ValueError, '元数据中不存在'):
            omics.compute(cfg, sources)

    def test_ora_uses_full_background_and_all_tested_terms_for_fdr(self):
        from scipy.stats import hypergeom
        cfg = conf('enrichment_ora', {'gene': 'gene', 'background_gene': 'gene'}, {'min_set_size': 1}, background='bg', genesets='sets')
        sources = [src('gene\ng1\ng2'), src('gene\ng1\ng2\ng3\ng4\ng5\ng6\ng7\ng8\ng9\ng10', 'bg'),
                   src('setA\tuser-v1\tg1\tg2\tg3\nsetB\tuser-v1\tg8\tg9', 'sets', 'sets.gmt')]
        out = omics.compute(cfg, sources); rows = out['tables']['enrichment']['rows']
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]['pvalue'], hypergeom.sf(1, 10, 3, 2))
        self.assertAlmostEqual(rows[0]['padj'], 2 * hypergeom.sf(1, 10, 3, 2))
        self.assertEqual(rows[1]['overlap'], 0)
        self.assertEqual(rows[1]['pvalue'], 1)

    def test_ora_rejects_incomplete_or_same_background(self):
        cfg = conf('enrichment_ora', {'gene': 'gene', 'background_gene': 'gene'}, {'min_set_size': 1}, background='bg', genesets='sets')
        sources = [src('gene\ng1\ng2'), src('gene\ng1', 'bg'), src('set\tdesc\tg1', 'sets', 'sets.gmt')]
        with self.assertRaisesRegex(ValueError, '完整实验背景'):
            omics.compute(cfg, sources)
        sources[1] = src('gene\ng1\ng2', 'bg')
        with self.assertRaisesRegex(ValueError, '完全相同'):
            omics.compute(cfg, sources)

    def test_cancel_before_work(self):
        with self.assertRaisesRegex(ValueError, '取消'):
            omics.compute(conf('expression_pca'), [], cancel=lambda: True)


class CellAnalysisTests(unittest.TestCase):
    def test_qc_normalization_retention_and_seed(self):
        cfg = conf('cell_embedding', {'id': 'cell'}, {'min_genes': 2, 'max_mito_percent': 50, 'mitochondrial_prefix': 'MT-'})
        source = src('cell,MT-a,g1,g2,g3\nc1,1,3,2,1\nc2,2,5,1,2\nc3,1,0,2,3\nc4,0,1,2,4\nbad,8,0,0,0')
        out = cell.compute(cfg, [source]); again = cell.compute(cfg, [source])
        self.assertEqual(out['tables']['coordinates'], again['tables']['coordinates'])
        self.assertEqual(out['statistics']['retained_cells'], 4)
        self.assertFalse(out['tables']['cell_qc']['rows'][-1]['retained'])
        self.assertAlmostEqual(out['tables']['cell_qc']['rows'][0]['mito_percent'], 100 / 7)
        json.dumps(out, allow_nan=False)

    def test_qc_no_invented_mito_id_or_double_normalization(self):
        cfg = conf('cell_embedding', {'id': 'cell'}, {'max_mito_percent': 20})
        source = src('cell,ENSG1,ENSG2\nc1,1,2\nc2,2,3\nc3,3,4')
        with self.assertRaisesRegex(ValueError, '没有匹配'):
            cell.compute(cfg, [source])
        cfg['parameters'] = {'data_scale': 'normalized'}
        with self.assertRaisesRegex(ValueError, '再次总量归一化'):
            cell.compute(cfg, [source])

    def test_tsne_repeatable_with_explicit_perplexity(self):
        rows = 'cell,a,b,c\n' + '\n'.join(f'c{i},{i + 1},{(i * i) % 11 + 1},{(i * 3) % 7 + 1}' for i in range(15))
        cfg = conf('cell_embedding', {'id': 'cell'}, {'embedding': 'tsne', 'perplexity': 4, 'seed': 42})
        a = cell.compute(cfg, [src(rows)]); b = cell.compute(cfg, [src(rows)])
        self.assertEqual(a['tables']['coordinates'], b['tables']['coordinates'])
        cfg['parameters']['perplexity'] = 30
        with self.assertRaisesRegex(ValueError, 'perplexity'):
            cell.compute(cfg, [src(rows)])

    def test_expression_equal_sample_weight_not_pooled_cells(self):
        cfg = conf('cell_expression', {'cell_id': 'cell', 'sample': 'sample', 'cell_type': 'type', 'gene': 'gene', 'value': 'value'})
        source = src('cell,sample,type,gene,value\nc1,s1,T,g1,10\nc2,s2,T,g1,0\nc3,s2,T,g1,0\nc4,s2,T,g1,0')
        out = cell.compute(cfg, [source]); summary = out['tables']['summary']['rows'][0]
        self.assertEqual(summary['mean_expression'], 5)
        self.assertEqual(summary['positive_fraction'], .5)
        self.assertEqual(summary['independent_samples'], 2)

    def test_composition_each_sample_sums_one_and_duplicate_cells_rejected(self):
        cfg = conf('cell_composition', {'cell_id': 'cell', 'sample': 'sample', 'cell_type': 'type'})
        text = 'cell,sample,type\nc1,s1,A\nc2,s1,A\nc3,s1,B\nc1,s2,B'
        out = cell.compute(cfg, [src(text)])
        for sample in ['s1', 's2']:
            self.assertAlmostEqual(sum(r['fraction'] for r in out['tables']['composition']['rows'] if r['sample'] == sample), 1)
        with self.assertRaisesRegex(ValueError, '重复'):
            cell.compute(cfg, [src(text + '\nc1,s2,A')])

    def test_spatial_does_not_overlay_multiple_sections(self):
        cfg = conf('spatial_expression', {'id': 'spot', 'x': 'x', 'y': 'y', 'value': 'value', 'section': 'section'})
        source = src('spot,x,y,value,section\ns1,0,1,10,A\ns1,0,1,20,B')
        with self.assertRaisesRegex(ValueError, '多个切片'):
            cell.compute(cfg, [source])
        cfg['parameters']['section'] = 'B'
        out = cell.compute(cfg, [source]); self.assertEqual(out['tables']['spatial']['rows'][0]['value'], 20)

    def test_variant_coordinates_and_cnv_sample_selection(self):
        cfg = conf('variant_summary', {'chromosome': 'chr', 'position': 'pos'}, {'view': 'lollipop'})
        source = src('chr,pos\n1,100\n1,100\n2,100')
        with self.assertRaisesRegex(ValueError, '多个染色体'):
            cell.compute(cfg, [source])
        cfg['parameters']['chromosome'] = '1'
        out = cell.compute(cfg, [source]); self.assertEqual(out['tables']['variants']['rows'], [dict(position=100, records=2)])
        cfg = conf('variant_summary', {'sample': 'sample', 'position': 'start', 'end': 'end', 'value': 'ratio'}, {'view': 'cnv'})
        source = src('sample,start,end,ratio\na,1,100,0.5\nb,1,100,-0.3')
        with self.assertRaisesRegex(ValueError, '多个样本'):
            cell.compute(cfg, [source])


class OptionalScientificEngineTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('anndata'), '可选 anndata 未安装')
    def test_h5ad_sparse_true_counts_and_existing_annotations(self):
        import anndata as ad
        import numpy as np
        import pandas as pd
        from scipy import sparse
        from app.analysis_special import read_h5ad
        matrix = sparse.csr_matrix([[1, 2, 0], [2, 0, 3], [0, 4, 1], [3, 1, 2]])
        data = ad.AnnData(matrix, obs=pd.DataFrame({'type': ['A', 'B', 'A', 'B']}, index=['c1', 'c2', 'c3', 'c4']),
                         var=pd.DataFrame(index=['g1', 'g2', 'g3']))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.h5ad'; data.write_h5ad(path, compression='gzip'); raw = path.read_bytes()
        info = inspect_special(raw, 'synthetic.h5ad')
        self.assertTrue(info['parsed']); self.assertEqual(info['features'], 3)
        restored, ids, genes, obs = read_h5ad(raw)
        np.testing.assert_array_equal(restored.toarray(), matrix.toarray())
        cfg = conf('expression_pca', {'group': 'type'})
        out = omics.compute(cfg, [src(raw, name='synthetic.h5ad')])
        self.assertEqual([r['group'] for r in out['tables']['coordinates']['rows']], ['A', 'B', 'A', 'B'])

    @unittest.skipUnless(importlib.util.find_spec('h5py'), '可选 h5py 未安装')
    def test_h5ad_external_links_and_bad_sparse_pointers_rejected(self):
        import h5py
        from app.analysis_special import _h5ad_preflight
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unsafe.h5ad'
            with h5py.File(path, 'w') as f:
                f['outside'] = h5py.ExternalLink('outside.h5', 'X')
            with self.assertRaisesRegex(ValueError, '外部'):
                _h5ad_preflight(path)
            with h5py.File(path, 'w') as f:
                x = f.create_group('X'); x.attrs['shape'] = (3, 2); x.attrs['encoding-type'] = 'csr_matrix'
                x.create_dataset('data', data=[1., 2.]); x.create_dataset('indices', data=[0, 1]); x.create_dataset('indptr', data=[0, 1, 0, 2])
            with self.assertRaisesRegex(ValueError, '指针'):
                _h5ad_preflight(path)

    @unittest.skipUnless(importlib.util.find_spec('pydeseq2'), '可选 PyDESeq2 未安装')
    def test_pydeseq2_real_fit_rejects_normalized_counts(self):
        import numpy as np
        rng = np.random.default_rng(9)
        genes = [f'g{i}' for i in range(120)]
        x = rng.negative_binomial(8, .3, size=(8, 120))
        x[4:, :20] *= 5
        text = 'sample,' + ','.join(genes) + '\n' + '\n'.join('s' + str(i) + ',' + ','.join(map(str, row)) for i, row in enumerate(x))
        meta = 'sample,group,bio\n' + '\n'.join(f's{i},{"A" if i < 4 else "B"},bio{i}' for i in range(8))
        cfg = conf('deseq2', {'id': 'sample', 'metadata_id': 'sample', 'group': 'group', 'replicate': 'bio'},
                   {'reference': 'A', 'comparison': 'B', 'independent_replicates': True}, metadata='meta')
        out = omics.compute(cfg, [src(text), src(meta, 'meta')])
        self.assertEqual(out['statistics']['genes_tested'], 120)
        self.assertGreater(sum(r['log2FC'] or 0 for r in out['tables']['differential']['rows'][:20]) / 20, 1)
        self.assertIn('pydeseq2', out['software']); json.dumps(out, allow_nan=False)
        self.assertIn(out['statistics']['dispersion_fit_type'], {'mean', 'parametric'})
        if out['statistics']['dispersion_fit_type'] == 'mean':
            self.assertTrue(any('mean-based' in message for message in out['warnings']))
        bad_lines = text.splitlines(); first = bad_lines[1].split(','); first[1] = '1.5'
        bad_lines[1] = ','.join(first)
        with self.assertRaisesRegex(ValueError, '非负整数'):
            omics.compute(cfg, [src('\n'.join(bad_lines)), src(meta, 'meta')])

    @unittest.skipUnless(importlib.util.find_spec('gseapy'), '可选 GSEApy 未安装')
    def test_gsea_real_permutations_and_repeatable_seed(self):
        data = src('gene,rank\n' + '\n'.join(f'g{i},{i - 14.5}' for i in range(30)))
        sets = src('UP\tuser-v1\t' + '\t'.join(f'g{i}' for i in range(22, 30)) + '\nDOWN\tuser-v1\t' + '\t'.join(f'g{i}' for i in range(8)), 'sets', 'sets.gmt')
        cfg = conf('gsea_prerank', {'gene': 'gene', 'rank': 'rank'},
                   {'min_set_size': 3, 'max_set_size': 15, 'permutations': 100, 'seed': 7}, genesets='sets')
        a = omics.compute(cfg, [data, sets]); b = omics.compute(cfg, [data, sets])
        self.assertEqual(a['tables'], b['tables'])
        output = {r['gene_set']: r for r in a['tables']['gsea']['rows']}
        self.assertGreater(output['UP']['NES'], 0); self.assertLess(output['DOWN']['NES'], 0)

    @unittest.skipUnless(importlib.util.find_spec('umap'), '可选 UMAP 未安装')
    def test_umap_and_optional_scanpy_leiden_real_fit(self):
        rows = 'cell,a,b,c,d\n' + '\n'.join(f'c{i},{i + 1},{(i * i) % 11 + 1},{(i * 3) % 7 + 1},{(i * 5) % 13 + 1}' for i in range(30))
        cfg = conf('cell_embedding', {'id': 'cell'}, {'embedding': 'umap', 'n_neighbors': 5, 'seed': 23})
        out = cell.compute(cfg, [src(rows)])
        self.assertEqual(out['statistics']['retained_cells'], 30)
        self.assertIn('umap-learn', out['software'])
        if all(importlib.util.find_spec(name) for name in ['scanpy', 'igraph', 'leidenalg']):
            cfg['parameters']['cluster'] = True
            out = cell.compute(cfg, [src(rows)])
            self.assertTrue(all('leiden_cluster' in r for r in out['tables']['coordinates']['rows']))
            self.assertIn('scanpy', out['software'])


if __name__ == '__main__':
    unittest.main()
