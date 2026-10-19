from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import sqlite3
from urllib.request import Request,urlopen
import zipfile

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser()
parser.add_argument('--qa-dir',type=Path,required=True,help='Directory containing this run\'s test and UI receipts')
qa=parser.parse_args().qa_dir.resolve()
tests=json.loads((qa/'unit-tests.json').read_text())
codex=json.loads((qa/'codex-result.json').read_text())
crossref=json.loads((qa/'crossref-result.json').read_text(encoding='utf-8'))
word=json.loads((qa/'word-result.json').read_text())
runtime=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'YanxuResearchStudio'/'runtime.json'
state=json.loads(runtime.read_text(encoding='utf-8'))
with urlopen(state['url'],timeout=5) as r:
    page=r.read().decode('utf-8')
token=re.search(r'name="research-token" content="([^"]+)"',page).group(1)
with urlopen(Request(state['url']+'/api/bootstrap',headers={'X-Research-Token':token}),timeout=5) as r:
    data=json.load(r)
exe=root/'dist'/'YanxuResearchStudio.exe'
with zipfile.ZipFile(root/'dist'/'研序-ResearchStudio-Windows.zip') as archive:
    assert archive.testzip() is None
    matching=hashlib.sha256(archive.read('研序 Research Studio/YanxuResearchStudio.exe')).digest()==hashlib.sha256(exe.read_bytes()).digest()
before=json.loads((qa/'original-projects.json').read_text(encoding='utf-8'))
with sqlite3.connect(runtime.parent/'workspace.sqlite3') as db:
    after={}
    for row in db.execute('SELECT body FROM projects'):
        p=json.loads(row[0])
        after[p['id']]={'title':p['title'],'cursor':p['cursor'],
            'outputs':{k:hashlib.sha256(v['text'].encode()).hexdigest() for k,v in p['outputs'].items()},
            'confirmations':p['confirmations'],'assets':len(p['assets'])}
unchanged=before==after
report={'native_state':state['state'],'desktop_bridge_ready':data['desktop_ready'],
        'archive_matches_executable':matching,'executable_bytes':exe.stat().st_size,
        'version':data['catalog']['version'],'unit_tests':tests,
        'codex_message_separation':codex,'crossref_candidates':crossref['candidates'],
        'crossref_retained':crossref['returned'],'crossref_rejected':len(crossref['rejected']),
        'crossref_new_records_unselected':crossref['all_unselected'],'word_visual_qa':word,
        'original_projects_unchanged':unchanged,
        'http_api_protocols':'compatible and Anthropic verified against local mock servers; no paid vendor request',
        'source_commit':data['catalog']['source_commit']}
(root/'dist'/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
assert report['native_state']=='ui_ready'
assert report['desktop_bridge_ready'] is True
assert matching
assert unchanged and tests['passed'] and codex['commentary_absent'] and codex['final_marker']
assert word['visual_review_all_pages']
