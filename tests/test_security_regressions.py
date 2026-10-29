"""Exercise credential redirects, HTTP framing, and completing jobs over local sockets."""
from contextlib import closing
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
import sys
import tempfile
import threading
import unittest

from app.backups import MAX_BACKUP
from app.providers import ProviderError, api_complete
from app.server import LocalServer
from app.service import Service
from app.storage import Store


class APIRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.followed = []

        class MockAPI(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def destination(self):
                cls.followed.append(dict(self.headers))
                body = json.dumps({
                    'choices': [{'message': {'content': 'redirected'}}],
                    'content': [{'type': 'text', 'text': 'redirected'}],
                }).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.destination()

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/destination':
                    return self.destination()
                self.send_response(cls.redirect_code)
                self.send_header('Location', cls.destination_url)
                self.send_header('Content-Length', '0')
                self.end_headers()

        cls.servers = [ThreadingHTTPServer(('127.0.0.1', 0), MockAPI) for _ in range(2)]
        cls.threads = []
        for server in cls.servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server, thread in zip(cls.servers, cls.threads):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_redirects_never_send_credentials_to_another_request(self):
        origin, target = self.servers
        for provider in ('compatible', 'anthropic'):
            for code in (301, 302, 303, 307, 308):
                for destination in (
                    f'http://localhost:{target.server_port}/destination',
                    f'http://127.0.0.1:{origin.server_port}/destination',
                ):
                    with self.subTest(provider=provider, code=code, destination=destination):
                        type(self).redirect_code = code
                        type(self).destination_url = destination
                        self.followed.clear()
                        settings = {'provider': provider, 'api_model': 'mock',
                                    'base_url': f'http://127.0.0.1:{origin.server_port}/v1'}
                        with self.assertRaisesRegex(ProviderError, '重定向') as error:
                            api_complete(settings, 'synthetic-not-a-real-key', 'system', 'prompt',
                                         lambda *args: None, threading.Event())
                        self.assertEqual(self.followed, [])
                        self.assertNotIn('synthetic-not-a-real-key', str(error.exception))
                        self.assertNotIn(destination, str(error.exception))


class RecordingServer(LocalServer):
    def handle_error(self, request, client_address):
        self.handler_errors.append(sys.exc_info()[0])


class CompletingJobs(dict):
    """Finish a job at the boundary between its lookup and its use."""
    def __contains__(self, key):
        present = super().__contains__(key)
        self.pop(key, None)
        return present

    def get(self, key, default=None):
        job = super().get(key, default)
        self.pop(key, None)
        return job


class LocalHTTPRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(temporary.name)
        self.service = Service(self.store)
        self.pid = self.store.create({'title': 'HTTP regression fixture'})['id']
        self.server = RecordingServer(self.service)
        self.server.handler_errors = []
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.host = f'127.0.0.1:{self.server.server_port}'

    def raw_exchange(self, packet):
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as connection:
            connection.sendall(packet.encode())
            parts = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    return b''.join(parts)
                parts.append(chunk)

    def test_rejected_posts_close_before_parsing_unread_body(self):
        embedded = (f'GET /api/projects HTTP/1.1\r\nHost: {self.host}\r\n'
                    f'X-Research-Token: {self.server.token}\r\nConnection: close\r\n\r\n')
        for name, path, length, token, status in (
            ('unauthorized', '/api/project', len(embedded), '', 403),
            ('oversize JSON', '/api/project', 30 * 1024 * 1024 + 1, self.server.token, 413),
            ('oversize backup', '/api/backups/import', MAX_BACKUP + 1, self.server.token, 413),
            ('unconfirmed backup', '/api/backups/import', len(embedded), self.server.token, 400),
            ('negative length', '/api/project', -1, self.server.token, 413),
            ('invalid length', '/api/project', 'invalid', self.server.token, 400),
        ):
            with self.subTest(name=name):
                packet = (f'POST {path} HTTP/1.1\r\nHost: {self.host}\r\n'
                          f'X-Research-Token: {token}\r\nContent-Length: {length}\r\n\r\n{embedded}')
                response = self.raw_exchange(packet)
                self.assertEqual(re.findall(rb'HTTP/1\.1 (\d{3})', response), [str(status).encode()])
                self.assertIn(b'Connection: close\r\n', response.split(b'\r\n\r\n', 1)[0])
        self.assertEqual(self.server.handler_errors, [])

    def test_unsupported_and_ambiguous_body_framing_cannot_create_projects(self):
        body = '{"title":"must not be created"}'
        for framing in (
            f'Transfer-Encoding: chunked\r\nContent-Length: {len(body)}\r\n',
            f'Content-Length: {len(body)}\r\nContent-Length: {len(body) + 1}\r\n',
        ):
            with self.subTest(framing=framing):
                packet = (f'POST /api/project HTTP/1.1\r\nHost: {self.host}\r\n'
                          f'X-Research-Token: {self.server.token}\r\nConnection: close\r\n'
                          f'{framing}\r\n{body}')
                response = self.raw_exchange(packet)
                self.assertEqual(re.findall(rb'HTTP/1\.1 (\d{3})', response), [b'400'])
                self.assertEqual(len(self.store.list()), 1)

    def test_successful_posts_still_reuse_the_connection(self):
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)) as connection:
            headers = {'X-Research-Token': self.server.token, 'Content-Type': 'application/json'}
            connection.request('POST', f'/api/project/{self.pid}/info', '{}', headers)
            first = connection.getresponse()
            self.assertEqual(first.status, 200)
            self.assertNotEqual(first.getheader('Connection'), 'close')
            first.read()
            original_socket = connection.sock
            connection.request('POST', f'/api/project/{self.pid}/info', '{}', headers)
            second = connection.getresponse()
            self.assertEqual(second.status, 200)
            second.read()
            self.assertIs(connection.sock, original_socket)

    def install_completing_job(self):
        project = self.store.get(self.pid)
        project['runs'] = [{'id': 'finishing-job', 'stage': 'research', 'status': 'running'}]
        self.store.put(project)
        job = {'cancel': threading.Event(), 'progress': 'finishing', 'partial': 'saved preview'}
        self.service.jobs = CompletingJobs({'finishing-job': job})
        return job

    def test_project_get_responds_when_job_finishes_during_lookup(self):
        self.install_completing_job()
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)) as connection:
            connection.request('GET', f'/api/project/{self.pid}',
                               headers={'X-Research-Token': self.server.token})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            project = json.load(response)
        self.assertEqual(project['runs'][0]['progress'], 'finishing')
        self.assertEqual(project['runs'][0]['partial'], 'saved preview')
        self.assertEqual(self.service.jobs, {})
        self.assertEqual(self.server.handler_errors, [])

    def test_stop_uses_retained_job_when_completion_removes_it(self):
        job = self.install_completing_job()
        self.assertEqual(self.service.stop(self.pid), {'ok': True})
        self.assertTrue(job['cancel'].is_set())
        self.assertEqual(self.service.jobs, {})

    def test_completed_job_can_already_be_absent(self):
        self.install_completing_job()
        self.service.jobs.clear()
        self.assertEqual(self.service.public_project(self.pid)['runs'][0]['id'], 'finishing-job')
        self.assertEqual(self.service.stop(self.pid), {'ok': True})


if __name__ == '__main__':
    unittest.main()
