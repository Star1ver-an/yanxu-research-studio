"""Synthetic independent model-comparison studies, not clinical study data."""
import io
import json
import math
import threading
import unittest

import numpy as np

from app import analysis_meta as meta
from tests.test_analysis_clinical import source


def config(method='meta_forest', columns=None, **parameters):
    if parameters.get('effect_scale') in {'ratio', 'log_ratio'} and 'effect_measure' not in parameters:
        parameters['effect_measure'] = 'rr'
    return {'method': method, 'inputs': {'data': 'table'},
            'columns': columns or {'study': 'study', 'effect': 'effect', 'standard_error': 'se'},
            'parameters': {'independent_units': True, **parameters}}


def rows(n=12):
    rng = np.random.default_rng(12)
    return [{'study': f'AI benchmark {i + 1}', 'effect': float(rng.normal(.3, .2)), 'se': float(rng.uniform(.08, .2))} for i in range(n)]


class MetaAnalysisTests(unittest.TestCase):
    def test_catalog_and_uncertainty_choice(self):
        self.assertEqual({x['id'] for x in meta.catalog()}, {'meta_forest', 'meta_funnel', 'meta_sensitivity', 'meta_baujat'})
        for mapping in [{'study': 'study', 'effect': 'effect'},
                        {'study': 'study', 'effect': 'effect', 'lower': 'lower'},
                        {'study': 'study', 'effect': 'effect', 'standard_error': 'se', 'lower': 'lower', 'upper': 'upper'}]:
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                meta.validate(config(columns=mapping))

    def test_equal_variance_known_fixed_mean_and_zero_heterogeneity(self):
        data = [{'study': str(i), 'effect': 2., 'se': 1.} for i in range(3)]
        result = meta.compute(config(), source(data))
        stats = result['statistics']
        self.assertAlmostEqual(stats['fixed']['estimate'], 2)
        self.assertAlmostEqual(stats['fixed']['analysis_se'], 1 / math.sqrt(3))
        self.assertAlmostEqual(stats['tau2'], 0)
        self.assertAlmostEqual(stats['i2'], 0)
        json.dumps(result, allow_nan=False)

    def test_paule_mandel_matches_statsmodels_and_leave_one_out(self):
        from statsmodels.stats.meta_analysis import combine_effects
        data = rows()
        result = meta.compute(config(), source(data))
        yi = np.array([r['effect'] for r in data]); vi = np.array([r['se'] ** 2 for r in data])
        expected = combine_effects(yi, vi, method_re='iterated', maxiter=1000, atol=1e-8)
        self.assertAlmostEqual(result['statistics']['random']['analysis_estimate'], expected.mean_effect_re)
        self.assertAlmostEqual(result['statistics']['tau2'], expected.tau2)
        removed = result['tables']['leave_one_out']['rows'][0]
        leave = combine_effects(yi[1:], vi[1:], method_re='iterated', maxiter=1000, atol=1e-8)
        self.assertAlmostEqual(removed['estimate'], leave.mean_effect_re)
        self.assertAlmostEqual(sum(r['fixed_weight'] for r in result['tables']['studies']['rows']), 1)
        self.assertAlmostEqual(sum(r['random_weight'] for r in result['tables']['studies']['rows']), 1)

    def test_ratio_log_analysis_and_back_transform(self):
        data = [{'study': str(i), 'effect': value, 'se': .1} for i, value in enumerate([2, 4, 8])]
        result = meta.compute(config(effect_scale='ratio', model='fixed'), source(data))
        self.assertAlmostEqual(result['statistics']['fixed']['estimate'], 4)
        self.assertAlmostEqual(result['statistics']['fixed']['analysis_estimate'], math.log(4))
        self.assertTrue(result['statistics']['display_ratio'])
        log_data = [{**r, 'effect': math.log(r['effect'])} for r in data]
        log_result = meta.compute(config(effect_scale='log_ratio'), source(log_data))
        self.assertEqual(result['statistics']['random'], log_result['statistics']['random'])

    def test_ratio_ci_converts_se_on_log_scale(self):
        z = 1.959963984540054
        data = [{'study': str(i), 'effect': 2, 'lower': math.exp(math.log(2) - z * .2), 'upper': math.exp(math.log(2) + z * .2)} for i in range(3)]
        cfg = config(columns={'study': 'study', 'effect': 'effect', 'lower': 'lower', 'upper': 'upper'}, effect_scale='ratio')
        result = meta.compute(cfg, source(data))
        self.assertAlmostEqual(result['tables']['studies']['rows'][0]['analysis_se'], .2)
        self.assertAlmostEqual(result['statistics']['fixed']['estimate'], 2)

    def test_invalid_ratios_standard_error_and_duplicate_studies(self):
        bad = [[{'study': 'a', 'effect': 0, 'se': .1}, {'study': 'b', 'effect': 2, 'se': .1}],
               [{'study': 'a', 'effect': 1, 'se': 0}, {'study': 'b', 'effect': 2, 'se': .1}],
               [{'study': 'a', 'effect': 1, 'se': .1}, {'study': 'a', 'effect': 2, 'se': .1}]]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                meta.compute(config(effect_scale='ratio'), source(data))

    def test_egger_matches_ols_only_when_eligible(self):
        import statsmodels.api as sm
        data = rows(12)
        result = meta.compute(config(), source(data))
        yi = np.array([r['effect'] for r in data]); se = np.array([r['se'] for r in data])
        expected = sm.OLS(yi / se, sm.add_constant(1 / se)).fit()
        self.assertAlmostEqual(result['statistics']['egger']['intercept'], expected.params[0])
        self.assertAlmostEqual(result['statistics']['egger']['p'], expected.pvalues[0])
        small = meta.compute(config(), source(data[:9]))
        self.assertIsNone(small['statistics']['egger'])
        self.assertTrue(any('少于 10' in warning for warning in small['warnings']))

    def test_egger_does_not_apply_ordinary_test_to_or_or_smd(self):
        for measure, scale in [('or', 'log_ratio'), ('smd', 'difference')]:
            result = meta.compute(config(effect_measure=measure, effect_scale=scale), source(rows()))
            self.assertIsNone(result['statistics']['egger'])
            self.assertTrue(any('人为相关' in warning for warning in result['warnings']))
        with self.assertRaisesRegex(ValueError, '尺度不一致'):
            meta.validate(config(effect_measure='or', effect_scale='difference'))

    def test_baujat_q_contributions_sum_to_q_and_influence_definition(self):
        data = rows()
        result = meta.compute(config('meta_baujat'), source(data))
        self.assertAlmostEqual(sum(r['q_contribution'] for r in result['tables']['baujat']['rows']), result['statistics']['q'])
        weights = np.array([1 / r['se'] ** 2 for r in data])
        effects = np.array([r['effect'] for r in data])
        pooled = float(np.sum(weights * effects) / weights.sum())
        leave = float(np.sum(weights[1:] * effects[1:]) / weights[1:].sum())
        self.assertAlmostEqual(result['tables']['baujat']['rows'][0]['influence'], (leave - pooled) ** 2 * weights[1:].sum())

    def test_two_study_leave_one_out_does_not_invent_tau(self):
        result = meta.compute(config(), source(rows(2)))
        self.assertEqual(len(result['tables']['leave_one_out']['rows']), 2)
        self.assertIsNone(result['tables']['leave_one_out']['rows'][0]['tau2'])
        self.assertIsNone(result['tables']['leave_one_out']['rows'][0]['i2'])

    def test_missing_strategy_and_independence_required(self):
        data = rows(4); data[0]['effect'] = None
        with self.assertRaisesRegex(ValueError, '缺失'):
            meta.compute(config(), source(data))
        result = meta.compute(config(missing='drop'), source(data))
        self.assertEqual(result['statistics']['excluded_missing'], 1)
        with self.assertRaisesRegex(ValueError, '独立'):
            meta.compute(config(independent_units=False), source(rows()))

    def test_study_limit_and_forest_height_remain_readable(self):
        cfg = config()
        result = meta.compute(cfg, source(rows(50)))
        figure = meta.draw(result, cfg, {'width': 8, 'height': 3})
        self.assertGreaterEqual(figure.get_figheight(), 11)
        self.assertLessEqual(figure.get_figheight(), 16)
        with self.assertRaisesRegex(ValueError, '50'):
            meta.compute(cfg, source(rows(51)))

    def test_ratio_log_ticks_do_not_show_literal_math_markup(self):
        from app import analysis
        cfg = config(effect_scale='ratio')
        data = [{'study': str(i), 'effect': value, 'se': .1} for i, value in enumerate([2, 4, 8])]
        result = meta.compute(cfg, source(data))
        svg = analysis.render(result, cfg, {'width': 7, 'height': 4, 'dpi': 90, 'font_size': 9, 'palette': 'muted'}, 'Ratio effects', 'svg')
        self.assertNotIn(b'mathdefault', svg)

    def test_cancel_and_strict_params(self):
        event = threading.Event(); event.set()
        with self.assertRaisesRegex(ValueError, '取消'):
            meta.compute(config(), source(rows()), cancel=event)
        with self.assertRaises(ValueError):
            meta.validate(config(effect_scale='auto'))
        with self.assertRaises(ValueError):
            meta.validate(config(raw_code='anything'))

    def test_all_meta_views_render_saved_results_without_compute(self):
        from unittest.mock import patch
        data = rows(4)
        for method in ('meta_forest', 'meta_funnel', 'meta_sensitivity', 'meta_baujat'):
            with self.subTest(method=method):
                cfg = config(method)
                result = meta.compute(cfg, source(data))
                with patch.object(meta, '_combine', side_effect=AssertionError('render must not refit')):
                    figure = meta.draw(result, cfg, {'width': 7, 'height': 5}, title='Synthetic AI study comparison')
                    for fmt in ('png', 'svg', 'pdf'):
                        output = io.BytesIO(); figure.savefig(output, format=fmt)
                        self.assertGreater(len(output.getvalue()), 100)


if __name__ == '__main__':
    unittest.main()
