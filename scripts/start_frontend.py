"""Start the local preview independently of the launching terminal on Windows."""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', required=True)
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    frontend = root / 'frontend'
    runtime = root / '.runtime'
    runtime.mkdir(exist_ok=True)
    node = Path(args.node).resolve(strict=True)
    env = {**os.environ, 'CI': 'true'}
    options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    with (runtime / 'frontend.log').open('ab') as output, (runtime / 'frontend.error.log').open('ab') as errors:
        process = subprocess.Popen(
            [str(node), str(frontend / 'scripts' / 'serve-dev.mjs'), args.host],
            cwd=frontend, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
            env=env, close_fds=True, **options,
        )
    print(process.pid)


if __name__ == '__main__':
    main()
