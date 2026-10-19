"""Pinned Scientific Agent Skills discovery and bounded method loading."""
from functools import lru_cache
import json


@lru_cache(maxsize=1)
def manifest():
    from .workflows import ROOT
    path=ROOT/'resources'/'scientific'/'manifest.json'
    if not path.is_file():raise ValueError('科研技能资源缺失，请使用完整的软件包。')
    return json.loads(path.read_text(encoding='utf-8'))


def catalog():
    data=manifest()
    return {k:data[k] for k in ('repository','commit','version','skills')}


def skill_text(sid,limit=12000):
    from .workflows import ROOT
    if sid not in {x['id'] for x in manifest()['skills']}:
        raise ValueError('未找到此科研技能。')
    return (ROOT/'resources'/'scientific'/'skills'/sid/'SKILL.md').read_text(encoding='utf-8')[:limit]


def method_context(stage,selected=''):
    ids=list(dict.fromkeys(stage.get('scientific_skills',[])+([selected] if selected else [])))[:4]
    return '\n'.join('\n专业方法参考 '+sid+'：\n'+skill_text(sid,10000) for sid in ids)


def skill_detail(sid):
    entry=next((x for x in manifest()['skills'] if x['id']==sid),None)
    if not entry:raise ValueError('未找到此科研技能。')
    return {**entry,'text':skill_text(sid,60000),'source':manifest()['repository']+'/blob/'+manifest()['commit']+'/skills/'+sid+'/SKILL.md',
        'availability':'方法参考；依赖以技能原文为准。内置计算由具体工作流标明。'}
