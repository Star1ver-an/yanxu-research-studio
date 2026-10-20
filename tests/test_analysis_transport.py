"""Loopback HTTP, real isolated worker, cancellation and resource boundaries.

Fixtures are synthetic device reliability records in disposable workspaces.
No user workspace, external API, package installer or updater is invoked.
"""
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from app import analysis
from app.server import LocalServer
from app.service import Service
from app.storage import Store


class AnalysisTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Store(self.temp.name))
        self.service.backups.configure({'enabled': False, 'retain': 3})
        self.server = LocalServer(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.gates = []
        self.pid = self.request('/api/project', {'title': '合成 HTTP 验收｜设备可靠性', 'flow': 'full'})['id']
        self.other = self.request('/api/project', {'title': '另一独立验收项目', 'flow': 'full'})['id']
        self.request(f'/api/project/{self.pid}/upload', {'name': 'devices.csv', 'data': base64.b64encode(
            b'unit,time,event,group\na,1,1,A\nb,2,0,A\nc,3,1,A\nd,4,0,A\ne,1,0,B\nf,2,1,B\ng,3,0,B\nh,5,1,B\n').decode()})
        self.aid = self.service.store.get(self.pid)['assets'][0]['id']
        self.spec = {'version': 1, 'kind': 'analysis', 'title': '合成设备运行分析', 'source_ids': [self.aid],
                     'style': {'width': 7, 'height': 5, 'dpi': 80}, 'mapping': {}, 'options': {},
                     'analysis': {'method': 'km', 'inputs': {'data': self.aid},
                                  'columns': {'duration': 'time', 'event': 'event', 'group': 'group'},
                                  'parameters': {'independent_units': True}}}

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.server.shutdown()
        self.server.server_close()
        # close_analysis deliberately does not block the UI. Tests wait for its
        # supervisor's finally block before deleting their disposable workspace.
        self.service.analysis_pool.shutdown(wait=True, cancel_futures=True)
        self.service.plot_worker.shutdown(wait=True, cancel_futures=True)
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, path, data=None, *, status=200, token=True, host=None, raw=False):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Research-Token'] = self.server.token if token is True else token
        if host:
            headers['Host'] = host
        payload = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = Request(self.base + path, data=payload, headers=headers)
        try:
            response = urlopen(req, timeout=90)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            self.assertEqual(response.status, status, (path, body[:1000]))
            self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
            if raw:
                return body, response.headers
            return json.loads(body)

    def endpoint(self, action, data=None, *, pid=None, **kwargs):
        return self.request(f'/api/project/{pid or self.pid}/{action}', {} if data is None else data, **kwargs)

    def start(self, spec=None, request_id=None):
        spec = spec or self.spec
        preview = self.endpoint('analysis-preview', {'config': spec['analysis']})
        data = {'spec': spec, 'signature': preview['signature'], 'request_id': request_id or uuid.uuid4().hex}
        return self.endpoint('analysis-start', data), data

    def wait_final(self, identity, timeout=45):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = self.endpoint('analysis-job', {'id': identity})
            if value['status'] not in {'queued', 'running'}:
                return value
            time.sleep(.04)
        self.fail('本机分析任务在验收时间内没有结束。')

    def blocked_queue(self):
        gate = threading.Event()
        self.gates.append(gate)
        future = self.service.analysis_pool.submit(lambda: gate.wait(20))
        return gate, future

    def test_http_catalog_inspect_preflight_auth_and_source_ownership(self):
        for token in (False, 'incorrect-local-token'):
            self.request('/api/bootstrap', status=403, token=token)
        self.request('/api/bootstrap', status=403, host='untrusted.example')
        methods = self.endpoint('analysis-catalog')['methods']
        self.assertTrue(any(row['id'] == 'km' and row['available'] for row in methods))
        info = self.endpoint('analysis-inspect', {'asset_id': self.aid})
        self.assertEqual(info['row_count'], 8)
        self.assertNotIn('preview', info)
        self.assertNotIn('rows', info)
        self.endpoint('analysis-inspect', {'asset_id': self.aid}, pid=self.other, status=400)
        self.endpoint('analysis-preview', {'config': self.spec['analysis']}, pid=self.other, status=400)
        preflight = self.endpoint('analysis-preview', {'config': self.spec['analysis']})
        self.assertEqual(len(preflight['signature']), 64)
        bad = copy.deepcopy(self.spec['analysis']); bad['columns']['duration'] = 'absent'
        self.endpoint('analysis-preview', {'config': bad}, status=400)
        bad = copy.deepcopy(self.spec['analysis']); bad['parameters']['independent_units'] = False
        self.endpoint('analysis-preview', {'config': bad}, status=400)
        self.endpoint('analysis-start', {'spec': self.spec, 'signature': 'stale', 'request_id': uuid.uuid4().hex}, status=400)

    def test_http_real_worker_idempotency_tables_and_cross_project_job_guards(self):
        with patch('app.plotting.api_complete', side_effect=AssertionError('analysis must not call a paid API')) as model:
            job, data = self.start()
            same = self.endpoint('analysis-start', data)
            self.assertEqual(job['id'], same['id'])
            for action in ('analysis-job', 'analysis-cancel', 'plot-analysis-result', 'analysis-table'):
                self.endpoint(action, {'id': job['id'], 'table': 'curve'}, pid=self.other, status=400)
            final = self.wait_final(job['id'])
            self.assertEqual(final['status'], 'completed', final)
            self.assertTrue(base64.b64decode(final['preview']).startswith(b'\x89PNG'))
            self.assertEqual(final['result']['statistics']['analyzed_n'], 8)
            self.assertEqual(self.endpoint('analysis-start', data)['id'], job['id'])
            self.assertNotIn('path', json.dumps(final['result']))
            rows = self.endpoint('analysis-table', {'id': job['id'], 'table': 'curve', 'format': 'json'})
            self.assertEqual(rows['columns'], ['group', 'time', 'estimate', 'lower', 'upper'])
            csv, headers = self.endpoint('analysis-table', {'id': job['id'], 'table': 'curve'}, raw=True)
            self.assertTrue(csv.startswith(b'\xef\xbb\xbf'))
            self.assertIn('attachment', headers['Content-Disposition'])
            self.endpoint('analysis-table', {'id': job['id'], 'table': 'nonexistent'}, status=400)
            self.endpoint('analysis-table', {'id': job['id'], 'table': 'curve', 'format': 'code'}, status=400)
            model.assert_not_called()
        folder = Path(self.temp.name) / 'analysis-jobs' / job['id']
        end = time.monotonic() + 3
        while folder.exists() and time.monotonic() < end:
            time.sleep(.03)
        self.assertFalse(folder.exists(), '原始输入应随成功任务清理')

    def test_queued_cancel_is_immediate_and_does_not_spawn_worker(self):
        gate, busy = self.blocked_queue()
        job, data = self.start()
        self.assertEqual(job['status'], 'queued')
        cancelled = self.endpoint('analysis-cancel', {'id': job['id']})
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertFalse(self.service.analysis_busy(self.pid))
        self.assertEqual(self.endpoint('analysis-start', data)['status'], 'cancelled', '重复请求不能意外重新计算')
        self.assertFalse((Path(self.temp.name) / 'analysis-jobs' / job['id']).exists())
        gate.set(); busy.result(timeout=3)
        # A barrier after the cancelled task confirms its supervisor completed.
        self.service.analysis_pool.submit(lambda: None).result(timeout=5)
        self.assertEqual(self.endpoint('analysis-job', {'id': job['id']})['status'], 'cancelled')

    def test_running_cancel_kills_real_worker_and_cleans_temporary_inputs(self):
        job, _ = self.start()
        end = time.monotonic() + 8
        process = None
        while time.monotonic() < end:
            process = self.service.analysis_jobs[job['id']].get('process')
            if process is not None and process.poll() is None:
                break
            time.sleep(.01)
        self.assertIsNotNone(process, '验收必须实际启动计算子进程')
        self.assertIsNone(process.poll(), '需在真实运行期间测试取消')
        self.endpoint('analysis-cancel', {'id': job['id']})
        final = self.wait_final(job['id'], timeout=10)
        self.assertEqual(final['status'], 'cancelled', final)
        self.service.analysis_pool.submit(lambda: None).result(timeout=5)
        self.assertIsNotNone(process.poll())
        self.assertFalse((Path(self.temp.name) / 'analysis-jobs' / job['id']).exists())
        self.assertNotIn('plotting', self.service.store.get(self.pid))

    def test_busy_guards_prevent_project_mutation_backup_runtime_and_update_install(self):
        from main import DesktopAPI
        gate, busy = self.blocked_queue()
        job, _ = self.start()
        before = self.service.store.get(self.pid)
        for action in ('duplicate', 'trash', 'archive', 'rename'):
            payload = {'ids': [self.pid], 'action': action, 'acknowledge': True, 'titles': {self.pid: '不能在分析中改名'}}
            self.request('/api/projects/manage', payload, status=400)
        self.request('/api/backups/create', {}, status=400)
        self.request('/api/analysis/runtime-check', {'python': sys.executable}, status=400)
        api = DesktopAPI(self.service)
        api._updates = Mock()
        api._window = Mock()
        with self.assertRaisesRegex(ValueError, '仍在运行'):
            api._install_update()
        api._updates.prepare_install.assert_not_called()
        api._window.destroy.assert_not_called()
        self.assertFalse(self.service.updating)
        self.assertEqual(before, self.service.store.get(self.pid))
        self.endpoint('analysis-cancel', {'id': job['id']})
        gate.set(); busy.result(timeout=3)

    def test_runtime_check_valid_current_python_and_invalid_paths(self):
        self.request('/api/analysis/runtime-check', {'python': 'relative-python.exe'}, status=400)
        self.request('/api/analysis/runtime-check', {'python': str(Path(self.temp.name) / 'missing-python.exe')}, status=400)
        runtime = self.request('/api/analysis/runtime-check', {'python': sys.executable})
        self.assertEqual(Path(runtime['python']).resolve(), Path(sys.executable).resolve())
        self.assertTrue(runtime['capabilities']['numpy'])
        self.assertTrue(runtime['capabilities']['lifelines'])
        self.assertFalse(runtime['private'])
        self.assertEqual(self.request('/api/analysis/runtime', {})['python'], runtime['python'])

    def test_external_runtime_special_inspection_exposes_channels_without_raw_events(self):
        from tests.test_analysis_flow import fcs_bytes
        # Select a real executable, forcing the dedicated inspection subprocess.
        self.service.analysis_runtime.python = sys.executable
        raw = fcs_bytes([[1, 2], [3, 4], [5, 6]])
        self.endpoint('upload', {'name': 'synthetic-events.fcs', 'data': base64.b64encode(raw).decode()})
        aid = self.service.store.get(self.pid)['assets'][-1]['id']
        inspected = self.endpoint('analysis-inspect', {'asset_id': aid})
        self.assertTrue(inspected['parsed'])
        self.assertEqual({x['name'] for x in inspected['columns']}, {'x', 'y'})
        self.assertNotIn('preview', inspected)
        self.assertNotIn('data', inspected)
        self.assertEqual(list((Path(self.temp.name) / 'analysis-jobs').iterdir()), [])

    def test_pure_json_strict_result_shapes_and_expanded_pixel_guard(self):
        valid = {'method': 'km', 'tables': {'curve': {'columns': ['x'], 'rows': [{'x': 1.0}]}},
                 'statistics': {}, 'notes': [], 'warnings': [], 'software': {'synthetic': '1'}}
        self.assertEqual(json.loads(analysis.checked_result(valid)), valid)
        bad_values = [dict(valid, tables={'curve': []}), dict(valid, tables={'curve': {'columns': ['x', 'x'], 'rows': []}}),
                      dict(valid, tables={'curve': {'columns': ['x'], 'rows': [{'hidden': 1}]}}),
                      dict(valid, statistics={'value': float('nan')}), dict(valid, warnings='text'),
                      dict(valid, statistics=[]), dict(valid, software=[])]
        for bad in bad_values:
            with self.subTest(value=bad), self.assertRaises(ValueError):
                analysis.checked_result(bad)
        from matplotlib.figure import Figure
        expanded = Figure(figsize=(16, 16))
        style = {'width': 16, 'height': 2.5, 'dpi': 600, 'font_size': 9, 'palette': 'muted'}
        # Requested canvas passes the original 24M cap; dynamic forest height
        # would create >90M pixels. Refuse before allocating the raster buffer.
        self.assertLess(style['width'] * style['height'] * style['dpi'] ** 2, 24_000_000)
        with patch('app.analysis.draw', return_value=expanded), self.assertRaisesRegex(ValueError, '像素'):
            analysis.render(valid, self.spec['analysis'], style)

    def test_startup_removes_only_owned_orphan_analysis_job_folders(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Path(root) / 'analysis-jobs'
            orphan = jobs / uuid.uuid4().hex
            orphan.mkdir(parents=True)
            (orphan / 'raw-private.csv').write_bytes(b'unit,value\nsynthetic,1')
            keep = jobs / 'unrelated-folder'
            keep.mkdir()
            service = Service(Store(root))
            try:
                self.assertFalse(orphan.exists())
                self.assertTrue(keep.is_dir())
            finally:
                service.close_plotting(); service.assistant.close(); service.backups.close()


if __name__ == '__main__':
    unittest.main()
