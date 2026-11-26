import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { chromium } from '../../../frontend/node_modules/playwright/index.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const TIMELINE = JSON.parse(fs.readFileSync(path.join(ROOT, 'assets', 'timeline.json'), 'utf-8'));

const TOTAL_FRAMES = TIMELINE.total_frames;
const FPS = TIMELINE.fps || 60;
const WORKERS = 6;
const KEYFRAMES_ONLY = process.argv.includes('--keyframes');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8',
  '.webp': 'image/webp',
};

function startLocalServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const u = decodeURIComponent((req.url || '/').split('?')[0]);
      const filePath = path.join(ROOT, u.replace(/^\/+/, ''));
      if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        res.writeHead(404);
        res.end('Not found: ' + u);
        return;
      }
      const ext = path.extname(filePath).toLowerCase();
      res.writeHead(200, {
        'Content-Type': MIME[ext] || 'application/octet-stream',
        'Cache-Control': 'public, max-age=3600',
      });
      fs.createReadStream(filePath).pipe(res);
    });
    server.listen(0, '127.0.0.1', () => {
      resolve({ server, port: server.address().port });
    });
  });
}

function buildKeyframeTimes() {
  const s = Object.fromEntries(TIMELINE.scenes.map((x) => [x.id, x]));
  const mid = (sc, r = 0.5) => sc.start + (sc.end - sc.start) * r;
  return [
    mid(s.s01_hero_griddle, 0.25),      // 01: Opening griddle hero + 22:10
    mid(s.s01_hero_griddle, 0.72),      // 02: Griddle hook subtitle + title
    mid(s.s02_empty_gate, 0.55),        // 03: Empty gate punchline
    mid(s.s03_receipt_dissection, 0.72),// 04: Dual thermal receipts stamped
    mid(s.s04_brand_map, 0.32),         // 05: Brand reveal YANHUO MAP
    mid(s.s04_brand_map, 0.78),         // 06: Campus map route & live stall pin
    mid(s.s05_freshness_flap, 0.32),    // 07: Mechanical split-flap 60:00 countdown
    mid(s.s05_freshness_flap, 0.76),    // 08: Mechanical split-flap 停单 red lock
    mid(s.s06_dual_track, 0.65),        // 09: G2026 A/B dual-track highway
    mid(s.s07_optical_prism, 0.68),     // 10: Optical glass prism 95% / 5% zero pool
    mid(s.s08_student_live, 0.35),      // 11: Real Student Home + per-portion note
    mid(s.s08_student_live, 0.78),      // 12: Real Student 4-digit pickup pass
    mid(s.s09_merchant_ai, 0.24),       // 13: Real Merchant Workbench oil-hand button (制作中)
    mid(s.s09_merchant_ai, 0.78),       // 14: AI photo-assist dish scan
    mid(s.s10_handoff_ops, 0.65),       // 15: Campus pavilion + Real /ops desktop console
    mid(s.s11_montage_finale, 0.78),    // 16: Editorial ticket & brand finale
  ];
}

async function main() {
  const t0 = Date.now();
  const { server, port } = await startLocalServer();
  const filmUrl = `http://127.0.0.1:${port}/assets/film.html`;
  const kfDir = path.join(ROOT, 'work', 'keyframes');
  fs.mkdirSync(kfDir, { recursive: true });

  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: [
      '--use-gl=angle',
      '--enable-webgl',
      '--ignore-gpu-blocklist',
      '--font-render-hinting=none',
    ],
  });

  try {
    // 1. Render 16 diagnostic keyframes + contact-sheet.jpg + poster.jpg
    const kfPage = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      deviceScaleFactor: 1,
    });
    kfPage.on('pageerror', (err) => console.error('PAGE ERROR:', err));
    await kfPage.goto(filmUrl, { waitUntil: 'networkidle' });
    await kfPage.waitForFunction(() => window.__FILM_READY__ === true, null, { timeout: 20000 });

    const kfTimes = buildKeyframeTimes();
    const kfPaths = [];
    for (let i = 0; i < kfTimes.length; i++) {
      const fIdx = Math.max(0, Math.min(TOTAL_FRAMES - 1, Math.round(kfTimes[i] * FPS)));
      await kfPage.evaluate((f) => window.seekToFrame(f), fIdx);
      const outP = path.join(kfDir, `kf_${String(i + 1).padStart(2, '0')}.jpg`);
      await kfPage.screenshot({ path: outP, type: 'jpeg', quality: 92 });
      kfPaths.push(outP);
    }

    // Poster uses hero finale frame (kf_16)
    fs.copyFileSync(kfPaths[15], path.join(ROOT, 'poster.jpg'));

    const ffInputs = kfPaths.flatMap((p) => ['-i', p]);
    const filter =
      kfPaths.map((_, idx) => `[${idx}:v]scale=480:270[v${idx}]`).join(';') +
      ';' +
      kfPaths.map((_, idx) => `[v${idx}]`).join('') +
      'xstack=inputs=16:layout=0_0|w0_0|w0+w1_0|w0+w1+w2_0|0_h0|w0_h0|w0+w1_h0|w0+w1+w2_h0|0_h0+h4|w0_h0+h4|w0+w1_h0+h4|w0+w1+w2_h0+h4|0_h0+h4+h8|w0_h0+h4+h8|w0+w1_h0+h4+h8|w0+w1+w2_h0+h4+h8';
    execFileSync('ffmpeg', [
      '-y', '-v', 'error',
      ...ffInputs,
      '-filter_complex', filter,
      '-q:v', '3',
      path.join(ROOT, 'contact-sheet.jpg'),
    ]);
    await kfPage.close();
    console.log(`Keyframes (16) & contact-sheet.jpg rendered in ${((Date.now() - t0) / 1000).toFixed(1)}s.`);

    if (KEYFRAMES_ONLY) return;

    // 2. Full 60fps parallel render across 6 workers
    const framesDir = path.join(ROOT, 'work', 'frames');
    fs.mkdirSync(framesDir, { recursive: true });

    console.log(`Rendering ${TOTAL_FRAMES} frames (${TIMELINE.duration_seconds}s @ ${FPS}fps) across ${WORKERS} workers...`);
    const chunkSize = Math.ceil(TOTAL_FRAMES / WORKERS);
    const segPaths = [];

    await Promise.all(
      Array.from({ length: WORKERS }, async (_, wIdx) => {
        const startF = wIdx * chunkSize;
        const endF = Math.min(TOTAL_FRAMES, startF + chunkSize);
        if (startF >= endF) return;

        const page = await browser.newPage({
          viewport: { width: 1920, height: 1080 },
          deviceScaleFactor: 1,
        });
        await page.goto(filmUrl, { waitUntil: 'networkidle' });
        await page.waitForFunction(() => window.__FILM_READY__ === true, null, { timeout: 20000 });

        for (let f = startF; f < endF; f++) {
          await page.evaluate((frameIndex) => window.seekToFrame(frameIndex), f);
          const jpgPath = path.join(framesDir, `f_${String(f).padStart(5, '0')}.jpg`);
          await page.screenshot({ path: jpgPath, type: 'jpeg', quality: 91 });
        }
        await page.close();

        const segMp4 = path.join(ROOT, 'work', `seg_${wIdx}.mp4`);
        segPaths[wIdx] = segMp4;
        execFileSync('ffmpeg', [
          '-y', '-v', 'error',
          '-framerate', String(FPS),
          '-start_number', String(startF),
          '-i', path.join(framesDir, 'f_%05d.jpg'),
          '-frames:v', String(endF - startF),
          '-c:v', 'libx264',
          '-preset', 'medium',
          '-crf', '18',
          '-pix_fmt', 'yuv420p',
          '-color_range', 'tv',
          '-colorspace', 'bt709',
          '-color_primaries', 'bt709',
          '-color_trc', 'bt709',
          '-x264-params', `colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off:keyint=${FPS}:min-keyint=${FPS}`,
          segMp4,
        ]);
        console.log(`Worker ${wIdx} encoded frames ${startF}..${endF - 1}`);
      })
    );

    const concatTxt = path.join(ROOT, 'work', 'concat.txt');
    fs.writeFileSync(
      concatTxt,
      segPaths.filter(Boolean).map((p) => `file '${p.replace(/\\/g, '/')}'`).join('\n'),
      'utf-8'
    );

    const outMp4 = path.join(ROOT, 'yanhuo-cinema-1080p60.mp4');
    const audioWav = path.join(ROOT, 'audio', 'soundtrack.wav');

    execFileSync('ffmpeg', [
      '-y', '-v', 'error',
      '-f', 'concat', '-safe', '0', '-i', concatTxt,
      '-i', audioWav,
      '-map', '0:v:0', '-map', '1:a:0',
      '-c:v', 'copy',
      '-bsf:v', 'h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0',
      '-color_range', 'tv',
      '-colorspace', 'bt709',
      '-color_primaries', 'bt709',
      '-color_trc', 'bt709',
      '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
      '-movflags', '+faststart',
      '-shortest',
      outMp4,
    ]);

    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`Done in ${elapsed}s -> ${outMp4}`);
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
