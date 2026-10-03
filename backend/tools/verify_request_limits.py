"""Optional Caddy network verification with a supplied official binary.

Uses disposable localhost ports and a pure WSGI fixture, never a Django DB.
Example: python tools/verify_request_limits.py --caddy C:/Temp/caddy.exe
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from market import test_request_limits as network


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--caddy', required=True, type=Path)
    parser.add_argument('--report', type=Path, default=BACKEND.parent / '.runtime' / 'request-limits-verification.json')
    options = parser.parse_args()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    environment = {**os.environ, 'PUBLIC_DOMAIN': 'pilot-verification.invalid'}
    version = subprocess.check_output([str(options.caddy), 'version'], text=True, creationflags=flags).strip()
    source = BACKEND.parent / 'deploy' / 'Caddyfile'
    with tempfile.TemporaryDirectory(prefix='yanhuo-caddy-adapt-') as adapt_directory:
        result = subprocess.run([str(options.caddy), 'adapt', '--config', str(source), '--adapter', 'caddyfile', '--validate'],
            env={**environment, 'XDG_CONFIG_HOME': adapt_directory, 'XDG_DATA_HOME': adapt_directory},
            capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=flags)
    if result.returncode:
        raise RuntimeError('Caddy validation failed: ' + result.stderr)
    adapted = json.loads(result.stdout)
    assert adapted.get('apps', {}).get('http'), 'Expected adapted HTTP app.'
    handlers = list(dictionaries(adapted))
    body_handlers = [item for item in handlers if item.get('handler') == 'request_body']
    proxy_handlers = [item for item in handlers if item.get('handler') == 'reverse_proxy']
    assert body_handlers and all(item.get('max_size') == network.LIMIT for item in body_handlers)
    assert proxy_handlers and all(item.get('headers', {}).get('request', {}).get('set', {}).get('X-Real-Ip')
        == ['{http.request.remote.host}'] for item in proxy_handlers)
    report = {'caddy_version': version, 'platform': sys.platform, 'deployment_config_validated': True,
        'request_limit_bytes': network.LIMIT, 'checks': []}
    # The upstream deliberately permits 18 MiB so a proxy 413 proves the edge
    # rejected the request, rather than the downstream Waitress limit doing it.
    network.SERVER_CODE = network.SERVER_CODE.replace('max_request_body_size=6291456', 'max_request_body_size=18874368')
    network.SERVER_CODE = network.SERVER_CODE.replace("'bytes': len(body)",
        "'bytes': len(body), 'real_ip': environ.get('HTTP_X_REAL_IP'), 'forwarded_for': environ.get('HTTP_X_FORWARDED_FOR')")
    fixture = network.WaitressBodyLimitTests
    fixture.setUpClass()
    try:
        client = fixture(methodName='runTest')
        upstream = client.port
        block = b'x' * (1024 * 1024)
        status, _ = client.request('/upstream-control', f'Content-Length: {network.LIMIT + 1}\r\n', [block] * 6 + [b'x'])
        assert status == 200, f'Upstream control returned {status}'
        report['checks'].append({'case': 'upstream-control', 'status': status,
            'received_bytes': network.LIMIT + 1, 'application_called': True})
        with tempfile.TemporaryDirectory(prefix='yanhuo-caddy-proxy-') as directory:
            root = Path(directory)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            config = root / 'Caddyfile'
            config.write_text('{\n admin off\n auto_https off\n}\n'
                f'http://:{port} {{\n bind 127.0.0.1\n request_body {{\n  max_size 6MiB\n }}\n'
                f' reverse_proxy 127.0.0.1:{upstream} {{\n  header_up X-Real-IP {{remote_host}}\n }}\n}}\n', encoding='utf-8')
            with (root / 'caddy.log').open('wb') as output:
                process = subprocess.Popen([str(options.caddy), 'run', '--config', str(config), '--adapter', 'caddyfile'],
                    stdout=output, stderr=subprocess.STDOUT, creationflags=flags,
                    env={**environment, 'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data')})
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Temporary Caddy proxy did not start.')
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                                break
                        except OSError:
                            time.sleep(0.02)
                    client.port = port
                    cases = [
                        ('edge-large-length', f'Content-Length: {network.LIMIT + 1}\r\n', [block] * 6 + [b'x']),
                        ('edge-large-chunked', 'Transfer-Encoding: chunked\r\n',
                            [b'100000\r\n' + block + b'\r\n'] * 6 + [b'1\r\nx\r\n0\r\n\r\n']),
                    ]
                    for name, headers, chunks in cases:
                        try:
                            status, _ = client.request('/' + name, headers, chunks)
                        except AssertionError as exc:
                            raise AssertionError(f'{name}: {exc}; Caddy log: {(root / "caddy.log").read_text(encoding="utf-8")}') from exc
                        assert status == 413, f'{name} returned {status}'
                        assert not any(row['path'] == '/' + name for row in client.app_calls()), 'Rejected body reached application.'
                        report['checks'].append({'case': name, 'status': status, 'application_called': False})
                    status, _ = client.request('/header-check',
                        'Content-Length: 3\r\nX-Real-IP: 203.0.113.99\r\nX-Forwarded-For: 203.0.113.88\r\n', [b'abc'])
                    assert status == 200
                    row = next(row for row in client.app_calls() if row['path'] == '/header-check')
                    assert row['real_ip'] == '127.0.0.1'
                    assert '203.0.113.88' not in (row['forwarded_for'] or '')
                    report['checks'].append({'case': 'untrusted-client-headers', 'status': status,
                        'real_ip': row['real_ip'], 'forwarded_for': row['forwarded_for']})
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=10)
    finally:
        fixture.tearDownClass()
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
