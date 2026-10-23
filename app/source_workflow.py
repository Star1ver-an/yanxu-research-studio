"""Local source provenance, text search and traceability checks; no scientific verdicts."""
import re
import unicodedata

from .research import workspace, pages, normalize, citation_ids, STATE_LABELS
from .storage import now

BRIEF_SECTIONS = ('question', 'scope', 'materials', 'method', 'outcomes', 'limits', 'open_questions')


def validate_source_record(p, group, row):
    if row.get('brief_section') and row['brief_section'] not in BRIEF_SECTIONS:
        raise ValueError('请选择有效的简报部分。')
    if group == 'searches':
        if not row.get('query') or not row.get('database') or not row.get('source_date'):
            raise ValueError('请记录检索式、检索平台和检索日期。')
        return
    asset = next((a for a in p['assets'] if a['id'] == row.get('asset_id')), None)
    if asset:
        if any(s['id'] != row['id'] and s.get('asset_id') == asset['id'] for s in workspace(p)['sources']):
            raise ValueError('此材料已有来源登记，请编辑已有记录。')
        row['source_sha256'] = asset['sha256']
    if row.get('coverage') == 'fulltext' and (not asset or not row.get('url') or not row.get('source_date') or not row.get('license')):
        raise ValueError('登记全文前，请关联材料并填写来源链接、获取日期和使用条件。')
    if row.get('decision') in ('included', 'excluded') and not row.get('reason'):
        raise ValueError('请记录纳入或排除的理由。')
    ref = next((r for r in workspace(p)['references'] if r['id'] == row.get('reference_id')), None)
    if ref and asset and ref.get('asset_id') and ref['asset_id'] != asset['id']:
        raise ValueError('来源材料与关联文献的材料不一致。')


def clean_quote(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


class SourceWorkflow:
    def material_search(self, pid, data):
        query = data.get('query', '')
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
            raise ValueError('请输入 1–120 字的材料检索词。')
        query = query.strip()
        enabled_only = data.get('enabled_only', True)
        if type(enabled_only) is not bool:
            raise ValueError('材料选择无效。')
        p = self.store.get(pid)
        sources = {s['asset_id']: s for s in workspace(p)['sources'] if s.get('asset_id')}
        terms = list(dict.fromkeys(query.split()))
        patterns = [re.compile(re.escape(t), re.I) for t in terms]
        results, matched_pages, scanned_pages = [], 0, 0
        for asset in p['assets']:
            if enabled_only and not asset.get('enabled', True):
                continue
            for page, text in enumerate(pages(asset), 1):
                scanned_pages += 1
                matches = [pattern.search(text) for pattern in patterns]
                if not all(matches):
                    continue
                matched_pages += 1
                if len(results) >= 60:
                    continue
                at = min(m.start() for m in matches)
                source = sources.get(asset['id'], {})
                results.append({'asset_id': asset['id'], 'name': asset['name'], 'page': page,
                                'pdf': asset['name'].lower().endswith('.pdf'),
                                'snippet': text[max(0, at - 100):at + 380],
                                'enabled': asset.get('enabled', True), 'sha256': asset['sha256'],
                                'coverage': source.get('coverage', ''), 'url': source.get('url', '')})
        return {'project_id': pid, 'query': query, 'results': results, 'matched_pages': matched_pages,
                'scanned_pages': scanned_pages, 'truncated': matched_pages > len(results),
                'meaning': '仅搜索本机提取的文字；多个词须在同一页出现。未命中不代表原文不存在，扫描件和公式可能未被提取。'}

    def source_audit(self, pid, data=None):
        p = self.store.get(pid)
        return self._source_audit(p)

    def _source_audit(self, p):
        w = workspace(p)
        assets = {a['id']: a for a in p['assets']}
        refs = {r['id']: r for r in w['references']}
        sources = {s['asset_id']: s for s in w['sources'] if s.get('asset_id')}
        evidence = {e['id']: e for e in w['evidence']}
        issues, rows, checked_files = [], [], {}

        def issue(kind, identity, title, message):
            issues.append({'kind': kind, 'id': identity, 'title': title, 'message': message})

        def file_ok(asset):
            if asset['id'] not in checked_files:
                try:
                    self.material_bytes(p['id'], asset['id'])
                    checked_files[asset['id']] = True
                except (ValueError, OSError):
                    checked_files[asset['id']] = False
            return checked_files[asset['id']]

        for source in w['sources']:
            title, rid = source['title'], source['id']
            asset = assets.get(source.get('asset_id'))
            if source.get('asset_id') and (not asset or not file_ok(asset) or source.get('source_sha256') != asset['sha256']):
                issue('sources', rid, title, '来源文件缺失、已修改或指纹不一致，请重新导入并核对。')
            if source.get('decision') != 'included':
                issue('sources', rid, title, '来源尚未纳入，或已被排除。')
            if source.get('coverage') != 'fulltext':
                issue('sources', rid, title, '此来源不是已登记全文，不能替代全文核对。')
            if not all(source.get(k) for k in ('url', 'source_date', 'license', 'reference_id')):
                issue('sources', rid, title, '来源链接、获取日期、使用条件或关联文献尚未补齐。')
            if p.get('study_brief', {}).get('profile') in ('finance', 'history') and not source.get('domain_notes'):
                issue('sources', rid, title, '尚未记录时点、交易成本或历史可比性等领域条件。')

        for ev in w['evidence']:
            aid, rid, title = ev.get('asset_id'), ev['id'], ev['title']
            asset, source, ref = assets.get(aid), sources.get(aid), refs.get(ev.get('reference_id'))
            try:
                page = int(ev.get('page', ''))
                parts = pages(asset) if asset else []
                located = bool(asset and 1 <= page <= len(parts) and len(normalize(ev.get('quote', ''))) >= 3
                               and clean_quote(ev['quote']) in clean_quote(parts[page - 1])
                               and ev.get('source_sha256') == asset['sha256'] and file_ok(asset))
            except (ValueError, TypeError):
                located = False
            if not located:
                issue('evidence', rid, title, '摘录或来源指纹无法在原文件指定页复核。')
            if asset and not asset.get('enabled', True):
                issue('evidence', rid, title, '关联材料已停用。')
            if not source:
                issue('evidence', rid, title, '来源尚未登记，请补充内容层级与使用条件。')
            elif source.get('coverage') != 'fulltext' or source.get('decision') != 'included':
                issue('evidence', rid, title, '来源层级或筛选状态不足，须保留证据边界。')
            if not ref or (ref.get('asset_id') and ref['asset_id'] != aid) or (source and source.get('reference_id') != ev.get('reference_id')):
                issue('evidence', rid, title, '证据、来源与文献引用尚未一致关联。')
            if not ev.get('limitations'):
                issue('evidence', rid, title, '尚未说明证据适用范围与不可外推之处。')

        for claim in w['claims']:
            start = len(issues)
            ev = evidence.get(claim.get('evidence_id'))
            source = sources.get(ev.get('asset_id')) if ev else None
            ref = refs.get(ev.get('reference_id')) if ev else None
            text = p['outputs'].get(claim.get('stage'), {}).get('text', '')
            paragraphs = [part for part in re.split(r'\n\s*\n', text) if claim['title'] in part]
            if not paragraphs:
                issue('claims', claim['id'], claim['title'], '论断文本未在关联阶段找到；请同步修改论断与稿件位置。')
            if not ev:
                issue('claims', claim['id'], claim['title'], '尚未关联证据卡片。')
            elif not ref or not any(ref['id'] in citation_ids(part) for part in paragraphs):
                issue('claims', claim['id'], claim['title'], '论断所在段落尚未引用对应文献。')
            linked = len(issues) == start
            if claim.get('assessment') in ('unknown', 'partial', 'contradicts'):
                issue('claims', claim['id'], claim['title'], '支持关系尚需复核，请保留判断理由与相反证据。')
            if ev and ev.get('level') == 'hypothesis' and claim.get('assessment') == 'supports':
                issue('claims', claim['id'], claim['title'], '待检验假说不能作为已证实结论的依据。')
            related = [i for i in issues if (i['kind'] == 'evidence' and ev and i['id'] == ev['id'])
                       or (i['kind'] == 'sources' and source and i['id'] == source['id'])]
            rows.append({'claim': claim, 'evidence': ev, 'source': source, 'reference': ref,
                         'locatable': linked and not related})
        for stage, output in p['outputs'].items():
            for rid in set(citation_ids(output.get('text', ''))):
                if rid not in refs:
                    issue('manuscript', stage, stage, '稿件含缺失文献的引用。')
                elif not any(c.get('stage') == stage and evidence.get(c.get('evidence_id'), {}).get('reference_id') == rid for c in w['claims']):
                    issue('manuscript', stage, refs[rid]['title'], '此稿件引用尚未关联已登记论断。')
        return {'project_id': p['id'], 'checked_at': now(), 'claims': rows, 'issues': issues,
                'counts': {'sources': len(w['sources']), 'evidence': len(w['evidence']), 'claims': len(rows),
                           'locatable': sum(r['locatable'] for r in rows), 'issues': len(issues)},
                'meaning': '只检查已登记记录的出处、文件指纹和段落引用关系；不判断研究结论是否正确，也不覆盖未登记论断。'}

    def source_export(self, pid, data=None):
        # Keep the audit, manuscript and ledger on the same project revision.
        with self.lock:
            return self._source_export(pid)

    def _source_export(self, pid):
        p = self.store.get(pid)
        audit = self._source_audit(p)
        out = ['# ' + p['title'] + '：来源核对包', '> ' + audit['meaning'], '\n生成时间：' + audit['checked_at'],
               '\n## 待核对事项']
        out.extend('- ' + i['title'] + '：' + i['message'] for i in audit['issues'])
        if not audit['issues']:
            out.append('已登记记录未发现关联缺项；科学内容仍需研究者核读。' if audit['claims'] else '尚未登记论断，不能据此判断稿件已获证据支持。')
        out.append('\n## 论断与原文位置')
        for row in audit['claims']:
            claim, ev, source = row['claim'], row['evidence'], row['source']
            out += ['\n### ' + claim['title'], '- 稿件位置：' + (claim.get('location') or '未填'),
                    '- 支持关系（登记判断）：' + STATE_LABELS.get(claim.get('assessment'), '尚未判断'),
                    '- 判断理由：' + (claim.get('reason') or '未填')]
            if ev:
                asset = next((a for a in p['assets'] if a['id'] == ev.get('asset_id')), {})
                out += ['- 证据类别：' + STATE_LABELS.get(ev.get('level'), '未填'),
                        '- 原文：' + asset.get('name', '来源缺失') + '，' + ('PDF物理页 ' if asset.get('name', '').lower().endswith('.pdf') else '文字分段 ') + ev.get('page', ''),
                        '- 文件 SHA-256：' + ev.get('source_sha256', ''),
                        '- 适用边界：' + ev.get('limitations', ''), '\n> ' + ev.get('quote', '').replace('\n', '\n> ')]
            if source:
                out += ['- 来源链接：' + source.get('url', ''), '- 获取日期：' + source.get('source_date', ''),
                        '- 内容层级：' + STATE_LABELS.get(source.get('coverage'), ''),
                        '- 使用条件：' + source.get('license', ''), '- 领域条件：' + source.get('domain_notes', '')]
        out += ['\n## 稿件（保留当前状态）', self.export(pid, 'md')[1].decode('utf-8-sig'),
                '\n## 检索与筛选台账', self.research_export(pid, 'research')[1].decode('utf-8-sig')]
        return '来源核对包.md', '\n'.join(out).encode('utf-8-sig'), 'text/markdown'
