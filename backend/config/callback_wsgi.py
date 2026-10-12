"""Dedicated small callback surface; never routes ordinary application requests.

Run behind a body-bounded HTTP server (Waitress: 2 MiB, 2 threads, 32 connections).
The application guard also covers direct WSGI use and rejects unrelated routes.
"""
import io
import os
import re

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
_django_application = get_wsgi_application()
_limit = 2 * 1024 * 1024
_path = re.compile(r'/api/v1/payments/wechat/notify/[A-Za-z0-9_-]{1,64}\Z')


def _response(start_response, status, body):
    start_response(status, [('Content-Type', 'application/json'), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store')])
    return [body]


def application(environ, start_response):
    if environ.get('REQUEST_METHOD') == 'GET' and environ.get('PATH_INFO') == '/callback-health':
        return _response(start_response, '200 OK', b'{"status":"ok"}')
    if environ.get('REQUEST_METHOD') != 'POST' or not _path.fullmatch(environ.get('PATH_INFO', '')):
        return _response(start_response, '404 Not Found', b'{"code":"NOT_FOUND"}')
    try:
        length = int(environ.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        return _response(start_response, '400 Bad Request', b'{"code":"FAIL"}')
    if length < 0 or length > _limit:
        return _response(start_response, '413 Content Too Large', b'{"code":"FAIL"}')
    body = environ['wsgi.input'].read(length if length else _limit + 1)
    if len(body) > _limit:
        return _response(start_response, '413 Content Too Large', b'{"code":"FAIL"}')
    environ['wsgi.input'], environ['CONTENT_LENGTH'] = io.BytesIO(body), str(len(body))
    return _django_application(environ, start_response)
