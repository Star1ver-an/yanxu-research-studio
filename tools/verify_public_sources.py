"""Import a pinned public corpus and verify real source-to-draft paths, without models.

Downloads are separate: inspect sources and save original PDFs to --materials first.
Existing reports and user records are preserved. This is a local acceptance helper.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.storage import Store, extract_file
from app.service import Service
from app.source_workflow import clean_quote
from app.research import pages
from app.workflows import FLOWS

PLANS = {
    'biomedical': {'blueprint': 'tmem43-aki', 'query': '"TMEM43" "kidney"\n"TMEM43" "fibrosis"\nTMEM43 renal kidney ischemia reperfusion acute kidney injury fibrosis',
        'database': '公开网页检索，定向PMC和期刊官网', 'url': 'https://pmc.ncbi.nlm.nih.gov/',
        'criteria': '优先原始研究与可获取全文；区分TMEM43跨器官线索、肾脏IRI模型与真正的靶基因肾脏机制。不是系统综述。',
        'gap': 'TMEM43在IRI-AKI慢性转归中的作用方向尚待验证。',
        'next': '补充TMEM43肾脏表达时程、细胞来源和功能操作证据',
        'criterion': '进一步检索数据库与全文，并由研究者确认模型、细胞来源及验证路径；不能由跨器官线索替代。', 'term': 'TMEM43'},
    'finance': {'blueprint': 'llm-ashare', 'query': 'LLM stock returns predict news headlines A shares transaction costs paper\nsite.arxiv.org large language models stock look ahead bias transaction costs China\nsite.arxiv.org/abs/2306.14222',
        'database': '公开网页检索，arXiv与ACL Anthology', 'url': 'https://aclanthology.org/',
        'criteria': '纳入可获得全文的偏差、组合评价和中国市场研究；保留预印本版本。论文结果不作为本课题已实现收益。',
        'gap': '本课题尚未获得独立样本外收益、风险和净成本结果。',
        'next': '确定可用A股数据、历史规则和事前冻结的评价协议',
        'criterion': '明确股票池、样本期、模型与新闻可获得时间、基准、波动分组和成本来源，再接入专用回测。', 'term': 'backtest'},
    'history': {'blueprint': 'japan-reconstruction', 'query': 'site.boj.or.jp postwar reconstruction Japan 1945 1955 economic history pdf\nsite.mof.go.jp english copyright terms use content',
        'database': '公开网页检索，日本财政省官方出版物', 'url': 'https://www.mof.go.jp/english/pri/publication/policy_1945-1971/index.htm',
        'criteria': '采用重建与稳定化时期官方回顾章节；区别同期档案和回顾叙述。中国对比窗口及政策净效应另需材料。',
        'gap': '这些历史资料尚不足以识别对中国政策的适用条件与效果。',
        'next': '补充同期政策档案及口径可比的中日统计资料',
        'criterion': '确认中国比较窗口、历史阶段、统计定义和数据版本；收集替代解释，区分事实与政策推断。', 'term': 'Dodge'},
}


def run(materials, workspace, output):
    manifest = json.loads((ROOT / 'resources/research_validation_sources.json').read_text(encoding='utf-8'))
    corpus = []
    # Verify every original and every quotation before touching any project.
    for source in manifest['sources']:
        path = materials / (source['key'] + '.pdf')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('原件指纹不一致：' + source['key'])
        text = extract_file(path.name, raw)
        parts = pages({'name': path.name, 'text': text})
        if clean_quote(source['quote']) not in clean_quote(parts[source['page'] - 1]):
            raise ValueError('指定页摘录不匹配：' + source['key'])
        corpus.append((source, path, raw))
    store = Store(workspace)
    service = Service(store)
    output.mkdir(parents=True, exist_ok=True)
    before = {p['id']: hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for p in store.list()}
    receipt = {'meaning': manifest['meaning'], 'projects': [], 'models_called': 0}
    try:
        if before:
            backup = service.backups.create()
            receipt['backup_before_import'] = backup['items'][0]['id']
        for profile, plan in PLANS.items():
            p = service.study_create({'blueprint': plan['blueprint']})
            pid = p['id']
            stage = FLOWS[p['flow']]['stages'][0]
            receipt_project = {'id': pid, 'profile': profile, 'sources': []}
            selected = [row for row in corpus if row[0]['profile'] == profile]
            task = next((t for t in p['research']['tasks'] if any(word in t['title'] for word in ('检索', '文献', '史料', '资料'))), p['research']['tasks'][0])

            def save(group, record, identity):
                current = store.get(pid)['research'].get(group, [])
                found = next((r for r in current if identity(r)), None)
                if found:
                    return found
                service.research_save(pid, {'collection': group, 'record': record})
                return store.get(pid)['research'][group][-1]

            save('searches', {'title': '公开验证材料检索｜2026-10-23', 'query': plan['query'], 'database': plan['database'],
                'source_date': manifest['retrieved_on'], 'url': plan['url'], 'criteria': plan['criteria'],
                'reason': '助手初步筛选；有限范围，未执行穷尽检索。全文已获取，支持关系待研究者核读。',
                'task_id': task['id'], 'brief_section': 'materials'}, lambda r: r['title'] == '公开验证材料检索｜2026-10-23')
            paragraphs = []
            for source, path, raw in selected:
                p = store.get(pid)
                asset = next((a for a in p['assets'] if a['sha256'] == source['sha256']), None)
                if not asset:
                    service.upload(pid, {'name': path.name, 'data': base64.b64encode(raw).decode()})
                    asset = store.get(pid)['assets'][-1]
                ref = save('references', {k: source[k] for k in ('title', 'authors', 'year', 'venue', 'doi', 'url', 'kind')} | {'asset_id': asset['id']},
                           lambda r: (r.get('doi') and r['doi'].lower() == source['doi'].lower()) or r['title'] == source['title'])
                src = save('sources', {'title': source['title'], 'asset_id': asset['id'], 'reference_id': ref['id'], 'url': source['url'],
                    'source_date': manifest['retrieved_on'], 'coverage': 'fulltext', 'license': source['license'], 'version': source['version'],
                    'language': 'English', 'decision': 'included', 'reason': source['reason'], 'domain_notes': source['domain_notes'],
                    'task_id': task['id'], 'brief_section': 'materials'}, lambda r: r.get('asset_id') == asset['id'])
                ev = save('evidence', {'title': '初步核读｜' + source['claim'], 'asset_id': asset['id'], 'reference_id': ref['id'],
                    'page': str(source['page']), 'quote': source['quote'], 'finding': source['finding'], 'method': source['version'],
                    'limitations': source['limitations'], 'level': source['level']}, lambda r: r['title'] == '初步核读｜' + source['claim'])
                save('claims', {'title': source['claim'], 'evidence_id': ev['id'], 'stage': stage, 'location': '公开材料初步核读 / 已有线索',
                    'assessment': 'partial', 'reason': '助手初步核读可定位出处，尚待研究者确认。' + source['limitations']}, lambda r: r['title'] == source['claim'])
                paragraphs.append(source['claim'] + ' ' + source['finding'] + ' [@' + ref['id'] + ']\n\n边界：' + source['limitations'])
                receipt_project['sources'].append({'key': source['key'], 'source_id': src['id'], 'asset_id': asset['id'],
                    'reference_id': ref['id'], 'evidence_id': ev['id'], 'page': source['page'], 'sha256': asset['sha256']})
            save('claims', {'title': plan['gap'], 'stage': stage, 'location': '公开材料初步核读 / 仍待解决', 'assessment': 'unknown',
                'reason': '材料与验证尚不充分；保留开放问题，不填入推测结果。'}, lambda r: r['title'] == plan['gap'])
            save('tasks', {'title': plan['next'], 'purpose': '公开材料初步核读后仍需补齐的真实研究条件', 'materials': '原始来源、版本与待核读全文',
                'criterion': plan['criterion'], 'status': 'todo', 'stage': stage}, lambda r: r['title'] == plan['next'])
            p = store.get(pid)
            # Preserve the original material declaration and every other brief field.
            # Keep the dated update a draft; it is not a researcher confirmation.
            marker = '[公开材料验证更新：' + manifest['retrieved_on'] + ']'
            brief = p['study_brief']
            if marker not in brief['fields']['materials']:
                fields = dict(brief['fields'])
                fields['materials'] += '\n\n' + marker + '\n已导入' + str(len(selected)) + '份公开全文，来源、版本、许可及页码见研究工具的“检索与来源”。上文为建档时的材料情况；本次只补充文献，不包含本课题原始实验、行情或比较统计数据。\n' + '\n'.join('- ' + row[0]['title'] for row in selected)
                service.study_save(pid, {'fields': fields, 'revision': brief['revision'],
                    'context_enabled': brief.get('context_enabled', False), 'confirm': False})
                p = store.get(pid)
            if not p['outputs'].get(stage, {}).get('text'):
                draft = '# 公开材料初步核读草稿\n\n本稿用于验证材料、证据、引用和导出流程，由助手初步整理，尚未由研究者核定。它不是系统综述，也不包含本课题的新实验、回测或政策因果结果。\n\n## 已有线索\n\n' + '\n\n'.join(paragraphs) + '\n\n## 仍待解决\n\n' + plan['gap'] + '\n\n' + plan['criterion']
                service.edit(pid, {'stage': stage, 'text': draft})
                receipt_project['draft_written'] = True
            else:
                receipt_project['draft_written'] = False
            audit = service.source_audit(pid)
            search = service.material_search(pid, {'query': plan['term']})
            if receipt_project['draft_written'] and audit['counts']['locatable'] < len(selected):
                raise AssertionError('来源到稿件关联未通过：' + profile)
            if not search['results']:
                raise AssertionError('真实全文未命中：' + profile)
            for kind in ('source', 'md'):
                name, data, _ = service.source_export(pid) if kind == 'source' else service.export(pid, 'md')
                if '[@' in data.decode('utf-8-sig'):
                    raise AssertionError('导出含未转换引用')
                (output / (profile + '-' + ('来源核对包.md' if kind == 'source' else '初步核读.md'))).write_bytes(data)
            (output / (profile + '-研究简报.md')).write_bytes(service.study_export(pid)[1])
            receipt_project.update(audit=audit['counts'], unresolved=[i['message'] for i in audit['issues']], search_hits=search['matched_pages'])
            receipt['projects'].append(receipt_project)
        touched = {p['id'] for p in receipt['projects']}
        unchanged = {p['id']: hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for p in store.list() if p['id'] in before and p['id'] not in touched}
        assert all(before[pid] == digest for pid, digest in unchanged.items()), '其他项目被意外修改'
        receipt['unrelated_projects_unchanged'] = len(unchanged)
        receipt['material_count'] = len(corpus)
        receipt['project_count'] = len(store.list())
        (output / '公开材料验收记录.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'projects': len(receipt['projects']), 'materials': len(corpus), 'models_called': 0,
                          'unrelated_unchanged': len(unchanged), 'output': str(output)}, ensure_ascii=True))
    finally:
        service.assistant.close()
        service.close_plotting()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.materials.resolve(), args.workspace.resolve(), args.output.resolve())
