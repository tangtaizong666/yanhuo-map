import sqlite3
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Consistent local SQLite backup with read-only integrity/foreign-key verification. PostgreSQL must use pg_dump.'
    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        if settings.PRODUCTION or db['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('This command only backs up the local SQLite demonstration database.')
        directory = settings.BASE_DIR / 'backups'
        directory.mkdir(exist_ok=True)
        destination = directory / ('demo-' + timezone.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
        with sqlite3.connect(db['NAME']) as source, sqlite3.connect(destination) as target:
            source.backup(target)
        with sqlite3.connect(f'file:{destination.as_posix()}?mode=ro', uri=True) as restored:
            result = restored.execute('PRAGMA integrity_check').fetchone()[0]
            failures = restored.execute('PRAGMA foreign_key_check').fetchall()
            count = restored.execute('SELECT COUNT(*) FROM market_order').fetchone()[0]
        if result != 'ok' or failures: raise CommandError('Backup integrity verification failed.')
        self.stdout.write(self.style.SUCCESS(f'Backup verified: {destination}; readable orders={count}; integrity=ok; foreign_keys=ok'))
