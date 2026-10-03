"""Real loopback HTTP checks against our own disposable Waitress process.

No Django/database, production ports, merchant data, or external payment calls.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest


LIMIT = 6291456
SERVER_CODE = r'''
import json, sys
from pathlib import Path
from waitress import create_server
calls, ready = Path(sys.argv[1]), Path(sys.argv[2])
def app(environ, start_response):
    body = environ['wsgi.input'].read()
    with calls.open('a', encoding='utf-8') as output:
        output.write(json.dumps({'path': environ['PATH_INFO'], 'bytes': len(body)}) + '\n')
    response = str(len(body)).encode()
    start_response('200 OK', [('Content-Type', 'text/plain'), ('Content-Length', str(len(response)))])
    return [response]
server = create_server(app, host='127.0.0.1', port=0, threads=1,
    max_request_body_size=6291456, channel_timeout=5)
ready.write_text(str(server.effective_port), encoding='ascii')
server.run()
'''


class WaitressBodyLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='yanhuo-body-limit-')
        root = Path(cls.directory.name)
        cls.calls, cls.ready = root / 'calls.jsonl', root / 'ready.txt'
        cls.log = (root / 'server.log').open('wb')
        # Windows venv launchers may spawn a child; invoke the actual interpreter
        # so this Popen owns exactly the server process that teardown terminates.
        environment = {**os.environ, 'PYTHONPATH': os.pathsep.join(os.path.abspath(path) for path in sys.path if path)}
        cls.process = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-B', '-c', SERVER_CODE, str(cls.calls), str(cls.ready)],
            stdout=cls.log, stderr=subprocess.STDOUT,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 10
        while not cls.ready.exists():
            if cls.process.poll() is not None or time.monotonic() >= deadline:
                cls.tearDownClass()
                raise RuntimeError('Temporary Waitress did not start.')
            time.sleep(0.02)
        cls.port = int(cls.ready.read_text(encoding='ascii'))

    @classmethod
    def tearDownClass(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        cls.log.close()
        cls.directory.cleanup()

    def request(self, path, headers, chunks):
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as peer:
            request = f'POST {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n{headers}\r\n'
            peer.sendall(request.encode('ascii'))
            try:
                for chunk in chunks:
                    peer.sendall(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The server may reject without receiving the remaining body.
            response = bytearray()
            while True:
                try:
                    data = peer.recv(65536)
                except ConnectionResetError:
                    break
                if not data:
                    break
                response.extend(data)
        self.assertTrue(response.startswith(b'HTTP/1.1 '), response[:100])
        return int(response.split(b' ', 2)[1]), bytes(response)

    def app_calls(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text(encoding='utf-8').splitlines()]

    def test_oversized_content_length_is_rejected_before_body_or_app(self):
        status, _ = self.request('/oversized-length', f'Content-Length: {LIMIT + 1}\r\n', [])
        self.assertEqual(status, 413)
        self.assertFalse(any(row['path'] == '/oversized-length' for row in self.app_calls()))

    def test_oversized_chunked_body_is_rejected_before_app(self):
        block = b'x' * (1024 * 1024)
        chunks = [b'100000\r\n' + block + b'\r\n'] * 6 + [b'1\r\nx\r\n0\r\n\r\n']
        status, _ = self.request('/oversized-chunked', 'Transfer-Encoding: chunked\r\n', chunks)
        self.assertEqual(status, 413)
        self.assertFalse(any(row['path'] == '/oversized-chunked' for row in self.app_calls()))

    def test_below_limit_content_length_and_small_chunked_requests_reach_app(self):
        # Waitress treats max_request_body_size as an exclusive bound.
        size = LIMIT - 1
        status, response = self.request('/below-limit', f'Content-Length: {size}\r\n',
            [b'x' * (1024 * 1024)] * 5 + [b'x' * (1024 * 1024 - 1)])
        self.assertEqual(status, 200)
        self.assertTrue(response.endswith(str(size).encode()))
        status, response = self.request('/small-chunked', 'Transfer-Encoding: chunked\r\n', [b'3\r\nabc\r\n0\r\n\r\n'])
        self.assertEqual(status, 200)
        self.assertTrue(response.endswith(b'3'))
        rows = {row['path']: row['bytes'] for row in self.app_calls()}
        self.assertEqual(rows['/below-limit'], size)
        self.assertEqual(rows['/small-chunked'], 3)
