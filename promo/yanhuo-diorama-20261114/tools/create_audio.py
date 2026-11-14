"""Synthesize original 48.000s 118 BPM Tactile Chamber-Pop / Marimba & Pizzicato Score
+ Origami Paper-Fold & Maquette Foley for Film 2: 《折叠街角》(Pop-up Isometric Diorama).

100% procedural Python synthesis (numpy + scipy). Zero external samples.
Output: promo/yanhuo-diorama-20261114/audio/soundtrack.wav (48 kHz, 16-bit stereo, 48.000s).
"""

from pathlib import Path
import json
import wave
import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000
DURATION = 48.0
N_SAMPLES = round(SR * DURATION)
BPM = 118.0
BEAT = 60.0 / BPM  # ~0.5085s

RNG = np.random.default_rng(2026111402)
MIX = np.zeros((N_SAMPLES, 2), dtype=np.float64)


def hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def lp(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    cutoff = min(max(cutoff, 30.0), SR * 0.46)
    return sosfilt(butter(order, cutoff, btype="low", fs=SR, output="sos"), sig)


def bp(sig: np.ndarray, low: float, high: float, order: int = 2) -> np.ndarray:
    low = max(25.0, min(low, SR * 0.42))
    high = max(low + 40.0, min(high, SR * 0.46))
    return sosfilt(butter(order, [low, high], btype="band", fs=SR, output="sos"), sig)


def add_stereo(sig_l: np.ndarray, sig_r: np.ndarray, when: float, gain: float = 1.0):
    start = round(when * SR)
    if start >= N_SAMPLES:
        return
    if start < 0:
        sig_l = sig_l[-start:]
        sig_r = sig_r[-start:]
        start = 0
    n = min(len(sig_l), len(sig_r), N_SAMPLES - start)
    if n <= 0:
        return
    MIX[start : start + n, 0] += sig_l[:n] * gain
    MIX[start : start + n, 1] += sig_r[:n] * gain


def add_mono(sig: np.ndarray, when: float, gain: float = 1.0, pan: float = 0.0):
    pan = float(np.clip(pan, -1.0, 1.0))
    ang = (pan + 1.0) * (np.pi / 4.0)
    add_stereo(sig * np.cos(ang), sig * np.sin(ang), when, gain)


# ---------------- Handcrafted Chamber / Toy-Box Acoustic Synthesis ----------------

def marimba(midi: float, length: float = 0.85, vel: float = 0.9):
    """Warm rosewood marimba bar (fundamental + 4th & 10th tuned overtones)."""
    n = round(length * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    mallet = bp(RNG.normal(size=n), 900, 4200) * np.exp(-t * 240.0) * 0.16 * vel
    bar = (
        np.sin(2 * np.pi * f * t) * np.exp(-t / 0.42)
        + 0.24 * np.sin(2 * np.pi * (f * 3.99) * t) * np.exp(-t / 0.11)
        + 0.06 * np.sin(2 * np.pi * (f * 9.8) * t) * np.exp(-t / 0.04)
    ) * (1.0 - np.exp(-t * 520.0))
    end_env = np.minimum(1.0, np.maximum(0.0, (length - t) / 0.05))
    return lp((bar * vel + mallet) * end_env, 5200.0)


def kalimba_tine(midi: float, length: float = 1.2):
    """Sparkling thumb-piano metallic-wood tine."""
    n = round(length * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    sig = (
        np.sin(2 * np.pi * f * t) * np.exp(-t / 0.68)
        + 0.18 * np.sin(2 * np.pi * f * 2.98 * t) * np.exp(-t / 0.18)
        + 0.08 * np.sin(2 * np.pi * f * 5.85 * t) * np.exp(-t / 0.06)
    ) * (1.0 - np.exp(-t * 700.0))
    return lp(sig * np.minimum(1.0, np.maximum(0.0, (length - t) / 0.08)), 6400.0)


def pizzicato(midi: float, length: float = 0.48):
    """Crisp chamber pizzicato string pluck."""
    n = round(length * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    sig = np.zeros(n)
    for h in range(1, 7):
        sig += (1.0 / (h ** 1.45)) * np.sin(2 * np.pi * f * h * t) * np.exp(-t / (0.24 / (h ** 0.55)))
    body = bp(RNG.normal(size=n), 220, 1400) * np.exp(-t * 110.0) * 0.12
    env = (1.0 - np.exp(-t * 420.0)) * np.minimum(1.0, np.maximum(0.0, (length - t) / 0.05))
    return lp((sig + body) * env, 3400.0)


def acoustic_upright_bass(midi: float, length: float = 0.55):
    n = round(length * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    sig = (
        np.sin(2 * np.pi * f * t) * np.exp(-t / 0.48)
        + 0.32 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.26)
        + 0.11 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t / 0.15)
    )
    env = (1.0 - np.exp(-t * 140.0)) * np.minimum(1.0, np.maximum(0.0, (length - t) / 0.05))
    return lp(sig * env, 520.0)


def warm_celesta_pad(midis, length: float = 3.8):
    n = round(length * SR)
    t = np.arange(n) / SR
    l_ch = np.zeros(n)
    r_ch = np.zeros(n)
    env = np.minimum(1.0, t / 0.5) * np.minimum(1.0, np.maximum(0.0, (length - t) / 0.75))
    for i, m in enumerate(midis):
        f = hz(m)
        det = 0.002 * np.sin(2 * np.pi * 3.2 * t + i)
        l_ch += np.sin(2 * np.pi * f * (1 + det) * t) / len(midis)
        r_ch += np.sin(2 * np.pi * f * (1 - det) * t + 0.5) / len(midis)
    return lp(l_ch * env, 1900.0), lp(r_ch * env, 1900.0)


def cajon_bass():
    n = round(0.22 * SR)
    t = np.arange(n) / SR
    f = 56.0 + 68.0 * np.exp(-t * 42.0)
    ph = 2 * np.pi * np.cumsum(f) / SR
    slap = bp(RNG.normal(size=n), 700, 2800) * np.exp(-t * 120.0) * 0.15
    return lp(np.sin(ph) * np.exp(-t * 17.0) + slap, 280.0)


def woodblock_click(pitch: float = 960.0):
    n = round(0.065 * SR)
    t = np.arange(n) / SR
    tone = (
        np.sin(2 * np.pi * pitch * t) * np.exp(-t * 95.0)
        + 0.45 * np.sin(2 * np.pi * pitch * 1.58 * t) * np.exp(-t * 150.0)
    )
    snap = bp(RNG.normal(size=n), 1800, 6500) * np.exp(-t * 220.0) * 0.35
    return (tone + snap) * (1.0 - np.exp(-t * 1400.0))


def shaker_tick():
    n = round(0.055 * SR)
    t = np.arange(n) / SR
    return bp(RNG.normal(size=n), 5200, 12500) * (1.0 - np.exp(-t * 1500.0)) * np.exp(-t * 72.0)


# ---------------- Origami & Maquette Tactile Foley SFX ----------------

def paper_origami_unfold():
    """Crisp textured cardstock crease + 3 rapid pop-up flaps!"""
    dur = 0.65
    n = round(dur * SR)
    t = np.arange(n) / SR
    out_l = np.zeros(n)
    out_r = np.zeros(n)
    flaps = [(0.0, 950, -0.4), (0.16, 1350, 0.1), (0.33, 1780, 0.45)]
    for tau, fc, pan in flaps:
        s = round(tau * SR)
        flen = round(0.22 * SR)
        if s + flen > n:
            break
        ft = np.arange(flen) / SR
        rustle = bp(RNG.normal(size=flen), fc * 0.6, min(fc * 3.2, 11000))
        env = (1.0 - np.exp(-ft * 120.0)) * np.exp(-ft * 18.0)
        thump = np.sin(2 * np.pi * (140 + tau * 90) * ft) * np.exp(-ft * 45.0) * 0.32
        sig = rustle * env * 0.85 + thump
        ang = (pan + 1.0) * (np.pi / 4.0)
        out_l[s : s + flen] += sig * np.cos(ang)
        out_r[s : s + flen] += sig * np.sin(ang)
    return out_l, out_r


def maquette_glide_whoosh(duration: float = 0.52, pan0: float = 0.6, pan1: float = -0.6):
    n = round(duration * SR)
    t = np.arange(n) / SR
    u = t / max(duration, 1e-4)
    env = np.sin(np.pi * u) ** 1.5
    air = bp(RNG.normal(size=n), 450, 4200) * env * 0.58
    wood_slide = bp(RNG.normal(size=n), 180, 780) * env * 0.35
    sig = air + wood_slide
    pans = pan0 + (pan1 - pan0) * u
    angs = (pans + 1.0) * (np.pi / 4.0)
    return sig * np.cos(angs), sig * np.sin(angs)


def wooden_stamp_thud():
    n = round(0.14 * SR)
    t = np.arange(n) / SR
    low = np.sin(2 * np.pi * 115.0 * t) * np.exp(-t * 48.0) * 0.75
    clack = bp(RNG.normal(size=n), 1200, 5200) * np.exp(-t * 160.0) * 0.65
    return low + clack


def toy_bell_chime(notes):
    n = round(1.1 * SR)
    t = np.arange(n) / SR
    l_ch = np.zeros(n)
    r_ch = np.zeros(n)
    for i, m in enumerate(notes):
        d = i * 0.055
        td = np.maximum(0.0, t - d)
        mask = (t >= d).astype(float)
        tone = kalimba_tine(m, 0.95)
        s = round(d * SR)
        ln = min(len(tone), n - s)
        pan = -0.3 + 0.3 * i
        ang = (pan + 1.0) * (np.pi / 4.0)
        l_ch[s : s + ln] += tone[:ln] * np.cos(ang) * mask[s : s + ln]
        r_ch[s : s + ln] += tone[:ln] * np.sin(ang) * mask[s : s + ln]
    return l_ch * 0.45, r_ch * 0.45


def number_blocks_tumble():
    total = round(0.78 * SR)
    out = np.zeros(total)
    for idx, tau in enumerate([0.0, 0.09, 0.19, 0.30, 0.42, 0.56]):
        s = round(tau * SR)
        w = woodblock_click(780 + idx * 95)
        ln = min(len(w), total - s)
        out[s : s + ln] += w[:ln] * (1.15 if idx >= 2 else 0.8)
    return out


# ---------------- Compose Full 48.000s Chamber-Marimba Score ----------------

cues = []
def mark(t_sec, name):
    cues.append({"time": round(t_sec, 3), "cue": name})

# Intro [0.00 - 6.00s]: Curious Blueprint Plucks -> Origami Pop-Up into Cool Dusk Corner
intro_notes = [(0.10, 67), (0.36, 74), (0.62, 79), (1.10, 78), (1.45, 74)]
for ts, m in intro_notes:
    add_mono(pizzicato(m, 0.45), ts, 0.24, -0.2 + 0.1 * (m % 5))

# Origami Pop-Up Chord at 2.05s (Em9 curious miniature reveal)
ol, or_ = paper_origami_unfold()
add_stereo(ol, or_, 2.02, 0.55); mark(2.02, "blueprint-origami-popup")
for i, m in enumerate([52, 59, 64, 67, 74]):
    add_mono(marimba(m, 1.1, 0.78), 2.15 + i * 0.045, 0.20, -0.3 + 0.15 * i)

add_mono(woodblock_click(880), 2.90, 0.32, -0.35); mark(2.90, "flag-1-empty-spot")
add_mono(woodblock_click(740), 3.35, 0.32, 0.35); mark(3.35, "flag-2-cold-queue")
gl_l, gl_r = maquette_glide_whoosh(0.55, 0.65, -0.65)
add_stereo(gl_l, gl_r, 5.45, 0.52); mark(5.45, "maquette-glide-to-noodle-cart")

# Main Playful Warm Chamber-Pop Groove [6.00s - 42.50s] in G Major / E Minor9
# Progression: Gmaj9 | B7alt -> Em9 | Cmaj9 | Am9 -> D13sus4
CHORDS = [
    (43, [59, 62, 66, 69, 74]),  # Gmaj9
    (40, [59, 64, 67, 71, 74]),  # Em9
    (36, [60, 64, 67, 71, 74]),  # Cmaj9
    (38, [57, 60, 64, 69, 74]),  # D13sus4
]

t_cur = 6.00
b_idx = 0
while t_cur < 42.4:
    root_m, notes = CHORDS[b_idx % len(CHORDS)]
    seg_beats = 4
    seg_dur = seg_beats * BEAT
    if t_cur + seg_dur > 42.55:
        seg_dur = max(BEAT, 42.55 - t_cur)

    in_slabs = t_cur >= 33.4
    if in_slabs:
        pl, pr = warm_celesta_pad(notes[:4], seg_dur * 1.05)
        add_stereo(pl, pr, t_cur, 0.15)

    n_b = max(1, round(seg_dur / BEAT))
    for b in range(n_b):
        bt = t_cur + b * BEAT
        if bt >= 42.5:
            break
        # Cajon & Woodblock pocket rhythm
        if b in (0, 2):
            add_mono(cajon_bass(), bt, 0.32, 0.0)
        if b == 2:
            add_mono(cajon_bass(), bt + 0.5 * BEAT, 0.18, 0.0)
        if b in (1, 3):
            add_mono(woodblock_click(1080), bt, 0.22, -0.12)
        add_mono(shaker_tick(), bt, 0.11, 0.25)
        add_mono(shaker_tick(), bt + 0.5 * BEAT, 0.09, -0.25)

        # Acoustic upright walking/bouncy bass
        bass_m = root_m if b == 0 else (root_m + 7 if b == 2 else root_m + 12)
        if b in (0, 2):
            add_mono(acoustic_upright_bass(bass_m, BEAT * 0.88), bt, 0.30, 0.0)
        elif b == 3:
            add_mono(acoustic_upright_bass(root_m + 5, BEAT * 0.45), bt + 0.5 * BEAT, 0.20, 0.0)

        # Syncopated Marimba arpeggio + Pizzicato counter-melody
        m1 = notes[(b * 2) % len(notes)]
        m2 = notes[(b * 2 + 1) % len(notes)]
        add_mono(marimba(m1, 0.55, 0.82), bt, 0.19, -0.28 + 0.18 * b)
        add_mono(marimba(m2, 0.45, 0.72), bt + 0.5 * BEAT, 0.16, 0.28 - 0.15 * b)
        if (b_idx + b) % 2 == 0:
            add_mono(kalimba_tine(notes[-1] + 12, 0.7), bt + 0.75 * BEAT, 0.11, 0.32 * ((b % 2) * 2 - 1))

    t_cur += seg_dur
    b_idx += 1

# Finale Warm Resolution [42.50s - 48.00s]: Gmaj9(add13) Toy-Box Panorama
fin_notes = [55, 59, 62, 66, 69, 74, 79]
pl, pr = warm_celesta_pad(fin_notes[:5], 4.7)
add_stereo(pl, pr, 42.65, 0.18)
for idx, m in enumerate(fin_notes):
    add_mono(marimba(m, 2.2, 0.85), 42.75 + idx * 0.06, 0.18, -0.35 + 0.11 * idx)
    add_mono(kalimba_tine(m + 12, 2.4), 42.82 + idx * 0.07, 0.09, 0.35 - 0.11 * idx)
add_mono(acoustic_upright_bass(43, 3.4), 42.75, 0.28, 0.0)

# ---------------- Exact Diorama Scene & UI Pop-Up Foley ----------------

# Scene 2: Noodle Cart Glow + Easel Unfold (6.30s) + Location Stamp (7.40s) + 60m Hourglass (8.20s)
cl, cr = toy_bell_chime([79, 83, 86])
add_stereo(cl, cr, 6.25, 0.46); mark(6.25, "noodle-cart-lantern-bloom")
add_mono(wooden_stamp_thud(), 7.40, 0.42, 0.18); mark(7.40, "merchant-location-stamp")
cl, cr = toy_bell_chime([81, 86])
add_stereo(cl, cr, 8.20, 0.38); mark(8.20, "60min-hourglass-freshness")

# Scene 3: Accordion Dive (14.20s) + Flavor Pops + Pneumatic Capsule (18.10s) + Code Blocks (22.70s)
ol, or_ = paper_origami_unfold()
add_stereo(ol, or_, 14.18, 0.48); mark(14.18, "accordion-cards-unfold")
add_mono(woodblock_click(1220), 15.50, 0.34, -0.25); mark(15.50, "portion-1-pop")
add_mono(woodblock_click(1360), 16.35, 0.34, -0.20); mark(16.35, "portion-2-pop")
add_mono(wooden_stamp_thud(), 17.40, 0.38, -0.15); mark(17.40, "order-submit-stamp")
gl_l, gl_r = maquette_glide_whoosh(0.48, -0.55, 0.55)
add_stereo(gl_l, gl_r, 18.10, 0.48); mark(18.10, "capsule-slide-to-kitchen")
cl, cr = toy_bell_chime([84, 91])
add_stereo(cl, cr, 18.56, 0.48); mark(18.56, "kitchen-console-chime")
add_mono(woodblock_click(1140), 20.10, 0.35, 0.25); mark(20.10, "accept-cooking-click")
add_mono(number_blocks_tumble(), 22.65, 0.45, 0.15); mark(22.65, "wooden-code-blocks-4829")

# Scene 4: Whip-Glide to Handoff Pavilion (25.00s) + 3 Callout Pins (26.2s, 27.3s, 28.4s)
gl_l, gl_r = maquette_glide_whoosh(0.55, -0.65, 0.65)
add_stereo(gl_l, gl_r, 25.00, 0.52); mark(25.00, "glide-to-handoff-pavilion")
add_mono(woodblock_click(1180), 26.20, 0.32, -0.25); mark(26.20, "pavilion-pin-1-prepay")
add_mono(woodblock_click(1320), 27.30, 0.32, 0.0); mark(27.30, "pavilion-pin-2-privacy")
cl, cr = toy_bell_chime([79, 86])
add_stereo(cl, cr, 28.40, 0.42); mark(28.40, "pavilion-pin-3-verified-pickup")

# Scene 5: 3-Layer Isometric Architectural Stack Lift (33.40s)
ol, or_ = paper_origami_unfold()
add_stereo(ol, or_, 33.38, 0.48); mark(33.38, "isometric-3-slab-stack-lift")
cl, cr = toy_bell_chime([79, 83, 86])
add_stereo(cl, cr, 34.50, 0.44); mark(34.50, "slab-1-ops-governance")
cl, cr = toy_bell_chime([81, 86, 90])
add_stereo(cl, cr, 35.90, 0.44); mark(35.90, "slab-2-direct-wechat-settlement")
cl, cr = toy_bell_chime([83, 88, 95])
add_stereo(cl, cr, 37.30, 0.46); mark(37.30, "slab-3-campus-livelihood")

# Scene 6: Slabs Fold into Panorama Maquette Finale (42.40s)
gl_l, gl_r = maquette_glide_whoosh(0.62, -0.4, 0.4)
add_stereo(gl_l, gl_r, 42.38, 0.44); mark(42.38, "panorama-finale-fold")
cl, cr = toy_bell_chime([79, 86, 91])
add_stereo(cl, cr, 43.05, 0.42); mark(43.05, "brand-finale-bell")

# ---------------- Mastering ----------------

fade_in = np.minimum(1.0, np.arange(N_SAMPLES) / (0.025 * SR))
fade_out = np.minimum(1.0, np.maximum(0.0, (N_SAMPLES - 1 - np.arange(N_SAMPLES)) / (1.35 * SR)))
fade_out = 0.5 - 0.5 * np.cos(np.pi * fade_out)
MIX *= (fade_in * fade_out)[:, None]

peak_raw = float(np.max(np.abs(MIX)))
if peak_raw > 0:
    MIX = np.tanh(MIX / peak_raw * 1.32) / np.tanh(1.32)

rms = float(np.sqrt(np.mean(MIX ** 2)))
target_rms = 10.0 ** (-17.2 / 20.0)
MIX *= (target_rms / max(rms, 1e-6))
peak_after = float(np.max(np.abs(MIX)))
max_allowed = 10.0 ** (-2.2 / 20.0)
if peak_after > max_allowed:
    MIX *= (max_allowed / peak_after)

root = Path(__file__).resolve().parents[1]
audio_dir = root / "audio"
audio_dir.mkdir(parents=True, exist_ok=True)
wav_path = audio_dir / "soundtrack.wav"

pcm16 = np.clip(np.round(MIX * 32767.0), -32767, 32767).astype(np.int16)
with wave.open(str(wav_path), "wb") as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(SR)
    wf.writeframes(pcm16.tobytes())

meta_out = {
    "title": "折叠街角 · Original 118 BPM Chamber-Marimba & Origami Foley Score",
    "duration_seconds": DURATION,
    "sample_rate": SR,
    "channels": 2,
    "bpm": BPM,
    "style": "118 BPM Handcrafted Marimba / Pizzicato / Kalimba + Origami & Maquette Foley",
    "peak_dbfs": round(20.0 * np.log10(float(np.max(np.abs(MIX))) + 1e-12), 2),
    "rms_dbfs": round(20.0 * np.log10(float(np.sqrt(np.mean(MIX ** 2))) + 1e-12), 2),
    "cues": cues,
}
(audio_dir / "soundtrack.json").write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Wrote {wav_path} ({len(pcm16)} samples, peak={meta_out['peak_dbfs']} dBFS, rms={meta_out['rms_dbfs']} dBFS)")
