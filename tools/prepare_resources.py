"""Vendor only the ARS documents used or inspected by this adaptation."""
from pathlib import Path
import shutil
import json
import subprocess

root=Path(__file__).resolve().parent.parent
source=root/'upstream'
target=root/'resources'/'ars'
paths=[]
for skill in ['deep-research','academic-paper','academic-paper-reviewer','academic-pipeline','sr-screener']:
    paths += [source/skill/'SKILL.md']
    paths += list((source/skill/'agents').glob('*.md'))
paths += [source/'academic-pipeline'/'references'/'ai_research_failure_modes.md',source/'LICENSE',source/'POSITIONING.md',source/'README.md']
for p in paths:
    if p.is_file():
        dest=target/p.relative_to(source)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,dest)
commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
(root/'resources'/'upstream.json').write_text(json.dumps({'repository':'https://github.com/Imbad0202/academic-research-skills','commit':commit,'license':'CC BY-NC 4.0','adaptation':'Research Studio graphical local orchestration; not the original Claude Code runtime'},indent=2),encoding='utf-8')
print(f'Prepared {len(paths)} source documents; commit {commit}')
