"""Offline HTTP acceptance for briefs, provenance and packaged AES PDF support."""
import argparse
import base64
import io
import json
from pathlib import Path
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_analysis import Client, digest, utc


def aes_fixture():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 10 Tf 40 780 Td (Software fixture for RAG evaluation. No scientific findings.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.encrypt('', algorithm='AES-256')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def execute(client):
    boot = client.request('bootstrap')
    if not Path(boot['data_path']).resolve().is_relative_to((ROOT/'qa-runs').resolve()):
        raise ValueError('Only isolated qa-runs workspaces are allowed')
    settings = digest(boot['settings'])
    plans = client.request('studies')['blueprints']
    assert {plan['id'] for plan in plans} == {'tmem43-aki', 'llm-ashare', 'japan-reconstruction'}
    plan = client.request('studies/create', {'blueprint': 'llm-ashare'})
    assert client.request('studies/create', {'blueprint': 'llm-ashare'})['id'] == plan['id']
    assert plan['study_brief']['status'] == 'draft' and not plan['study_brief']['context_enabled']
    assert not plan['assets'] and not plan['outputs'] and not plan['runs']

    title = 'RAG source traceability software fixture'
    p = client.request('project', {'title': title, 'topic': 'Software verification only', 'flow': 'full', 'experiments': 'none'})
    pid = p['id']
    fields = dict(original_title=title, working_title=title, question='How can RAG evidence be located?',
        scope='Software acceptance only', materials='Synthetic one-page PDF', method='Check provenance links',
        outcomes='A verifiable export', limits='No scientific findings', open_questions='Researcher review remains required')
    payload = dict(revision=0, fields=fields, context_enabled=False, confirm=True, acknowledge=True)
    p = client.request(f'project/{pid}/study-save', payload)
    assert p['study_brief']['status'] == 'confirmed'
    try:
        client.request(f'project/{pid}/study-save', payload)
    except ValueError:
        pass
    else:
        raise AssertionError('A stale brief revision was accepted')
    assert title in client.request(f'project/{pid}/study-export', {}, raw=True).decode('utf-8-sig')
    p = client.request(f'project/{pid}/upload', {'name': 'rag-aes-fixture.pdf',
        'data': base64.b64encode(aes_fixture()).decode()})
    aid = p['assets'][-1]['id']
    found = client.request(f'project/{pid}/material-search', {'query': 'RAG evaluation'})
    assert found['matched_pages'] == 1 and found['results'][0]['page'] == 1

    def save(group, **record):
        result = client.request(f'project/{pid}/research-save', {'collection': group, 'record': record})
        return result['research'][group][-1]

    task = save('tasks', title='Validate RAG source links', criterion='Synthetic fixture exports with provenance')
    ref = save('references', title='RAG software fixture', asset_id=aid, url='https://example.org/rag-fixture')
    save('searches', title='Software test query', query='RAG AND evaluation', database='Offline fixture',
        source_date=utc()[:10], criteria='Software verification only', task_id=task['id'], brief_section='materials')
    save('sources', title='Synthetic RAG source', asset_id=aid, reference_id=ref['id'],
        url='https://example.org/rag-fixture', source_date=utc()[:10], coverage='fulltext',
        license='Self-authored synthetic software fixture', decision='included', reason='Verify software',
        task_id=task['id'], brief_section='materials')
    evidence = save('evidence', title='Synthetic excerpt', asset_id=aid, reference_id=ref['id'], page='1',
        quote='Software fixture for RAG evaluation.', level='direct', limitations='Software acceptance, no scientific evidence')
    client.request(f'project/{pid}/edit', {'stage': 'research', 'text': 'Fixture statement [@'+ref['id']+'].'})
    save('claims', title='Fixture statement', evidence_id=evidence['id'], stage='research',
        location='First paragraph', assessment='supports', reason='Text location only')
    audit = client.request(f'project/{pid}/source-audit', {})
    assert audit['counts']['locatable'] == 1 and not audit['issues']
    exported = client.request(f'project/{pid}/source-export', {}, raw=True).decode('utf-8-sig')
    assert 'rag-aes-fixture.pdf' in exported and 'https://example.org/rag-fixture' in exported and '[@' not in exported
    assert 'PDF物理页 1' in exported
    p = client.request(f'project/{pid}')
    b = p['study_brief']
    client.request(f'project/{pid}/study-save', dict(revision=b['revision'], fields=b['fields'],
        context_enabled=True, confirm=True, acknowledge=True))
    clone = client.request('projects/manage', {'action': 'duplicate', 'ids': [pid], 'mode': 'reports'})['created_ids'][0]
    backup = client.request('backups/create', {})['items'][0]['id']
    restored = client.request('backups/restore', {'id': backup, 'acknowledge': True})['created_ids']
    for identity in [clone, *restored]:
        restored_project = client.request('project/'+identity)
        if 'study_brief' not in restored_project:
            continue
        brief = restored_project['study_brief']
        assert brief['status'] == 'draft' and not brief['context_enabled']
        if brief['fields']['working_title'] == title:
            w = restored_project['research']
            assert w['sources'][0]['asset_id'] == restored_project['assets'][0]['id']
            assert w['claims'][0]['assessment'] == 'unknown'
    assert settings == digest(client.request('bootstrap')['settings'])
    return {'project': pid, 'brief_revision_guard': True, 'aes_pdf_import': True, 'source_search_audit_export': True,
        'duplicate_and_backup_restore': True, 'paid_model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url')
    parser.add_argument('--output', default='qa-runs/study-source/http')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to((ROOT/'qa-runs').resolve()):
        raise ValueError('Only qa-runs output is allowed')
    output.mkdir(parents=True, exist_ok=True)
    server = None
    try:
        if args.base_url:
            base = args.base_url
        else:
            from app.server import LocalServer
            from app.service import Service
            from app.storage import Store
            server = LocalServer(Service(Store(output/('workspace-'+uuid.uuid4().hex[:8]))), port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_port}'
        result = execute(Client(base))
        (output/'report.json').write_text(json.dumps({'passed': True, **result}, indent=2), encoding='utf-8')
        print('Study briefs, source traceability, AES PDFs and backup acceptance passed; no model calls.')
    finally:
        if server:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
