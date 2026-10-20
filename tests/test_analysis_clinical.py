"""Numerical/assumption checks on synthetic device reliability and AI data."""
import csv
import io
import json
import threading
import unittest

import numpy as np

from app import analysis_clinical as clinical


def source(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return [{'id': 'table', 'name': 'synthetic-devices.csv', 'raw': buf.getvalue().encode()}]


def config(method='km', columns=None, **parameters):
    default = {'duration': 'time', 'event': 'event'} if method in {'km', 'cif', 'cox'} else {'outcome': 'label', 'probability': 'probability'}
    if method == 'cox':
        default['numeric_covariates'] = ['load']
    if method == 'logistic_model':
        default = {'outcome': 'label', 'numeric_covariates': ['load', 'memory']}
    return {'method': method, 'inputs': {'data': 'table'}, 'columns': columns or default,
            'parameters': {'independent_units': True, **parameters}}


def binary_rows(n=100):
    rng = np.random.default_rng(42)
    rows = []
    for i in range(n):
        load, memory = rng.normal(size=2)
        probability = 1 / (1 + np.exp(-(.5 * load - .3 * memory)))
        label = int(rng.random() < probability)
        rows.append({'unit': i, 'label': label, 'probability': probability, 'load': load, 'memory': memory})
    return rows


def survival_rows(n=300):
    rng = np.random.default_rng(30)
    rows = []
    for i in range(n):
        load = float(rng.normal())
        group = 'accelerator' if i % 2 else 'baseline'
        time = float(rng.exponential(10 / np.exp(.7 * load + .4 * (group == 'accelerator'))))
        censor = float(rng.uniform(3, 20))
        rows.append({'unit': i, 'time': min(time, censor), 'event': int(time <= censor), 'load': load, 'group': group})
    return rows


class ClinicalAnalysisTests(unittest.TestCase):
    def assert_json(self, result):
        json.dumps(result, allow_nan=False)
        self.assertTrue({'method', 'tables', 'statistics', 'warnings', 'notes', 'software'} <= set(result))

    def test_catalog_and_strict_whitelist(self):
        self.assertEqual({x['id'] for x in clinical.catalog()}, {'km', 'cif', 'cox', 'binary_evaluation', 'logistic_model'})
        for bad in [dict(config(), generated_code='evil'), config(unknown=True), config(seed=True), config(seed=1.5), config(seed=-1), config(missing='guess')]:
            with self.subTest(config=bad), self.assertRaises(ValueError):
                clinical.validate(bad)
        bad = config('cox', {'duration': 'time', 'event': 'event', 'numeric_covariates': [{}]})
        with self.assertRaises(ValueError):
            clinical.validate(bad)

    def test_independent_units_confirmation_and_id_guard(self):
        rows = [{'time': 1, 'event': 1, 'unit': 'a'}, {'time': 2, 'event': 0, 'unit': 'a'}]
        with self.assertRaisesRegex(ValueError, '独立'):
            clinical.compute(config(independent_units=False), source(rows))
        cfg = config(columns={'duration': 'time', 'event': 'event', 'subject_id': 'unit'})
        with self.assertRaisesRegex(ValueError, 'ID 重复'):
            clinical.compute(cfg, source(rows))

    def test_km_known_product_limit_and_risk_before_time(self):
        rows = [{'time': 1, 'event': 1}, {'time': 2, 'event': 1}, {'time': 3, 'event': 0}, {'time': 4, 'event': 1}]
        result = clinical.compute(config(risk_points=5), source(rows))
        curve = {r['time']: r['estimate'] for r in result['tables']['curve']['rows']}
        for time, expected in [(0, 1), (1, .75), (2, .5), (3, .5), (4, 0)]:
            self.assertAlmostEqual(curve[time], expected)
        self.assertEqual([r['at_risk'] for r in result['tables']['risk']['rows']], [4, 4, 3, 2, 1])
        self.assertEqual(result['tables']['censor']['rows'][0]['time'], 3)
        self.assert_json(result)

    def test_km_confidence_interval_and_logrank_match_library(self):
        from lifelines import KaplanMeierFitter
        from lifelines.statistics import multivariate_logrank_test
        rows = survival_rows()
        cfg = config(columns={'duration': 'time', 'event': 'event', 'group': 'group'})
        result = clinical.compute(cfg, source(rows))
        expected = multivariate_logrank_test([r['time'] for r in rows], [r['group'] for r in rows], [r['event'] for r in rows])
        self.assertAlmostEqual(result['statistics']['logrank']['p'], expected.p_value)
        baseline = [r for r in rows if r['group'] == 'baseline']
        fit = KaplanMeierFitter().fit([r['time'] for r in baseline], [r['event'] for r in baseline])
        first = [r for r in result['tables']['curve']['rows'] if r['group'] == 'baseline'][1]
        self.assertAlmostEqual(first['lower'], fit.confidence_interval_.iloc[1, 0])
        self.assertAlmostEqual(first['upper'], fit.confidence_interval_.iloc[1, 1])

    def test_missing_strategy_is_explicit(self):
        rows = [{'time': 1, 'event': 1}, {'time': None, 'event': 0}, {'time': 3, 'event': 0}]
        with self.assertRaisesRegex(ValueError, '缺失'):
            clinical.compute(config(), source(rows))
        result = clinical.compute(config(missing='drop'), source(rows))
        self.assertEqual(result['statistics']['excluded_missing'], 1)
        self.assertEqual(result['statistics']['analyzed_n'], 2)

    def test_km_rejects_competing_events_and_bad_duration(self):
        for rows in [[{'time': 1, 'event': 2}], [{'time': -1, 'event': 1}], [{'time': 'bad', 'event': 1}]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                clinical.compute(config(), source(rows))

    def test_cif_keeps_competing_events(self):
        rows = [{'time': 1, 'event': 2}, {'time': 2, 'event': 1}, {'time': 3, 'event': 1}, {'time': 4, 'event': 0}]
        result = clinical.compute(config('cif'), source(rows))
        final = result['tables']['curve']['rows'][-1]['estimate']
        self.assertAlmostEqual(final, .5)
        # Treating event 2 as censoring would yield 2/3, a materially wrong answer.
        self.assertNotAlmostEqual(final, 2 / 3)
        self.assertEqual(result['tables']['groups']['rows'][0]['competing_events'], 1)
        self.assertNotIn('logrank', result['statistics'])
        self.assert_json(result)

    def test_cif_ties_are_reproducible_and_documented(self):
        rows = [{'time': 1, 'event': 2}, {'time': 1, 'event': 1}, {'time': 2, 'event': 1}, {'time': 3, 'event': 0}]
        a = clinical.compute(config('cif'), source(rows))
        b = clinical.compute(config('cif'), source(rows))
        self.assertEqual(a, b)
        self.assertTrue(any('微扰' in x for x in a['notes']))

    def test_cif_tiny_time_units_do_not_jitter_before_zero(self):
        rows = [{'time': 1e-8, 'event': 1}, {'time': 1e-8, 'event': 2}, {'time': 2e-8, 'event': 1}, {'time': 3e-8, 'event': 0}]
        result = clinical.compute(config('cif'), source(rows))
        self.assertTrue(all(r['time'] >= 0 for r in result['tables']['curve']['rows']))

    def test_cif_rejects_unrepresentable_jitter_instead_of_library_recursion(self):
        rows = [{'time': 1e20, 'event': 1}, {'time': 1e20, 'event': 2}, {'time': 2e20, 'event': 0}]
        with self.assertRaisesRegex(ValueError, '数值尺度'):
            clinical.compute(config('cif'), source(rows))

    def test_cox_matches_lifelines_and_ph_test(self):
        import pandas as pd
        from lifelines import CoxPHFitter
        from lifelines.statistics import proportional_hazard_test
        rows = survival_rows()
        result = clinical.compute(config('cox'), source(rows))
        data = pd.DataFrame(rows)[['time', 'event', 'load']]
        fitter = CoxPHFitter().fit(data, 'time', 'event')
        expected_ph = proportional_hazard_test(fitter, data, time_transform='rank')
        row = result['tables']['coefficients']['rows'][0]
        self.assertAlmostEqual(row['hr'], fitter.hazard_ratios_['load'])
        self.assertAlmostEqual(row['ph_p'], expected_ph.summary.loc['load', 'p'])
        self.assert_json(result)

    def test_cox_categories_require_explicit_existing_reference(self):
        cfg = config('cox', {'duration': 'time', 'event': 'event', 'categorical_covariates': ['group']})
        with self.assertRaisesRegex(ValueError, '参照'):
            clinical.validate(cfg)
        cfg['parameters']['reference_levels'] = '{"group":"baseline"}'
        result = clinical.compute(cfg, source(survival_rows()))
        self.assertIn('accelerator vs baseline', result['tables']['coefficients']['rows'][0]['covariate'])
        cfg['parameters']['reference_levels'] = '{"group":"nonexistent"}'
        with self.assertRaisesRegex(ValueError, '参照'):
            clinical.compute(cfg, source(survival_rows()))

    def test_cox_refuses_low_events_constant_and_collinearity(self):
        few = [{'time': i + 1, 'event': int(i < 3), 'load': i} for i in range(30)]
        with self.assertRaisesRegex(ValueError, '事件'):
            clinical.compute(config('cox'), source(few))
        rows = survival_rows()
        for r in rows:
            r['copy_load'] = r['load']
        cfg = config('cox', {'duration': 'time', 'event': 'event', 'numeric_covariates': ['load', 'copy_load']})
        with self.assertRaisesRegex(ValueError, '共线'):
            clinical.compute(cfg, source(rows))

    def test_binary_metrics_match_sklearn_and_bootstrap_seed(self):
        from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
        rows = binary_rows()
        cfg = config('binary_evaluation', evaluation_independent=True, bootstrap=100)
        a = clinical.compute(cfg, source(rows))
        b = clinical.compute(cfg, source(rows))
        self.assertEqual(a, b)
        y, p = [r['label'] for r in rows], [r['probability'] for r in rows]
        for key, expected in [('roc_auc', roc_auc_score(y, p)), ('average_precision', average_precision_score(y, p)), ('brier', brier_score_loss(y, p))]:
            self.assertAlmostEqual(a['statistics'][key], expected)
            low, high = a['statistics'][key + '_ci']
            self.assertLessEqual(low, high)
        self.assert_json(a)

    def test_binary_independence_outcome_probability_checks(self):
        with self.assertRaisesRegex(ValueError, '独立评估'):
            clinical.compute(config('binary_evaluation', bootstrap=100), source(binary_rows()))
        rows = binary_rows()
        rows[0]['probability'] = 1.2
        with self.assertRaisesRegex(ValueError, '0–1'):
            clinical.compute(config('binary_evaluation', evaluation_independent=True, bootstrap=100), source(rows))

    def test_dca_known_net_benefit(self):
        rows = [{'label': int(i >= 10), 'probability': .9 if i >= 10 else .1} for i in range(20)]
        result = clinical.compute(config('binary_evaluation', evaluation_independent=True, bootstrap=100), source(rows))
        mid = min(result['tables']['dca']['rows'], key=lambda r: abs(r['threshold'] - .5))
        self.assertAlmostEqual(mid['model'], .5)
        self.assertAlmostEqual(mid['all'], 0)

    def test_logistic_split_has_no_scaler_leakage_and_formula_reproduces(self):
        from sklearn.model_selection import train_test_split
        rows = binary_rows(300)
        cfg = config('logistic_model', bootstrap=100)
        result = clinical.compute(cfg, source(rows))
        stats = result['statistics']
        train, test = train_test_split(np.arange(len(rows)), test_size=.3, random_state=42, stratify=[r['label'] for r in rows])
        self.assertEqual(stats['training_rows'], (train + 1).tolist())
        self.assertEqual(set(stats['training_rows']) & set(stats['holdout_rows']), set())
        self.assertAlmostEqual(stats['training_means']['load'], np.mean([rows[i]['load'] for i in train]))
        coefficients = result['tables']['coefficients']['rows']
        for prediction in result['tables']['holdout_predictions']['rows']:
            row = rows[prediction['data_row'] - 1]
            logit = stats['intercept_original_units'] + sum(c['coef_original_unit'] * row[c['variable']] for c in coefficients)
            self.assertAlmostEqual(1 / (1 + np.exp(-logit)), prediction['probability'])
            score = sum((row[c['variable']] - c['point_origin']) * c['points_per_unit'] for c in coefficients)
            self.assertAlmostEqual(stats['score_baseline_logit'] + score / stats['points_per_logit'], logit)
        self.assert_json(result)

    def test_cancel_before_analysis(self):
        event = threading.Event(); event.set()
        with self.assertRaisesRegex(ValueError, '取消'):
            clinical.compute(config(), source([{'time': 1, 'event': 1}]), cancel=event)

    def test_draw_renders_saved_results_without_refitting(self):
        from unittest.mock import patch
        import matplotlib
        from app.plot_engine import _fonts
        result = clinical.compute(config(), source([{'time': 1, 'event': 1}, {'time': 2, 'event': 0}]))
        with matplotlib.rc_context({'font.family': _fonts()}), patch('lifelines.KaplanMeierFitter.fit', side_effect=AssertionError('render must not refit')):
            figure = clinical.draw(result, config(), {'width': 6, 'height': 5}, title='Device survival')
            for fmt in ('png', 'svg', 'pdf'):
                output = io.BytesIO(); figure.savefig(output, format=fmt)
                self.assertGreater(len(output.getvalue()), 100)

    def test_all_clinical_views_export_after_statistics_are_frozen(self):
        from app import analysis
        cases = [
            (config('cif'), [{'time': 1, 'event': 1}, {'time': 2, 'event': 2}, {'time': 3, 'event': 0}]),
            (config('cox'), survival_rows()),
            (config('binary_evaluation', evaluation_independent=True, bootstrap=100), binary_rows()),
            (config('logistic_model', bootstrap=100), binary_rows(300)),
        ]
        style = {'width': 8, 'height': 6, 'dpi': 90, 'font_size': 9, 'palette': 'accessible'}
        for cfg, rows in cases:
            with self.subTest(method=cfg['method']):
                result = clinical.compute(cfg, source(rows))
                original = json.dumps(result, sort_keys=True)
                for fmt in ('png', 'svg', 'pdf', 'tiff'):
                    rendered = analysis.render(result, cfg, style, title='合成设备与 AI 评估', fmt=fmt)
                    self.assertGreater(len(rendered), 100)
                self.assertEqual(original, json.dumps(result, sort_keys=True))

    def test_cox_log_ticks_do_not_show_literal_math_markup(self):
        from app import analysis
        cfg = config('cox')
        result = clinical.compute(cfg, source(survival_rows()))
        svg = analysis.render(result, cfg, {'width': 7, 'height': 4, 'dpi': 90, 'font_size': 9, 'palette': 'muted'}, 'Device HR', 'svg')
        self.assertNotIn(b'mathdefault', svg)


if __name__ == '__main__':
    unittest.main()
