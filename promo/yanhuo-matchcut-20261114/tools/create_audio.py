"""Synthesize original 48.000s Neo-Soul / Jazz-Hop score + frame-accurate tactile Foley
for Film 1: 《一镜穿烟火》(Yanhuo Map Seamless Match-Cut Defense Promo).

100% original procedural synthesis (numpy + scipy). Zero external samples or copyright risk.
Output: promo/yanhuo-matchcut-20261114/audio/soundtrack.wav (48 kHz, 16-bit stereo, 48.000s).
"""

from pathlib import Path
import json
import wave
import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000
DURATION = 48.0
N_SAMPLES = round(SR * DURATION)
BPM = 112.0
BEAT = 60.0 / BPM  # ~0.5357s

RNG = np.random.default_rng(2026111401)
MIX = np.zeros((N_SAMPLES, 2), dtype=np.float64)


def hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def lp(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    cutoff = min(max(cutoff, 30.0), SR * 0.46)
    return sosfilt(butter(order, cutoff, btype="low", fs=SR, output="sos"), sig)


def hp(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    cutoff = min(max(cutoff, 20.0), SR * 0.46)
    return sosfilt(butter(order, cutoff, btype="high", fs=SR, output="sos"), sig)


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
    angle = (pan + 1.0) * (np.pi / 4.0)
    add_stereo(sig * np.cos(angle), sig * np.sin(angle), when, gain)


# ---------------- Musical Instruments (Warm Jazz-Hop / Neo-Soul) ----------------

def rhodes_note(midi: float, length: float = 2.2, vel: float = 0.85):
    n = round(length * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    attack = 1.0 - np.exp(-t * 310.0)
    release = np.minimum(1.0, np.maximum(0.0, (length - t) / 0.12))
    # Warm electric piano body + subtle bell tine
    body = (
        np.sin(2 * np.pi * f * t) * np.exp(-t / 1.45)
        + 0.28 * np.sin(2 * np.pi * f * 2.0 * t + 0.3) * np.exp(-t / 0.72)
        + 0.09 * np.sin(2 * np.pi * f * 3.0 * t) * np.exp(-t / 0.35)
        + 0.065 * vel * np.sin(2 * np.pi * f * 5.99 * t) * np.exp(-t / 0.09)
    )
    sig = lp(body * attack * release * vel, 3100.0)
    # Gentle stereo Rhodes autopan/tremolo
    trem_l = 0.86 + 0.14 * np.sin(2 * np.pi * 3.8 * t)
    trem_r = 0.86 - 0.14 * np.sin(2 * np.pi * 3.8 * t)
    return sig * trem_l, sig * trem_r


def warm_bass(midi: float, length: float = 0.52):
    n = round(length * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    glide = f * (1.0 + 0.025 * np.exp(-t * 45.0))
    phase = 2 * np.pi * np.cumsum(glide) / SR
    sig = (
        np.sin(phase)
        + 0.24 * np.sin(2 * phase) * np.exp(-t / 0.28)
        + 0.07 * np.sin(3 * phase) * np.exp(-t / 0.14)
    )
    env = (1.0 - np.exp(-t * 110.0)) * np.exp(-t / 0.52)
    env *= np.minimum(1.0, np.maximum(0.0, (length - t) / 0.05))
    return lp(sig * env, 480.0)


def jazz_pluck(midi: float, length: float = 0.75):
    n = round(length * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    sig = np.zeros(n)
    for h in range(1, 6):
        sig += (1.0 / (h ** 1.65)) * np.sin(2 * np.pi * f * h * t) * np.exp(-t / (0.38 / (h ** 0.5)))
    env = (1.0 - np.exp(-t * 450.0)) * np.minimum(1.0, np.maximum(0.0, (length - t) / 0.06))
    return lp(sig * env, 2900.0)


def warm_pad_chord(midis, length: float = 4.5):
    n = round(length * SR)
    t = np.arange(n) / SR
    l_ch = np.zeros(n)
    r_ch = np.zeros(n)
    env = np.minimum(1.0, t / 0.65) * np.minimum(1.0, np.maximum(0.0, (length - t) / 0.85))
    for idx, m in enumerate(midis):
        f = hz(m)
        vib = 0.0022 * np.sin(2 * np.pi * (3.6 + 0.3 * idx) * t + idx)
        s1 = np.sin(2 * np.pi * f * (1.0 + vib) * t) + 0.25 * np.sin(2 * np.pi * f * 2.002 * t)
        s2 = np.sin(2 * np.pi * f * 1.003 * (1.0 - vib) * t + 0.7) + 0.25 * np.sin(2 * np.pi * f * 1.998 * t)
        l_ch += s1 / len(midis)
        r_ch += s2 / len(midis)
    return lp(l_ch * env, 1750.0), lp(r_ch * env, 1750.0)


def soft_kick():
    n = round(0.26 * SR)
    t = np.arange(n) / SR
    freq_env = 49.0 + 78.0 * np.exp(-t * 38.0)
    phase = 2 * np.pi * np.cumsum(freq_env) / SR
    click = bp(RNG.normal(size=n), 600, 2400) * np.exp(-t * 140.0) * 0.14
    body = np.sin(phase) * np.exp(-t * 14.5) * (1.0 - np.exp(-t * 900.0))
    return lp(body + click, 220.0)


def wood_rimshot():
    n = round(0.11 * SR)
    t = np.arange(n) / SR
    wood = (
        np.sin(2 * np.pi * 430.0 * t) * np.exp(-t * 68.0)
        + 0.65 * np.sin(2 * np.pi * 1190.0 * t) * np.exp(-t * 110.0)
    )
    snap = bp(RNG.normal(size=n), 1400, 6200) * np.exp(-t * 95.0) * 0.55
    return (wood * 0.65 + snap) * (1.0 - np.exp(-t * 1500.0))


def brush_hat(open_hat: bool = False):
    dur = 0.16 if open_hat else 0.055
    n = round(dur * SR)
    t = np.arange(n) / SR
    noise = bp(RNG.normal(size=n), 4800, 12800)
    decay = 18.0 if open_hat else 68.0
    return noise * (1.0 - np.exp(-t * 1600.0)) * np.exp(-t * decay)


# ---------------- Tactile Foley & Spatial Transition SFX ----------------

def stereo_whoosh(duration: float, f0: float, f1: float, pan0: float = -0.6, pan1: float = 0.6):
    n = round(duration * SR)
    t = np.arange(n) / SR
    u = t / max(duration, 1e-4)
    # Smooth bell envelope peaking at 45%
    env = np.sin(np.pi * (u ** 0.85)) ** 1.6
    raw = RNG.normal(size=n)
    low = bp(raw, min(f0, f1) * 0.6, min(f0, f1) * 1.8)
    high = bp(raw, max(f0, f1) * 0.7, min(max(f0, f1) * 2.2, 8500))
    sweep = (1.0 - u) * low + u * high if f1 >= f0 else u * low + (1.0 - u) * high
    sub = np.sin(2 * np.pi * (55.0 + 65.0 * env) * t) * env * 0.28
    sig = (sweep * 0.75 + sub) * env
    pans = pan0 + (pan1 - pan0) * u
    angles = (pans + 1.0) * (np.pi / 4.0)
    return sig * np.cos(angles), sig * np.sin(angles)


def pull_chain_lamp_click():
    # Crisp two-part pull-chain click-clack + warm filament bloom
    n = round(0.45 * SR)
    t = np.arange(n) / SR
    c1 = bp(RNG.normal(size=n), 1800, 7500) * np.exp(-t * 190.0)
    t2 = np.maximum(0.0, t - 0.065)
    mask2 = (t >= 0.065).astype(float)
    c2 = bp(RNG.normal(size=n), 2400, 8800) * np.exp(-t2 * 160.0) * mask2 * 0.85
    metal = np.sin(2 * np.pi * 2680.0 * t) * np.exp(-t * 85.0) * 0.25
    bloom = (
        np.sin(2 * np.pi * 220.0 * t2) + 0.4 * np.sin(2 * np.pi * 440.0 * t2)
    ) * mask2 * (1.0 - np.exp(-t2 * 35.0)) * np.exp(-t2 * 6.5) * 0.25
    return c1 * 0.7 + c2 * 0.7 + metal + bloom


def bubble_pop(pitch: float = 680.0):
    n = round(0.09 * SR)
    t = np.arange(n) / SR
    f = pitch * (0.72 + 0.55 * (1.0 - np.exp(-t * 55.0)))
    phase = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(phase) * (1.0 - np.exp(-t * 500.0)) * np.exp(-t * 42.0)


def magnetic_snap(pitch: float = 1450.0):
    n = round(0.055 * SR)
    t = np.arange(n) / SR
    click = bp(RNG.normal(size=n), 1800, 7200) * np.exp(-t * 240.0) * 0.65
    tone = np.sin(2 * np.pi * pitch * t) * np.exp(-t * 120.0) * 0.45
    thump = np.sin(2 * np.pi * 190.0 * t) * np.exp(-t * 95.0) * 0.35
    return click + tone + thump


def merchant_order_bell():
    # Crisp, pleasant two-note warm bell (C6 -> G6)
    n = round(0.85 * SR)
    t = np.arange(n) / SR
    b1 = (
        np.sin(2 * np.pi * hz(84) * t) * np.exp(-t / 0.32)
        + 0.3 * np.sin(2 * np.pi * hz(96) * t) * np.exp(-t / 0.12)
    ) * (1.0 - np.exp(-t * 600.0))
    t2 = np.maximum(0.0, t - 0.115)
    m2 = (t >= 0.115).astype(float)
    b2 = (
        np.sin(2 * np.pi * hz(91) * t2) * np.exp(-t2 / 0.42)
        + 0.28 * np.sin(2 * np.pi * hz(103) * t2) * np.exp(-t2 / 0.14)
    ) * (1.0 - np.exp(-t2 * 600.0)) * m2
    return (b1 * 0.65 + b2 * 0.78) * 0.55


def wok_flame_and_sizzle(duration: float = 3.7):
    n = round(duration * SR)
    t = np.arange(n) / SR
    # Initial flame whoosh burst (low-mid turbulent rush)
    rush_env = (1.0 - np.exp(-t * 28.0)) * np.exp(-t / 0.65)
    flame = bp(RNG.normal(size=n), 75, 420) * rush_env * 1.15
    # Continuous warm sizzle bed + Poisson oil crackles
    bed_env = np.minimum(1.0, t / 0.18) * np.minimum(1.0, np.maximum(0.0, (duration - t) / 0.35))
    hiss = bp(RNG.normal(size=n), 2200, 9500) * 0.22 * bed_env
    crackles = np.zeros(n)
    n_sparks = int(duration * 68)
    positions = RNG.integers(0, max(1, n - 400), size=n_sparks)
    for p in positions:
        clen = int(RNG.integers(90, 320))
        ct = np.arange(clen) / SR
        cf = float(RNG.uniform(2800, 7800))
        crackles[p : p + clen] += (
            np.sin(2 * np.pi * cf * ct) * np.exp(-ct * 180.0) * float(RNG.uniform(0.15, 0.55))
        )
    crackles *= bed_env
    sig_l = flame + hiss + crackles
    sig_r = flame * 0.95 + bp(RNG.normal(size=n), 2200, 9500) * 0.22 * bed_env + np.roll(crackles, 19) * 0.9
    return sig_l, sig_r


def split_flap_cascade():
    # 12 rapid mechanical card flips locking in 4-8-2-9
    total = round(0.95 * SR)
    out = np.zeros(total)
    times = [0.0, 0.055, 0.11, 0.165, 0.225, 0.285, 0.35, 0.42, 0.495, 0.58, 0.675, 0.78]
    for idx, tau in enumerate(times):
        s = round(tau * SR)
        clen = round(0.045 * SR)
        if s + clen >= total:
            break
        ct = np.arange(clen) / SR
        accent = 1.25 if idx in (2, 5, 8, 11) else 0.75
        flap = (
            bp(RNG.normal(size=clen), 1200, 5800) * np.exp(-ct * 210.0) * 0.75
            + np.sin(2 * np.pi * (820 + idx * 28) * ct) * np.exp(-ct * 130.0) * 0.38
        ) * accent
        out[s : s + clen] += flap
    return out


def crystalline_chime(midi_notes):
    n = round(1.25 * SR)
    t = np.arange(n) / SR
    l_ch = np.zeros(n)
    r_ch = np.zeros(n)
    for i, m in enumerate(midi_notes):
        delay = i * 0.045
        td = np.maximum(0.0, t - delay)
        mask = (t >= delay).astype(float)
        f = hz(m)
        tone = (
            np.sin(2 * np.pi * f * td) * np.exp(-td / 0.55)
            + 0.22 * np.sin(2 * np.pi * f * 2.0 * td) * np.exp(-td / 0.22)
        ) * (1.0 - np.exp(-td * 500.0)) * mask
        pan = -0.35 + 0.35 * i
        ang = (pan + 1.0) * (np.pi / 4.0)
        l_ch += tone * np.cos(ang)
        r_ch += tone * np.sin(ang)
    return l_ch * 0.38, r_ch * 0.38


# ---------------- Compose Full 48.000s Score & Foley ----------------

cues_log = []

# 1. Intro [0.00s - 6.20s]: Warm mystery Rhodes -> Cold radar suspense
l, r = rhodes_note(65, 2.3, 0.7)  # F4
add_stereo(l, r, 0.08, 0.24)
l, r = rhodes_note(69, 2.1, 0.65)  # A4
add_stereo(l, r, 0.22, 0.20)
l, r = rhodes_note(72, 2.0, 0.65)  # C5
add_stereo(l, r, 0.38, 0.19)
add_mono(jazz_pluck(76, 1.1), 0.95, 0.16, 0.22)  # E5

# Suspended uncertain chord when bowl morphs into empty night map (2.35s)
for idx, m in enumerate([50, 57, 60, 64, 67]):  # Dm9 cool suspense
    l, r = rhodes_note(m, 2.4, 0.58)
    add_stereo(l, r, 2.35 + idx * 0.04, 0.14)

# 2. Main Neo-Soul / Jazz-Hop Groove [6.20s - 43.00s]
# 4-bar loop = 16 beats (~8.571s) starting right on the lamp ignition at 6.20s!
GROOVE_START = 6.20
# Lush Neo-Soul progression: Fmaj9 | E7alt -> Am9 | Dm9 | Gm9 -> C13sus4
BAR_CHORDS = [
    (41, [60, 64, 67, 69, 74]),   # Fmaj9 (F2, C4 E4 G4 A4 D5)
    (40, [59, 62, 68, 71]),       # E7#9/G# feel -> leads to Am9
    (45, [60, 64, 67, 71, 74]),   # Am9
    (38, [57, 60, 64, 69, 72]),   # Dm9
    (43, [58, 62, 65, 69, 72]),   # Gm9
    (36, [58, 62, 65, 69, 74]),   # C13sus4
]

total_bars = 17  # Covers 6.20s -> ~42.6s (2 beats per chord change or 4 beats)
t_cursor = GROOVE_START
bar_idx = 0
while t_cursor < 42.4:
    root_midi, voicing = BAR_CHORDS[bar_idx % len(BAR_CHORDS)]
    bar_len = 2 * BEAT if (bar_idx % 3 in (1, 2)) else 4 * BEAT
    if t_cursor + bar_len > 42.7:
        bar_len = max(BEAT, 42.7 - t_cursor)

    # Energy contour: lighter during wok fire (21.5-25.0s) so sizzle shines, fuller in Ecosystem (33.2-42.4s)
    in_wok = 21.3 <= t_cursor <= 25.0
    in_eco = t_cursor >= 33.2
    chord_gain = 0.13 if in_wok else (0.175 if in_eco else 0.155)

    for j, note in enumerate(voicing):
        l, r = rhodes_note(note, bar_len * 0.95, 0.82)
        add_stereo(l, r, t_cursor + j * 0.018, chord_gain)

    if in_eco:
        pl, pr = warm_pad_chord(voicing[:4], bar_len * 1.05)
        add_stereo(pl, pr, t_cursor, 0.13)

    # Pocket Bass + Brushed Jazz-Hop Drums per beat
    n_beats = max(1, round(bar_len / BEAT))
    for b in range(n_beats):
        bt = t_cursor + b * BEAT
        if bt >= 42.5:
            break
        # Kick on beat 0 and syncopated 2.5
        add_mono(soft_kick(), bt, 0.34 if not in_wok else 0.24, 0.0)
        if b == 1 and n_beats >= 3:
            add_mono(soft_kick(), bt + 0.54 * BEAT, 0.22, 0.0)
        # Crisp wood rimshot on backbeats (1, 3)
        if b % 2 == 1:
            add_mono(wood_rimshot(), bt, 0.24 if not in_wok else 0.16, -0.08)
        # Swung 8th/16th brush hats
        add_mono(brush_hat(False), bt, 0.12, 0.22)
        add_mono(brush_hat(b == n_beats - 1), bt + 0.58 * BEAT, 0.095, -0.22)

        # Bassline
        if b == 0:
            add_mono(warm_bass(root_midi, BEAT * 0.92), bt, 0.30, 0.0)
        elif b == 2:
            add_mono(warm_bass(root_midi + 7, BEAT * 0.75), bt, 0.23, 0.0)
        elif b == n_beats - 1:
            add_mono(warm_bass(root_midi + 12, BEAT * 0.45), bt + 0.56 * BEAT, 0.18, 0.0)

        # Playful melodic jazz plucks in gaps
        if not in_wok and (bar_idx + b) % 3 == 0:
            mel = voicing[(bar_idx + b) % len(voicing)] + 12
            add_mono(jazz_pluck(mel, 0.48), bt + 0.58 * BEAT, 0.115, 0.3 * ((b % 2) * 2 - 1))

    t_cursor += bar_len
    bar_idx += 1

# 3. Finale Resolution [42.60s - 48.00s]: Warm Lantern Fmaj9(add13)
finale_voicing = [53, 60, 64, 67, 69, 74, 79]  # F3 C4 E4 G4 A4 D5 G5
pl, pr = warm_pad_chord(finale_voicing[:5], 4.6)
add_stereo(pl, pr, 42.75, 0.16)
for idx, m in enumerate(finale_voicing):
    l, r = rhodes_note(m, 4.3, 0.80)
    add_stereo(l, r, 42.85 + idx * 0.065, 0.145)
add_mono(warm_bass(41, 3.6), 42.85, 0.28, 0.0)

# ---------------- Exact Transition & Interaction Foley SFX ----------------

def log_sfx(t_sec: float, label: str):
    cues_log.append({"time": round(t_sec, 3), "cue": label})

# Scene 1: Circle morph plunge (2.10s) + chat bubbles (2.85s, 3.25s) + whip-pan (4.30s)
wl, wr = stereo_whoosh(0.55, 900, 260, 0.0, 0.0)
add_stereo(wl, wr, 2.08, 0.42); log_sfx(2.08, "bowl-to-map-circle-morph")
add_mono(bubble_pop(620), 2.85, 0.28, -0.28); log_sfx(2.85, "empty-chat-pill-1")
add_mono(bubble_pop(510), 3.25, 0.28, 0.28); log_sfx(3.25, "empty-chat-pill-2")
wl, wr = stereo_whoosh(0.42, 420, 1450, 0.75, -0.75)
add_stereo(wl, wr, 4.28, 0.46); log_sfx(4.28, "whip-pan-street-hook")

# Scene 2: Pull-chain lamp ON (6.20s) + Zoom-out to toggle switch (8.10s) + Card snaps
add_mono(pull_chain_lamp_click(), 6.18, 0.52, 0.05); log_sfx(6.18, "stall-lamp-ignition-click")
wl, wr = stereo_whoosh(0.52, 1300, 380, 0.2, -0.15)
add_stereo(wl, wr, 8.10, 0.42); log_sfx(8.10, "lamp-to-toggle-zoomout")
add_mono(magnetic_snap(1580), 8.56, 0.38, 0.12); log_sfx(8.56, "merchant-toggle-on")
add_mono(magnetic_snap(1320), 9.60, 0.26, -0.15)
cl, cr = crystalline_chime([77, 81])
add_stereo(cl, cr, 9.62, 0.42); log_sfx(9.62, "live-green-freshness-badge")

# Scene 3: Portal dive through pin (14.00s) + Per-item flavor snaps + Flying ticket (18.10s) + Wok Fire (21.20s)
add_mono(magnetic_snap(1720), 13.96, 0.34, 0.0)
wl, wr = stereo_whoosh(0.56, 360, 1550, 0.0, 0.0)
add_stereo(wl, wr, 14.00, 0.46); log_sfx(14.00, "pin-portal-dive")
add_mono(magnetic_snap(1420), 15.40, 0.32, -0.25); log_sfx(15.40, "item-1-flavor-snap")
add_mono(magnetic_snap(1560), 16.30, 0.32, -0.20); log_sfx(16.30, "item-2-flavor-snap")
add_mono(magnetic_snap(1180), 17.40, 0.38, -0.15); log_sfx(17.40, "submit-order-press")
wl, wr = stereo_whoosh(0.48, 520, 1680, -0.65, 0.65)
add_stereo(wl, wr, 18.08, 0.48); log_sfx(18.08, "flying-ticket-whip")
add_mono(merchant_order_bell(), 18.55, 0.56, 0.32); log_sfx(18.55, "merchant-new-order-bell")
add_mono(magnetic_snap(1240), 20.40, 0.38, 0.25); log_sfx(20.40, "merchant-accept-cooking")
fl, fr = wok_flame_and_sizzle(3.85)
add_stereo(fl, fr, 21.18, 0.48); log_sfx(21.18, "button-ignition-teppanyaki-sizzle")
cl, cr = crystalline_chime([76, 84])
add_stereo(cl, cr, 23.80, 0.42); log_sfx(23.80, "order-ready-snap")

# Scene 4: 3D Box-to-Pass Flip (25.20s) + Split-flap 4829 (25.75s) + Diagonal Dual-Track (28.40s)
add_mono(magnetic_snap(980), 25.18, 0.42, 0.0)
wl, wr = stereo_whoosh(0.46, 780, 460, 0.4, -0.4)
add_stereo(wl, wr, 25.20, 0.42); log_sfx(25.20, "3d-card-flip-pickup")
add_mono(split_flap_cascade(), 25.72, 0.44, 0.0); log_sfx(25.72, "split-flap-code-4829")
wl, wr = stereo_whoosh(0.48, 440, 1280, -0.5, 0.5)
add_stereo(wl, wr, 28.40, 0.40); log_sfx(28.40, "diagonal-dual-track-split")
cl, cr = crystalline_chime([79, 86])
add_stereo(cl, cr, 30.10, 0.44); log_sfx(30.10, "dual-handoff-verified")

# Scene 5: 10x Skyward Pullback (33.20s) + 3 Governance Pillars (34.60s, 36.00s, 37.40s)
wl, wr = stereo_whoosh(0.68, 240, 1380, 0.0, 0.0)
add_stereo(wl, wr, 33.18, 0.52); log_sfx(33.18, "ecosystem-10x-pullback")
cl, cr = crystalline_chime([77, 81, 84])
add_stereo(cl, cr, 34.60, 0.46); log_sfx(34.60, "pillar-1-tiered-license")
cl, cr = crystalline_chime([79, 84, 88])
add_stereo(cl, cr, 36.00, 0.46); log_sfx(36.00, "pillar-2-direct-wechat-pay")
cl, cr = crystalline_chime([81, 86, 93])
add_stereo(cl, cr, 37.40, 0.48); log_sfx(37.40, "pillar-3-freshness-risk-control")

# Scene 6: Golden Coalescence into Brand Lantern (42.40s)
wl, wr = stereo_whoosh(0.68, 1200, 340, -0.45, 0.45)
add_stereo(wl, wr, 42.38, 0.42); log_sfx(42.38, "lantern-firefly-coalescence")
cl, cr = crystalline_chime([77, 84, 89])
add_stereo(cl, cr, 43.10, 0.42); log_sfx(43.10, "brand-lockup-shimmer")

# ---------------- Final Mastering & Fade ----------------

# Clean 20ms fade-in and 1.35s cosine fade-out to silence
fade_in = np.minimum(1.0, np.arange(N_SAMPLES) / (0.025 * SR))
fade_out = np.minimum(1.0, np.maximum(0.0, (N_SAMPLES - 1 - np.arange(N_SAMPLES)) / (1.35 * SR)))
fade_out = 0.5 - 0.5 * np.cos(np.pi * fade_out)
MIX *= (fade_in * fade_out)[:, None]

# Gentle warm bus soft-clip + calibrate RMS/Peak for ~ -16.8 LUFS & TP <= -2.0 dBTP
peak_raw = float(np.max(np.abs(MIX)))
if peak_raw > 0:
    MIX = np.tanh(MIX / peak_raw * 1.35) / np.tanh(1.35)

rms = float(np.sqrt(np.mean(MIX ** 2)))
target_rms = 10.0 ** (-17.2 / 20.0)
MIX *= (target_rms / max(rms, 1e-6))
peak_after = float(np.max(np.abs(MIX)))
max_allowed = 10.0 ** (-2.2 / 20.0)  # ~0.776 (-2.2 dBFS)
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
    "title": "一镜穿烟火 · Original Jazz-Hop & Tactile Foley Score",
    "duration_seconds": DURATION,
    "sample_rate": SR,
    "channels": 2,
    "bpm": BPM,
    "style": "112 BPM Warm Neo-Soul / Jazz-Hop + Frame-Accurate Foley SFX",
    "peak_dbfs": round(20.0 * np.log10(float(np.max(np.abs(MIX))) + 1e-12), 2),
    "rms_dbfs": round(20.0 * np.log10(float(np.sqrt(np.mean(MIX ** 2))) + 1e-12), 2),
    "cues": cues_log,
}
(audio_dir / "soundtrack.json").write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Wrote {wav_path} ({len(pcm16)} samples, peak={meta_out['peak_dbfs']} dBFS, rms={meta_out['rms_dbfs']} dBFS)")
