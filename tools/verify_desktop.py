"""Verify a built desktop activation fix against saved native test receipts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
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
    assert state['state']=='ui_ready'
    with urlopen(state['url'],timeout=10) as response:
        page=response.read().decode()
    token=re.search(r'name="research-token" content="([^"]+)"',page).group(1)
    with urlopen(Request(state['url']+'/api/bootstrap',headers={'X-Research-Token':token}),timeout=10) as response:
        data=json.load(response)
    assert data['desktop_ready'] is True and data['catalog']['version']=='0.2.2'
    with sqlite3.connect(runtime_file.parent/'workspace.sqlite3') as connection:
        projects=[json.loads(row[0]) for row in connection.execute('SELECT body FROM projects')]
    after={p['id']:sha(json.dumps(p,ensure_ascii=False,sort_keys=True).encode()) for p in projects}
    assert after==json.loads((qa/'original-projects.json').read_text(encoding='utf-8'))
    tests=(qa/'python-tests.log').read_text(encoding='utf-8')
    assert re.search(r'Ran 66 tests.*?\bOK\b',tests,re.S)
    native=json.loads((qa/'native-checks.json').read_text(encoding='utf-8'))
    assert all(native[k] for k in ('hidden_launch_visible','minimized_duplicate_restored',
        'duplicate_exited_without_popup','same_primary_pid','graceful_exit_and_restart','packaged_window_visible'))
    assert native['packaged_pid']==state['pid']
    window_state=json.loads((runtime_file.parent/'window-state.json').read_text(encoding='utf-8'))
    assert window_state['pid']==state['pid'] and window_state['visible'] is True
    assert window_state['window_state']=='Normal' and window_state['activations']>=2
    with zipfile.ZipFile(ROOT/'dist/研序-ResearchStudio-Windows-v0.2.2.zip') as archive:
        assert archive.testzip() is None
        assert sha(archive.read('研序 Research Studio/YanxuResearchStudio.exe'))==sha((ROOT/'dist/YanxuResearchStudio.exe').read_bytes())
    with zipfile.ZipFile(ROOT/'dist/研序-ResearchStudio-Source-v0.2.2.zip') as archive:
        assert archive.testzip() is None
        for name in ('app/desktop.py','main.py','tests/test_desktop.py'):
            assert archive.read('academic-workbench/'+name)==(ROOT/name).read_bytes()
    report=dict(version='0.2.2',state=state['state'],desktop_ready=True,python_tests_passed=66,
        project_count=len(projects),original_projects_unchanged=True,native_checks=native,window_state=window_state,
        executable_sha256=sha((ROOT/'dist/YanxuResearchStudio.exe').read_bytes()),archives_verified=True)
    (ROOT/'dist/verification-v0.2.2.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
