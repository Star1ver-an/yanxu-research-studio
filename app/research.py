"""Local, structured research records. No model calls or experiment execution."""
import copy
import base64
import difflib
import hashlib
import json
import re
import unicodedata
import uuid
from datetime import date
from urllib.request import Request, urlopen
from urllib.parse import quote

from .storage import now
from .workflows import FLOWS, STAGES

COLLECTIONS = {
    'tasks': ('title', 'purpose', 'materials', 'criterion', 'status', 'depends_on', 'due', 'stage'),
    'references': ('title', 'authors', 'year', 'venue', 'doi', 'url', 'kind', 'asset_id'),
    'evidence': ('title', 'asset_id', 'page', 'quote', 'finding', 'method', 'limitations', 'level', 'reference_id'),
    'claims': ('title', 'evidence_id', 'stage', 'location', 'assessment', 'reason'),
    'experiments': ('title', 'hypothesis', 'baseline', 'variables', 'controls', 'dataset', 'split',
                    'metric', 'unit', 'seeds', 'sample_reason', 'analysis', 'compute', 'license', 'status', 'asset_id'),
    'figures': ('title', 'asset_id', 'experiment_id', 'procedure', 'environment', 'caption', 'stage', 'location', 'status'),
    'issues': ('title', 'source', 'priority', 'response', 'location', 'status', 'stage'),
    'checklist': ('title', 'category', 'status', 'evidence', 'location', 'source_url', 'source_date'),
}
ENUMS = {
    ('tasks', 'status'): ('todo', 'doing', 'done'),
    ('evidence', 'level'): ('direct', 'indirect', 'hypothesis'),
    ('claims', 'assessment'): ('unknown', 'supports', 'partial', 'contradicts'),
    ('experiments', 'status'): ('planned', 'running', 'completed'),
    ('figures', 'status'): ('draft', 'reviewed'),
    ('issues', 'status'): ('todo', 'doing', 'done'),
    ('issues', 'priority'): ('high', 'medium', 'low'),
    ('checklist', 'status'): ('todo', 'done', 'na'),
}
CHECKS = {
    'general': ['研究问题、贡献和适用范围已明确', '相关工作中的论断均有关联证据', '方法、假设和实现细节足以复核',
                '结论与实际结果对应，局限和失败情况已说明', '图表、引用、附录与正文一致'],
    'ml': ['数据来源、许可及训练验证测试划分已记录', '基线与消融比较的条件保持可比', '随机种子、重复次数及变异来源已说明',
           '指标定义、统计单位与不确定性报告方式已说明', '模型版本、超参数、算力及复现步骤已记录',
           '数据泄漏、隐私与潜在使用风险已评估'],
    'systems': ['硬件、系统、软件版本及负载已记录', '预热、缓存及测量环境的控制方法已说明',
                '吞吐量、时延、资源占用及重复测量方法已定义', '比较方案配置公平且给出适用范围'],
    'submission': ['目标期刊或会议及本次官方要求链接已记录', '页数、匿名要求与附件清单已逐项核对',
                   '作者、贡献、资金、利益冲突和工具使用声明已核对', '正文、补充材料、代码和数据的提交版本已冻结'],
}
FIELD_LABELS=dict(zip(
    'purpose materials criterion status depends_on due stage authors year venue doi url kind asset_id page quote finding method limitations level reference_id evidence_id location assessment reason hypothesis baseline variables controls dataset split metric unit seeds sample_reason analysis compute license experiment_id procedure environment caption source priority response category evidence source_url source_date'.split(),
    '目的 所需材料 完成标准 状态 前置任务 计划日期 关联阶段 作者 年份 发表来源 DOI 来源链接 文献类型 关联材料 页码 原文摘录 主要发现 方法条件 局限 证据类别 关联文献 关联证据 具体位置 支持关系 判断理由 待检验假说 比较基线 改变的变量 控制条件 数据来源 数据划分 评价指标 统计单位 随机种子及重复 次数依据 分析计划 计算资源 数据许可 关联实验 处理步骤 生成环境 图注 意见来源 优先级 逐条回复 类别 完成依据 官方来源 核对日期'.split()))
STATE_LABELS={'todo':'待处理','doing':'进行中','done':'已完成（人工记录）','na':'不适用','planned':'研究计划','running':'执行中（作者声明）','completed':'已执行（作者声明）','draft':'待核对','reviewed':'已人工核对','direct':'直接证据','indirect':'间接证据','hypothesis':'待检验假说','unknown':'尚未判断','supports':'支持','partial':'部分支持','contradicts':'存在矛盾','high':'高','medium':'中','low':'低'}


def workspace(p):
    result = p.setdefault('research', {})
    for key in COLLECTIONS:
        result.setdefault(key, [])
    result.setdefault('citation_style', 'author-year' if str(p.get('citation','')).upper().startswith(('APA','CHICAGO','MLA')) else 'numeric')
    return result


def snapshot(p, stage, reason):
    out = p['outputs'].get(stage)
    if not out or not out.get('text'):
        return
    versions = p.setdefault('versions', [])
    text = out['text']
    if versions and versions[-1]['stage'] == stage and versions[-1]['text'] == text:
        return
    versions.append({'id': uuid.uuid4().hex, 'stage': stage, 'text': text, 'created': now(),
                     'reason': reason, 'sha256': hashlib.sha256(text.encode()).hexdigest()})
    while len(versions) > 40 or sum(len(v['text']) for v in versions) > 5000000:
        versions.pop(0)


def identifier(value):
    return bool(re.fullmatch('[0-9a-f]{32}', str(value)))


def normalize(value):
    return re.sub(r'[^\w]+', '', unicodedata.normalize('NFKC', value).casefold())


def doi(value):
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value.strip(), flags=re.I).strip()
    if value and not re.fullmatch(r'10\.\d{4,9}/[^\s<>"{}]+', value, re.I):
        raise ValueError('DOI 格式无效，例如 10.1234/example。')
    return value.lower()


def pages(asset):
    text = asset.get('text', '')
    if asset['name'].lower().endswith('.pdf'):
        return re.split(r'\[第 \d+ 页\]\n', text)[1:] or [text]
    return [text[i:i + 16000] for i in range(0, len(text), 16000)] or ['']


CITATIONS = re.compile(r'\[@([0-9a-f]{32}(?:\s*;\s*@?[0-9a-f]{32})*)\]')

def citation_ids(text):
    return [rid.strip().lstrip('@') for match in CITATIONS.finditer(text) for rid in match[1].split(';')]

def bibliography(p, text):
    refs = {r['id']: r for r in workspace(p)['references']}
    order = list(dict.fromkeys(citation_ids(text)))
    style = workspace(p).get('citation_style', 'numeric')
    lines = []
    for index, rid in enumerate(order, 1):
        ref = refs.get(rid)
        if not ref:continue
        details = '. '.join(v for v in [ref.get('authors'), ref.get('title'), ref.get('venue'), ref.get('year')] if v)
        if ref.get('doi'):
            details += '. https://doi.org/' + ref['doi']
        elif ref.get('url'):
            details += '. ' + ref['url']
        lines.append(f'[{index}] {details}')
    def render(match):
        labels=[]
        for rid in citation_ids(match[0]):
            ref=refs.get(rid)
            labels.append('引用缺失：'+rid[:8] if not ref else str(order.index(rid)+1) if style=='numeric' else (ref.get('authors') or '作者未填').split(';')[0]+', '+(ref.get('year') or '年份未填'))
        return '['+(', ' if style=='numeric' else '; ').join(labels)+']'
    text=CITATIONS.sub(render,text)
    return text + ('\n\n# 参考文献\n\n' + '\n\n'.join(lines) if lines else '')


class ResearchTools:
    def research_demo(self):
        with self.lock:
            if self.updating:
                raise ValueError('软件正在更新，请稍后创建示例。')
            p=self.store.create({'title':'示例｜RAG 回答可靠性评估','flow':'full','discipline':'人工智能',
                'topic':'功能演示：规划检索增强生成的回答可靠性评估。尚无实际实验数据，不得将示例内容写为真实结果。',
                'experiments':'none','requirements':'全部示例笔记为虚构教学材料。比较基线、数据集与指标均待研究者选择；不声称模型优于任何方法。'})
            pid=p['id']
            try:
                note='虚构技术备忘录：仅用于研序功能演示，不是已发表论文，也不包含实验结果。\n评测前应冻结测试集与评价规则。\n方案拟比较无检索与检索增强两种设置，记录检索配置、回答质量及计算开销。具体指标和重复次数需要研究者进一步定义。'
                self.upload(pid,{'name':'AI研究演示笔记.txt','data':base64.b64encode(note.encode()).decode()})
                asset=self.store.get(pid)['assets'][0]['id']
                self.research_seed(pid,{'kind':'tasks'})
                self.research_seed(pid,{'kind':'ml'})
                self.research_save(pid,{'collection':'evidence','record':{'title':'演示：冻结测试规则的规划要求','asset_id':asset,'page':'1',
                    'quote':'评测前应冻结测试集与评价规则。','finding':'虚构技术笔记中的方案要求，不是文献研究证据。','limitations':'仅供操作演示，不能引用为科研事实。','level':'hypothesis'}})
                self.research_save(pid,{'collection':'references','record':{'title':'NeurIPS Paper Checklist Guidelines','authors':'NeurIPS',
                    'url':'https://neurips.cc/public/guides/PaperChecklist','kind':'官方指南网页'}})
                self.research_save(pid,{'collection':'experiments','record':{'title':'演示：RAG 对比评测计划','hypothesis':'检索策略是否影响回答可靠性及计算开销，方向待检验。',
                    'baseline':'相同基础模型、无检索设置','variables':'检索配置','controls':'保持基础模型和提示条件可比','status':'planned'}})
                self.edit(pid,{'stage':'research','text':'# AI 课题研究计划（功能演示）\n\n本项目用于练习软件操作，尚无真实实验数据。\n\n## 研究问题\n检索增强策略在什么条件下影响回答可靠性与计算开销？\n\n## 待补材料\n- 实际文献全文与可追溯证据\n- 数据集来源、许可与划分方式\n- 评价指标、重复方式和计算预算\n\n所有结论待实际研究验证。'})
                p=self.store.get(pid);p['demo']=True;self.store.put(p)
            except Exception:
                self._remove_owned([self._owned_folder('materials',pid)])
                with self.store.connect() as c:c.execute('DELETE FROM projects WHERE id=?',(pid,))
                raise
        return {'id':pid}

    def research_status(self, p):
        w = workspace(p)
        stages = FLOWS[p['flow']]['stages']
        current = stages[min(p['cursor'], len(stages) - 1)]
        tasks = w['tasks']
        done = {t['id'] for t in tasks if t.get('status') == 'done'}
        ready = [t for t in tasks if t.get('status') != 'done' and (not t.get('depends_on') or t['depends_on'] in done)]
        ready.sort(key=lambda task:(task.get('due') or '9999-12-31',task.get('status')!='doing',task.get('created','')))
        warnings = []
        for item in w['claims']:
            if item.get('assessment', 'unknown') in ('unknown', 'partial', 'contradicts'):
                warnings.append('论点待复核：' + item['title'])
        for item in w['evidence'] + w['figures']:
            asset = next((a for a in p['assets'] if a['id'] == item.get('asset_id')), None)
            if not asset or not asset.get('enabled', True) or asset['sha256'] != item.get('source_sha256'):
                warnings.append('材料来源需复核：' + item['title'])
        combined = '\n'.join(o.get('text', '') for o in p['outputs'].values())
        ids = {r['id'] for r in w['references']}
        for rid in set(citation_ids(combined)) - ids:
            warnings.append('稿件引用缺失：' + rid[:8])
        for x in w['experiments']:
            missing = [name for name in ('baseline', 'dataset', 'split', 'metric', 'unit', 'seeds', 'sample_reason', 'analysis') if not x.get(name)]
            if missing:
                warnings.append('实验设计尚有缺项：' + x['title'] + f'（{len(missing)} 项）')
        for x in w['figures']:
            exp = next((t for t in w['experiments'] if t['id'] == x.get('experiment_id')), None)
            if exp and x.get('experiment_digest') != hashlib.sha256(json.dumps(exp, sort_keys=True).encode()).hexdigest():
                warnings.append('实验方案已变更，图表需重新核对：' + x['title'])
        return {'current_stage': current, 'next': ready[0] if ready else None, 'blocked': len([t for t in tasks if t.get('status') != 'done']) - len(ready),
                'tasks_done': len(done), 'tasks_total': len(tasks), 'warnings': warnings,
                'guidance': STAGES[current]['instruction'], 'flow_completed': p['cursor'] >= len(stages)}

    def material_page(self, pid, data):
        p = self.store.get(pid)
        asset = next((a for a in p['assets'] if a['id'] == data.get('id')), None)
        if not asset:
            raise ValueError('未找到材料。')
        parts = pages(asset)
        page = data.get('page', 1)
        if type(page) != int or not 1 <= page <= len(parts):
            raise ValueError('页码超出范围。')
        return {'id': asset['id'], 'name': asset['name'], 'page': page, 'count': len(parts),
                'text': parts[page - 1], 'pdf': asset['name'].lower().endswith('.pdf'), 'sha256': asset['sha256']}

    def material_bytes(self, pid, aid):
        p = self.store.get(pid)
        asset = next((a for a in p['assets'] if a['id'] == aid), None)
        if not asset:
            raise ValueError('未找到材料。')
        path = (self.store.root / asset['file']).resolve(strict=True)
        if not path.is_relative_to(self._owned_folder('materials', pid)) or not path.is_file():
            raise ValueError('材料位置无效。')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset['sha256']:
            raise ValueError('材料文件已被外部修改，请重新导入。')
        return raw, 'application/pdf' if path.suffix == '.pdf' else 'text/plain; charset=utf-8'

    def prepare_research_record(self, p, group, supplied):
        """Validate and apply to an in-memory project; caller commits atomically."""
        if group not in COLLECTIONS:
            raise ValueError('研究工具类型无效。')
        w = workspace(p)
        rows = w[group]
        if not isinstance(supplied, dict):
            raise ValueError('请填写记录。')
        rid = supplied.get('id')
        previous = next((r for r in rows if r['id'] == rid), None)
        if rid and not previous:
            raise ValueError('记录已不存在，请刷新。')
        if not previous and len(rows) >= 200:
            raise ValueError('每类最多保存 200 条记录。')
        record = {'id': rid or uuid.uuid4().hex, 'created': previous['created'] if previous else now(), 'updated': now()}
        for key in COLLECTIONS[group]:
            value = supplied.get(key, '')
            if not isinstance(value, str) or len(value) > (16000 if key == 'quote' else 6000):
                raise ValueError('字段过长或格式无效：' + key)
            record[key] = value.strip()
            allowed = ENUMS.get((group, key))
            if allowed:
                record[key] = record[key] or allowed[0]
                if record[key] not in allowed:
                    raise ValueError('请选择有效状态。')
        if not record['title'] or len(record['title']) > 500:
            raise ValueError('请填写 1–500 字的名称或条目。')
        for field in ('due','source_date'):
            if record.get(field):
                try:date.fromisoformat(record[field])
                except ValueError:raise ValueError('日期格式应为 YYYY-MM-DD。') from None
        for field in ('url', 'source_url'):
            if record.get(field) and not re.match(r'^https?://[^\s<>"\x00-\x1f]+$', record[field]):
                raise ValueError('来源链接须为 http 或 https 地址。')
        if record.get('stage') and record['stage'] not in FLOWS[p['flow']]['stages']:
            raise ValueError('请选择当前项目的阶段。')
        links = {'reference_id': 'references', 'evidence_id': 'evidence', 'experiment_id': 'experiments', 'depends_on': 'tasks'}
        for field, target in links.items():
            if record.get(field) and not any(r['id'] == record[field] for r in w[target]):
                raise ValueError('关联记录不存在。')
        if record.get('asset_id') and not any(a['id'] == record['asset_id'] for a in p['assets']):
            raise ValueError('关联材料不存在。')
        if group == 'tasks':
            node = record.get('depends_on')
            seen = {record['id']}
            lookup = {r['id']: r for r in rows}
            while node:
                if node in seen:
                    raise ValueError('任务依赖不能形成循环。')
                seen.add(node)
                node = lookup[node].get('depends_on')
            if record['status'] == 'done' and record.get('depends_on') and lookup[record['depends_on']]['status'] != 'done':
                raise ValueError('请先完成前置任务。')
            if previous and previous.get('status') == 'done' and record['status'] != 'done' and any(t.get('depends_on') == rid and t.get('status') == 'done' for t in rows):
                raise ValueError('请先将后置任务改为未完成。')
        if group == 'references':
            if record['year'] and not re.fullmatch(r'\d{4}',record['year']):
                raise ValueError('年份请填写四位数字，未知可留空。')
            record['doi'] = doi(record['doi'])
            for ref in rows:
                if ref['id'] != rid and ((record['doi'] and ref.get('doi') == record['doi']) or (normalize(record['title']) == normalize(ref['title']) and record['year'] == ref.get('year', ''))):
                    raise ValueError('这篇文献已在引用库中，请编辑已有记录。')
            if previous and all(previous.get(k) == record.get(k) for k in ('title', 'authors', 'year', 'venue', 'doi')):
                if previous.get('verification'):
                    record['verification'] = previous['verification']
        if group == 'evidence':
            asset = next((a for a in p['assets'] if a['id'] == record['asset_id']), None)
            if not asset:
                raise ValueError('请先导入并选择原始材料。')
            try:
                page = int(record['page'])
            except ValueError:
                raise ValueError('请填写有效页码。') from None
            parts = pages(asset)
            clean = lambda value: re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))
            if not 1 <= page <= len(parts) or len(normalize(record['quote'])) < 3 or clean(record['quote']) not in clean(parts[page - 1]):
                raise ValueError('原文摘录未在指定页找到，请从材料文字中选择并核对。')
            record['source_sha256'] = asset['sha256']
            record['quote_matched'] = True
        if group == 'claims' and record['assessment'] != 'unknown' and (not record['evidence_id'] or not record['reason']):
            raise ValueError('判断支持关系时，请关联证据并填写理由。')
        if group == 'experiments' and record['status'] == 'completed' and not record['asset_id']:
            raise ValueError('标记已执行前，请关联真实结果材料。')
        if group == 'figures':
            asset = next((a for a in p['assets'] if a['id'] == record['asset_id']), None)
            if not asset:
                raise ValueError('请关联图表使用的原始数据材料。')
            record['source_sha256'] = asset['sha256']
            exp = next((r for r in w['experiments'] if r['id'] == record['experiment_id']), None)
            if exp:
                record['experiment_digest'] = hashlib.sha256(json.dumps(exp, sort_keys=True).encode()).hexdigest()
            if record['status'] == 'reviewed' and (not record['procedure'] or not record['caption'] or not record['location']):
                raise ValueError('核对图表前，请补充处理步骤、图注和稿件位置。')
        if group == 'issues' and record['status'] == 'done' and (not record['response'] or not record['location']):
            raise ValueError('完成意见处理前，请填写回复与修改位置。')
        if group == 'checklist' and record['status'] in ('done', 'na') and not record['evidence']:
            raise ValueError('请填写完成依据或不适用的理由。')
        if previous:
            rows[rows.index(previous)] = record
        else:
            rows.append(record)
        if w.get('enabled') and group in ('references', 'evidence', 'claims', 'experiments', 'figures'):
            self.invalidate_inputs(p)
        return record

    def research_save(self, pid, data):
        group = data.get('collection')
        if group not in COLLECTIONS:
            raise ValueError('研究工具类型无效。')
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p)
            if self.updating:
                raise ValueError('软件正在更新，请稍后保存。')
            self.prepare_research_record(p, group, data.get('record'))
            self.store.put(p)
        return self.public_project(pid)

    def research_delete(self, pid, data):
        group, rid = data.get('collection'), data.get('id')
        if group not in COLLECTIONS or data.get('acknowledge') is not True:
            raise ValueError('请确认删除这条研究记录。')
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p)
            w = workspace(p)
            if not any(r['id'] == rid for r in w[group]):
                raise ValueError('记录不存在。')
            for rows in (w[key] for key in COLLECTIONS):
                if any(any(item.get(k) == rid for k in ('reference_id', 'evidence_id', 'experiment_id', 'depends_on')) for item in rows):
                    raise ValueError('其他记录仍有关联，请先修改关联。')
            if group == 'references' and any(rid in citation_ids(o.get('text', '')) for o in p['outputs'].values()):
                raise ValueError('稿件中仍有此引用，请先修改正文。')
            w[group] = [r for r in w[group] if r['id'] != rid]
            if w.get('enabled'):
                self.invalidate_inputs(p)
            self.store.put(p)
        return self.public_project(pid)

    def research_seed(self, pid, data):
        kind = data.get('kind')
        if kind not in ('tasks', *CHECKS):
            raise ValueError('请选择有效模板。')
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p)
            w = workspace(p)
            if kind == 'tasks':
                records = [dict(title='明确可检验的研究问题', purpose='限定贡献和适用范围', materials='已有想法与相关工作', criterion='写出问题、比较方案和评价标准', stage=FLOWS[p['flow']]['stages'][0]),
                           dict(title='整理关键文献与证据', purpose='让论断可追溯', materials='有权使用的论文全文', criterion='为关键论点建立证据卡片'),
                           dict(title='补齐实验或分析方案', purpose='提前发现设计缺项', materials='基线、数据划分及可用计算资源', criterion='明确指标、重复方式和不确定性分析'),
                           dict(title='检查成果与待补材料', purpose='据实推进写作', materials='稿件或开题草稿', criterion='逐项处理未验证论点和缺项')]
                group = 'tasks'
            else:
                records = [dict(title=title, category=kind, source_url='https://neurips.cc/public/guides/PaperChecklist' if kind == 'ml' else '', source_date='2026-10-19' if kind == 'ml' else '') for title in CHECKS[kind]]
                group = 'checklist'
            for record in records:
                if len(w[group]) >= 200:
                    raise ValueError('条目数量已达上限。')
                if not any(r['title'] == record['title'] for r in w[group]):
                    w[group].append({**record, 'id': uuid.uuid4().hex, 'created': now(), 'updated': now(), 'status': 'todo'})
            self.store.put(p)
        return self.public_project(pid)

    def research_style(self, pid, data):
        if data.get('style') not in ('numeric', 'author-year'):
            raise ValueError('请选择编号或作者年份格式。')
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p)
            workspace(p)['citation_style'] = data['style']
            self.store.put(p)
        return self.public_project(pid)

    def research_context(self, pid, data):
        if type(data.get('enabled')) != bool:
            raise ValueError('请选择是否加入模型上下文。')
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p)
            if workspace(p).get('enabled', False) != data['enabled']:
                workspace(p)['enabled'] = data['enabled']
                self.invalidate_inputs(p)
                self.store.put(p)
        return self.public_project(pid)

    def run_preview(self, pid, data):
        p = self.store.get(pid)
        sid = data.get('stage')
        steps = FLOWS[p['flow']]['stages']
        if sid not in steps or steps.index(sid) > p['cursor']:
            raise ValueError('请先完成前置阶段。')
        self.assert_idle(p)
        context = self.context(p, sid, str(data.get('note', ''))[:20000])
        return {'materials': [a['name'] for a in p['assets'] if a.get('enabled', True)],
                'literature': len([r for r in p['literature'] if r.get('enabled', True)]),
                'research_enabled': bool(workspace(p).get('enabled')), 'context_chars': len(context),
                'calls': 0 if STAGES[sid].get('local') else 6 if sid == 'review' else 3 if sid == 'screen' else 1,
                'affected': steps[steps.index(sid) + 1:], 'sha256': hashlib.sha256(context.encode()).hexdigest()}

    def version_compare(self, pid, data):
        p = self.store.get(pid)
        version = next((v for v in p.get('versions', []) if v['id'] == data.get('id')), None)
        if not version:
            raise ValueError('未找到这个历史版本。')
        current = p['outputs'].get(version['stage'], {}).get('text', '')
        return {'version': version, 'current': current, 'diff': '\n'.join(difflib.unified_diff(version['text'].splitlines(), current.splitlines(), fromfile='历史版本', tofile='当前版本', lineterm='')),
                'affected': FLOWS[p['flow']]['stages'][FLOWS[p['flow']]['stages'].index(version['stage']) + 1:]}

    def version_restore(self, pid, data):
        if data.get('acknowledge') is not True:
            raise ValueError('请确认恢复；后续成果将需要重新核对。')
        compare = self.version_compare(pid, data)
        return self.edit(pid, {'stage': compare['version']['stage'], 'text': compare['version']['text']})

    def issues_import(self, pid, data):
        text = data.get('text', '')
        if not isinstance(text, str) or not 1 <= len(text) <= 60000:
            raise ValueError('请粘贴 1–60000 字的意见，每条一行。')
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p)
            rows = workspace(p)['issues']
            if len(rows) + len(lines) > 200 or any(len(line) > 500 for line in lines):
                raise ValueError('最多 200 条意见，每条不超过 500 字。长意见请分段后单独编辑。')
            for line in lines:
                rows.append({'id': uuid.uuid4().hex, 'title': line, 'status': 'todo', 'priority': 'medium', 'source': str(data.get('source', '导师或审稿意见'))[:500], 'created': now(), 'updated': now()})
            self.store.put(p)
        return self.public_project(pid)

    def reference_import(self, pid, data):
        text = data.get('text', '')
        if not isinstance(text, str) or len(text) > 2000000:
            raise ValueError('书目文件请控制在 200 万字符内。')
        records = []
        if data.get('format') == 'csl':
            try:
                entries = json.loads(text)
                if not isinstance(entries, list):
                    raise ValueError()
                for x in entries:
                    authors = '; '.join(' '.join(filter(None, [a.get('given'), a.get('family'), a.get('literal')])) for a in x.get('author', []))
                    parts = x.get('issued', {}).get('date-parts', [[]])
                    records.append({'title': x.get('title', ''), 'authors': authors, 'year': str(parts[0][0]) if parts and parts[0] else '', 'venue': x.get('container-title', ''), 'doi': x.get('DOI', ''), 'url': x.get('URL', ''), 'kind': x.get('type', '')})
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                raise ValueError('请导入 Zotero 导出的 CSL JSON 数组。') from None
        elif data.get('format') == 'ris':
            entry = {}; authors = []
            for line in text.splitlines():
                match = re.match(r'^([A-Z0-9]{2})  - ?(.*)$', line)
                if not match:
                    continue
                key, value = match.groups()
                if key == 'TY':
                    entry = {'kind': value}; authors = []
                elif key in ('AU', 'A1'):
                    authors.append(value)
                elif key == 'ER':
                    records.append({**entry, 'authors': '; '.join(authors)}); entry = {}; authors = []
                else:
                    target = {'TI': 'title', 'T1': 'title', 'JO': 'venue', 'JF': 'venue', 'T2': 'venue', 'PY': 'year', 'Y1': 'year', 'DO': 'doi', 'UR': 'url'}.get(key)
                    if target:
                        entry[target] = value.split('/')[0] if target == 'year' else value
            if entry.get('title'):
                raise ValueError('RIS 缺少 ER 结束标记，请重新导出。')
        else:
            raise ValueError('支持 Zotero CSL JSON 或 RIS 书目文件。')
        if not records or len(records) > 200:
            raise ValueError('一次导入 1–200 条书目。')
        with self.lock:
            original = self.store.get(pid)
            self.assert_idle(original)
            # Validate all records against a temporary project before committing any changes.
            trial = copy.deepcopy(original)
            rows = workspace(trial)['references']
            added = skipped = 0
            for item in records:
                if any(not isinstance(v, str) or len(v) > 6000 for v in item.values()) or not item.get('title', '').strip():
                    raise ValueError('书目含缺失标题或无效字段，未导入。')
                item = {k: v.strip() for k, v in item.items()}
                if len(item['title'])>500 or item.get('year') and not re.fullmatch(r'\d{4}',item['year']):
                    raise ValueError('书目题名过长或年份无效，未导入本批。')
                item['doi'] = doi(item.get('doi', ''))
                if item.get('url') and not re.match(r'^https?://[^\s<>"\x00-\x1f]+$', item['url']):
                    raise ValueError('书目来源链接无效。')
                if any((item['doi'] and r.get('doi') == item['doi']) or (normalize(r['title']) == normalize(item['title']) and r.get('year', '') == item.get('year', '')) for r in rows):
                    skipped += 1
                    continue
                if len(rows) >= 200:
                    raise ValueError('引用库最多 200 条，未导入本批。')
                rows.append({**item, 'id': uuid.uuid4().hex, 'created': now(), 'updated': now()})
                added += 1
            if workspace(trial).get('enabled'):
                self.invalidate_inputs(trial)
            self.store.put(trial)
        return {'project': self.public_project(pid), 'added': added, 'skipped': skipped}

    def reference_verify(self, pid, data):
        p = self.store.get(pid)
        self.assert_idle(p)
        ref = next((r for r in workspace(p)['references'] if r['id'] == data.get('id')), None)
        if not ref or not ref.get('doi'):
            raise ValueError('请先填写文献的 DOI。')
        request = Request('https://api.crossref.org/works/' + quote(doi(ref['doi']), safe=''), headers={'User-Agent': 'YanxuResearchStudio/0.4.0 (local bibliography verification)'})
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(2000001)
            if len(raw) > 2000000:
                raise ValueError()
            message = json.loads(raw)['message']
            title = message.get('title', [''])[0]
            result = {'checked': now(), 'source': 'Crossref', 'title': title,
                      'title_match': normalize(title) == normalize(ref['title']),
                      'updates': message.get('update-to', []),
                      'meaning': '仅比对 DOI 书目标题；不证明全文支持论点。无更新记录不代表未撤稿。'}
        except Exception:
            raise ValueError('Crossref 暂时无法核对这条 DOI，请检查 DOI 或稍后重试。记录仍为未验证。') from None
        with self.lock:
            p = self.store.get(pid)
            self.assert_idle(p)
            current = next((r for r in workspace(p)['references'] if r['id'] == ref['id']), None)
            if not current or any(current.get(k) != ref.get(k) for k in COLLECTIONS['references']):
                raise ValueError('核对期间书目已变化，请重新核对。')
            current['verification'] = result
            self.store.put(p)
        return self.public_project(pid)

    def research_export(self, pid, kind):
        p = self.store.get(pid)
        w = workspace(p)
        if kind == 'csl':
            kinds={'CONF':'paper-conference','JOUR':'article-journal','官方指南网页':'webpage','网页':'webpage'}
            valid={'article','article-journal','paper-conference','book','chapter','report','thesis','webpage','dataset','software','document'}
            entries = [{'id': r['id'], 'type':r.get('kind') if r.get('kind') in valid else kinds.get(r.get('kind'),'article'), 'title': r['title'],
                        'author': [{'literal': a.strip()} for a in r.get('authors', '').split(';') if a.strip()],
                        'container-title': r.get('venue', ''), 'DOI': r.get('doi', ''), 'URL': r.get('url', ''),
                        **({'issued': {'date-parts': [[int(r['year'])]]}} if re.fullmatch(r'\d{4}', r.get('year', '')) else {})} for r in w['references']]
            return 'references.json', json.dumps(entries, ensure_ascii=False, indent=2).encode(), 'application/json'
        if kind not in ('research', 'responses', 'checklist'):
            raise ValueError('导出类型无效。')
        keys = ('issues',) if kind == 'responses' else ('checklist',) if kind == 'checklist' else tuple(COLLECTIONS)
        out = ['# ' + p['title'], '> 用户整理的研究记录，完成状态不构成真实性认证。']
        for key in keys:
            out.append('\n## ' + {'tasks': '研究任务', 'references': '引用文献', 'evidence': '证据卡片', 'claims': '论点与证据', 'experiments': '实验设计', 'figures': '图表台账', 'issues': '意见与回复', 'checklist': '写作与投稿检查'}[key])
            for row in w[key]:
                out.append('\n### ' + row['title'])
                for field in COLLECTIONS[key]:
                    if field=='title' or not row.get(field):continue
                    value=row[field]
                    if field=='stage':value=STAGES.get(value,{}).get('title',value)
                    elif field=='asset_id':value=next((a['name'] for a in p['assets'] if a['id']==value),'来源材料缺失')
                    elif field in ('status','assessment','level','priority'):value=STATE_LABELS.get(value,value)
                    elif field in ('reference_id','evidence_id','experiment_id','depends_on'):
                        target={'reference_id':'references','evidence_id':'evidence','experiment_id':'experiments','depends_on':'tasks'}[field]
                        value=next((r['title'] for r in w[target] if r['id']==value),'关联记录缺失')
                    out.append(f'- {FIELD_LABELS.get(field,field)}: {value}')
        return kind + '.md', '\n'.join(out).encode('utf-8-sig'), 'text/markdown'
