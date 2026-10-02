"""Real PostgreSQL request races; no live accounts or live database are used."""
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from .models import AccountRecovery, AuditLog
from .views import check_password


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL row-lock waits')
@override_settings(AUTH_TRUST_PROXY_CLIENT_IP=False)
class RecoveryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.original_password = 'OriginalRiver!2026'
        self.recovered_password = 'RecoveredSunlight!2027'
        self.competing_password = 'CompetingMountain!2028'
        self.user = User.objects.create_user('concurrent_recovery', password=self.original_password)
        self.old_session = APIClient()
        self.old_session.force_login(self.user)
        response = self.old_session.post('/api/v1/auth/recovery', {'password': self.original_password}, format='json')
        self.assertEqual(response.status_code, 200)
        self.recovery_code = response.data['recovery_code']

    def reset_request(self, password):
        return APIClient().post('/api/v1/auth/recovery/reset', {
            'username': self.user.username, 'recovery_code': self.recovery_code,
            'new_password': password}, format='json')

    def race_after_recovery_locks_user(self, second_request):
        """Pause the first real handler after locking; prove the second waits in PG.

        Password validation remains real. The hook only delays the first handler,
        so the test does not replace either row lock or the password/code checks.
        """
        first_locked, release_first, second_attempted = Event(), Event(), Event()
        second_pid = Queue()

        def hold_after_validation(password, user):
            check_password(password, user)
            first_locked.set()
            if not release_first.wait(timeout=20):
                raise RuntimeError('Timed out waiting to release the isolated recovery transaction')

        def first():
            close_old_connections()
            try: return self.reset_request(self.recovered_password)
            finally: connections.close_all()

        def second():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    second_pid.put(cursor.fetchone()[0])

                def observe(execute, sql, params, many, context):
                    if 'FOR UPDATE' in str(sql) and 'auth_user' in str(sql):
                        second_attempted.set()
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(observe):
                    return second_request()
            finally: connections.close_all()

        with patch('market.recovery.check_password', side_effect=hold_after_validation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first_future = pool.submit(first)
                try:
                    self.assertTrue(first_locked.wait(timeout=10), 'Recovery must hold its actual User row lock')
                    second_future = pool.submit(second)
                    pid = second_pid.get(timeout=10)
                    self.assertTrue(second_attempted.wait(timeout=10), 'Competing handler must request a fresh User row lock')
                    blocked = False
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s', [pid])
                            state = cursor.fetchone()
                        if state and state[0] == 'Lock':
                            blocked = True
                            break
                        sleep(0.02)
                    self.assertTrue(blocked, 'PostgreSQL must observe both requests concurrently, with the second waiting on a lock')
                finally:
                    release_first.set()
                return first_future.result(timeout=20), second_future.result(timeout=20)

    def test_same_code_two_concurrent_resets_only_one_password_wins(self):
        first, second = self.race_after_recovery_locks_user(lambda: self.reset_request(self.competing_password))
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual((second.status_code, second.data['code']), (400, 'invalid_recovery'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.recovered_password))
        self.assertFalse(self.user.check_password(self.competing_password))
        record = AccountRecovery.objects.get(user=self.user)
        self.assertEqual((record.code_hash, record.password_stamp), ('', ''))
        self.assertIsNotNone(record.used_at)
        self.assertEqual(AuditLog.objects.filter(action='account_password_recovered').count(), 1)

    def test_stale_authenticated_password_change_waits_and_cannot_overwrite_recovery(self):
        def old_session_change():
            return self.old_session.post('/api/v1/auth/password', {
                'old_password': self.original_password, 'new_password': self.competing_password}, format='json')

        first, second = self.race_after_recovery_locks_user(old_session_change)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual((second.status_code, second.data['code']), (400, 'invalid_password'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.recovered_password))
        self.assertFalse(self.user.check_password(self.competing_password))
        self.assertIsNone(self.old_session.get('/api/v1/auth/me').json())
        self.assertEqual(AuditLog.objects.filter(action='account_password_recovered').count(), 1)
