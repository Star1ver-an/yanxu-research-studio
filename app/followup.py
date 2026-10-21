"""Local follow-up checks and explicit, editable assistant memory."""
import copy
import hashlib
import json
import uuid
from datetime import date

from .storage import now
from .workflows import FLOWS, STAGES, VERSION
from .research import citation_ids, FIELD_LABELS
from .project_management import lifecycle


def stamp(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def project_stamp(p):
    # Viewing/snoozing follow-ups must never expire a suggestion or save preview.
    return stamp({k: v for k, v in p.items()
                  if k not in ('assistant_chat', 'assistant_followup', 'updated', 'versions')})


def memory_state(p):
    return copy.deepcopy(p.get('assistant_memory', {'revision': 0, 'items': []}))


def changed_basis(p):
    return {
        '材料': stamp([(a['id'], a.get('sha256'), a.get('enabled', True), a['name']) for a in p['assets']]),
        '阶段成果': stamp(p['outputs']),
        '研究目标与记录': stamp({k: p.get(k) for k in ('title', 'topic', 'requirements', 'experiments', 'cursor', 'research')}),
        '项目记忆': stamp(memory_state(p)['items']),
    }


def pending_advice(p):
    messages = p.get('assistant_chat', {}).get('messages', [])
    superseded = {m.get('review_of') for m in messages if m.get('status') == 'completed' and m.get('role') == 'assistant'}
    current = project_stamp(p)
    basis = changed_basis(p)
    result = []
    for m in messages:
        if (m.get('role') != 'assistant' or m.get('status') != 'completed' or m.get('mode') == 'local'
                or m['id'] in superseded or m.get('proposal', {}).get('status') in ('saved', 'discarded')):
            continue
        if m.get('state') == current and m.get('version') == VERSION:
            continue
        changed = [name for name, mark in basis.items() if m.get('basis', {}).get(name) != mark]
        if m.get('version') != VERSION: changed.append('软件版本')
        question = next((x['text'] for x in messages if x.get('turn') == m['turn'] and x.get('role') == 'user'), '')
        result.append({'id': m['id'], 'question': question[:160], 'changed': changed or ['项目状态'],
                       'created': m.get('created', ''), 'stage': m.get('stage', '')})
    return result[::-1]


def followup(p):
    """Derived from registered records only; never invokes a model or mutates p."""
    stages = FLOWS[p['flow']]['stages']
    sid = stages[min(p['cursor'], len(stages) - 1)]
    outputs, w = p['outputs'], p.get('research', {})
    deferred = p.get('assistant_followup', {}).get('deferred', {})
    checks = []

    def add(key, title, detail, target, evidence, level='review'):
        mark = stamp({'key': key, 'evidence': evidence})
        checks.append({'id': key, 'title': title, 'detail': detail, 'target': target, 'level': level,
                       'signature': mark, 'deferred': deferred.get(key) == mark})

    stale = [s for s in stages if outputs.get(s, {}).get('stale')]
    if stale:
        add('stale-reports', f'{len(stale)} 个阶段成果需要重核',
            '先重新运行最早受影响的阶段，再逐步核对后续成果。', {'tab': 'work', 'stage': stale[0]},
            [(s, outputs[s]) for s in stale], 'blocking')
    if p['experiments'] == 'undecided':
        add('experiment-source', '确认当前有没有真实实验数据', '尚未确定实验来源，确认阶段成果时会被阻止。',
            {'action': 'project-info'}, p['experiments'], 'blocking')
    assets = {a['id']: a for a in p['assets']}
    tables = [a for a in p['assets'] if a.get('enabled', True) and a['name'].lower().endswith(('.csv', '.tsv'))]
    if p['flow'] == 'scientific_data' and not tables:
        add('table-input', '导入用于本机质控的 CSV / TSV', '本路径的表格质控需要至少一份启用的原始表格。',
            {'tab': 'materials'}, [], 'blocking')
    elif not any(a.get('enabled', True) for a in p['assets']) and not any(x.get('enabled', True) for x in p['literature']):
        add('no-material', '还没有用于研究的材料', '可以先梳理研究问题，形成证据与结论前再补充可追溯的资料。',
            {'tab': 'materials'}, [], 'info')
    for group in ('evidence', 'figures'):
        for item in w.get(group, []):
            asset = assets.get(item.get('asset_id'))
            source = (asset.get('sha256'), asset.get('enabled', True)) if asset else None
            if not asset or not source[1] or source[0] != item.get('source_sha256'):
                add(group + ':' + item['id'], '来源材料需要复核：' + item['title'],
                    '关联材料缺失、停用或内容已变化，请回到原文核对。',
                    {'tab': 'research', 'group': group, 'record': item['id']}, [item, source])
            if group == 'figures' and item.get('experiment_id'):
                exp = next((x for x in w.get('experiments', []) if x['id'] == item['experiment_id']), None)
                if not exp or stamp(exp) != item.get('experiment_digest'):
                    # Research records use default JSON ASCII encoding for this hash.
                    digest = hashlib.sha256(json.dumps(exp, sort_keys=True).encode()).hexdigest() if exp else None
                    if digest != item.get('experiment_digest'):
                        add('figure-plan:' + item['id'], '图表关联方案已变化：' + item['title'],
                            '核对图表处理步骤与实验条件是否仍然一致。',
                            {'tab': 'research', 'group': 'figures', 'record': item['id']}, [item, exp])
    for item in w.get('experiments', []):
        missing = [k for k in ('baseline', 'dataset', 'split', 'metric', 'unit', 'seeds', 'sample_reason', 'analysis', 'compute')
                   if not item.get(k, '').strip()]
        if missing:
            add('experiment:' + item['id'], '补充实验条件：' + item['title'],
                '尚未填写：' + '、'.join(FIELD_LABELS[k] for k in missing) + '。未知项可以保持待确认。',
                {'tab': 'research', 'group': 'experiments', 'record': item['id']}, item)
    from .plotting import figures,plot_stale
    for item in figures(p):
        if plot_stale(p,item):
            add('plot:'+item['id'],'研究图表需要复核：'+item['title'],
                '来源或关联实验发生变化，重新核对并保存后再导出。',
                {'tab':'research','group':'figures'},[item,[(a['id'],a.get('sha256')) for a in p['assets']],w.get('experiments',[])])
    for item in w.get('claims', []):
        if item.get('assessment', 'unknown') in ('unknown', 'partial', 'contradicts'):
            add('claim:' + item['id'], '论点支持关系待核对：' + item['title'],
                '当前记录为尚未判断、部分支持或存在矛盾。', {'tab': 'research', 'group': 'claims', 'record': item['id']}, item)
    refids = {x['id'] for x in w.get('references', [])}
    for stage, out in outputs.items():
        missing = sorted(set(citation_ids(out.get('text', ''))) - refids)
        if missing:
            add('citations:' + stage, STAGES[stage]['title'] + '存在缺失的引用',
                f'{len(missing)} 个引用标记找不到对应书目，导出前请补齐或修正。',
                {'tab': 'work', 'stage': stage}, missing)
    tasks = w.get('tasks', [])
    for item in tasks:
        if item.get('status') != 'done' and item.get('due') and item['due'] < date.today().isoformat():
            add('overdue:' + item['id'], '计划日期已过：' + item['title'],
                '计划日期 ' + item['due'] + '，可以更新安排或记录实际进展。',
                {'tab': 'research', 'group': 'tasks', 'record': item['id']}, item)
    for item in w.get('issues', []):
        if item.get('status') != 'done' and item.get('priority') == 'high':
            add('issue:' + item['id'], '优先处理意见：' + item['title'],
                '高优先级意见尚未记录为已处理。',
                {'tab': 'research', 'group': 'issues', 'record': item['id']}, item)
    advice = pending_advice(p)
    advice_basis = project_stamp(p) if advice else ''
    for m in advice:
        add('advice:' + m['id'], '重新核对建议：' + m['question'],
            '发生变化：' + '、'.join(m['changed']) + '。重新提问前可检查本次参考范围。',
            {'action': 'recheck', 'message': m['id']}, [advice_basis, VERSION, m['id']])
    checks.sort(key=lambda x: (x['deferred'], {'blocking': 0, 'review': 1, 'info': 2}[x['level']]))
    running = any(r['status'] == 'running' for r in p['runs'])
    completed = p['cursor'] >= len(stages)
    target = {'tab': 'work', 'stage': sid}
    if running:
        next_step = {'title': '查看正在生成的阶段', 'detail': '可以离开页面，完成后会保存在项目中。', 'target': target}
    elif stale:
        next_step = {'title': '重核「' + STAGES[stale[0]]['title'] + '」', 'detail': '前序输入发生变化，从这里继续。', 'target': {'tab': 'work', 'stage': stale[0]}}
    elif p['flow'] == 'scientific_data' and not tables:
        next_step = {'title': '导入第一份表格', 'detail': '先准备 CSV / TSV，后续质控由本机计算。', 'target': {'tab': 'materials'}}
    elif completed:
        next_step = {'title': '检查并导出成果', 'detail': '流程已走完，复核待办和引用后即可导出。', 'target': {'action': 'export'}}
    else:
        has = bool(outputs.get(sid))
        next_step = {'title': ('阅读并确认「' if has else '开始「') + STAGES[sid]['title'] + '」',
                     'detail': '核实成果后继续下一步。' if has else STAGES[sid]['subtitle'], 'target': target}
    if stale and not running:
        next_step.update(title_template='重核「{0}」',title_args=[STAGES[stale[0]]['title']])
    elif not running and not completed and not (p['flow']=='scientific_data' and not tables):
        next_step.update(title_template='阅读并确认「{0}」' if outputs.get(sid) else '开始「{0}」',title_args=[STAGES[sid]['title']])
    counts = {'stages_done': p['cursor'], 'stages_total': len(stages), 'materials': len(p['assets']),
              'tasks_done': sum(x.get('status') == 'done' for x in tasks), 'tasks_total': len(tasks),
              'issues_open': sum(x.get('status') != 'done' for x in w.get('issues', [])),
              'checks': sum(not c['deferred'] for c in checks), 'deferred': sum(c['deferred'] for c in checks),
              'advice': len(advice)}
    timeline = [{'stage': s, 'title': STAGES[s]['title'],
                 'status': '需重核' if outputs.get(s, {}).get('stale') else '已确认' if outputs.get(s, {}).get('approved')
                 else '待确认' if s in outputs else '未开始'} for s in stages]
    return {'next': next_step, 'counts': counts, 'checks': checks, 'timeline': timeline,
            'memory': memory_state(p), 'readonly': lifecycle(p) != 'active', 'running': running,
            'meaning': '本机检查已登记的记录与关联，不判断结论真实性，也不代表完成科学审查。'}


class AssistantFollowup:
    def followup_state(self, data):
        with self.service.lock:
            p = self._project(data.get('project', ''))
            if not p: raise ValueError('请先打开一个研究项目。')
            return followup(p)

    def followup_defer(self, data):
        with self.service.lock:
            p = self._project(data.get('project', ''))
            if not p: raise ValueError('请先打开一个研究项目。')
            self.service.assert_idle(p)
            if type(data.get('defer')) is not bool: raise ValueError('暂缓选项无效。')
            item = next((c for c in followup(p)['checks'] if c['id'] == data.get('id')), None)
            if not item or item['signature'] != data.get('signature'):
                raise ValueError('检查依据已变化，请刷新后再处理。')
            state = p.setdefault('assistant_followup', {})
            saved = state.setdefault('deferred', {})
            if data['defer']: saved[item['id']] = item['signature']
            else: saved.pop(item['id'], None)
            active_ids = {c['id'] for c in followup(p)['checks']}
            state['deferred'] = {k: v for k, v in saved.items() if k in active_ids}
            self.service.store.put(p)
            return self.service.public_project(p['id'])

    def memory_save(self, data):
        with self.service.lock:
            p = self._project(data.get('project', ''))
            if not p: raise ValueError('项目记忆只能保存在具体项目中。')
            self.service.assert_idle(p)
            if self.busy(p['id']): raise ValueError('请等待助手回答结束后再修改记忆。')
            state = memory_state(p)
            if type(data.get('revision')) is not int or data['revision'] != state['revision']:
                raise ValueError('项目记忆已变化，请重新打开核对。')
            record = data.get('record')
            if not isinstance(record, dict) or set(record) - {'id', 'title', 'text', 'enabled'}:
                raise ValueError('项目记忆字段无效。')
            for key, limit in (('title', 100), ('text', 4000)):
                if not isinstance(record.get(key), str) or not record[key].strip() or len(record[key]) > limit:
                    raise ValueError('请填写记忆名称与内容，并控制在 100 / 4000 字以内。')
            if type(record.get('enabled', True)) is not bool: raise ValueError('记忆开关无效。')
            rid = record.get('id')
            old = next((x for x in state['items'] if x['id'] == rid), None)
            if rid and not old: raise ValueError('未找到本项目的记忆条目。')
            if not old and len(state['items']) >= 30: raise ValueError('每个项目最多保存 30 条记忆，请编辑已有条目。')
            item = {'id': rid or uuid.uuid4().hex, 'title': record['title'].strip(), 'text': record['text'].strip(),
                    'enabled': record.get('enabled', True), 'source': '用户确认',
                    'created': old['created'] if old else now(), 'updated': now()}
            if old: state['items'][state['items'].index(old)] = item
            else: state['items'].append(item)
            state['revision'] += 1
            p['assistant_memory'] = state
            self.service.store.put(p)
            return self.service.public_project(p['id'])

    def recheck(self, data):
        with self.service.lock:
            p = self._project(data.get('project', ''))
            if not p: raise ValueError('请先打开研究项目。')
            self.service.assert_idle(p)
            messages = self._read(p['id'])['messages']
            m = next((x for x in messages if x['id'] == data.get('message') and x['role'] == 'assistant'
                      and x['status'] == 'completed' and x.get('mode') != 'local'), None)
            if not m: raise ValueError('未找到本项目可复核的建议。')
            user = next((x for x in messages if x['turn'] == m['turn'] and x['role'] == 'user'), None)
            if not user: raise ValueError('原问题不存在，请重新输入问题。')
            choices = m.get('options', {})
            ids = {a['id'] for a in p['assets'] if a.get('enabled', True)}
            stage = m.get('stage', '')
            stages = FLOWS[p['flow']]['stages']
            if stage not in stages or stages.index(stage) > p['cursor']:
                stage = stages[min(p['cursor'], len(stages) - 1)]
            report = bool(choices.get('report') and stage in p['outputs'])
            return {'message': m['id'], 'text': user['text'], 'stage': stage, 'draft_kind': m.get('draft_kind', ''),
                    'options': {'history': False, 'report': report, 'memory': choices.get('memory', True),
                                'materials': [a for a in choices.get('materials', []) if a in ids]},
                    'note': '已带回原问题；历史对话已关闭。新增材料需在参考范围中主动勾选。旧建议只作对比，不代表当前事实。'}

    def progress_export(self, data):
        from .i18n import tr,format_text
        with self.service.lock:
            p = self._project(data.get('project', ''))
            if not p: raise ValueError('请先打开研究项目。')
            f = followup(p); c = f['counts']
            locale=self.service.store.language()['ui_language']
            t=lambda text:tr(text,locale)
            lines = ['# ' + p['title'] + ' · '+t('进度简报'), '\n'+format_text('整理时间：{0}',locale,now()),
                     f"\n流程已推进 {c['stages_done']}/{c['stages_total']}；材料 {c['materials']} 份；"
                     f"任务完成 {c['tasks_done']}/{c['tasks_total']}；未处理意见 {c['issues_open']} 条。",
                     '\n## '+t('建议下一步')+'\n' + (format_text(f['next']['title_template'],locale,*[t(x) for x in f['next']['title_args']]) if f['next'].get('title_template') else t(f['next']['title'])) + '\n' + t(f['next']['detail'])]
            lines[2]=t(lines[2])
            lines += ['\n## '+t('阶段进展')] + ['- ' + t(row['title']) + '：' + t(row['status']) for row in f['timeline']]
            lines += ['\n## '+t('待处理与暂缓事项')] + ['- ' + (t('[已暂缓] ') if x['deferred'] else '') + t(x['title']) + '：' + t(x['detail']) for x in f['checks']]
            if not f['checks']: lines.append(t('本次未发现已登记记录中的待处理项。'))
            lines += ['\n## '+t('用户确认的项目记忆')] + ['- ' + x['title'] + '：' + x['text'] + (t('（已停用）') if not x['enabled'] else '') for x in f['memory']['items']]
            lines += ['\n' + t(f['meaning']), t('本简报来自本机状态，不调用模型；流程确认与任务完成记录不等于科学结论得到验证。')]
            return '\n'.join(lines).encode()
