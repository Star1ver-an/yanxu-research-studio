"""Inverse-variance meta-analysis of independently estimated study effects.

No article extraction or raw-arm effect estimation is inferred here. Reference:
https://www.statsmodels.org/stable/generated/statsmodels.stats.meta_analysis.combine_effects.html
Paule–Mandel variance uses the library implementation. Egger intercept uses
statsmodels OLS; <10 studies are explicitly not tested (Cochrane Handbook 13).
https://training.cochrane.org/handbook/current/chapter-13
"""
import json
import math

from .analysis_clinical import (_clean, _col, _common_parameters, _data, _numeric,
                                _param, _select, _software, _table, _tick, _validate)


def catalog():
    definitions = [('meta_forest', 'Meta 森林图与合并效应', '逐研究区间、固定与随机效应、异质性与权重。'),
                   ('meta_funnel', 'Meta 漏斗图与小样本效应检查', '研究效应对标准误与伪 95% 界限；达到研究数限制时才执行 Egger。'),
                   ('meta_sensitivity', 'Meta 留一法敏感性分析', '逐一移除一项独立研究后重新估计合并效应与异质性。'),
                   ('meta_baujat', 'Meta Baujat 影响诊断', '展示各研究对 Cochran Q 的贡献及对固定效应合并估计的标准化影响。')]
    output = []
    for ident, label, description in definitions:
        params = _common_parameters()
        params[1]['label'] = '已确认每行代表一项独立研究，未重复纳入队列/相关效应'
        output.append({'id': ident, 'label': label, 'family': 'meta', 'description': description,
                       'inputs': [{'key': 'data', 'label': '独立研究效应表', 'formats': ['csv', 'tsv', 'xlsx'], 'required': True}],
                       'columns': [_col('study', '研究名称（唯一）', 'categorical'), _col('effect', '已计算效应估计'),
                                   _col('standard_error', '标准误（比值类必须是 log 比值的 SE）', required=False),
                                   _col('lower', '已计算 95% CI 下限（与效应同尺度）', required=False),
                                   _col('upper', '已计算 95% CI 上限（与效应同尺度）', required=False)],
                       'parameters': [*params,
                                      _select('effect_measure', '明确效应类型（OR / SMD 不执行普通 Egger）', 'continuous_difference', [('continuous_difference', '未标准化连续差值 / 数值系数'), ('smd', '标准化均数差 SMD'), ('or', '比值比 OR'), ('rr', '风险比 RR'), ('hr', '风险比 HR')]),
                                      _select('effect_scale', '输入效应尺度（必须明确）', 'difference', [('difference', '差值/回归系数：原尺度'), ('ratio', '比值 OR/RR/HR：正数，分析时取 log'), ('log_ratio', '已有 log(OR/RR/HR)：分析时保持 log')]),
                                      _select('model', '展示的主模型', 'random', [('random', '随机效应：Paule–Mandel'), ('fixed', '共同效应：逆方差')]),
                                      _param('egger', '研究数 ≥10 时执行 Egger 小研究效应检验', 'boolean', True)],
                       'limitations': ['2–50 项独立研究；只接受效应+SE，或效应+双侧正态近似 95% CI；不从摘要猜测误差或样本量。',
                                       '比值类一律在 log 尺度合并；SE 也必须为 log 尺度，原比值 CI 会取 log 后反推 SE。',
                                       '区间采用正态近似；小研究数和异质性大时应由统计人员复核，漏斗不对称不等于发表偏倚。',
                                       '普通 Egger 仅在至少 10 项、精度有变化且效应类型适用时执行；OR / SMD 禁用，不以其替代 Harbord 等专门方法。',
                                       '不支持相关效应、多层模型、网络 Meta、Meta 回归或稀有事件连续性校正。']})
    return output


def validate(config):
    out = _validate(config, catalog())
    cols = out['columns']
    fields = [v for v in cols.values() if v]
    if len(fields) != len(set(fields)):
        raise ValueError('研究名、效应、标准误和区间必须映射到不同的列。')
    if bool(cols['lower']) != bool(cols['upper']):
        raise ValueError('95% CI 需要同时选择上下限。')
    if bool(cols['standard_error']) == bool(cols['lower']):
        raise ValueError('请在“标准误”和“95% CI 上下限”之间明确选择一种输入。')
    is_ratio = out['parameters']['effect_measure'] in {'or', 'rr', 'hr'}
    if is_ratio != (out['parameters']['effect_scale'] in {'ratio', 'log_ratio'}):
        raise ValueError('效应类型与尺度不一致；OR/RR/HR 必须选择原比值或 log 比值尺度，连续差值/SMD 使用差值尺度。')
    return out


def _combine(effect, variance, names=None):
    import numpy as np
    from statsmodels.stats.meta_analysis import combine_effects
    result = combine_effects(np.asarray(effect, dtype=float), np.asarray(variance, dtype=float), method_re='iterated',
                             row_names=names, use_t=False, alpha=.05, maxiter=1000, atol=1e-8)
    if result.tau2 < 0 or not all(math.isfinite(float(v)) for v in [result.mean_effect_fe, result.mean_effect_re,
                                                                 result.sd_eff_w_fe, result.sd_eff_w_re, result.tau2]):
        raise ValueError('Meta 数值估计未能稳定收敛，请检查效应与标准误的尺度。')
    if result.tau2 > 0:
        weights = 1 / (np.asarray(variance) + result.tau2)
        residual_q = float(np.sum(weights * (np.asarray(effect) - result.mean_effect_re) ** 2))
        if abs(residual_q - (len(effect) - 1)) > .001:
            raise ValueError('Paule–Mandel 方差未收敛，不保存不可靠的合并结果。')
    return result


def compute(config, sources, cancel=None, progress=None):
    import numpy as np
    from scipy.stats import norm, chi2
    import statsmodels.api as sm
    config = validate(config)
    _tick(cancel, progress, '检查研究独立性、效应尺度与标准误')
    c, p = config['columns'], config['parameters']
    frame, stats, notes = _data(config, sources, 50)
    if len(frame) < 2:
        raise ValueError('Meta 至少需要两项独立研究。')
    names = frame[c['study']].astype(str).tolist()
    if len(set(names)) != len(names):
        raise ValueError('研究名称重复；请先确认独立研究与重复队列，不能自动合并相关效应。')
    numeric = [c['effect']] + ([c['standard_error']] if c['standard_error'] else [c['lower'], c['upper']])
    _numeric(frame, numeric)
    effect = frame[c['effect']].to_numpy(dtype=float)
    ratio = p['effect_scale'] in {'ratio', 'log_ratio'}
    warn = []
    if p['effect_scale'] == 'ratio':
        if (effect <= 0).any():
            raise ValueError('原尺度比值效应必须大于 0；负值 log 比值请明确选择 log 比值尺度。')
        effect = np.log(effect)
    z = float(norm.ppf(.975))
    if c['standard_error']:
        se = frame[c['standard_error']].to_numpy(dtype=float)
        notes.append('使用明确提供的标准误；比值类标准误按 log 比值尺度解释。')
    else:
        low, high = frame[c['lower']].to_numpy(dtype=float), frame[c['upper']].to_numpy(dtype=float)
        if p['effect_scale'] == 'ratio':
            if (low <= 0).any() or (high <= 0).any():
                raise ValueError('原尺度比值置信区间上下限必须大于 0。')
            low, high = np.log(low), np.log(high)
        if not ((low < high) & (low <= effect) & (effect <= high)).all():
            raise ValueError('效应必须位于递增的 95% CI 内。')
        se = (high - low) / (2 * z)
        if (np.abs(effect - (low + high) / 2) > .1 * (high - low)).any():
            warn.append('部分区间在分析尺度明显不对称；由正态 95% CI 反推 SE 可能不适用，请核对原研究。')
        notes.append('SE=(分析尺度上限−分析尺度下限)/(2×Φ⁻¹(0.975))，只适用于双侧正态近似 95% CI。')
    if not np.isfinite(se).all() or (se <= 0).any() or (se < 1e-10).any() or (np.abs(effect) > 1e6).any():
        raise ValueError('标准误必须为正且不小于 1e-10，效应不得超过数值稳定范围。')
    if ratio and ((np.abs(effect) + z * se) > 500).any():
        raise ValueError('比值类效应/区间超出稳定的指数回变换范围，请核对 log 尺度和 SE。')
    variance = se ** 2
    _tick(cancel, progress, '计算共同效应与 Paule–Mandel 随机效应')
    model = _combine(effect, variance, names)
    k = len(effect)
    # Algebraically identical to Cochran Q, using centered residuals to avoid
    # catastrophic cancellation for large common offsets and tiny SE values.
    q = float(np.sum((effect - model.mean_effect_fe) ** 2 / variance))
    i2 = max(0., (q - k + 1) / q) if q > 0 else 0.
    transform = (lambda v: float(np.exp(v))) if ratio else (lambda v: float(v))
    fixed = {'estimate': transform(model.mean_effect_fe), 'lower': transform(model.mean_effect_fe - z * model.sd_eff_w_fe),
             'upper': transform(model.mean_effect_fe + z * model.sd_eff_w_fe), 'analysis_estimate': float(model.mean_effect_fe), 'analysis_se': float(model.sd_eff_w_fe)}
    random = {'estimate': transform(model.mean_effect_re), 'lower': transform(model.mean_effect_re - z * model.sd_eff_w_re),
              'upper': transform(model.mean_effect_re + z * model.sd_eff_w_re), 'analysis_estimate': float(model.mean_effect_re), 'analysis_se': float(model.sd_eff_w_re)}
    stats.update({'study_n': k, 'effect_scale': p['effect_scale'], 'effect_measure': p['effect_measure'], 'display_ratio': ratio, 'selected_model': p['model'], 'fixed': fixed,
                  'random': random, 'q': q, 'q_df': k - 1, 'q_p': float(chi2.sf(q, k - 1)), 'i2': i2, 'tau2': float(model.tau2),
                  'tau2_method': 'Paule–Mandel (statsmodels iterated)', 'interval_method': 'normal approximation, two-sided 95%', 'egger': None})
    studies = []
    for index, name in enumerate(names):
        studies.append({'study': name, 'effect': transform(effect[index]), 'lower': transform(effect[index] - z * se[index]),
                        'upper': transform(effect[index] + z * se[index]), 'analysis_effect': float(effect[index]), 'analysis_se': float(se[index]),
                        'fixed_weight': float(model.weights_rel_fe[index]), 'random_weight': float(model.weights_rel_re[index])})
    leave, baujat = [], []
    # With only one remaining study tau² has no identifiable between-study meaning.
    for index, name in enumerate(names):
        _tick(cancel, progress, f'留一法 {index + 1}/{k}')
        keep = np.arange(k) != index
        if k == 2:
            mean, stderr = float(effect[keep][0]), float(se[keep][0])
            leave_tau, leave_i2 = None, None
            leave_fixed, leave_fixed_var = mean, stderr ** 2
        else:
            fitted = _combine(effect[keep], variance[keep])
            mean = float(fitted.mean_effect_re if p['model'] == 'random' else fitted.mean_effect_fe)
            stderr = float(fitted.sd_eff_w_re if p['model'] == 'random' else fitted.sd_eff_w_fe)
            leave_tau = float(fitted.tau2)
            leave_q = float(np.sum((effect[keep] - fitted.mean_effect_fe) ** 2 / variance[keep]))
            leave_i2 = max(0., (leave_q - (k - 2)) / leave_q) if leave_q else 0.
            leave_fixed, leave_fixed_var = float(fitted.mean_effect_fe), float(fitted.sd_eff_w_fe ** 2)
        leave.append({'omitted_study': name, 'remaining_studies': k - 1, 'estimate': transform(mean), 'lower': transform(mean - z * stderr),
                      'upper': transform(mean + z * stderr), 'tau2': leave_tau, 'i2': leave_i2})
        # Baujat's ordinate: squared difference in pooled FE estimates, divided
        # by the variance of the leave-one-out FE estimate (Baujat et al. 2002).
        baujat.append({'study': name, 'q_contribution': float((effect[index] - model.mean_effect_fe) ** 2 / variance[index]),
                       'influence': float((leave_fixed - model.mean_effect_fe) ** 2 / leave_fixed_var)})
    if p['egger']:
        if p['effect_measure'] in {'or', 'smd'}:
            warn.append('OR / SMD 的效应与标准误可能存在人为相关，未执行普通 Egger；需要适合该效应类型的专门方法。')
        elif k < 10:
            warn.append('研究数少于 10，未执行 Egger；小研究数时检验效能不足，漏斗不对称不能诊断发表偏倚。')
        elif np.ptp(1 / se) < 1e-10:
            warn.append('各研究精度相同，Egger 截距模型不可识别，未执行检验。')
        else:
            regression = sm.OLS(effect / se, sm.add_constant(1 / se)).fit()
            stats['egger'] = {'intercept': float(regression.params[0]), 'se': float(regression.bse[0]),
                              't': float(regression.tvalues[0]), 'p': float(regression.pvalues[0]), 'df': int(regression.df_resid),
                              'description': 'OLS standardized effect ~ intercept + precision; two-sided t test for intercept'}
    if k < 5:
        warn.append('研究数较少，随机效应方差和正态近似区间不稳定，应谨慎解释并作方法敏感性复核。')
    notes.extend(['随机效应采用 Paule–Mandel，tau² 约束非负；同时保留共同效应结果供比较。',
                  '输入差值或 log 比值在分析尺度合并；比值显示值经 exp 回变换，tau² 保持分析尺度。',
                  'Baujat 横轴=(yi−共同效应均值)²/vi，纵轴=(留一共同效应均值−全体共同效应均值)²/留一共同效应方差。',
                  '漏斗伪区间使用正态 95% 界限，未进行 trim-and-fill 或推断缺失研究。',
                  '图上过长研究名以行号与省略号显示，完整名称保存在结果表；Baujat 超过 10 项时只标注影响最大的 10 项，全部研究点仍显示。',
                  'https://www.statsmodels.org/stable/generated/statsmodels.stats.meta_analysis.combine_effects.html'])
    result = _clean({'method': config['method'], 'tables': {'studies': _table(studies), 'leave_one_out': _table(leave), 'baujat': _table(baujat)},
                     'statistics': stats, 'warnings': [*warn], 'notes': notes, 'software': _software('statsmodels', 'scipy', 'numpy', 'pandas')})
    _tick(cancel)
    if len(json.dumps(result, allow_nan=False).encode()) > 20 * 1024 * 1024:
        raise ValueError('分析结果超过 20 MB。')
    return result


def draw(result, config, style, title=''):
    from matplotlib.figure import Figure
    from .plot_engine import PALETTES
    import numpy as np
    import textwrap
    stats, tables = result['statistics'], result['tables']
    method, ratio = result['method'], stats['display_ratio']
    figure = Figure(figsize=(style.get('width', 8), style.get('height', 6)), layout='constrained')
    ax = figure.add_subplot()
    palette = PALETTES.get(style.get('palette', 'muted'), PALETTES['muted'])
    color = palette[0]
    selected = stats[stats['selected_model']]
    if method in {'meta_forest', 'meta_sensitivity'}:
        rows = tables['studies' if method == 'meta_forest' else 'leave_one_out']['rows']
        labels = [r['study'] if method == 'meta_forest' else '− ' + r['omitted_study'] for r in rows]
        labels = [label if len(label) <= 36 else f'[{i + 1}] ' + label[:29] + '…' for i, label in enumerate(labels)]
        figure.set_figheight(max(figure.get_figheight(), len(rows) * .19 + 2))
        y = list(range(len(rows)))
        values = [r['effect'] if method == 'meta_forest' else r['estimate'] for r in rows]
        ax.errorbar(values, y, xerr=[[v - r['lower'] for v, r in zip(values, rows)], [r['upper'] - v for v, r in zip(values, rows)]], fmt='s', color=color, capsize=3, markersize=4)
        ax.errorbar([selected['estimate']], [len(rows) + .5], xerr=[[selected['estimate'] - selected['lower']], [selected['upper'] - selected['estimate']]], fmt='D', color=palette[1], capsize=4)
        ax.set_yticks([*y, len(rows) + .5], [*labels, stats['selected_model'] + ' pooled'])
        ax.axvline(1 if ratio else 0, color='#A0ABB5', linestyle='--')
        if ratio:
            from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
            ax.set_xscale('log')
            ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:g}'))
            ax.xaxis.set_minor_formatter(NullFormatter())
        ax.invert_yaxis(); ax.set_xlabel('Ratio (95% CI)' if ratio else 'Effect (95% CI)')
        ax.text(.99, .01, f'I²={stats["i2"] * 100:.1f}%   τ²={stats["tau2"]:.4g}   Q p={stats["q_p"]:.3g}', transform=ax.transAxes, ha='right', fontsize=8)
    elif method == 'meta_funnel':
        rows = tables['studies']['rows']
        se = [r['analysis_se'] for r in rows]
        mean = selected['analysis_estimate']
        ax.scatter([r['analysis_effect'] for r in rows], se, color=color, s=24)
        span = np.linspace(0, max(se) * 1.12, 100)
        ax.plot(mean - 1.959963984540054 * span, span, '--', color='#A0ABB5')
        ax.plot(mean + 1.959963984540054 * span, span, '--', color='#A0ABB5')
        ax.axvline(mean, color=palette[1]); ax.invert_yaxis()
        ax.set(xlabel='Log ratio' if ratio else 'Effect', ylabel='Standard error')
        if stats['egger']:
            ax.text(.02, .97, f'Egger p={stats["egger"]["p"]:.4g}', transform=ax.transAxes, va='top', fontsize=8)
    else:
        rows = tables['baujat']['rows']
        ax.scatter([r['q_contribution'] for r in rows], [r['influence'] for r in rows], s=25, color=color)
        labeled = sorted(rows, key=lambda r: r['q_contribution'] + r['influence'], reverse=True)[:10]
        for row in labeled:
            label = row['study'] if len(row['study']) <= 28 else row['study'][:25] + '…'
            ax.annotate(label, (row['q_contribution'], row['influence']), xytext=(4, 4), textcoords='offset points', fontsize=7)
        ax.set(xlabel='Contribution to Cochran Q', ylabel='Standardized influence on pooled fixed effect')
    figure.suptitle('\n'.join(textwrap.wrap(title or next(x['label'] for x in catalog() if x['id'] == method), max(20, int(style.get('width', 8) * 9)))), fontsize=12)
    return figure
