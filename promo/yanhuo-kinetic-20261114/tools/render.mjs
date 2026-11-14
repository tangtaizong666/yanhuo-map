import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { chromium } from '../../../frontend/node_modules/playwright/index.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const THREE_BUILD_DIR = 'C:\\my_pycharm\\airi-main\\node_modules\\.pnpm\\three@0.184.0\\node_modules\\three\\build';

const TOTAL_FRAMES = 2280; // 38.000s @ 60fps
const WORKERS = 6;
const KEYFRAMES_ONLY = process.argv.includes('--keyframes');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.jpg': 'image/jpeg',
  '.png': 'image/png',
  '.json': 'application/json',
};

function startLocalServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const u = decodeURIComponent((req.url || '/').split('?')[0]);
      let filePath;
      if (u.startsWith('/three/')) {
        filePath = path.join(THREE_BUILD_DIR, u.slice('/three/'.length));
      } else {
        filePath = path.join(ROOT, u.replace(/^\/+/, ''));
      }
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

function saveDataUrlJpg(dataUrl, destPath) {
  const b64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
  fs.writeFileSync(destPath, Buffer.from(b64, 'base64'));
}

const KEYFRAME_SECONDS = [
  0.35,  // 01: Wok fire + 十点下课
  1.10,  // 02: Sauce toss + 就馋这一口
  2.25,  // 03: Sprint steps
  2.50,  // 04: Edgar Wright optical whip-pan smear!
  3.55,  // 05: Empty gate + 人呢？又跑空了？
  5.45,  // 06: 3D Titanium Macro Phone + detached 3D badge
  7.35,  // 07: 3D Campus Night Street + AR Live Pin
  10.25, // 08: 3D Brass & Frosted-Glass 60:00 Auto-Stop Shutter
  15.05, // 09: 3D Serrated Thermal Receipt + 3 Live Video Collage Windows
  16.12, // 10: Serrated Paper Tear Opening!
  18.95, // 11: Diagonal Live Wok Fire x 3D Merchant Ready Console
  21.40, // 12: Dual 3D Phones — 4829 Pickup Pass
  23.20, // 13: Dual 3D Phones — Fixed Campus Handoff Point
  27.20, // 14: Ordinary Folk 3D Direct-Pay Marble Bridge (Zero Pool!)
  31.35, // 15: Sensory Climax Knockout
  36.20, // 16: Warm Lantern Bokeh & 3D Brand Finale
];

async function main() {
  const t0 = Date.now();
  const { server, port } = await startLocalServer();
  const sceneUrl = `http://127.0.0.1:${port}/assets/scene.html`;

  const kfDir = path.join(ROOT, 'work', 'keyframes');
  fs.mkdirSync(kfDir, { recursive: true });

  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--use-gl=angle', '--enable-webgl', '--ignore-gpu-blocklist'],
  });

  try {
    // 1. Always render diagnostic keyframes & contact-sheet.jpg + poster.jpg first
    const kfPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    kfPage.on('pageerror', (err) => console.error('PAGE ERROR:', err));
    await kfPage.goto(sceneUrl, { waitUntil: 'networkidle' });
    await kfPage.waitForFunction(() => window.__SCENE_READY__ === true, null, { timeout: 15000 });

    const kfPaths = [];
    for (let i = 0; i < KEYFRAME_SECONDS.length; i++) {
      const fIdx = Math.min(TOTAL_FRAMES - 1, Math.round(KEYFRAMES_SECONDS_AT(i) * 60));
      const dataUrl = await kfPage.evaluate((f) => window.renderFrame(f), fIdx);
      const outP = path.join(kfDir, `kf_${String(i + 1).padStart(2, '0')}.jpg`);
      saveDataUrlJpg(dataUrl, outP);
      kfPaths.push(outP);
    }

    // Poster from 36.20s (kf_16)
    fs.copyFileSync(kfPaths[15], path.join(ROOT, 'poster.jpg'));

    // Build 4x4 contact-sheet.jpg via FFmpeg
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
    console.log('Keyframes & contact-sheet.jpg rendered.');

    if (KEYFRAMES_ONLY) {
      return;
    }

    // 2. Full 2,280-frame 60fps parallel render across 6 GPU Edge pages
    const framesDir = path.join(ROOT, 'work', 'frames');
    fs.mkdirSync(framesDir, { recursive: true });

    console.log(`Rendering ${TOTAL_FRAMES} frames @ 60fps across ${WORKERS} GPU workers...`);
    const chunkSize = Math.ceil(TOTAL_FRAMES / WORKERS);
    const segPaths = [];

    await Promise.all(
      Array.from({ length: WORKERS }, async (_, wIdx) => {
        const startF = wIdx * chunkSize;
        const endF = Math.min(TOTAL_FRAMES, startF + chunkSize);
        if (startF >= endF) return;

        const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
        await page.goto(sceneUrl, { waitUntil: 'networkidle' });
        await page.waitForFunction(() => window.__SCENE_READY__ === true, null, { timeout: 15000 });

        for (let f = startF; f < endF; f++) {
          const dataUrl = await page.evaluate((frameIndex) => window.renderFrame(frameIndex), f);
          const jpgPath = path.join(framesDir, `f_${String(f).padStart(5, '0')}.jpg`);
          saveDataUrlJpg(dataUrl, jpgPath);
        }
        await page.close();

        // Encode chunk to BT.709 H.264 segment
        const segMp4 = path.join(ROOT, 'work', `seg_${wIdx}.mp4`);
        segPaths[wIdx] = segMp4;
        execFileSync('ffmpeg', [
          '-y', '-v', 'error',
          '-framerate', '60',
          '-start_number', String(startF),
          '-i', path.join(framesDir, 'f_%05d.jpg'),
          '-frames:v', String(endF - startF),
          '-c:v', 'libx264',
          '-preset', 'medium',
          '-crf', '17',
          '-pix_fmt', 'yuv420p',
          '-color_range', 'tv',
          '-colorspace', 'bt709',
          '-color_primaries', 'bt709',
          '-color_trc', 'bt709',
          '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off:keyint=60:min-keyint=60',
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

    const outMp4 = path.join(ROOT, 'yanhuo-kinetic-38s-1080p60.mp4');
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

function KEYFRAMES_SECONDS_AT(i) {
  return KEYFRAME_SECONDS[i];
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
