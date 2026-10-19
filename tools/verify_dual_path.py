"""Verify the v0.2 desktop build against this run's isolated test evidence."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import sqlite3
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def digest(value):
    return hashlib.sha256(value).hexdigest()


def projects(database):
    with sqlite3.connect(database) as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT body FROM projects')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qa-dir', type=Path, required=True)
    qa = parser.parse_args().qa_dir.resolve()
    runtime_path = Path(os.environ['LOCALAPPDATA']) / 'YanxuResearchStudio/runtime.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    with urlopen(runtime['url'], timeout=10) as response:
        page = response.read().decode('utf-8')
    token = re.search(r'name="research-token" content="([^"]+)"', page).group(1)
    with urlopen(Request(runtime['url']+'/api/bootstrap', headers={'X-Research-Token': token}), timeout=10) as response:
        boot = json.load(response)
    assert runtime['state'] == 'ui_ready' and boot['desktop_ready'] is True
    assert boot['catalog']['version'] == '0.2.0'
    assert set(boot['catalog']['routes']) == {'ars', 'scientific'}
    for path in ('/ars', '/scientific', '/pathways.js'):
        with urlopen(runtime['url']+path, timeout=10) as response:
            assert response.status == 200

    current = {p['id']: digest(json.dumps(p, ensure_ascii=False, sort_keys=True).encode('utf-8'))
               for p in projects(runtime_path.parent/'workspace.sqlite3')}
    before = json.loads((qa/'original-projects.json').read_text(encoding='utf-8'))
    assert current == before, 'Existing project records changed'

    manifest = json.loads((ROOT/'resources/scientific/manifest.json').read_text(encoding='utf-8'))
    assert len(manifest['skills']) == 177 and not manifest['omitted_reference_assets']
    for entry in manifest['files']:
        assert digest((ROOT/'resources/scientific'/entry['path']).read_bytes()) == entry['sha256']

    test_projects = projects(qa/'workspace/workspace.sqlite3')
    ars = next(p for p in test_projects if p.get('route') == 'ars' and p.get('route_choice') == 'recommended')
    science = json.loads((qa/'science-project.json').read_text(encoding='utf-8'))['project']
    manual = next(p for p in test_projects if p['title'] == '手动选择路径验收')
    assert ars['flow'] == 'full' and science['flow'] == 'scientific_data'
    assert manual['route'] == 'ars' and not manual['selected_skill']
    assert ars['runs'][-1]['status'] == 'completed' and ars['runs'][-1]['mode'] == 'codex'
    assert science['runs'][0]['source_commit'] == manifest['commit']
    assert science['runs'][0]['status'] == 'completed' and science['runs'][0]['mode'] == 'codex'
    run = science['runs'][-1]
    execution = run['tool_execution']
    profile = execution['profiles'][0]
    assert run['mode'] == 'local' and execution['model_called'] is False and execution['network'] is False
    assert (profile['rows'], profile['columns'], profile['duplicate_rows']) == (5, 3, 1)
    value = profile['fields'][2]
    assert (value['missing'], value['n'], value['mean'], value['median']) == (1, 4, 2.25, 2.5)
    assert manifest['commit'] in (qa/'science-report.md').read_text(encoding='utf-8-sig')
    search = manual['searches'][-1]
    assert search['source'] == 'PubMed' and search['returned'] > 0
    records = [a for a in manual['literature'] if a.get('pmid')]
    assert len(records) == search['added'] and all(not a['enabled'] for a in records)
    tests = (qa/'python-tests.log').read_text(encoding='utf-8')
    assert re.search(r'Ran 62 tests.*?\bOK\b', tests, re.S)

    exe = ROOT/'dist/YanxuResearchStudio.exe'
    with zipfile.ZipFile(ROOT/'dist/研序-ResearchStudio-Windows-v0.2.0.zip') as archive:
        assert archive.testzip() is None
        assert digest(archive.read('研序 Research Studio/YanxuResearchStudio.exe')) == digest(exe.read_bytes())
        assert '研序 Research Studio/Scientific-Agent-Skills-LICENSE.txt' in archive.namelist()
    with zipfile.ZipFile(ROOT/'dist/研序-ResearchStudio-Source-v0.2.0.zip') as archive:
        assert archive.testzip() is None
        assert 'academic-workbench/app/routing.py' in archive.namelist()
        assert 'academic-workbench/app/data_tools.py' in archive.namelist()
        bundled = json.loads(archive.read('academic-workbench/resources/scientific/manifest.json'))
        assert bundled == manifest
        assert not any(name.endswith('.sqlite3') for name in archive.namelist())

    report = dict(version='0.2.0', native_state=runtime['state'], desktop_bridge_ready=True,
        original_projects_unchanged=True, original_project_count=len(before), python_tests_passed=62,
        routes=['ars', 'scientific'], scientific_source_commit=manifest['commit'],
        skill_count=len(manifest['skills']), verified_source_files=len(manifest['files']),
        recommended_and_manual_creation_verified=True, local_codex_smoke_both_routes=True,
        csv_calculation=execution, pubmed=dict(candidates=search['candidates'], retained=search['returned'],
        rejected=len(search['rejected']), all_new_records_unselected=True),
        archives_verified=True, executable_sha256=digest(exe.read_bytes()),
        limits='Recommendation uses local rules; advanced skills require their own environments; no paid vendor API used.')
    (ROOT/'dist/verification-v0.2.0.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='csv_calculation'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
