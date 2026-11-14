import { chromium } from '../../../frontend/node_modules/playwright/index.mjs';
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const WORK = path.join(ROOT, 'work');
const SCENE_URL = pathToFileURL(path.join(ROOT, 'assets', 'scene.html')).href;
const AUDIO_WAV = path.join(ROOT, 'audio', 'soundtrack.wav');
const OUT_MP4 = path.join(ROOT, 'yanhuo-diorama-48s-1080p60.mp4');

const FPS = 60;
const DURATION = 48;
const TOTAL_FRAMES = FPS * DURATION; // 2880
const WORKERS = 6;

fs.mkdirSync(WORK, { recursive: true });

const KEYFRAME_TIMES = [
  1.10,  // 01: Flat architectural blueprint
  2.32,  // 02: Origami 3D fold-up transition
  4.30,  // 03: Empty campus corner maquette + brass pin flags
  5.74,  // 04: Workbench glide transition
  9.60,  // 05: Glowing noodle cart diorama + pop-up merchant easel
  14.48, // 06: Bi-fold accordion hinge opening
  16.90, // 07: Student budget & per-portion customization
  18.34, // 08: Pneumatic order capsule sliding across wings
  21.20, // 09: Merchant cooking state
  23.80, // 10: Tumbling wooden pickup code blocks 4829
  25.28, // 11: Glide to miniature handoff pavilion
  29.80, // 12: Handoff pavilion with 3 privacy & prepay callout pins
  33.70, // 13: Transition to 3-tier isometric governance stack
  38.80, // 14: Full 3-tier isometric exploded governance architecture
  42.75, // 15: Isometric stack folding into panorama finale
  45.50, // 16: Miniature campus panorama brand finale
];

async function renderKeyframes() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto(SCENE_URL, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);

  const kfDir = path.join(WORK, 'keyframes');
  fs.mkdirSync(kfDir, { recursive: true });

  for (let i = 0; i < KEYFRAME_TIMES.length; i++) {
    const t = KEYFRAME_TIMES[i];
    await page.evaluate((timeSec) => window.renderAt(timeSec), t);
    const out = path.join(kfDir, `kf-${String(i + 1).padStart(2, '0')}.jpg`);
    await page.screenshot({ path: out, type: 'jpeg', quality: 95 });
  }
  await browser.close();

  const inputs = [];
  for (let i = 0; i < 16; i++) {
    inputs.push('-i', path.join(kfDir, `kf-${String(i + 1).padStart(2, '0')}.jpg`));
  }
  execFileSync('ffmpeg', [
    '-y', '-hide_banner', '-loglevel', 'error',
    ...inputs,
    '-filter_complex',
    'hstack=inputs=4[r0];[4:v][5:v][6:v][7:v]hstack=inputs=4[r1];[8:v][9:v][10:v][11:v]hstack=inputs=4[r2];[12:v][13:v][14:v][15:v]hstack=inputs=4[r3];[r0][r1][r2][r3]vstack=inputs=4,scale=2560:1440',
    '-q:v', '2',
    path.join(ROOT, 'contact-sheet.jpg'),
  ]);
  fs.copyFileSync(path.join(kfDir, 'kf-16.jpg'), path.join(ROOT, 'poster.jpg'));
  console.log('Film 2 keyframes & contact-sheet.jpg generated.');
}

async function renderChunk(browser, workerIdx, startFrame, endFrame) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto(SCENE_URL, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);

  const segPath = path.join(WORK, `seg-${workerIdx}.mp4`);
  const ff = spawn('ffmpeg', [
    '-y', '-hide_banner', '-loglevel', 'error',
    '-f', 'image2pipe',
    '-vcodec', 'mjpeg',
    '-framerate', String(FPS),
    '-i', '-',
    '-vf', 'scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p',
    '-c:v', 'libx264',
    '-preset', 'fast',
    '-crf', '15',
    '-pix_fmt', 'yuv420p',
    '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off',
    '-color_range', 'tv',
    '-colorspace', 'bt709',
    '-color_primaries', 'bt709',
    '-color_trc', 'bt709',
    '-r', String(FPS),
    segPath,
  ], { stdio: ['pipe', 'inherit', 'inherit'] });

  for (let f = startFrame; f < endFrame; f++) {
    const t = f / FPS;
    await page.evaluate((timeSec) => window.renderAt(timeSec), t);
    const buf = await page.screenshot({ type: 'jpeg', quality: 96 });
    if (!ff.stdin.write(buf)) {
      await new Promise((res) => ff.stdin.once('drain', res));
    }
  }
  ff.stdin.end();
  await new Promise((resolve, reject) => {
    ff.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg seg ${workerIdx} exited ${code}`))));
  });
  await page.close();
  return segPath;
}

async function renderFullVideo() {
  const t0 = Date.now();
  console.log(`Rendering Film 2: ${TOTAL_FRAMES} frames @ ${FPS}fps across ${WORKERS} workers...`);
  const browser = await chromium.launch({ headless: true });

  const perWorker = Math.ceil(TOTAL_FRAMES / WORKERS);
  const tasks = [];
  for (let w = 0; w < WORKERS; w++) {
    const s = w * perWorker;
    const e = Math.min(TOTAL_FRAMES, (w + 1) * perWorker);
    tasks.push(renderChunk(browser, w, s, e));
  }
  const segPaths = await Promise.all(tasks);
  await browser.close();

  const concatList = path.join(WORK, 'concat.txt');
  fs.writeFileSync(
    concatList,
    segPaths.map((p) => `file '${p.replace(/\\/g, '/')}'`).join('\n'),
    'utf8'
  );

  console.log('Muxing Film 2 1080p60 BT.709 MP4 with synthesized soundtrack...');
  execFileSync('ffmpeg', [
    '-y', '-hide_banner', '-loglevel', 'error',
    '-f', 'concat', '-safe', '0', '-i', concatList,
    '-i', AUDIO_WAV,
    '-map', '0:v:0',
    '-map', '1:a:0',
    '-c:v', 'copy',
    '-bsf:v', 'h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0',
    '-color_range', 'tv',
    '-colorspace', 'bt709',
    '-color_primaries', 'bt709',
    '-color_trc', 'bt709',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-ar', '48000',
    '-ac', '2',
    '-movflags', '+faststart',
    '-shortest',
    OUT_MP4,
  ]);
  console.log(`Done in ${((Date.now() - t0) / 1000).toFixed(1)}s -> ${OUT_MP4}`);
}

if (process.argv.includes('--keyframes')) {
  await renderKeyframes();
} else {
  await renderKeyframes();
  await renderFullVideo();
}
