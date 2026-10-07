"""Run isolated monitor acceptance and save a machine-readable evidence report."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report_path = Path(options.report).resolve()
    if report_path.exists():
        parser.error('report already exists; choose a new path to preserve previous evidence')
    git = ['git', '-c', 'safe.directory=' + str(root)]
    revision = subprocess.run([*git, 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run([*git, 'status', '--porcelain'], cwd=root, capture_output=True, text=True, check=True).stdout != ''
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(root / 'scripts' / 'tests'), pattern='test_operations_monitor.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        'version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(), 'revision': revision, 'working_tree_dirty': dirty,
        'status': 'passed' if result.wasSuccessful() else 'failed', 'tests': result.testsRun,
        'duration_seconds': round(time.monotonic() - started, 3),
        'failures': [str(case) for case, detail in result.failures + result.errors],
        'database': 'none; business-state collector responses are synthetic in this host-tool suite',
        'https': {'receiver': 'isolated loopback ThreadingHTTPServer', 'certificate': 'ephemeral RSA-2048 certificate',
                  'trust': 'explicit client CA only; system trust unchanged', 'separate_process_per_poll': True,
                  'verified': ['HTTP 503 delivery failure', 'persistent event ID across process restart',
                               'retry suppression until due', 'HTTP 204 delivery', 'single recovery',
                               'untrusted TLS rejected', 'redirect not followed']},
        'limits': ['Retry intervals use a test-only logical clock.',
                   'Docker/business collection is fixture-driven here; deployment acceptance covers real containers.',
                   'No real operator webhook or offsite backup destination is exercised.'],
        'evidence_path': str(report_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves earlier evidence even if another run raced us.
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': report['status'], 'tests': result.testsRun, 'report': str(report_path)}))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
