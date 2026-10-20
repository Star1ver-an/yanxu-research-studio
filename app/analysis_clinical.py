"""Bounded local survival/prediction workflows, with explicit assumptions.

Method references (consulted 2026-10-20):
https://lifelines.readthedocs.io/en/latest/fitters/univariate/KaplanMeierFitter.html
https://lifelines.readthedocs.io/en/latest/fitters/univariate/AalenJohansenFitter.html
https://lifelines.readthedocs.io/en/latest/Survival%20Regression.html
https://scikit-learn.org/stable/common_pitfalls.html
https://www.mskcc.org/sites/default/files/node/4509/documents/dca-paper.pdf
No arbitrary formulas, generated code, silent imputation or automatic retries.
"""
import importlib.metadata
import json
import math
import warnings as pywarnings


def _col(key, label, kind='numeric', required=True, multiple=False):
    return {'key': key, 'label': label, 'type': kind, 'required': required,
            'multiple': multiple, 'source': 'data'}


def _param(key, label, kind, default, **extra):
    return {'key': key, 'label': label, 'type': kind, 'default': default, **extra}


def _select(key, label, default, options):
    return _param(key, label, 'select', default,
                  options=[{'value': value, 'label': text} for value, text in options])


def _common_parameters():
    return [_select('missing', '缺失值策略', 'error', [('error', '发现缺失即停止'), ('drop', '显式完整案例分析（报告删除数）')]),
            _param('independent_units', '已确认每行是独立研究单位（重复测量不可作为独立样本）', 'boolean', False),
            _param('seed', '可复现随机种子', 'integer', 42, min=0, max=2147483647)]


def _entry(ident, label, description, columns, parameters=(), limitations=()):
    return {'id': ident, 'label': label, 'family': 'clinical', 'description': description,
            'inputs': [{'key': 'data', 'label': '逐个独立单位数据', 'formats': ['csv', 'tsv', 'xlsx'], 'required': True}],
            'columns': [*columns, _col('subject_id', '单位 ID（可选；若选择，要求唯一）', 'categorical', False)],
            'parameters': [*_common_parameters(), *parameters], 'limitations': list(limitations)}


def catalog():
    survival = [_col('duration', '随访/运行时长'), _col('event', '事件编码（0=删失，1=目标事件）'),
                _col('group', '分组（可选，最多 6 组）', 'categorical', False)]
    bootstrap = [_param('bootstrap', '分层 bootstrap 次数（95% 百分位区间）', 'integer', 200, min=100, max=1000)]
    return [
        _entry('km', 'Kaplan–Meier 生存分析', '从独立单位时长与 0/1 事件拟合曲线，含 95% CI、删失标记、在险人数及组间 log-rank。',
               survival, [_param('risk_points', '在险表时间点数', 'integer', 6, min=3, max=10)],
               ['右删失、独立单位，组内删失应与事件过程独立；不支持左截断、区间删失、加权或配对数据。', 'log-rank 仅总体组间检验；不自动进行多重两两比较。']),
        _entry('cif', '竞争风险累计发生率', '使用 Aalen–Johansen；0=删失，正整数为不同事件，竞争事件不会作为删失处理。',
               [survival[0], _col('event', '事件编码（0=删失，1/2/…=各事件）'), survival[2]],
               [_param('event_of_interest', '目标事件的正整数编码', 'integer', 1, min=1, max=100)],
               ['最多 2,000 行；同时间不同事件使用 lifelines 固定种子微扰，并明确报告。', '不提供 Gray 检验，不把普通 log-rank 用于竞争风险比较。']),
        _entry('cox', '多变量 Cox 比例风险回归', '数值协变量与明确参照水平的分类变量；输出 HR、95% CI 及 Schoenfeld 比例风险检验。',
               [survival[0], survival[1], _col('numeric_covariates', '数值协变量（多选）', required=False, multiple=True),
                _col('categorical_covariates', '分类协变量（多选）', 'categorical', False, True)],
               [_param('reference_levels', '分类参照水平 JSON，例如 {"model":"baseline"}', 'text', '{}')],
               ['每参数至少 10 个事件，最多 20 个编码参数、5,000 行；不接受完全分离、常数或共线设计。', '需条件独立删失及比例风险；不含时间依赖协变量、分层、聚类标准误或因果解释；PH 违背时需更换模型。']),
        _entry('binary_evaluation', '二分类独立评估：ROC / PR / 校准 / DCA', '输入已有独立评估集真实 0/1 标签与概率，计算 AUC、AP、Brier、bootstrap CI 和决策曲线。',
               [_col('outcome', '真实标签（0/1）'), _col('probability', '预测正类概率（0–1）')],
               [*bootstrap, _param('evaluation_independent', '已确认概率来自未参与训练或调参的评估集', 'boolean', False)],
               ['至少每类 5 个独立单位；bootstrap 按类别分层，不适用于重复测量或聚类数据。', 'DCA 使用事先定义的阈值范围 0.01–0.99；仅适用于代表性评估队列，病例对照/重采样队列不可直接解释净获益。', 'AP/Brier 的分层 bootstrap 固定当前阳性率；净获益不等同于临床有效性。']),
        _entry('logistic_model', '逻辑回归建模与内部留出评估', '先固定分层训练/测试拆分，再在训练集拟合标准化与 L2 逻辑回归；输出可复现评分公式和留出集评估。',
               [_col('outcome', '真实标签（0/1）'), _col('numeric_covariates', '数值预测变量（多选）', multiple=True)],
               [*bootstrap, _param('test_fraction', '内部留出集比例', 'number', 0.3, min=0.2, max=0.5),
                _param('regularization_c', 'L2 正则化 C（本次不调参）', 'number', 1.0, min=0.001, max=100)],
               ['仅数值预测变量、最多 20 列；每类至少 20 个单位；没有缺失值填补或特征筛选。', '内部留出不是外部验证；重复尝试种子/特征后指标不能继续视为无偏。', '正则化系数不提供普通最大似然 p 值或 Wald 区间；评分表为模型公式的可解释变换。']),
    ]


def _validate(config, entries):
    if not isinstance(config, dict) or set(config) - {'method', 'inputs', 'columns', 'parameters'}:
        raise ValueError('分析配置只允许 method、inputs、columns、parameters。')
    entry = next((x for x in entries if x['id'] == config.get('method')), None)
    if not entry:
        raise ValueError('不支持的统计分析方法。')
    out = {'method': entry['id'], 'inputs': {}, 'columns': {}, 'parameters': {}}
    for field in ('inputs', 'columns', 'parameters'):
        if not isinstance(config.get(field, {}), dict):
            raise ValueError(field + ' 必须是对象。')
    if set(config.get('inputs', {})) - {x['key'] for x in entry['inputs']}:
        raise ValueError('分析输入包含未知字段。')
    for spec in entry['inputs']:
        value = config.get('inputs', {}).get(spec['key'])
        if not isinstance(value, str) or not value or len(value) > 100:
            raise ValueError('请选择分析材料。')
        out['inputs'][spec['key']] = value
    cols = config.get('columns', {})
    if set(cols) - {x['key'] for x in entry['columns']}:
        raise ValueError('分析字段映射包含未知字段。')
    for spec in entry['columns']:
        value = cols.get(spec['key'])
        if spec['multiple']:
            if value is None or value == '':
                value = []
            if not isinstance(value, list) or len(value) > 20 or any(not isinstance(x, str) for x in value) or len(set(value)) != len(value):
                raise ValueError(spec['label'] + ' 必须为最多 20 个不重复列名。')
            values = value
        else:
            values = [] if value in (None, '') else [value]
        if spec['required'] and not values:
            raise ValueError('请选择：' + spec['label'])
        if any(not isinstance(x, str) or not x or len(x) > 200 for x in values):
            raise ValueError('字段必须为有效列名。')
        out['columns'][spec['key']] = list(values) if spec['multiple'] else (values[0] if values else None)
    raw = config.get('parameters', {})
    if set(raw) - {x['key'] for x in entry['parameters']}:
        raise ValueError('分析参数包含未知字段。')
    for spec in entry['parameters']:
        value = raw.get(spec['key'], spec['default'])
        kind = spec['type']
        if kind == 'boolean' and not isinstance(value, bool):
            raise ValueError(spec['label'] + ' 必须为布尔值。')
        if kind in {'number', 'integer'}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(spec['label'] + ' 必须为有限数值。')
            if kind == 'integer' and int(value) != value or not spec.get('min', -math.inf) <= value <= spec.get('max', math.inf):
                raise ValueError(spec['label'] + ' 超出允许范围。')
            value = int(value) if kind == 'integer' else float(value)
        if kind == 'select' and value not in [x['value'] for x in spec['options']]:
            raise ValueError(spec['label'] + ' 选项无效。')
        if kind == 'text' and (not isinstance(value, str) or len(value) > 4000):
            raise ValueError(spec['label'] + ' 必须为不超过 4,000 字符的文本。')
        out['parameters'][spec['key']] = value
    return out


def validate(config):
    out = _validate(config, catalog())
    columns = out['columns']
    all_fields = [v for v in columns.values() if isinstance(v, str)]
    for v in columns.values():
        if isinstance(v, list):
            all_fields.extend(v)
    if len(set(all_fields)) != len(all_fields):
        raise ValueError('时长、事件、分组、ID 与协变量不可重复映射到同一列。')
    if out['method'] == 'cox':
        if not columns['numeric_covariates'] and not columns['categorical_covariates']:
            raise ValueError('Cox 回归至少需要一个协变量。')
        try:
            refs = json.loads(out['parameters']['reference_levels'])
        except (ValueError, TypeError):
            raise ValueError('分类参照水平必须是 JSON 对象。') from None
        if not isinstance(refs, dict) or set(refs) != set(columns['categorical_covariates']) or any(not isinstance(v, (str, int, float)) or isinstance(v, bool) for v in refs.values()):
            raise ValueError('请为每个分类协变量明确填写且只填写一个参照水平。')
    return out


def _tick(cancel, progress=None, message=None):
    if cancel is not None and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel()):
        raise ValueError('分析已取消。')
    if progress and message:
        progress(message)


def _software(*names):
    return {name: importlib.metadata.version(name) for name in names}


def _clean(value):
    """Strict JSON values; undefined estimates become null with contextual notes."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(x) for x in value]
    if hasattr(value, 'item'):
        return _clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _table(rows, columns=None):
    return {'columns': list(columns or (rows[0].keys() if rows else [])), 'rows': _clean(rows)}


def _data(config, sources, max_rows=10000):
    import pandas as pd
    from .analysis_common import source_table
    table = source_table(config, sources, key='data')
    cols = config['columns']
    fields = []
    for value in cols.values():
        fields.extend(value if isinstance(value, list) else [value] if value else [])
    if any(field not in [x['name'] for x in table['columns']] for field in fields):
        raise ValueError('所选列已不存在，请重新映射分析字段。')
    if len(table['rows']) > max_rows:
        raise ValueError(f'本分析最多支持 {max_rows:,} 行，请使用事先定义的独立分析队列。')
    if not config['parameters']['independent_units']:
        raise ValueError('请先确认每行是独立研究单位；重复测量或相关单位不能直接作为独立样本。')
    frame = pd.DataFrame(table['rows'])[fields].copy()
    count = len(frame)
    missing = frame.isna().any(axis=1)
    excluded = int(missing.sum())
    if excluded and config['parameters']['missing'] == 'error':
        raise ValueError(f'分析字段有 {excluded} 行缺失；请修正或显式选择完整案例分析。')
    frame = frame.loc[~missing].copy()
    if frame.empty:
        raise ValueError('完整案例为空，无法分析。')
    ident = cols.get('subject_id')
    if ident and frame[ident].astype(str).duplicated().any():
        raise ValueError('单位 ID 重复；当前方法不接受重复测量或聚类单位。')
    notes = [*table.get('warnings', [])]
    if excluded:
        notes.append(f'按显式完整案例策略删除 {excluded}/{count} 行，未作任何缺失值填补。')
    return frame, {'input_n': count, 'analyzed_n': len(frame), 'excluded_missing': excluded}, notes


def _numeric(frame, columns):
    import numpy as np
    for name in columns:
        values = frame[name].tolist()
        if any(isinstance(v, (str, bool)) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise ValueError(f'列“{name}”需要有限数值，不能把文本或类别猜测为数值。')
        frame[name] = np.asarray(values, dtype=float)


def _groups(frame, group):
    if group:
        labels = frame[group].astype(str)
        names = sorted(labels.unique().tolist())
        if len(names) > 6:
            raise ValueError('分组最多 6 组；请使用预先定义的分组。')
        return [(name, frame.loc[labels == name].copy()) for name in names]
    return [('全体', frame.copy())]


def _survival(config, sources, cancel, progress):
    import numpy as np
    from lifelines import KaplanMeierFitter, AalenJohansenFitter
    from lifelines.statistics import multivariate_logrank_test
    method, c, p = config['method'], config['columns'], config['parameters']
    frame, stats, notes = _data(config, sources, 2000 if method == 'cif' else 10000)
    _numeric(frame, [c['duration'], c['event']])
    duration, events = frame[c['duration']], frame[c['event']]
    if (duration < 0).any() or method == 'cif' and (duration <= 0).any():
        raise ValueError('时长不得为负；竞争风险分析要求时长大于 0。')
    if method == 'km' and not events.isin([0, 1]).all():
        raise ValueError('KM 事件必须明确编码为 0=删失、1=事件；有竞争事件请使用竞争风险分析。')
    if method == 'cif' and (not (events % 1 == 0).all() or (events < 0).any() or (events > 100).any()):
        raise ValueError('竞争风险事件必须为 0–100 的整数编码，0 为删失。')
    target = p.get('event_of_interest', 1)
    if not (events == target).any():
        raise ValueError('未观察到目标事件，无法估计所请求结果。')
    curves, risks, censor, summary = [], [], [], []
    groups = _groups(frame, c['group'])
    risk_times = np.linspace(0, float(duration.max()), p.get('risk_points', 6))
    for name, data in groups:
        _tick(cancel, progress, '拟合 ' + name)
        t, e = data[c['duration']], data[c['event']].astype(int)
        with pywarnings.catch_warnings(record=True) as caught:
            pywarnings.simplefilter('always')
            if method == 'km':
                fitter = KaplanMeierFitter(alpha=.05).fit(t, e, label=name)
                estimate = fitter.survival_function_.iloc[:, 0]
                interval = fitter.confidence_interval_
            else:
                jitter_level = min(.0001, float(t.min()) * .001)
                if jitter_level <= 0:
                    raise ValueError('竞争风险时长单位过小，请转换为数值稳定的时间单位。')
                cause_counts = e[e != 0].groupby(t[e != 0]).nunique()
                tied_times = cause_counts[cause_counts > 1].index.to_numpy(dtype=float)
                if len(tied_times) and jitter_level <= float(np.max(np.spacing(tied_times))) * 10:
                    raise ValueError('竞争风险并列时长的数值尺度无法稳定微扰，请换算时间单位后再分析。')
                fitter = AalenJohansenFitter(alpha=.05, seed=p['seed'], jitter_level=jitter_level).fit(t, e, event_of_interest=target, label=name)
                estimate = fitter.cumulative_density_.iloc[:, 0]
                interval = fitter.confidence_interval_cumulative_density_
            for warning in caught:
                if 'tied' in str(warning.message).lower() or 'jitter' in str(warning.message).lower():
                    notes.append('同时间不同事件由 lifelines 固定随机种子微扰；保存种子以复现，原始时长未改写。')
        for i, (time, value) in enumerate(estimate.items()):
            bounds = sorted(float(x) for x in interval.iloc[i].tolist())
            curves.append({'group': name, 'time': float(time), 'estimate': float(value), 'lower': bounds[0], 'upper': bounds[-1]})
        for time in risk_times:
            risks.append({'group': name, 'time': float(time), 'at_risk': int((t >= time).sum())})
        if method == 'km':
            for time in sorted(t[e == 0].unique()):
                censor.append({'group': name, 'time': float(time), 'estimate': float(fitter.predict(time)), 'n': int(((t == time) & (e == 0)).sum())})
        summary.append({'group': name, 'n': len(t), 'events': int((e == target).sum()), 'censored': int((e == 0).sum()),
                        'competing_events': int(((e != 0) & (e != target)).sum()),
                        'median_survival': float(fitter.median_survival_time_) if method == 'km' else None,
                        'jitter_level': jitter_level if method == 'cif' else None})
    if method == 'km' and len(groups) >= 2:
        test = multivariate_logrank_test(duration, frame[c['group']].astype(str), events)
        stats['logrank'] = {'statistic': float(test.test_statistic), 'p': float(test.p_value), 'df': len(groups) - 1}
    stats['event_of_interest'] = target
    notes.extend(['在险人数取各时间点发生事件/删失之前的独立单位数。', '未达到的中位生存时间、未定义区间以空值表示。'])
    if method == 'cif':
        notes.append('竞争事件保留其独立事件编码；没有执行 Gray 检验或组间 log-rank。')
    return {'method': method, 'tables': {'curve': _table(curves), 'risk': _table(risks), 'censor': _table(censor), 'groups': _table(summary)},
            'statistics': stats, 'warnings': [], 'notes': notes, 'software': _software('lifelines', 'numpy', 'pandas')}


def _cox(config, sources, cancel, progress):
    import numpy as np
    import pandas as pd
    from lifelines import CoxPHFitter
    from lifelines.exceptions import ConvergenceError, ConvergenceWarning
    from lifelines.statistics import proportional_hazard_test
    c, p = config['columns'], config['parameters']
    frame, stats, notes = _data(config, sources, 5000)
    _numeric(frame, [c['duration'], c['event'], *c['numeric_covariates']])
    if (frame[c['duration']] < 0).any() or not frame[c['event']].isin([0, 1]).all():
        raise ValueError('Cox 需要非负时长与 0/1 事件编码。')
    design = pd.DataFrame({'duration': frame[c['duration']], 'event': frame[c['event']]})
    names = {}
    for col in c['numeric_covariates']:
        key = 'x' + str(len(names))
        design[key] = frame[col].astype(float)
        names[key] = col
    refs = json.loads(p['reference_levels'])
    for col in c['categorical_covariates']:
        values = frame[col].astype(str)
        levels = sorted(values.unique())
        reference = str(refs[col])
        if reference not in levels or not 2 <= len(levels) <= 8:
            raise ValueError(f'分类变量“{col}”需要 2–8 个水平，参照值必须确实存在。')
        for level in levels:
            if level != reference:
                key = 'x' + str(len(names))
                design[key] = (values == level).astype(float)
                names[key] = f'{col}: {level} vs {reference}'
    k = len(names)
    nevents = int(design['event'].sum())
    if k > 20 or nevents < max(10, k * 10):
        raise ValueError(f'Cox 需每个编码参数至少 10 个事件、最多 20 参数；当前 {nevents} 个事件/{k} 参数。')
    x = design[list(names)].to_numpy()
    if any(np.std(x, axis=0) < 1e-10) or np.linalg.matrix_rank(x - x.mean(axis=0)) < k:
        raise ValueError('Cox 协变量为常数或线性共线，无法稳定估计。')
    _tick(cancel, progress, '拟合 Cox 模型并检验比例风险假设')
    with pywarnings.catch_warnings(record=True) as caught:
        pywarnings.simplefilter('always')
        try:
            model = CoxPHFitter(penalizer=0).fit(design, duration_col='duration', event_col='event', show_progress=False)
            ph = proportional_hazard_test(model, design, time_transform='rank')
        except (ConvergenceError, np.linalg.LinAlgError, ValueError):
            raise ValueError('Cox 未收敛或设计不可识别；请检查事件数、完全分离及共线性，不输出不可靠 HR。') from None
        if any(isinstance(w.message, ConvergenceWarning) for w in caught):
            raise ValueError('Cox 收敛诊断不可靠；请检查完全分离和共线性后再分析。')
    rows = []
    warn = []
    for key, row in model.summary.iterrows():
        values = [row['exp(coef)'], row['exp(coef) lower 95%'], row['exp(coef) upper 95%'], row['p']]
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError('Cox 产生非有限估计，停止保存不可靠结果。')
        ph_row = ph.summary.loc[key]
        rows.append({'covariate': names[key], 'coef': float(row['coef']), 'hr': float(values[0]), 'lower': float(values[1]),
                     'upper': float(values[2]), 'p': float(values[3]), 'ph_statistic': float(ph_row['test_statistic']), 'ph_p': float(ph_row['p'])})
        if ph_row['p'] < .05:
            warn.append(f'“{names[key]}”比例风险检验 p<0.05，应进一步检查时间效应，勿直接按恒定 HR 解释。')
    stats.update({'events': nevents, 'encoded_parameters': k, 'concordance': float(model.concordance_index_),
                  'log_likelihood': float(model.log_likelihood_), 'ph_test': 'scaled Schoenfeld residuals; rank time transform; individual tests, unadjusted'})
    notes.append('Cox 使用 Efron 并列事件处理；分类变量按明确参照作哑变量，数值 HR 为原单位增加 1。')
    notes.append('PH 为各协变量的缩放 Schoenfeld 残差 rank 时间检验，p 未经多重校正；p>0.05 不能证明假设成立。')
    return {'method': 'cox', 'tables': {'coefficients': _table(rows)}, 'statistics': stats, 'warnings': warn,
            'notes': notes, 'software': _software('lifelines', 'numpy', 'pandas')}


def _evaluate(y, probability, p, cancel, progress):
    import numpy as np
    from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, roc_curve, precision_recall_curve
    from sklearn.calibration import calibration_curve
    y, probability = np.asarray(y, dtype=int), np.asarray(probability, dtype=float)
    if min(int((y == v).sum()) for v in (0, 1)) < 5:
        raise ValueError('独立评估集每类至少需要 5 个单位。')
    rng = np.random.default_rng(p['seed'])
    by_class = [np.flatnonzero(y == value) for value in (0, 1)]
    boot = []
    for iteration in range(p['bootstrap']):
        if iteration % 20 == 0:
            _tick(cancel, progress, f'分层 bootstrap {iteration}/{p["bootstrap"]}')
        sample = np.concatenate([rng.choice(indices, size=len(indices), replace=True) for indices in by_class])
        boot.append([roc_auc_score(y[sample], probability[sample]), average_precision_score(y[sample], probability[sample]), brier_score_loss(y[sample], probability[sample])])
    intervals = np.quantile(np.asarray(boot), [.025, .975], axis=0)
    fpr, tpr, threshold = roc_curve(y, probability, drop_intermediate=False)
    precision, recall, pr_threshold = precision_recall_curve(y, probability)
    fractions, means = calibration_curve(y, probability, n_bins=10, strategy='uniform')
    dca = []
    for cut in np.linspace(.01, .99, 99):
        selected = probability >= cut
        tp = np.sum(selected & (y == 1))
        fp = np.sum(selected & (y == 0))
        prevalence = float(y.mean())
        dca.append({'threshold': float(cut), 'model': float(tp / len(y) - fp / len(y) * cut / (1 - cut)),
                    'all': prevalence - (1 - prevalence) * cut / (1 - cut), 'none': 0.})
    stats = {'n': len(y), 'positive_n': int(y.sum()), 'negative_n': int((y == 0).sum()),
             'roc_auc': float(roc_auc_score(y, probability)), 'average_precision': float(average_precision_score(y, probability)),
             'brier': float(brier_score_loss(y, probability)), 'bootstrap': p['bootstrap'], 'ci_level': .95,
             'bootstrap_method': 'stratified percentile bootstrap; class counts fixed',
             'roc_auc_ci': intervals[:, 0].tolist(), 'average_precision_ci': intervals[:, 1].tolist(), 'brier_ci': intervals[:, 2].tolist()}
    tables = {'roc': _table([{'fpr': float(x), 'tpr': float(z), 'threshold': float(t)} for x, z, t in zip(fpr, tpr, threshold)]),
              'pr': _table([{'recall': float(r), 'precision': float(q), 'threshold': float(pr_threshold[i]) if i < len(pr_threshold) else None} for i, (r, q) in enumerate(zip(recall, precision))]),
              'calibration': _table([{'mean_probability': float(m), 'observed_fraction': float(f)} for f, m in zip(fractions, means)]),
              'dca': _table(dca)}
    return stats, tables


def _binary(config, sources, cancel, progress):
    c, p = config['columns'], config['parameters']
    if not p['evaluation_independent']:
        raise ValueError('请确认概率来自未参与训练或调参的独立评估集。')
    frame, stats, notes = _data(config, sources)
    _numeric(frame, [c['outcome'], c['probability']])
    if not frame[c['outcome']].isin([0, 1]).all() or not frame[c['probability']].between(0, 1).all():
        raise ValueError('真实标签只能为 0/1，预测概率必须在 0–1 之间。')
    metrics, tables = _evaluate(frame[c['outcome']], frame[c['probability']], p, cancel, progress)
    stats.update(metrics)
    notes.extend(['输入为已有预测概率；本流程没有训练、选择或校准模型。',
                  'ROC/PR 的无穷或无对应阈值表示为 null；AP 是平均精确率，不是梯形 PR 曲线面积。',
                  '校准按 10 个等宽概率箱计算，仅显示非空箱；DCA 是给定阈值效用假设下的描述。',
                  '分层 bootstrap 固定类别数量，因此 AP/Brier 区间不包含阳性率变动的不确定性；未重新拟合输入模型。',
                  'DCA 以当前评估集阳性率估计净获益；若为病例对照、过采样或重采样队列，不能直接解释为目标人群获益。',
                  'DCA 图为可读性将纵轴下界设为 −0.1，更低净获益仍完整保存在 dca 结果表。'])
    return {'method': 'binary_evaluation', 'tables': tables, 'statistics': stats, 'warnings': [], 'notes': notes,
            'software': _software('scikit-learn', 'numpy', 'pandas')}


def _logistic(config, sources, cancel, progress):
    import numpy as np
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.exceptions import ConvergenceWarning
    c, p = config['columns'], config['parameters']
    frame, stats, notes = _data(config, sources)
    _numeric(frame, [c['outcome'], *c['numeric_covariates']])
    y = frame[c['outcome']].to_numpy()
    if not set(y) <= {0, 1} or min(int((y == value).sum()) for value in (0, 1)) < 20:
        raise ValueError('逻辑回归需要 0/1 标签，且每类至少 20 个独立单位。')
    x = frame[c['numeric_covariates']].to_numpy(dtype=float)
    indices = np.arange(len(y))
    train, test = train_test_split(indices, test_size=p['test_fraction'], random_state=p['seed'], stratify=y)
    if any(np.std(x[train], axis=0) < 1e-10):
        raise ValueError('训练集预测变量含常数列，无法解释评分。')
    if min(int((y[train] == value).sum()) for value in (0, 1)) < max(10, 5 * x.shape[1]):
        raise ValueError('训练集每类至少需要 max(10, 5×预测变量数) 个独立单位。')
    _tick(cancel, progress, '仅使用训练集拟合标准化与逻辑回归')
    pipeline = Pipeline([('scale', StandardScaler()), ('model', LogisticRegression(C=p['regularization_c'], max_iter=1000, solver='lbfgs', random_state=p['seed']))])
    with pywarnings.catch_warnings(record=True) as caught:
        pywarnings.simplefilter('always')
        pipeline.fit(x[train], y[train])
        if any(isinstance(w.message, ConvergenceWarning) for w in caught):
            raise ValueError('逻辑回归未收敛，请检查变量尺度与数据后再分析。')
    probability = pipeline.predict_proba(x[test])[:, 1]
    metrics, tables = _evaluate(y[test], probability, p, cancel, progress)
    scale, model = pipeline['scale'], pipeline['model']
    beta = model.coef_[0] / scale.scale_
    intercept = float(model.intercept_[0] - np.dot(beta, scale.mean_))
    stats.update(metrics)
    stats.update({'training_n': len(train), 'holdout_n': len(test), 'evaluation_type': 'internal stratified holdout; not external validation',
                  'training_rows': (frame.index.to_numpy()[train] + 1).tolist(), 'holdout_rows': (frame.index.to_numpy()[test] + 1).tolist(),
                  'training_means': dict(zip(c['numeric_covariates'], scale.mean_.tolist())), 'intercept_original_units': intercept,
                  'training_positive_n': int(y[train].sum()), 'training_negative_n': int((y[train] == 0).sum())})
    coefficients = []
    ranges = x[train].max(axis=0) - x[train].min(axis=0)
    max_span = float(np.max(np.abs(beta) * ranges))
    points_per_logit = 100 / max_span if max_span > 1e-12 else 1.
    baseline = intercept
    for i, name in enumerate(c['numeric_covariates']):
        low, high = float(x[train, i].min()), float(x[train, i].max())
        origin = low if beta[i] >= 0 else high
        baseline += float(beta[i]) * origin
        coefficients.append({'variable': name, 'coef_original_unit': float(beta[i]), 'training_min': low, 'training_max': high,
                             'point_origin': origin, 'points_per_unit': float(beta[i] * points_per_logit),
                             'max_points': float(abs(beta[i]) * (high - low) * points_per_logit)})
    stats['score_baseline_logit'] = float(baseline)
    stats['points_per_logit'] = float(points_per_logit)
    tables['coefficients'] = _table(coefficients)
    tables['holdout_predictions'] = _table([{'data_row': int(frame.index.to_numpy()[j]) + 1, 'outcome': int(y[j]), 'probability': float(probability[i])} for i, j in enumerate(test)])
    total = sum(row['max_points'] for row in coefficients)
    tables['score_probability'] = _table([{'total_points': float(v), 'probability': float(1 / (1 + np.exp(-np.clip(baseline + v / points_per_logit, -700, 700))))} for v in np.linspace(0, total, 101)])
    notes.extend(['先拆分再拟合 Pipeline；标准化均值与方差仅来自训练集。未调参、未填补、未作变量选择。',
                  '内部留出评估不是外部验证；所有概率评估与 bootstrap 仅使用留出集。',
                  'bootstrap 仅重采样留出集，固定类别数量和已拟合模型；不计训练集或阳性率变动的不确定性。',
                  'DCA 依赖评估集阳性率及给定阈值效用；病例对照、过采样或重采样队列不能直接解释为目标人群获益。',
                  'DCA 图纵轴下界为 −0.1，低于该范围的净获益仍保存在完整结果表。',
                  'logit(p)=intercept_original_units+Σ(coef_original_unit×x)；points=Σ((x−point_origin)×points_per_unit)，p=sigmoid(score_baseline_logit+points/points_per_logit)。',
                  '积分刻度仅在各变量训练范围内展示；属于模型解释，不是已通过临床验证的风险工具。'])
    return {'method': 'logistic_model', 'tables': tables, 'statistics': stats, 'warnings': [], 'notes': notes,
            'software': _software('scikit-learn', 'numpy', 'pandas')}


def compute(config, sources, cancel=None, progress=None):
    config = validate(config)
    _tick(cancel, progress, '检查独立单位、编码与缺失值策略')
    method = config['method']
    result = (_survival if method in {'km', 'cif'} else _cox if method == 'cox' else _binary if method == 'binary_evaluation' else _logistic)(config, sources, cancel, progress)
    refs = {'km': 'https://lifelines.readthedocs.io/en/latest/fitters/univariate/KaplanMeierFitter.html',
            'cif': 'https://lifelines.readthedocs.io/en/latest/fitters/univariate/AalenJohansenFitter.html',
            'cox': 'https://lifelines.readthedocs.io/en/latest/Survival%20Regression.html',
            'binary_evaluation': 'https://scikit-learn.org/stable/modules/model_evaluation.html',
            'logistic_model': 'https://scikit-learn.org/stable/common_pitfalls.html'}
    result['notes'].append(refs[method])
    _tick(cancel)
    result = _clean(result)
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 20 * 1024 * 1024:
        raise ValueError('分析结果超过 20 MB，请减少输入规模。')
    return result


def draw(result, config, style, title=''):
    """Plot only saved result tables; rendering never refits a statistical model."""
    from matplotlib.figure import Figure
    from .plot_engine import PALETTES
    import textwrap
    method = result['method']
    tables, stats = result['tables'], result['statistics']
    figure = Figure(figsize=(style.get('width', 8), style.get('height', 6)), layout='constrained')
    palette = PALETTES.get(style.get('palette', 'muted'), PALETTES['muted'])
    if method in {'km', 'cif'}:
        grid = figure.add_gridspec(2, 1, height_ratios=[4, 1.2])
        ax, risk_ax = figure.add_subplot(grid[0]), figure.add_subplot(grid[1])
        groups = list(dict.fromkeys(r['group'] for r in tables['curve']['rows']))
        for index, group in enumerate(groups):
            rows = [r for r in tables['curve']['rows'] if r['group'] == group]
            x, y = [r['time'] for r in rows], [r['estimate'] for r in rows]
            color = palette[index]
            label = '\n'.join(textwrap.wrap(group, 24))
            ax.step(x, y, where='post', label=label, color=color)
            valid = [r for r in rows if r['lower'] is not None and r['upper'] is not None]
            if valid:
                ax.fill_between([r['time'] for r in valid], [r['lower'] for r in valid], [r['upper'] for r in valid], step='post', alpha=.13, color=color)
            censored = [r for r in tables['censor']['rows'] if r['group'] == group]
            ax.plot([r['time'] for r in censored], [r['estimate'] for r in censored], '+', color=color, markersize=6)
        ax.set(xlabel='Time', ylabel='Survival probability' if method == 'km' else 'Cumulative incidence', ylim=(-.02, 1.02))
        ax.legend(loc='best', fontsize=8)
        if stats.get('logrank'):
            ax.text(.02, .03, f'Overall log-rank p={stats["logrank"]["p"]:.4g}', transform=ax.transAxes, fontsize=8)
        risk_ax.axis('off')
        riskrows = tables['risk']['rows']
        times = [r['time'] for r in riskrows if r['group'] == groups[0]]
        table = risk_ax.table(cellText=[[r['at_risk'] for r in riskrows if r['group'] == group] for group in groups],
                              rowLabels=[g if len(g) <= 22 else g[:19] + '…' for g in groups], colLabels=[f'{v:.3g}' for v in times], loc='center')
        table.auto_set_font_size(False); table.set_fontsize(8)
        risk_ax.set_title('Number at risk (before time)', fontsize=9)
    elif method == 'cox':
        from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
        ax = figure.add_subplot()
        rows = tables['coefficients']['rows']
        y = list(range(len(rows)))
        ax.errorbar([r['hr'] for r in rows], y, xerr=[[r['hr'] - r['lower'] for r in rows], [r['upper'] - r['hr'] for r in rows]], fmt='o', color=palette[0], capsize=3)
        labels = [r['covariate'] if len(r['covariate']) <= 40 else f'[{i + 1}] ' + r['covariate'][:33] + '…' for i, r in enumerate(rows)]
        ax.set_yticks(y, labels); ax.invert_yaxis(); ax.set_xscale('log')
        # Parent disables math parsing for untrusted labels. Matplotlib's usual
        # log formatter emits mathtext strings, which otherwise show literally.
        ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:g}'))
        ax.xaxis.set_minor_formatter(NullFormatter())
        figure.set_figheight(max(figure.get_figheight(), len(rows) * .24 + 1.7))
        ax.axvline(1, color='#9099A2', linestyle='--'); ax.set_xlabel('Hazard ratio (95% CI)')
    else:
        axes = figure.subplots(2, 3 if method == 'logistic_model' else 2).ravel()
        roc, pr, cal, dca = [tables[key]['rows'] for key in ('roc', 'pr', 'calibration', 'dca')]
        auc_ci, ap_ci = stats['roc_auc_ci'], stats['average_precision_ci']
        axes[0].plot([r['fpr'] for r in roc], [r['tpr'] for r in roc], color=palette[0], label=f'AUC {stats["roc_auc"]:.3f}\n95% CI {auc_ci[0]:.3f}–{auc_ci[1]:.3f}')
        axes[0].plot([0, 1], [0, 1], '--', color='#A0ABB5'); axes[0].set(xlabel='False positive rate', ylabel='True positive rate', title='ROC'); axes[0].legend(fontsize=8)
        axes[1].step([r['recall'] for r in pr], [r['precision'] for r in pr], where='post', color=palette[0], label=f'AP {stats["average_precision"]:.3f}\n95% CI {ap_ci[0]:.3f}–{ap_ci[1]:.3f}')
        axes[1].set(xlabel='Recall', ylabel='Precision', title='Precision–recall'); axes[1].legend(fontsize=8)
        axes[2].plot([r['mean_probability'] for r in cal], [r['observed_fraction'] for r in cal], 'o-', color=palette[0])
        axes[2].plot([0, 1], [0, 1], '--', color='#A0ABB5'); axes[2].set(xlabel='Mean predicted probability', ylabel='Observed fraction', title=f'Calibration · Brier {stats["brier"]:.3f}')
        for key, color, label in [('model', palette[0], 'Model'), ('all', palette[1], 'Treat all'), ('none', '#A0ABB5', 'Treat none')]:
            axes[3].plot([r['threshold'] for r in dca], [r[key] for r in dca], label=label, color=color)
        axes[3].set(xlabel='Threshold probability', ylabel='Net benefit', title='Decision curve', ylim=(-.1, max(.1, stats['positive_n'] / stats['n'] + .05))); axes[3].legend(fontsize=7)
        if method == 'logistic_model':
            rows = tables['coefficients']['rows']
            axes[4].barh([r['variable'] for r in rows], [r['coef_original_unit'] for r in rows], color=palette[0]); axes[4].set(xlabel='Logit coefficient (original unit)', title='L2 model coefficients')
            score = tables['score_probability']['rows']
            axes[5].plot([r['total_points'] for r in score], [r['probability'] for r in score], color=palette[0]); axes[5].set(xlabel='Total score points', ylabel='Predicted probability', title='Score-to-probability scale')
    figure.suptitle('\n'.join(textwrap.wrap(title or next(x['label'] for x in catalog() if x['id'] == method), max(20, int(style.get('width', 8) * 9)))), fontsize=12)
    return figure
