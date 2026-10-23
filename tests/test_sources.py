import base64
import copy
import io
import json
import tempfile
import threading
import unittest
import zipfile
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from app.storage import Store, extract_file
from app.service import Service
from app.server import LocalServer


class SourceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.service = Service(self.store)
        self.pid = self.store.create({'title': 'Software validation fixture', 'flow': 'full'})['id']
        self.service.upload(self.pid, {'name': 'fixture.txt', 'data': base64.b64encode(
            b'Software fixture only: time separated evaluation. No research findings.').decode()})
        self.asset = self.store.get(self.pid)['assets'][0]
        self.ref = self.save('references', title='Software fixture', asset_id=self.asset['id'], url='https://example.org/fixture')

    def tearDown(self):
        self.service.assistant.close()
        self.service.close_plotting()
        self.tmp.cleanup()

    def save(self, group, **record):
        self.service.research_save(self.pid, {'collection': group, 'record': record})
        return self.store.get(self.pid)['research'][group][-1]

    def source(self, **override):
        return self.save('sources', **({'title': 'Fixture source', 'asset_id': self.asset['id'],
            'reference_id': self.ref['id'], 'url': 'https://example.org/fixture', 'source_date': '2026-10-23',
            'coverage': 'fulltext', 'license': 'Self authored software fixture', 'decision': 'included',
            'reason': 'Validate software only', 'domain_notes': 'No scientific inference'} | override))

    def evidence(self, **override):
        return self.save('evidence', **({'title': 'Fixture excerpt', 'asset_id': self.asset['id'],
            'reference_id': self.ref['id'], 'page': '1', 'quote': 'time separated evaluation',
            'level': 'direct', 'limitations': 'Software fixture, no scientific evidence'} | override))

    def linked_claim(self):
        self.source()
        ev = self.evidence()
        self.service.edit(self.pid, {'stage': 'research', 'text': 'Fixture statement [@' + self.ref['id'] + '].'})
        return self.save('claims', title='Fixture statement', stage='research', location='Fixture paragraph',
                         evidence_id=ev['id'], assessment='supports', reason='Verifies text location only')

    def test_fulltext_metadata_and_invalid_save_are_atomic(self):
        before = self.store.get(self.pid)
        for change in ({'license': ''}, {'asset_id': ''}, {'source_date': '2026-13-01'}, {'url': 'javascript:alert(1)'},
                       {'reason': ''}, {'coverage': 'invented'}, {'task_id': 'f' * 32}, {'brief_section': 'invalid'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.source(**change)
            self.assertEqual(self.store.get(self.pid), before)
        row = self.source()
        self.assertEqual(row['source_sha256'], self.asset['sha256'])
        with self.assertRaisesRegex(ValueError, '已有来源'):
            self.source()

    def test_search_ledger_links_tasks_and_preserves_criteria(self):
        task = self.save('tasks', title='Screen source')
        self.save('searches', title='Fixture query', query='fixture AND evaluation', database='Local test',
                  source_date='2026-10-23', criteria='Not a scientific search', task_id=task['id'], brief_section='materials')
        with self.assertRaisesRegex(ValueError, '关联'):
            self.service.research_delete(self.pid, {'collection': 'tasks', 'id': task['id'], 'acknowledge': True})
        self.assertIn('fixture AND evaluation', self.service.research_export(self.pid, 'research')[1].decode('utf-8-sig'))

    def test_non_fulltext_cannot_be_promoted_to_direct_evidence(self):
        self.source(coverage='abstract')
        with self.assertRaisesRegex(ValueError, '全文'):
            self.evidence()
        self.evidence(level='indirect')
        audit = self.service.source_audit(self.pid)
        self.assertTrue(any('不是已登记全文' in i['message'] for i in audit['issues']))

    def test_search_is_local_read_only_and_respects_disabled_materials(self):
        before = self.store.get(self.pid)
        found = self.service.material_search(self.pid, {'query': 'time evaluation'})
        self.assertEqual(found['matched_pages'], 1)
        self.assertEqual(found['results'][0]['page'], 1)
        self.assertEqual(self.store.get(self.pid), before)
        self.service.toggle_asset(self.pid, {'id': self.asset['id'], 'enabled': False})
        self.assertEqual(self.service.material_search(self.pid, {'query': 'time'})['results'], [])
        self.assertEqual(len(self.service.material_search(self.pid, {'query': 'time', 'enabled_only': False})['results']), 1)
        with self.assertRaises(ValueError):
            self.service.material_search(self.pid, {'query': 'a' * 121})

    def test_audit_checks_same_paragraph_and_edit_drift(self):
        self.linked_claim()
        audit = self.service.source_audit(self.pid)
        self.assertEqual(audit['counts']['locatable'], 1)
        self.assertEqual(audit['issues'], [])
        self.service.edit(self.pid, {'stage': 'research', 'text': 'Fixture statement.\n\nUnrelated paragraph [@' + self.ref['id'] + ']'})
        self.assertTrue(any('所在段落' in i['message'] for i in self.service.source_audit(self.pid)['issues']))
        self.service.edit(self.pid, {'stage': 'research', 'text': 'Changed statement [@' + self.ref['id'] + ']'})
        self.assertTrue(any('未在关联阶段' in i['message'] for i in self.service.source_audit(self.pid)['issues']))

    def test_audit_rechecks_file_instead_of_trusting_old_quote_flag(self):
        self.linked_claim()
        (self.store.root / self.asset['file']).write_bytes(b'Externally modified')
        audit = self.service.source_audit(self.pid)
        self.assertEqual(audit['counts']['locatable'], 0)
        self.assertTrue(any('指纹' in i['message'] for i in audit['issues']))

    def test_source_reference_mismatch_is_rejected(self):
        self.service.upload(self.pid, {'name': 'second.txt', 'data': base64.b64encode(b'Second fixture').decode()})
        asset = self.store.get(self.pid)['assets'][-1]
        other = self.save('references', title='Different fixture', asset_id=asset['id'])
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.source(reference_id=other['id'])
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.evidence(reference_id=other['id'])

    def test_export_retains_sources_pages_unknowns_and_no_verdict(self):
        empty = self.service.source_export(self.pid)[1].decode('utf-8-sig')
        self.assertIn('尚未登记论断', empty)
        claim = self.linked_claim()
        self.save('claims', **(claim | {'assessment': 'partial', 'reason': 'Unresolved boundary'}))
        result = self.service.source_export(self.pid)[1].decode('utf-8-sig')
        for text in ('Unresolved boundary', 'https://example.org/fixture', self.asset['sha256'], '文字分段 1', '不判断研究结论是否正确', '[1]'):
            self.assertIn(text, result)
        self.assertNotIn('[@', result)

    def test_backup_and_duplicate_preserve_links_and_reject_invalid_sources(self):
        self.linked_claim()
        raw = self.service.export(self.pid, 'zip')[1]
        restored = self.service.backups.restore(raw)
        p = self.store.get(restored['created_ids'][0])
        self.assertEqual(p['research']['sources'][0]['asset_id'], p['assets'][0]['id'])
        self.assertEqual(p['research']['claims'][0]['assessment'], 'unknown')
        duplicate = self.service.manage_projects({'action': 'duplicate', 'ids': [self.pid]})
        d = self.store.get(duplicate['created_ids'][0])
        self.assertEqual(d['research']['sources'][0]['asset_id'], d['assets'][0]['id'])
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(out, 'w') as changed:
            for entry in original.infolist():
                value = original.read(entry)
                if entry.filename == '项目备份.json':
                    obj = json.loads(value)
                    obj['project']['research']['sources'][0]['license'] = ''
                    value = json.dumps(obj).encode()
                changed.writestr(entry.filename, value)
        before = self.store.list()
        with self.assertRaises(ValueError):
            self.service.backups.restore(out.getvalue())
        self.assertEqual(self.store.list(), before)

    def test_http_search_audit_export_and_auth(self):
        self.linked_claim()
        server = LocalServer(self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            for action, body in [('material-search', {'query': 'time'}), ('source-audit', {}), ('source-export', {})]:
                url = base + '/api/project/' + self.pid + '/' + action
                with self.assertRaises(HTTPError):
                    urlopen(Request(url, data=json.dumps(body).encode()))
                response = urlopen(Request(url, data=json.dumps(body).encode(), headers={'X-Research-Token': server.token}))
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
            self.assertEqual(urlopen(base + '/sources.js').status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_public_empty_password_aes_pdf_is_readable(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 10 Tf 40 780 Td (Software fixture for a public empty password AES PDF.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        writer.encrypt('', algorithm='AES-256')
        output = io.BytesIO()
        writer.write(output)
        self.assertIn('[第 1 页]', extract_file('public-empty-password.pdf', output.getvalue()))
