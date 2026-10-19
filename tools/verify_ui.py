"""Check the packaged Soft UI update using saved browser-test evidence."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import sqlite3
from urllib.request import Request, urlopen
import zipfile

ROOT=Path(__file__).resolve().parent.parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--qa-dir',type=Path,required=True)
    qa=parser.parse_args().qa_dir.resolve()
    runtime_file=Path(os.environ['LOCALAPPDATA'])/'YanxuResearchStudio/runtime.json'
    state=json.loads(runtime_file.read_text(encoding='utf-8'))
    with urlopen(state['url'],timeout=10) as response:
        page=response.read().decode('utf-8')
    token=re.search(r'name="research-token" content="([^"]+)"',page).group(1)
    with urlopen(Request(state['url']+'/api/bootstrap',headers={'X-Research-Token':token}),timeout=10) as response:
        bootstrap=json.load(response)
    assert state['state']=='ui_ready' and bootstrap['desktop_ready'] is True
    assert bootstrap['catalog']['version']=='0.2.1'
    resources={}
    for name in ('soft-ui.css','appearance.js','app.js','pathways.js'):
        assert '/'+name in page
        with urlopen(state['url']+'/'+name,timeout=10) as response:
            data=response.read()
        resources[name]=sha(data)
        assert data==(ROOT/'web'/name).read_bytes(),name
    css=(ROOT/'web/soft-ui.css').read_text(encoding='utf-8')
    assert not re.search(r'@import|url\(\s*[\'"]?https?://',css)
    with sqlite3.connect(runtime_file.parent/'workspace.sqlite3') as connection:
        projects=[json.loads(row[0]) for row in connection.execute('SELECT body FROM projects')]
    actual={p['id']:sha(json.dumps(p,ensure_ascii=False,sort_keys=True).encode()) for p in projects}
    assert actual==json.loads((qa/'original-projects.json').read_text(encoding='utf-8'))
    assert re.search(r'Ran 62 tests.*?\bOK\b',(qa/'python-tests.log').read_text(encoding='utf-8'),re.S)
    frontend=(qa/'frontend-tests.log').read_text(encoding='utf-8')
    assert 'Frontend regression checks passed' in frontend and 'Appearance checks passed' in frontend
    ui=json.loads((qa/'ui-checks.json').read_text(encoding='utf-8'))
    assert not ui['console_errors'] and not ui['horizontal_overflow']
    assert all(ui['keyboard'].values()) and ui['theme_switch_preserved_stage_note']
    exported=(qa/'export-smoke.md').read_text(encoding='utf-8-sig')
    assert 'TMEM43' in exported and '过程记录' in exported
    with zipfile.ZipFile(ROOT/'dist/研序-ResearchStudio-Windows-v0.2.1.zip') as archive:
        assert archive.testzip() is None
        assert sha(archive.read('研序 Research Studio/YanxuResearchStudio.exe'))==sha((ROOT/'dist/YanxuResearchStudio.exe').read_bytes())
    with zipfile.ZipFile(ROOT/'dist/研序-ResearchStudio-Source-v0.2.1.zip') as archive:
        assert archive.testzip() is None
        for name in resources:
            assert archive.read('academic-workbench/web/'+name)==(ROOT/'web'/name).read_bytes()
        assert not any('qa-runs/' in name or name.endswith('.sqlite3') for name in archive.namelist())
    report=dict(version='0.2.1',native_state=state['state'],desktop_bridge_ready=True,
        original_projects_unchanged=True,project_count=len(projects),python_tests_passed=62,
        frontend_and_appearance_tests_passed=True,ui_checks=ui,served_resource_sha256=resources,
        design_reference=json.loads((qa/'reference-source.json').read_text(encoding='utf-8')),
        executable_sha256=sha((ROOT/'dist/YanxuResearchStudio.exe').read_bytes()),archives_verified=True)
    (ROOT/'dist/verification-v0.2.1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('served_resource_sha256','ui_checks')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
