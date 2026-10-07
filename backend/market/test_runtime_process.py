"""Subprocess fixtures for runtime acceptance; never a production worker option.

Only Django's explicitly named test databases are accepted. Context and ephemeral
keys are supplied by test_runtime_acceptance, never by the deployed application.
"""
import argparse
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('context', type=Path)
    parser.add_argument('mode', choices=('callback', 'worker', 'claim-and-pause'))
    args = parser.parse_args()
    context = json.loads(args.context.read_text(encoding='utf-8'))
    database = context['database']
    if database['ENGINE'] != 'django.db.backends.postgresql' or not database['NAME'].startswith('test_'):
        raise RuntimeError('Runtime fixtures require a dedicated PostgreSQL test database')
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    from django.conf import settings
    settings.DATABASES = {'default': database}
    settings.ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
    settings.SECURE_SSL_REDIRECT = False
    settings.WECHAT_PAY_ENABLED = False  # Signature verification is independent of creation enablement.
    settings.WECHAT_PAY_CONFIG_FILE = context['accounts']
    settings.WECHAT_PAY_PUBLIC_ORIGIN = 'https://runtime-acceptance.example.com'
    settings.MEDIA_ROOT = str(args.context.parent / 'media')
    # There is no outbound payment transport in this acceptance. Verify/decrypt
    # and the inbox worker must work without any gateway API request.
    from urllib.request import OpenerDirector
    def forbidden_transport(*unused_args, **unused_kwargs):
        raise AssertionError('Outbound HTTP is forbidden in runtime acceptance subprocesses')
    OpenerDirector.open = forbidden_transport
    import django
    django.setup()
    if args.mode == 'callback':
        from waitress import serve
        from config.callback_wsgi import application
        serve(application, host='127.0.0.1', port=context['port'], threads=2,
              connection_limit=32, channel_timeout=30, max_request_body_size=2097152)
    elif args.mode == 'worker':
        from django.core.management import call_command
        call_command('process_payment_notifications', loop=True, interval=0.1, limit=100)
    else:
        from market.notification_inbox import claim_notification
        row = claim_notification()
        if row is None:
            raise RuntimeError('Expected durable work to claim')
        Path(context['claim_marker']).write_text(json.dumps({
            'id': str(row.pk), 'lease_until': row.lease_until.isoformat(),
        }), encoding='utf-8')
        # The parent really terminates this process. Lease timestamps are neither
        # modified nor fast-forwarded. This pause exists only in this test file.
        while True:
            time.sleep(0.1)


if __name__ == '__main__':
    main()
