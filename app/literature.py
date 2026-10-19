"""Transparent metadata filters, not a full-text relevance judgement."""
import re


def identifier_terms(query):
    return list(dict.fromkeys(re.findall(r'\b(?=[A-Z0-9]*\d)[A-Z][A-Z0-9]{3,}\b',query)))[:8]


def required_terms(value):
    terms = list(dict.fromkeys(t.strip() for t in re.split(r'[,，;；\s]+',value or '') if t.strip()))
    if len(terms)>10 or any(len(t)>80 for t in terms):
        raise ValueError('限定词最多 10 个，每个不超过 80 字。')
    return terms


def metadata_matches(entry, terms):
    text = entry['title']+' '+entry.get('abstract','')
    return not terms or any(re.search((r'(?<![A-Za-z0-9_])'+re.escape(t)+r'(?![A-Za-z0-9_])')
                            if t.isascii() else re.escape(t),text,re.I) for t in terms)


def filter_entries(entries, terms, journal_only):
    kept, rejected = [], []
    for entry in entries:
        reason = ''
        if journal_only and entry.get('type')!='journal-article':
            reason = '非期刊论文元数据类型'
        elif journal_only and entry.get('is_supplement'):
            reason = '独立补充材料记录'
        elif journal_only and re.search(r'\bsuppl(?:ement)?[\s_.-]*\d*\b',entry.get('issue',''),re.I):
            reason = '期刊补充期记录，可能为会议摘要（可取消类型限制查看）'
        elif not metadata_matches(entry,terms):
            reason = '题名及可用摘要未命中限定词'
        if reason:
            rejected.append({'doi':entry.get('doi',''),'title':entry['title'],'reason':reason})
        else:
            kept.append(entry)
    return kept,rejected
