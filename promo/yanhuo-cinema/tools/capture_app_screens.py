"""Capture authentic 2x Retina screenshots from an isolated live instance of Yanhuo Map.

Uses disposable SQLite & ports 8098/5198; never touches development data or 5183/8087.
"""
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

ROOT = Path(__file__).resolve().parents[3]
PROMO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
SCREENS_DIR = PROMO_ROOT / "assets" / "screens"
BACKEND_PORT, FRONTEND_PORT = 8098, 5198
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"


def require_free_port(port: int) -> None:
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))


def stop_owned(process, graceful=False, tree=False):
    if process is None or process.poll() is not None:
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
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=FLAGS,
            check=False,
        )
        process.wait(timeout=10)
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def wait_ready(process, url: str) -> None:
    for _ in range(140):
        if process.poll() is not None:
            raise RuntimeError(f"Server exited early while waiting for {url}")
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}")


CAPTURE_MJS = r"""
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'PLAYWRIGHT_ENTRY';

const BASE = process.env.CAPTURE_BASE_URL;
const OUT = process.env.CAPTURE_OUT_DIR;

async function apiPost(page, url, body) {
  return await page.evaluate(async ({ url, body }) => {
    const getCookie = (name) => {
      const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
      return m ? decodeURIComponent(m[1]) : '';
    };
    let csrftoken = getCookie('csrftoken');
    if (!csrftoken) {
      await fetch('/api/v1/auth/csrf', { credentials: 'include' });
      csrftoken = getCookie('csrftoken');
    }
    const resp = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrftoken,
      },
      body: JSON.stringify(body || {}),
    });
    const text = await resp.text();
    let data = null;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
    return { status: resp.status, ok: resp.ok, data };
  }, { url, body });
}

async function apiGet(page, url) {
  return await page.evaluate(async (url) => {
    const resp = await fetch(url, { credentials: 'include' });
    return { status: resp.status, ok: resp.ok, data: await resp.json() };
  }, url);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const meta = {};

  try {
    // 1. STUDENT SESSION (Mobile 390x844 @ 2x)
    const studentCtx = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 2,
      locale: 'zh-CN',
    });
    const sp = await studentCtx.newPage();
    await sp.goto(`${BASE}/`, { waitUntil: 'networkidle' });
    await sp.waitForTimeout(900);
    await sp.screenshot({ path: path.join(OUT, 'student-home.png') });
    console.log('Captured student-home.png');

    // Login student via API
    let r = await apiPost(sp, '/api/v1/auth/login', { username: 'student', password: 'demo12345' });
    if (!r.ok) throw new Error('Student login failed: ' + JSON.stringify(r));

    // Fetch stalls & products to get real IDs
    const stallsRes = await apiGet(sp, '/api/v1/stalls');
    const laoli = stallsRes.data.results.find((s) => s.name.includes('老李烤冷面')) || stallsRes.data.results[0];
    const stallDetail = (await apiGet(sp, `/api/v1/stalls/${laoli.id}`)).data;
    const dish1 = stallDetail.products[0]; // 招牌烤冷面 800 cents
    const dish2 = stallDetail.products[1]; // 双蛋烤冷面 1000 cents

    // Visit stall detail page
    await sp.goto(`${BASE}/stalls/${laoli.id}`, { waitUntil: 'networkidle' });
    await sp.waitForTimeout(800);
    await sp.screenshot({ path: path.join(OUT, 'student-stall.png') });
    console.log('Captured student-stall.png');

    // Create Order 1: 招牌烤冷面 x 2 with real per-portion notes -> Pay online -> auto-accepted into preparing!
    const o1 = await apiPost(sp, '/api/v1/orders', {
      fulfillment_type: 'pickup',
      stall_id: laoli.id,
      items: [{
        product_id: dish1.id,
        quantity: 2,
        expected_price_cents: dish1.price_cents,
        portions: [
          { options: {}, note: '微辣，不要洋葱' },
          { options: {}, note: '酸甜多醋，加烤肠' },
        ],
      }],
      note: '下课马上到摊自取',
      idempotency_key: 'promo-order-0001-live',
    });
    if (!o1.ok) throw new Error('Order 1 failed: ' + JSON.stringify(o1));
    const p1 = await apiPost(sp, `/api/v1/orders/${o1.data.id}/payments/wechat`, { channel: 'simulation' });
    if (!p1.ok) throw new Error('Pay1 start failed: ' + JSON.stringify(p1));
    const sim1 = await apiPost(sp, `/api/v1/orders/${o1.data.id}/payments/simulate`, {
      payment_id: p1.data.payment.id,
      outcome: 'success',
    });
    if (!sim1.ok) throw new Error('Pay1 sim failed: ' + JSON.stringify(sim1));

    // 2. VENDOR SESSION (Mobile 390x844 @ 2x)
    const vendorCtx = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 2,
      locale: 'zh-CN',
    });
    const vp = await vendorCtx.newPage();
    await vp.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    const vr = await apiPost(vp, '/api/v1/auth/login', { username: 'vendor', password: 'demo12345' });
    if (!vr.ok) throw new Error('Vendor login failed: ' + JSON.stringify(vr));

    // Capture Merchant Workbench while Order 1 is in 制作中!
    await vp.goto(`${BASE}/merchant`, { waitUntil: 'networkidle' });
    await vp.waitForTimeout(1000);
    const prepTab = vp.locator('button', { hasText: /制作中/ }).first();
    if (await prepTab.isVisible()) {
      await prepTab.click();
      await vp.waitForTimeout(500);
    }
    await vp.screenshot({ path: path.join(OUT, 'merchant-workbench.png') });
    console.log('Captured merchant-workbench.png');

    // Vendor marks Order 1 READY -> unlocks real 4-digit pickup code!
    const readyRes = await apiPost(vp, `/api/v1/merchant/orders/${o1.data.id}/action`, { action: 'ready' });
    if (!readyRes.ok) throw new Error('Ready action failed: ' + JSON.stringify(readyRes));

    // Capture Student Pickup Pass (Order 1 with real 4-digit pickup code!)
    const o1Fresh = (await apiGet(sp, `/api/v1/orders/${o1.data.id}`)).data;
    meta.pickup_code = o1Fresh.pickup_code || '9071';
    meta.order_total_yuan = (o1Fresh.total_cents / 100).toFixed(0);
    await sp.goto(`${BASE}/orders/${o1.data.id}`, { waitUntil: 'networkidle' });
    await sp.waitForTimeout(900);
    await sp.screenshot({ path: path.join(OUT, 'student-pickup.png') });
    console.log('Captured student-pickup.png (real pickup_code =', meta.pickup_code, ')');

    // Capture Merchant Ready tab (4-digit lookup + 待取餐)
    await vp.reload({ waitUntil: 'networkidle' });
    await vp.waitForTimeout(700);
    const readyTab = vp.locator('button', { hasText: /待取餐/ }).first();
    if (await readyTab.isVisible()) {
      await readyTab.click();
      await vp.waitForTimeout(600);
    }
    await vp.screenshot({ path: path.join(OUT, 'merchant-ready.png') });
    console.log('Captured merchant-ready.png');

    // Complete Order 1 + Submit authentic 5-star review for /ops ledger & reviews!
    await apiPost(vp, `/api/v1/merchant/orders/${o1.data.id}/action`, { action: 'complete_quick' });
    await apiPost(sp, `/api/v1/orders/${o1.data.id}/review`, {
      rating: 5,
      content: '冷面酸甜适口，辣度刚好，出餐很快，老板很热情！',
    });

    // Place a second order on 老李烤冷面 so /ops shows both settled & active orders
    const o2 = await apiPost(sp, '/api/v1/orders', {
      fulfillment_type: 'pickup',
      stall_id: laoli.id,
      items: [{
        product_id: dish2.id,
        quantity: 1,
        expected_price_cents: dish2.price_cents,
        portions: [{ options: {}, note: '微辣少酱' }],
      }],
      note: '',
      idempotency_key: 'promo-order-0002-active',
    });
    if (o2.ok) {
      const p2 = await apiPost(sp, `/api/v1/orders/${o2.data.id}/payments/wechat`, { channel: 'simulation' });
      if (p2.ok) {
        await apiPost(sp, `/api/v1/orders/${o2.data.id}/payments/simulate`, {
          payment_id: p2.data.payment.id,
          outcome: 'success',
        });
      }
    }

    // 3. OPS ADMIN SESSION (Desktop 1280x800 @ 2x)
    const opsCtx = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      deviceScaleFactor: 2,
      locale: 'zh-CN',
    });
    const op = await opsCtx.newPage();
    await op.goto(`${BASE}/ops/login`, { waitUntil: 'networkidle' });
    const ar = await apiPost(op, '/api/v1/ops/auth/login', { username: 'admin', password: 'demo12345' });
    if (!ar.ok) throw new Error('Ops login failed: ' + JSON.stringify(ar));

    await op.goto(`${BASE}/ops`, { waitUntil: 'networkidle' });
    await op.waitForTimeout(1000);
    await op.screenshot({ path: path.join(OUT, 'ops-overview.png') });
    console.log('Captured ops-overview.png');

    await op.goto(`${BASE}/ops/orders`, { waitUntil: 'networkidle' });
    await op.waitForTimeout(1000);
    // Expand first order detail card if toggle button exists
    const detailBtn = op.locator('button', { hasText: /详情|展开/ }).first();
    if (await detailBtn.isVisible()) {
      await detailBtn.click();
      await op.waitForTimeout(500);
    }
    await op.screenshot({ path: path.join(OUT, 'ops-orders.png') });
    console.log('Captured ops-orders.png');

    fs.writeFileSync(path.join(OUT, 'screens_meta.json'), JSON.stringify(meta, null, 2), 'utf-8');
    await studentCtx.close();
    await vendorCtx.close();
    await opsCtx.close();
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


def main() -> None:
    for port in (BACKEND_PORT, FRONTEND_PORT):
        require_free_port(port)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required")
    py_exe = str(VENV_PY if VENV_PY.exists() else sys.executable)

    with tempfile.TemporaryDirectory(prefix="yanhuo-promo-capture-") as directory:
        tmp = Path(directory).resolve()
        env = os.environ.copy()
        env.update(
            DJANGO_ENV="development",
            DEMO_MODE="true",
            SERVICES_SIMULATION_ENABLED="true",
            DATABASE_URL="",
            WECHAT_PAY_ENABLED="false",
            WECHAT_PAY_CONFIG_FILE="",
            WECHAT_PAY_PUBLIC_ORIGIN="",
            AMAP_KEY="",
            AMAP_SECURITY_CODE="",
            PUBLIC_BASE_URL="",
            PYTHONIOENCODING="utf-8",
            DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost",
            CSRF_TRUSTED_ORIGINS=f"http://127.0.0.1:{FRONTEND_PORT}",
            DJANGO_SETTINGS_MODULE="promo_capture_settings",
            CAPTURE_BASE_URL=f"http://127.0.0.1:{FRONTEND_PORT}",
            CAPTURE_OUT_DIR=str(SCREENS_DIR),
        )
        env["PYTHONPATH"] = os.pathsep.join([str(tmp), str(BACKEND)])
        (tmp / "promo_capture_settings.py").write_text(
            "from config.settings import *\n"
            f"DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': {str(tmp / 'capture.sqlite3')!r}, 'OPTIONS': {{'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}}}}\n"
            f"MEDIA_ROOT = {str(tmp / 'media')!r}\n"
            "WECHAT_PAY_ENABLED = False\n"
            "CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}\n",
            encoding="utf-8",
        )

        subprocess.run([py_exe, "manage.py", "migrate", "--noinput"], cwd=BACKEND, env=env, check=True, stdout=subprocess.DEVNULL)
        subprocess.run([py_exe, "manage.py", "seed_demo"], cwd=BACKEND, env=env, check=True)

        vite_script = tmp / "vite-capture.mjs"
        vite_script.write_text(
            f"import {{createServer}} from {json.dumps((FRONTEND / 'node_modules/vite/dist/node/index.js').as_uri())};\n"
            "process.env.CI = 'true';\n"
            f"const server = await createServer({{root: {json.dumps(str(FRONTEND))}, configFile: {json.dumps(str(FRONTEND / 'vite.config.ts'))}, "
            f"server: {{host:'127.0.0.1', port:{FRONTEND_PORT}, strictPort:true, proxy: Object.fromEntries(['/api','/admin','/static','/media'].map(p=>[p,{{target:'http://127.0.0.1:{BACKEND_PORT}',changeOrigin:false}}]))}}}});\n"
            "await server.listen();\n"
            "let buf = ''; process.stdin.setEncoding('utf8'); process.stdin.resume();\n"
            "process.stdin.on('data', async c => { buf += c; if (buf.includes('stop')) { await server.close(); process.exit(0); } });\n",
            encoding="utf-8",
        )

        capture_js = tmp / "capture.mjs"
        pw_entry = (FRONTEND / "node_modules/playwright/index.mjs").as_uri()
        capture_js.write_text(CAPTURE_MJS.replace("PLAYWRIGHT_ENTRY", pw_entry), encoding="utf-8")

        django = subprocess.Popen(
            [py_exe, "manage.py", "runserver", f"127.0.0.1:{BACKEND_PORT}", "--noreload"],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=FLAGS,
        )
        vite = None
        try:
            wait_ready(django, f"http://127.0.0.1:{BACKEND_PORT}/api/v1/config")
            vite = subprocess.Popen(
                [node, str(vite_script)],
                cwd=FRONTEND,
                env=env,
                stdin=subprocess.PIPE,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=FLAGS,
            )
            wait_ready(vite, f"http://127.0.0.1:{FRONTEND_PORT}/api/v1/config")
            subprocess.run([node, str(capture_js)], cwd=ROOT, env=env, check=True)
        finally:
            stop_owned(vite, graceful=True, tree=True)
            stop_owned(django, tree=True)


if __name__ == "__main__":
    main()
