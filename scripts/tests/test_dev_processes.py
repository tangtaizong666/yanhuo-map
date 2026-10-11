"""Partial launch recovery and PID reuse protection, without starting services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

SHELL = shutil.which('pwsh') or shutil.which('powershell')
ROOT = Path(__file__).resolve().parents[2]
PROCESS_OPTIONS = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(SHELL, 'PowerShell is unavailable')
class DevProcessRegistryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='yanhuo process test ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / '.runtime').mkdir()

    def run_script(self, body):
        script = "$ErrorActionPreference = 'Stop'\n"
        script += '. ' + ps_quote(ROOT / 'scripts' / 'dev-processes.ps1') + '\n'
        script += '$testRoot = ' + ps_quote(self.root) + '\n' + body
        result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=30, **PROCESS_OPTIONS)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_later_launch_failure_preserves_each_started_process_and_lan_url(self):
        self.run_script("""
$registry = New-DevProcessRegistry -Root $testRoot -LanUrl 'http://192.168.1.10:5183'
try {
    Register-DevProcess $registry ([pscustomobject]@{Id=100001; StartTime=[datetime]'2026-01-01T00:00:00Z'})
    Register-DevProcess $registry ([pscustomobject]@{Id=100002; StartTime=[datetime]'2026-01-01T00:00:01Z'})
    throw 'synthetic frontend launch failure'
} catch {
    if ($_.Exception.Message -ne 'synthetic frontend launch failure') { throw }
}
Write-Output 'preserved'
""")
        state = json.loads((self.root / '.runtime/processes.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(state['root'], str(self.root))
        self.assertEqual(state['lan_url'], 'http://192.168.1.10:5183')
        self.assertEqual([row['id'] for row in state['processes']], [100001, 100002])

    def seed(self, root=None):
        (self.root / '.runtime/processes.json').write_text(json.dumps({
            'root': str(root or self.root),
            'processes': [{'id': 100001, 'started': '2026-01-01T00:00:00Z'}],
        }), encoding='utf-8')

    def test_existing_worker_blocks_duplicate_start_without_overwriting_record(self):
        self.seed()
        before = (self.root / '.runtime/processes.json').read_bytes()
        output = self.run_script("""
function Get-Process { param($Id, $ErrorAction) return [pscustomobject]@{Id=$Id; StartTime=[datetime]'2026-01-01T00:00:00Z'} }
try { New-DevProcessRegistry -Root $testRoot | Out-Null; throw 'not blocked' }
catch { if ($_.Exception.Message -notlike 'Previously recorded*') { throw }; Write-Output 'blocked' }
""")
        self.assertEqual(output, 'blocked')
        self.assertEqual((self.root / '.runtime/processes.json').read_bytes(), before)

    def test_reused_pid_is_not_treated_as_owned_or_stopped(self):
        self.seed()
        output = self.run_script("""
function Get-Process { param($Id, $ErrorAction) return [pscustomobject]@{Id=$Id; StartTime=[datetime]'2026-01-02T00:00:00Z'} }
function Stop-Process { throw 'Must never stop a process when opening a registry.' }
$registry = New-DevProcessRegistry -Root $testRoot
Write-Output $registry.processes.Count
""")
        self.assertEqual(output, '0')

    def test_foreign_workspace_record_is_rejected(self):
        self.seed(self.root / 'another-workspace')
        output = self.run_script("""
try { New-DevProcessRegistry -Root $testRoot | Out-Null; throw 'not blocked' }
catch { if ($_.Exception.Message -ne 'Process record belongs to another workspace.') { throw }; Write-Output 'blocked' }
""")
        self.assertEqual(output, 'blocked')

    def hold_lock_in_another_process(self, release_after_failure=False):
        ready = self.root / 'holder-ready'
        script = "$ErrorActionPreference = 'Stop'\n"
        script += '. ' + ps_quote(ROOT / 'scripts' / 'dev-processes.ps1') + '\n'
        script += '$testRoot = ' + ps_quote(self.root) + '\n'
        script += "$lock = Enter-DevProcessLock -Root $testRoot\n"
        if release_after_failure:
            script += "try { try { throw 'Synthetic launch failure' } finally { $lock.Dispose() } }\n"
            script += "catch { if ($_.Exception.Message -ne 'Synthetic launch failure') { throw } }\n"
        else:
            script += "try {\n$registry = New-DevProcessRegistry -Root $testRoot\n"
            script += "Register-DevProcess $registry ([pscustomobject]@{Id=100001; StartTime=[datetime]'2026-01-01T00:00:00Z'})\n"
        script += '[IO.File]::WriteAllText(' + ps_quote(ready) + ", 'ready')\n"
        script += "[Console]::In.ReadLine() | Out-Null\n"
        if not release_after_failure:
            script += "} finally { $lock.Dispose() }\n"
        process = subprocess.Popen([SHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, **PROCESS_OPTIONS)

        def release():
            if process.poll() is None:
                try:
                    process.communicate('\n', timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()  # Only the synthetic child created by this test.
                    process.communicate(timeout=10)
            else:
                process.communicate(timeout=10)

        self.addCleanup(release)
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail('Synthetic lock holder exited: ' + process.communicate()[1])
            time.sleep(0.05)
        self.assertTrue(ready.exists(), 'Synthetic lock holder did not become ready')
        return process

    def test_independent_startup_cannot_overwrite_same_workspace_registry(self):
        holder = self.hold_lock_in_another_process()
        before = (self.root / '.runtime/processes.json').read_bytes()
        output = self.run_script("""
try {
    $lock = Enter-DevProcessLock -Root $testRoot
    try { throw 'Second startup acquired the lock' } finally { $lock.Dispose() }
} catch {
    if ($_.Exception.Message -notlike 'Another startup or stop operation*') { throw }
    Write-Output 'blocked'
}
""")
        self.assertEqual(output, 'blocked')
        self.assertIsNone(holder.poll())
        self.assertEqual((self.root / '.runtime/processes.json').read_bytes(), before)

    def test_independent_workspace_can_start_while_first_holds_lock(self):
        holder = self.hold_lock_in_another_process()
        output = self.run_script("""
$otherRoot = Join-Path $testRoot 'other workspace'
$lock = Enter-DevProcessLock -Root $otherRoot
try {
    $registry = New-DevProcessRegistry -Root $otherRoot
    Register-DevProcess $registry ([pscustomobject]@{Id=100002; StartTime=[datetime]'2026-01-01T00:00:01Z'})
    Write-Output 'independent'
} finally { $lock.Dispose() }
""")
        self.assertEqual(output, 'independent')
        self.assertIsNone(holder.poll())
        first = json.loads((self.root / '.runtime/processes.json').read_text(encoding='utf-8-sig'))
        other = json.loads((self.root / 'other workspace/.runtime/processes.json').read_text(encoding='utf-8-sig'))
        self.assertEqual([row['id'] for row in first['processes']], [100001])
        self.assertEqual([row['id'] for row in other['processes']], [100002])

    def test_exception_releases_lock_for_another_process(self):
        holder = self.hold_lock_in_another_process(release_after_failure=True)
        self.assertEqual(self.run_script("""
$lock = Enter-DevProcessLock -Root $testRoot
try { Write-Output 'released' } finally { $lock.Dispose() }
"""), 'released')
        self.assertIsNone(holder.poll(), 'The lock must release before its process exits')

    def test_actual_start_and_stop_scripts_reject_a_competing_operation_before_work(self):
        # Execute copies in the synthetic workspace. No business server is started
        # or stopped; a competing script must fail before any network/process work.
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('start-dev.ps1', 'stop-dev.ps1', 'dev-processes.ps1'):
            shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
        holder = self.hold_lock_in_another_process()
        for name in ('start-dev.ps1', 'stop-dev.ps1'):
            with self.subTest(script=name):
                output = self.run_script("""
function Get-NetTCPConnection { throw 'Port lookup must run after acquiring the lock' }
function Get-Process { throw 'Process lookup must run after acquiring the lock' }
function Start-Process { throw 'Never start services in this test' }
function Stop-Process { throw 'Never stop services in this test' }
try {
    & SCRIPT_PATH
    throw 'Concurrent operation was allowed'
} catch {
    if ($_.Exception.Message -notlike 'Another startup or stop operation*') { throw }
    Write-Output 'blocked'
}
""".replace('SCRIPT_PATH', ps_quote(scripts / name)))
                self.assertEqual(output, 'blocked')
        self.assertIsNone(holder.poll())


if __name__ == '__main__':
    unittest.main()
