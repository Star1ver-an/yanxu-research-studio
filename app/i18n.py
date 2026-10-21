"""Presentation-only localization and explicitly captured generation languages."""
from functools import lru_cache
import json
import re
from .workflows import ROOT

LANGUAGES={'zh-CN':'简体中文','zh-TW':'繁體中文','en':'English','ja':'日本語','ko':'한국어'}

def language(value):
    if not isinstance(value,str) or value not in LANGUAGES:raise ValueError('请选择支持的语言。')
    return value

@lru_cache(maxsize=1)
def catalogs():
    return json.loads((ROOT/'resources/locales/messages.json').read_text(encoding='utf-8'))

@lru_cache(maxsize=5)
def patterns(locale):
    result=[]
    for key,value in catalogs().get(locale,{}).items():
        fields=re.findall(r'\{\d+\}',key)
        if fields:
            pieces=re.split(r'(\{\d+\})',key)
            pattern=''.join('(.*?)' if re.fullmatch(r'\{\d+\}',x) else re.escape(x) for x in pieces)
            result.append((len(key),re.compile('^'+pattern+'$',re.S),fields,value))
    return sorted(result,key=lambda x:-x[0])

def tr(text,locale='zh-CN'):
    if locale=='zh-CN' or not isinstance(text,str):return text
    key=text.strip();mapping=catalogs().get(locale,{})
    if key in mapping:return text[:len(text)-len(text.lstrip())]+mapping[key]+text[len(text.rstrip()):]
    if '\n' in text:return '\n'.join(tr(line,locale) for line in text.split('\n'))
    prefix=re.match(r'^(?:#{1,6}\s+|>\s*|[-*]\s+)',text)
    if prefix:return prefix[0]+tr(text[len(prefix[0]):],locale)
    for _,pattern,fields,value in patterns(locale):
        match=pattern.fullmatch(key)
        if match:
            args=dict(zip(fields,match.groups()))
            return re.sub(r'\{\d+\}',lambda m:args.get(m[0],m[0]),value)
    return text

def generated_text(text,locale):
    """Only for application-generated guidance/reports, never author/AI content."""
    return '\n'.join(tr(line,locale) for line in text.split('\n'))

def output_language(project,store):
    value=project.get('output_language','auto') if project else 'auto'
    return store.language()['ui_language'] if value=='auto' else language(value)

def output_instruction(locale):
    name={'zh-CN':'Simplified Chinese','zh-TW':'Traditional Chinese (Taiwan)','en':'English','ja':'Japanese','ko':'Korean'}[language(locale)]
    return '\nOUTPUT LANGUAGE: Write all user-facing prose, headings, draft field VALUES and new figure labels in '+name+'. Preserve JSON keys, IDs, URLs, citations, code, dataset column names and quoted original evidence exactly. This language selection overrides the guide default; do not translate existing source material.\n'


def format_text(source,locale,*args):
    """Translate the application template, then insert untouched author values."""
    return re.sub(r"\{(\d+)\}",lambda match:str(args[int(match[1])]),tr(source,locale))
