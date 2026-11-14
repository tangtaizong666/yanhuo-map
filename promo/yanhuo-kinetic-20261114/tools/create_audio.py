import json
import subprocess
import wave
from pathlib import Path
import numpy as np
from scipy.signal import butter, lfilter

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "audio"
SR = 48000
DURATION = 38.0
N = int(SR * DURATION)
BPM = 120.0
BEAT = 60.0 / BPM  # 0.500s exact


def lp(sig: np.ndarray, cutoff: float, order: int = 3) -> np.ndarray:
    b, a = butter(order, min(cutoff / (SR * 0.5), 0.98), btype="low")
    return lfilter(b, a, sig)


def hp(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    b, a = butter(order, min(cutoff / (SR * 0.5), 0.98), btype="high")
    return lfilter(b, a, sig)


def bp(sig: np.ndarray, lo: float, hi: float, order: int = 2) -> np.ndarray:
    b, a = butter(order, [lo / (SR * 0.5), min(hi / (SR * 0.5), 0.98)], btype="band")
    return lfilter(b, a, sig)


def add_stereo(mix_l: np.ndarray, mix_r: np.ndarray, t0: float, sig: np.ndarray, gain_l: float = 1.0, gain_r: float = 1.0) -> None:
    i0 = int(round(t0 * SR))
    if i0 >= N or i0 + len(sig) <= 0:
        return
    s0 = max(0, -i0)
    d0 = max(0, i0)
    length = min(len(sig) - s0, N - d0)
    if length <= 0:
        return
    mix_l[d0:d0 + length] += sig[s0:s0 + length] * gain_l
    mix_r[d0:d0 + length] += sig[s0:s0 + length] * gain_r


def synth_kick(accent: float = 1.0) -> np.ndarray:
    dur = 0.24
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    freq = 48.0 + 135.0 * np.exp(-t * 36.0)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 13.5)
    click = bp(np.random.default_rng(7).standard_normal(len(t)), 900, 4200) * np.exp(-t * 95.0) * 0.28
    return np.tanh((body + click) * 1.45 * accent) * 0.62


def synth_snare(rng: np.random.Generator, rim: bool = False) -> np.ndarray:
    dur = 0.16 if not rim else 0.09
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    if rim:
        tone = np.sin(2 * np.pi * 880 * t) * np.exp(-t * 65.0) * 0.45 + np.sin(2 * np.pi * 1680 * t) * np.exp(-t * 85.0) * 0.25
        snap = bp(rng.standard_normal(len(t)), 1400, 6500) * np.exp(-t * 55.0) * 0.45
        return (tone + snap) * 0.46
    tone = np.sin(2 * np.pi * (185 + 90 * np.exp(-t * 45)) * t) * np.exp(-t * 28.0) * 0.45
    noise = bp(rng.standard_normal(len(t)), 1100, 8800) * np.exp(-t * 22.0) * 0.65
    return np.tanh((tone + noise) * 1.3) * 0.50


def synth_hat(rng: np.random.Generator, open_hat: bool = False) -> np.ndarray:
    dur = 0.18 if open_hat else 0.055
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    decay = 16.0 if open_hat else 68.0
    n = hp(rng.standard_normal(len(t)), 6800) * np.exp(-t * decay)
    return n * (0.20 if open_hat else 0.13)


def synth_funk_bass(freq: float, dur: float, slap: bool = False) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    env = (1.0 - np.exp(-t * 160.0)) * np.exp(-t * (4.8 if not slap else 7.2))
    env[-min(240, len(t)):] *= np.linspace(1, 0, min(240, len(t)))
    sig = (
        0.65 * np.sin(2 * np.pi * freq * t)
        + 0.28 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 8.0)
        + (0.20 if slap else 0.08) * np.sin(2 * np.pi * freq * 3 * t) * np.exp(-t * 18.0)
    )
    return lp(np.tanh(sig * 1.5), 1400 if slap else 750) * env * 0.36


def synth_clav_stab(freqs: list[float], dur: float = 0.18) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    env = (1.0 - np.exp(-t * 220.0)) * np.exp(-t * 10.5)
    out = np.zeros_like(t)
    for f in freqs:
        out += 0.55 * np.sin(2 * np.pi * f * t) + 0.30 * np.sin(2 * np.pi * f * 2 * t) + 0.15 * np.sin(2 * np.pi * f * 3 * t)
    return bp(out / max(len(freqs), 1), 320, 4600) * env * 0.22


def synth_warm_pad(freqs: list[float], dur: float) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    fade_in = np.clip(t / 0.45, 0, 1)
    fade_out = np.clip((dur - t) / 1.15, 0, 1)
    env = fade_in * fade_out
    l = np.zeros_like(t)
    r = np.zeros_like(t)
    for idx, f in enumerate(freqs):
        det = 1.0018 if idx % 2 == 0 else 0.9982
        l += np.sin(2 * np.pi * f * t) * 0.6 + np.sin(2 * np.pi * f * 2.0 * t) * 0.14
        r += np.sin(2 * np.pi * f * det * t + 0.3) * 0.6 + np.sin(2 * np.pi * f * 2.0 * det * t) * 0.14
    l = lp(l / len(freqs), 2200) * env * 0.20
    r = lp(r / len(freqs), 2200) * env * 0.20
    return l, r


def foley_wok_sizzle(rng: np.random.Generator, dur: float, clang: bool = True) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    env = np.clip(t / 0.03, 0, 1) * np.clip((dur - t) / 0.12, 0, 1)
    mod = 0.72 + 0.28 * np.sin(2 * np.pi * 23 * t) * np.sin(2 * np.pi * 7.5 * t)
    sizzle = bp(rng.standard_normal(len(t)), 2200, 11500) * env * mod * 0.22
    if clang:
        ct = t[:min(len(t), int(SR * 0.22))]
        metal = (
            np.sin(2 * np.pi * 1180 * ct) * 0.40
            + np.sin(2 * np.pi * 2490 * ct) * 0.32
            + np.sin(2 * np.pi * 3870 * ct) * 0.20
        ) * np.exp(-ct * 26.0) * 0.26
        sizzle[:len(ct)] += metal
    return sizzle


def foley_whip_pan(rng: np.random.Generator, dur: float = 0.24, left_to_right: bool = True) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    u = t / dur
    env = np.sin(np.pi * u) ** 1.6
    # Swept bandpass swoosh
    noise = rng.standard_normal(len(t))
    low_band = bp(noise, 220, 950)
    mid_band = bp(noise, 900, 3400)
    hi_band = bp(noise, 2800, 7500)
    sig = ((1 - u) * low_band + np.sin(np.pi * u) * mid_band * 1.4 + u * hi_band * 0.8) * env * 0.42
    pan = u if left_to_right else (1.0 - u)
    return sig * (1.0 - 0.65 * pan), sig * (0.35 + 0.65 * pan)


def foley_woodblock_tok() -> np.ndarray:
    dur = 0.085
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    sig = (np.sin(2 * np.pi * 790 * t) * 0.7 + np.sin(2 * np.pi * 1210 * t) * 0.35) * np.exp(-t * 58.0)
    return sig * 0.46


def foley_stamp_thud(rng: np.random.Generator) -> np.ndarray:
    dur = 0.14
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    thump = np.sin(2 * np.pi * (95 + 120 * np.exp(-t * 55)) * t) * np.exp(-t * 26.0) * 0.55
    paper = bp(rng.standard_normal(len(t)), 800, 4800) * np.exp(-t * 48.0) * 0.35
    return np.tanh((thump + paper) * 1.4) * 0.48


def foley_thermal_print(rng: np.random.Generator, dur: float = 0.55) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    pulse = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 165 * t))
    buzz = bp(pulse * rng.standard_normal(len(t)), 1600, 6200) * np.clip((dur - t) / 0.08, 0, 1)
    return buzz * 0.16


def foley_paper_rip(rng: np.random.Generator, dur: float = 0.28) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    u = t / dur
    crackle = (rng.random(len(t)) > 0.72).astype(float) * rng.standard_normal(len(t))
    rip = bp(crackle + 0.5 * rng.standard_normal(len(t)), 1500, 9500) * np.sin(np.pi * u) ** 0.7 * 0.42
    return rip * (0.9 - 0.3 * u), rip * (0.6 + 0.4 * u)


def foley_verify_chime(root_hz: float = 1046.5) -> np.ndarray:
    dur = 0.48
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    n1 = (np.sin(2 * np.pi * root_hz * t) + 0.25 * np.sin(2 * np.pi * root_hz * 2 * t)) * np.exp(-t * 12.0)
    t2 = np.maximum(0.0, t - 0.085)
    gate2 = (t >= 0.085).astype(float)
    f2 = root_hz * 1.5  # crisp perfect fifth above!
    n2 = gate2 * (np.sin(2 * np.pi * f2 * t2) + 0.30 * np.sin(2 * np.pi * f2 * 2 * t2)) * np.exp(-t2 * 7.5)
    return (n1 * 0.34 + n2 * 0.42) * 0.52


def foley_marble_roll(rng: np.random.Generator, dur: float = 1.75) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    u = t / dur
    # Rolling resonance around 650-1350Hz rising smoothly in speed
    roll_mod = 0.65 + 0.35 * np.sin(2 * np.pi * (14 + 18 * u) * t)
    glass = bp(rng.standard_normal(len(t)), 580, 2100) * roll_mod * np.sin(np.pi * u) * 0.22
    ring = np.sin(2 * np.pi * (880 + 260 * u) * t) * roll_mod * np.sin(np.pi * u) * 0.06
    sig = glass + ring
    return sig * (1.0 - 0.75 * u), sig * (0.25 + 0.75 * u)


def main() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261114)
    mix_l = np.zeros(N, dtype=np.float64)
    mix_r = np.zeros(N, dtype=np.float64)

    kick = synth_kick(1.0)
    kick_punch = synth_kick(1.18)

    # --- ACT I: 0.00s - 2.50s Craving micro-cuts ---
    add_stereo(mix_l, mix_r, 0.00, kick_punch, 1.0, 1.0)
    add_stereo(mix_l, mix_r, 0.00, foley_wok_sizzle(rng, 0.72, clang=True), 1.0, 0.85)
    add_stereo(mix_l, mix_r, 0.75, synth_snare(rng, rim=False), 0.95, 1.0)
    add_stereo(mix_l, mix_r, 0.75, foley_wok_sizzle(rng, 0.70, clang=True), 0.85, 1.0)
    add_stereo(mix_l, mix_r, 1.50, kick_punch, 1.0, 1.0)
    add_stereo(mix_l, mix_r, 1.50, synth_hat(rng, open_hat=True), 0.85, 1.0)
    # Sprint steps 2.00 - 2.46s
    for idx, st in enumerate([2.00, 2.17, 2.31, 2.43]):
        step = foley_stamp_thud(rng) * (0.45 + 0.1 * idx)
        add_stereo(mix_l, mix_r, st, step, 0.8 if idx % 2 == 0 else 0.5, 0.5 if idx % 2 == 0 else 0.8)
    wl, wr = foley_whip_pan(rng, 0.24, left_to_right=True)
    add_stereo(mix_l, mix_r, 2.42, wl, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 2.42, wr, 0.0, 1.0)

    # --- 2.50s - 4.25s: Deadpan Empty Gate Silence! ---
    breeze_t = np.linspace(0, 1.70, int(SR * 1.70), endpoint=False)
    breeze = bp(rng.standard_normal(len(breeze_t)), 280, 1100) * np.sin(np.pi * breeze_t / 1.70) * 0.035
    add_stereo(mix_l, mix_r, 2.55, breeze, 0.9, 1.0)
    add_stereo(mix_l, mix_r, 2.80, foley_woodblock_tok(), 0.95, 0.95)
    add_stereo(mix_l, mix_r, 3.25, foley_stamp_thud(rng), 1.0, 1.0)
    add_stereo(mix_l, mix_r, 3.25, synth_funk_bass(98.0, 0.22, slap=True) * 0.75, 1.0, 1.0)

    # --- 4.25s - 6.00s: Whip-in 3D Phone & Switch ---
    wl2, wr2 = foley_whip_pan(rng, 0.24, left_to_right=False)
    add_stereo(mix_l, mix_r, 4.22, wl2, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 4.22, wr2, 0.0, 1.0)
    add_stereo(mix_l, mix_r, 4.80, synth_snare(rng, rim=True), 0.9, 0.9)
    add_stereo(mix_l, mix_r, 5.25, foley_verify_chime(1174.66) * 0.65, 0.85, 1.0)
    # Dive whoosh into 6.00s drop
    wl3, wr3 = foley_whip_pan(rng, 0.34, left_to_right=True)
    add_stereo(mix_l, mix_r, 5.80, wl3 * 1.15, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 5.80, wr3 * 1.15, 0.0, 1.0)

    # --- MAIN 120 BPM FUNKY PERCUSSIVE GROOVE: 6.00s (Beat 12) to 34.00s (Beat 68) ---
    # Funk bass pattern in D Dorian / G Mixolydian (warm, upbeat, clever, non-cheesy!)
    bass_riff = [
        (0.0, 73.42, 0.36, True),   # D2
        (0.75, 73.42, 0.20, False),
        (1.0, 87.31, 0.24, True),   # F2
        (1.5, 98.00, 0.38, False),  # G2
        (2.0, 110.00, 0.34, True),  # A2
        (2.75, 98.00, 0.20, False),
        (3.0, 130.81, 0.24, True),  # C3
        (3.5, 110.00, 0.34, False), # A2
    ]
    chords = [
        [293.66, 349.23, 440.00, 523.25],  # Dm7
        [293.66, 392.00, 493.88, 587.33],  # G/D
    ]

    for bar in range(14):  # 14 bars * 2.0s = 28.0s -> 6.00s to 34.00s
        bar_t0 = 6.00 + bar * 2.0
        # Brief tasteful breather around paper rip (15.8-16.1s)
        for step16 in range(16):
            st = bar_t0 + step16 * 0.125
            if 15.82 <= st <= 16.05:
                continue
            # Kick on 0, 6, 8, 14 (classic syncopated funk pocket)
            if step16 in (0, 6, 8):
                add_stereo(mix_l, mix_r, st, kick, 1.0, 1.0)
            elif step16 == 14 and bar % 2 == 1:
                add_stereo(mix_l, mix_r, st, kick * 0.72, 1.0, 1.0)
            # Crisp snare / rim on 4 and 12
            if step16 in (4, 12):
                add_stereo(mix_l, mix_r, st, synth_snare(rng, rim=(bar in (4, 5))), 0.96, 1.0)
            # 16th hi-hats with groove swing/velocity
            if step16 % 2 == 0:
                add_stereo(mix_l, mix_r, st, synth_hat(rng, open_hat=(step16 == 10)), 0.85, 0.95)
            elif step16 in (3, 7, 11, 15):
                add_stereo(mix_l, mix_r, st + 0.012, synth_hat(rng, open_hat=False) * 0.55, 0.95, 0.80)

        # Bassline
        for beat_off, freq, dur, slap in bass_riff:
            bt = bar_t0 + beat_off * BEAT
            if bt < 33.8:
                add_stereo(mix_l, mix_r, bt, synth_funk_bass(freq, dur, slap=slap), 1.0, 1.0)

        # Clavinet stabs on off-beats
        chord = chords[bar % 2]
        for stab_beat in (0.5, 1.5, 2.5, 3.25):
            st = bar_t0 + stab_beat * BEAT
            if st < 33.8:
                pan_l = 0.85 if stab_beat in (0.5, 2.5) else 0.55
                pan_r = 0.55 if stab_beat in (0.5, 2.5) else 0.85
                add_stereo(mix_l, mix_r, st, synth_clav_stab(chord, 0.16), pan_l, pan_r)

    # --- SYNCHRONIZED STORY FOLEY CUES (6.00s - 38.00s) ---
    # Shot 08: Clockwork ticks 9.00 - 9.90s + glass shutter clack at 10.00s + reset ping at 10.75s
    for tk in np.arange(9.00, 9.95, 0.125):
        add_stereo(mix_l, mix_r, float(tk), foley_woodblock_tok() * 0.42, 0.8, 0.9)
    add_stereo(mix_l, mix_r, 10.00, foley_stamp_thud(rng) * 0.9, 1.0, 1.0)
    add_stereo(mix_l, mix_r, 10.75, foley_verify_chime(987.77) * 0.65, 0.9, 1.0)

    # Shot 09: Thermal receipt print 11.50s + 3 ink stamps (12.25s, 13.50s, 14.75s)
    add_stereo(mix_l, mix_r, 11.50, foley_thermal_print(rng, 0.62), 0.95, 0.95)
    for stamp_t in (12.25, 13.50, 14.75):
        add_stereo(mix_l, mix_r, stamp_t, foley_stamp_thud(rng) * 1.1, 1.0, 1.0)

    # Shot 10: Serrated paper RIP at 16.00s + wok fire sizzle + merchant button chimes
    rl, rr = foley_paper_rip(rng, 0.30)
    add_stereo(mix_l, mix_r, 15.96, rl, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 15.96, rr, 0.0, 1.0)
    add_stereo(mix_l, mix_r, 16.15, foley_wok_sizzle(rng, 2.80, clang=True) * 0.72, 1.0, 0.45)
    add_stereo(mix_l, mix_r, 17.50, synth_snare(rng, rim=True) * 1.1, 0.5, 1.0)
    add_stereo(mix_l, mix_r, 18.75, foley_verify_chime(880.0) * 0.75, 0.55, 1.0)

    # Shot 11: Dual 3D phone whip + 4829 chime (21.00s) + handoff chime (22.75s)
    wl4, wr4 = foley_whip_pan(rng, 0.25, left_to_right=True)
    add_stereo(mix_l, mix_r, 19.95, wl4, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 19.95, wr4, 0.0, 1.0)
    add_stereo(mix_l, mix_r, 21.00, foley_verify_chime(1046.50), 1.0, 0.75)
    add_stereo(mix_l, mix_r, 22.75, foley_verify_chime(1174.66), 0.75, 1.0)

    # Shot 12: Tactile track gate unlock (25.50s) + stereo marble roll (26.20-27.95s) + pocket landing (28.00s)
    add_stereo(mix_l, mix_r, 25.50, foley_verify_chime(1318.51) * 0.65, 0.9, 0.9)
    ml, mr = foley_marble_roll(rng, 1.75)
    add_stereo(mix_l, mix_r, 26.20, ml, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 26.20, mr, 0.0, 1.0)
    add_stereo(mix_l, mix_r, 28.00, foley_stamp_thud(rng) * 0.95, 0.7, 1.0)
    add_stereo(mix_l, mix_r, 28.02, foley_verify_chime(1046.50) * 0.95, 0.8, 1.0)

    # Shot 13: 4 rapid sensory payoff hits (30.00s, 31.00s, 32.00s, 33.00s)
    for idx, pt in enumerate([30.00, 31.00, 32.00, 33.00]):
        add_stereo(mix_l, mix_r, pt, kick_punch, 1.0, 1.0)
        add_stereo(mix_l, mix_r, pt, synth_clav_stab([293.66, 440.0, 587.33, 739.99], 0.28) * 1.3, 1.0, 1.0)
        if idx in (0, 1):
            add_stereo(mix_l, mix_r, pt, foley_wok_sizzle(rng, 0.65, clang=(idx == 1)) * 0.75, 0.95, 0.95)

    # Shot 14: 34.00s - 38.00s Warm Resolving Dmaj9 / Gmaj9 Finale!
    wl5, wr5 = foley_whip_pan(rng, 0.32, left_to_right=False)
    add_stereo(mix_l, mix_r, 33.88, wl5, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 33.88, wr5, 0.0, 1.0)
    add_stereo(mix_l, mix_r, 34.00, kick_punch, 1.0, 1.0)
    add_stereo(mix_l, mix_r, 34.00, foley_verify_chime(1174.66) * 0.85, 0.95, 0.95)
    pad_l, pad_r = synth_warm_pad([146.83, 220.00, 293.66, 369.99, 440.00, 554.37], 3.85)
    add_stereo(mix_l, mix_r, 34.00, pad_l * 1.45, 1.0, 0.0)
    add_stereo(mix_l, mix_r, 34.00, pad_r * 1.45, 0.0, 1.0)

    # Clean master fade-out at 37.55 - 37.98s so tail is silent
    tail_start = int(37.55 * SR)
    tail_end = int(37.98 * SR)
    fade = np.linspace(1.0, 0.0, tail_end - tail_start)
    mix_l[tail_start:tail_end] *= fade
    mix_r[tail_start:tail_end] *= fade
    mix_l[tail_end:] = 0.0
    mix_r[tail_end:] = 0.0

    # Soft limiter & LUFS normalization to ~ -14.8 LUFS
    raw_wav = AUDIO_DIR / "_raw.wav"
    final_wav = AUDIO_DIR / "soundtrack.wav"

    peak = max(np.max(np.abs(mix_l)), np.max(np.abs(mix_r)), 1e-6)
    stereo = np.stack([mix_l / peak * 0.82, mix_r / peak * 0.82], axis=1)
    pcm = np.clip(stereo * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(raw_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())

    # Loudnorm via FFmpeg to exact broadcast -14.8 LUFS, -1.5 dBTP
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(raw_wav),
            "-af", "loudnorm=I=-14.8:TP=-1.8:LRA=9.0,apad=whole_dur=38.0",
            "-ar", "48000", "-ac", "2", "-t", "38.000",
            str(final_wav),
        ],
        check=True,
    )
    raw_wav.unlink(missing_ok=True)

    meta = {
        "title": "烟火地图 · 热油甩镜与触感秩序 (Original Synced Foley & 120BPM Funk Groove)",
        "author": "烟火地图项目原创程序化合成配乐与拟音",
        "license": "Project Original Procedural Audio (Zero third-party samples or copyrighted recordings)",
        "sample_rate_hz": SR,
        "channels": 2,
        "duration_seconds": DURATION,
        "bpm": BPM,
        "sync_markers_seconds": {
            "craving_cuts": [0.00, 0.75, 1.50, 2.00],
            "whip_to_empty_gate_silence": 2.50,
            "chalk_outline_tok": 2.80,
            "empty_stamp": 3.25,
            "phone_whip_in": 4.25,
            "groove_drop_3d_campus": 6.00,
            "freshness_shutter_clack": 10.00,
            "thermal_receipt_print": 11.50,
            "ink_stamps": [12.25, 13.50, 14.75],
            "serrated_paper_rip": 16.00,
            "pickup_4829_chime": 21.00,
            "handoff_gate_chime": 22.75,
            "direct_pay_marble_roll": [26.20, 28.00],
            "rapid_payoff_hits": [30.00, 31.00, 32.00, 33.00],
            "brand_resolution": 34.00,
        },
    }
    (AUDIO_DIR / "soundtrack.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Generated", final_wav)


if __name__ == "__main__":
    main()
