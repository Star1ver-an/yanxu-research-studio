"""Transparent metadata filters, not a full-text relevance judgement."""
import re


def identifier_terms(query):
    return list(dict.fromkeys(re.findall(r'\b(?=[A-Z0-9]*\d)[A-Z][A-Z0-9]{3,}\b',query)))[:8]


def required_terms(value):
    # A phrase is one requirement; spaces are meaningful. The UI documents
    # commas/semicolons as separators so "retrieval augmented generation"
    # must not become three broad alternatives.
    terms = list(dict.fromkeys(re.sub(r'\s+',' ',t).strip() for t in re.split(r'[,，;；\n]+',value or '') if t.strip()))
    if len(terms)>10 or any(len(t)>80 for t in terms):
        raise ValueError('限定词最多 10 个，每个不超过 80 字。')
    return terms


def metadata_matches(entry, terms):
    text = entry['title']+' '+entry.get('abstract','')
    def pattern(term):
        if not term.isascii():return re.escape(term)
        words=re.split(r'[\s-]+',term)
        return r'(?<![A-Za-z0-9_])'+r'[\s\-‐‑–—]+'.join(re.escape(w) for w in words)+r'(?![A-Za-z0-9_])'
    return not terms or any(re.search(pattern(t),text,re.I) for t in terms)


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
