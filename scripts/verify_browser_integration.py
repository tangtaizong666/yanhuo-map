"""Run real browser/API flows on disposable SQLite, media and loopback servers.

Usage: backend/.venv/Scripts/python.exe scripts/verify_browser_integration.py
No working database, configured gateway, or existing server is used.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
BACKEND_PORT, FRONTEND_PORT = 8097, 5195
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def require_free_port(port):
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))


def stop_owned(process, graceful=False, tree=False):
    if process.poll() is not None:
        return
    if graceful and process.stdin:
        try:
            process.stdin.write("stop\n")
            process.stdin.flush()
            process.wait(timeout=10)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if tree and os.name == "nt":
        # A timed-out Playwright runner owns its browser children too. Restrict
        # cleanup to this Popen PID's tree; never stop by executable name or port.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=FLAGS, check=False)
        process.wait(timeout=10)
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def wait_ready(process, url):
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f"Owned server exited while waiting for {url}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Owned server did not become ready: {url}")


def main():
    for port in (BACKEND_PORT, FRONTEND_PORT):
        require_free_port(port)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required.")
    artifacts = ROOT / ".runtime" / ("browser-integration-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    artifacts.mkdir(parents=True, exist_ok=False)
    print(f"Artifacts: {artifacts}", flush=True)
    with tempfile.TemporaryDirectory(prefix="yanhuo-browser-integration-") as directory:
        temporary = Path(directory).resolve()
        env = os.environ.copy()
        env.update(DJANGO_ENV="development", DEMO_MODE="true", SERVICES_SIMULATION_ENABLED="true",
            DATABASE_URL="", WECHAT_PAY_ENABLED="false", WECHAT_PAY_CONFIG_FILE="", WECHAT_PAY_PUBLIC_ORIGIN="",
            AMAP_KEY="", AMAP_SECURITY_CODE="", PUBLIC_BASE_URL="", PYTHONIOENCODING="utf-8",
            DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost", CSRF_TRUSTED_ORIGINS=f"http://127.0.0.1:{FRONTEND_PORT}",
            DJANGO_SETTINGS_MODULE="pilot_integration_settings", E2E_ISOLATED="1",
            E2E_BASE_URL=f"http://127.0.0.1:{FRONTEND_PORT}")
        env.pop("E2E_PRODUCTION", None)
        env["PYTHONPATH"] = os.pathsep.join([str(temporary), str(BACKEND)])
        settings = temporary / "pilot_integration_settings.py"
        settings.write_text(
            "from config.settings import *\n"
            # Match development transaction locking: deferred SQLite transactions
            # cannot upgrade a read lock while the receiving heartbeat writes.
            f"DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': {str(temporary / 'integration.sqlite3')!r}, 'OPTIONS': {{'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}}}}\n"
            f"MEDIA_ROOT = {str(temporary / 'media')!r}\n"
            "WECHAT_PAY_ENABLED = False\nWECHAT_PAY_CONFIG_FILE = ''\nWECHAT_PAY_PUBLIC_ORIGIN = ''\n"
            "CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}\n",
            encoding="utf-8")
        (artifacts / "isolation.json").write_text(json.dumps({
            "database": str(temporary / "integration.sqlite3"), "media": str(temporary / "media"),
            "backend": f"http://127.0.0.1:{BACKEND_PORT}", "frontend": env["E2E_BASE_URL"],
            "real_wechat_enabled": False, "simulation_enabled": True,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        def run(name, command, cwd, timeout=300):
            print(f"Running {name} ...", flush=True)
            with (artifacts / f"{name}.log").open("w", encoding="utf-8") as output:
                process = subprocess.Popen([str(arg) for arg in command], cwd=cwd, env=env,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=FLAGS)
                try:
                    result = process.wait(timeout=timeout)
                finally:
                    stop_owned(process, tree=True)
            if result:
                raise RuntimeError(f"{name} failed; see {artifacts / (name + '.log')}")
            print(f"PASS {name}", flush=True)

        run("migrate", [sys.executable, "manage.py", "migrate", "--noinput"], BACKEND)
        run("seed-isolated-demo", [sys.executable, "manage.py", "seed_demo"], BACKEND)
        vite_script = temporary / "vite-integration.mjs"
        vite_script.write_text(
            f"import {{createServer}} from {json.dumps((FRONTEND / 'node_modules/vite/dist/node/index.js').as_uri())};\n"
            "process.env.CI = 'true'; // Keep Vite alive if the launching terminal closes stdin.\n"
            "process.on('exit', code => console.log('Integration Vite exit', code));\n"
            f"const server = await createServer({{root: {json.dumps(str(FRONTEND))}, configFile: {json.dumps(str(FRONTEND / 'vite.config.ts'))}, "
            f"server: {{host:'127.0.0.1', port:{FRONTEND_PORT}, strictPort:true, proxy: Object.fromEntries(['/api','/admin','/static','/media'].map(path=>[path,{{target:'http://127.0.0.1:{BACKEND_PORT}',changeOrigin:false}}]))}}}});\n"
            "await server.listen(); server.printUrls();\n"
            "let input = ''; process.stdin.setEncoding('utf8'); process.stdin.resume();\n"
            "process.stdin.on('data', async chunk => {input += chunk; if (input.split(/\\r?\\n/).slice(0, -1).includes('stop')) {await server.close();process.exit(0)}});\n",
            encoding="utf-8")
        with (artifacts / "django-server.log").open("w", encoding="utf-8") as django_log, (artifacts / "vite-server.log").open("w", encoding="utf-8") as vite_log:
            django = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{BACKEND_PORT}", "--noreload"],
                cwd=BACKEND, env=env, stdout=django_log, stderr=subprocess.STDOUT, creationflags=FLAGS)
            vite = None
            try:
                wait_ready(django, f"http://127.0.0.1:{BACKEND_PORT}/api/v1/config")
                vite = subprocess.Popen([node, str(vite_script)], cwd=FRONTEND, env=env, stdin=subprocess.PIPE,
                    text=True, stdout=vite_log, stderr=subprocess.STDOUT, creationflags=FLAGS)
                wait_ready(vite, env["E2E_BASE_URL"] + "/api/v1/config")
                with urllib.request.urlopen(env["E2E_BASE_URL"] + "/api/v1/config") as response:
                    config = json.load(response)
                if not config.get("demo_mode") or not config.get("services_simulation_enabled"):
                    raise RuntimeError("Isolated proxy did not report the required rehearsal configuration.")
                def public_json(path):
                    with urllib.request.urlopen(env["E2E_BASE_URL"] + "/api/v1" + path, timeout=10) as response:
                        return json.load(response)
                discovery = public_json("/stalls")
                if not isinstance(discovery, dict) or not {"results", "next"} <= discovery.keys():
                    raise RuntimeError("Public discovery did not return a bounded page.")
                for stall in discovery["results"]:
                    if len(stall.get("products", [])) > 2 or any(key in stall for key in (
                        "contact_phone", "order_count", "prep_capacity", "prep_active_orders", "receiving_seen_at")):
                        raise RuntimeError("Public summary exposed operating details or an unbounded menu.")
                    for product in stall.get("products", []):
                        if "stock" in product or "stock_version" in product or not {"availability", "max_order_quantity"} <= product.keys():
                            raise RuntimeError("Public product did not use the availability contract.")
                markers = public_json("/stalls/map")
                if not isinstance(markers, dict) or len(markers.get("results", [])) > 200 or any("products" in row for row in markers.get("results", [])):
                    raise RuntimeError("Map projection is unbounded or contains full menus.")
                (artifacts / "public-contract.json").write_text(json.dumps({
                    "status": "passed", "summary_count": len(discovery["results"]),
                    "map_count": len(markers["results"]), "exact_stock_exposed": False,
                }, indent=2), encoding="utf-8")
                run("browser-integration", [node, "node_modules/@playwright/test/cli.js", "test",
                    "e2e/counter-integration.spec.ts", "e2e/simulation-live.spec.ts",
                    "e2e/product-details.spec.ts", "e2e/checkout.spec.ts", "e2e/reorder.spec.ts",
                    "e2e/mobile-usability.spec.ts", "e2e/identity-cookie-integration.spec.ts",
                    "--reporter=list", f"--output={artifacts / 'browser-results'}"], FRONTEND, timeout=540)
            finally:
                if vite:
                    stop_owned(vite, graceful=True)
                stop_owned(django)
    print("PASS: disposable browser/API integration; servers stopped and temporary database/media removed.", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
