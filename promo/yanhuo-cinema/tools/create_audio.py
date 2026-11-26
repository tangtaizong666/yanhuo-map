"""Compose warm modern editorial cinema score, frame-locked foley SFX, and sidechain-ducked VO mix."""
import json
import subprocess
import wave
from pathlib import Path
import numpy as np
from scipy.signal import butter, filtfilt, lfilter

ROOT = Path(__file__).resolve().parents[1]
TIMELINE_PATH = ROOT / "assets" / "timeline.json"
AUDIO_DIR = ROOT / "audio"
SR = 48000


def lp(sig: np.ndarray, cutoff: float, order: int = 3) -> np.ndarray:
    b, a = butter(order, min(cutoff / (SR * 0.5), 0.96), btype="low")
    return lfilter(b, a, sig)


def hp(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    b, a = butter(order, min(cutoff / (SR * 0.5), 0.96), btype="high")
    return lfilter(b, a, sig)


def bp(sig: np.ndarray, lo: float, hi: float, order: int = 2) -> np.ndarray:
    b, a = butter(order, [max(lo / (SR * 0.5), 0.001), min(hi / (SR * 0.5), 0.96)], btype="band")
    return lfilter(b, a, sig)


def add_stereo(L: np.ndarray, R: np.ndarray, t0: float, sig_l: np.ndarray, sig_r: np.ndarray | None = None, gain: float = 1.0, pan: float = 0.0) -> None:
    if sig_r is None:
        sig_r = sig_l
    gl = gain * np.cos((pan + 1.0) * np.pi * 0.25) * np.sqrt(2.0)
    gr = gain * np.sin((pan + 1.0) * np.pi * 0.25) * np.sqrt(2.0)
    i0 = int(round(t0 * SR))
    n_tot = len(L)
    if i0 >= n_tot or i0 + len(sig_l) <= 0:
        return
    s0 = max(0, -i0)
    d0 = max(0, i0)
    length = min(len(sig_l) - s0, n_tot - d0)
    if length <= 0:
        return
    L[d0:d0 + length] += sig_l[s0:s0 + length] * gl
    R[d0:d0 + length] += sig_r[s0:s0 + length] * gr


def read_wav_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32768.0
        if wf.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        return data


def synth_rhodes_chord(freqs: list[float], dur: float, vel: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Warm stereo electric piano (Rhodes tine + warm bell body + gentle tremolo)."""
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    l = np.zeros(n)
    r = np.zeros(n)
    env = (1.0 - np.exp(-t * 95.0)) * np.exp(-t * 0.72)
    fade_out = np.clip((dur - t) / min(0.45, dur * 0.35), 0.0, 1.0)
    env *= fade_out
    tine_env = (1.0 - np.exp(-t * 280.0)) * np.exp(-t * 14.0)
    trem_l = 1.0 + 0.06 * np.sin(2 * np.pi * 2.8 * t)
    trem_r = 1.0 + 0.06 * np.sin(2 * np.pi * 2.8 * t + np.pi * 0.75)

    for idx, f in enumerate(freqs):
        det = 1.0014 if idx % 2 == 0 else 0.9986
        body_l = 0.72 * np.sin(2 * np.pi * f * t) + 0.22 * np.sin(2 * np.pi * f * 2 * t) * np.exp(-t * 1.8)
        body_r = 0.72 * np.sin(2 * np.pi * f * det * t + 0.15) + 0.22 * np.sin(2 * np.pi * f * det * 2 * t) * np.exp(-t * 1.8)
        tine = 0.14 * np.sin(2 * np.pi * f * 7.0 * t) * tine_env
        spread = (idx - (len(freqs) - 1) * 0.5) / max(len(freqs), 1)
        l += (body_l + tine) * (1.0 - 0.25 * spread)
        r += (body_r + tine) * (1.0 + 0.25 * spread)

    scale = vel * 0.26 / max(len(freqs) ** 0.7, 1.0)
    l = lp(l * env * trem_l, 2600) * scale
    r = lp(r * env * trem_r, 2600) * scale
    return l, r


def synth_warm_pad(freqs: list[float], dur: float, vel: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Silky analog string/pad swell."""
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    env = np.clip(t / min(0.9, dur * 0.35), 0.0, 1.0) * np.clip((dur - t) / min(1.2, dur * 0.4), 0.0, 1.0)
    l = np.zeros(n)
    r = np.zeros(n)
    for idx, f in enumerate(freqs):
        d1 = 1.0022 if idx % 2 == 0 else 0.9978
        d2 = 1.0041 if idx % 2 == 0 else 0.9959
        l += (np.sin(2 * np.pi * f * t) + 0.45 * np.sin(2 * np.pi * f * d1 * t + 0.4) + 0.16 * np.sin(2 * np.pi * f * 2 * t))
        r += (np.sin(2 * np.pi * f * t + 0.2) + 0.45 * np.sin(2 * np.pi * f * d2 * t + 0.9) + 0.16 * np.sin(2 * np.pi * f * 2 * t))
    scale = vel * 0.14 / max(len(freqs) ** 0.7, 1.0)
    return lp(l * env, 1650) * scale, lp(r * env, 1650) * scale


def synth_sub_bass(freq: float, dur: float, vel: float = 1.0) -> np.ndarray:
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    env = (1.0 - np.exp(-t * 85.0)) * np.exp(-t * 1.45) * np.clip((dur - t) / 0.06, 0.0, 1.0)
    sig = 0.78 * np.sin(2 * np.pi * freq * t) + 0.22 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 3.5)
    return lp(np.tanh(sig * 1.25), 360) * env * 0.30 * vel


def synth_soft_kick(vel: float = 1.0) -> np.ndarray:
    dur = 0.22
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    freq = 44.0 + 115.0 * np.exp(-t * 42.0)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 15.0)
    return lp(np.tanh(body * 1.3), 480) * 0.42 * vel


def synth_rim(rng: np.random.Generator, vel: float = 1.0) -> np.ndarray:
    dur = 0.075
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    wood = np.sin(2 * np.pi * 680 * t) * np.exp(-t * 72.0) * 0.55 + np.sin(2 * np.pi * 1390 * t) * np.exp(-t * 95.0) * 0.28
    snap = bp(rng.standard_normal(len(t)), 1500, 5200) * np.exp(-t * 68.0) * 0.30
    return (wood + snap) * 0.26 * vel


def synth_brush_hat(rng: np.random.Generator, open_hat: bool = False, vel: float = 1.0) -> np.ndarray:
    dur = 0.14 if open_hat else 0.045
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    decay = 20.0 if open_hat else 78.0
    sig = bp(rng.standard_normal(len(t)), 5800, 13500) * np.exp(-t * decay)
    return sig * (0.10 if open_hat else 0.065) * vel


def synth_plucked_bell(freq: float, dur: float = 1.1, vel: float = 1.0) -> np.ndarray:
    """Crystalline glass marimba/chime for prism & UI sparkle."""
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    env = (1.0 - np.exp(-t * 240.0)) * np.exp(-t * 3.8)
    sig = (
        0.65 * np.sin(2 * np.pi * freq * t)
        + 0.25 * np.sin(2 * np.pi * freq * 2.0 * t) * np.exp(-t * 6.0)
        + 0.12 * np.sin(2 * np.pi * freq * 3.98 * t) * np.exp(-t * 14.0)
    )
    return sig * env * 0.16 * vel


# --- BESPOKE CINEMA FOLEY SFX ---

def foley_griddle_sizzle(rng: np.random.Generator, dur: float) -> tuple[np.ndarray, np.ndarray]:
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    env = np.clip(t / 0.35, 0, 1) * np.clip((dur - t) / 0.45, 0, 1)
    crackle_l = bp(rng.standard_normal(n), 2800, 12500) * (0.68 + 0.32 * np.sin(2 * np.pi * 11.3 * t) * np.sin(2 * np.pi * 3.1 * t))
    crackle_r = bp(rng.standard_normal(n), 2600, 12000) * (0.68 + 0.32 * np.sin(2 * np.pi * 9.7 * t + 1.1) * np.sin(2 * np.pi * 2.7 * t))
    # Subtle warm hiss underneath
    steam = bp(rng.standard_normal(n), 900, 3200) * 0.18
    return (crackle_l + steam) * env * 0.095, (crackle_r + steam) * env * 0.095


def foley_night_wind(rng: np.random.Generator, dur: float) -> tuple[np.ndarray, np.ndarray]:
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    env = np.clip(t / 0.25, 0, 1) * np.clip((dur - t) / 0.30, 0, 1)
    l = bp(rng.standard_normal(n), 180, 620) * (0.7 + 0.3 * np.sin(2 * np.pi * 0.8 * t)) * env * 0.06
    r = bp(rng.standard_normal(n), 190, 640) * (0.7 + 0.3 * np.sin(2 * np.pi * 0.8 * t + 1.2)) * env * 0.06
    return l, r


def foley_shutter_whoosh(rng: np.random.Generator, dur: float = 0.24) -> np.ndarray:
    n = int(SR * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    env = np.sin(np.pi * t / dur) ** 1.6
    sig = bp(rng.standard_normal(n), 450, 3200) * env * 0.22
    thump = np.sin(2 * np.pi * (120 * np.exp(-t * 14.0)) * t) * np.exp(-t * 18.0) * 0.22
    return sig + thump


def foley_stamp_thud(rng: np.random.Generator, high: bool = False) -> np.ndarray:
    dur = 0.18
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    f0 = 155.0 if high else 92.0
    body = np.sin(2 * np.pi * (f0 * np.exp(-t * 22.0)) * t) * np.exp(-t * 24.0) * 0.50
    slap = bp(rng.standard_normal(len(t)), 900, 4800) * np.exp(-t * 58.0) * 0.34
    if high:
        chime = np.sin(2 * np.pi * 1046.5 * t) * np.exp(-t * 26.0) * 0.14
        return body + slap + chime
    return body + slap


def foley_split_flap(rng: np.random.Generator, heavy: bool = False) -> np.ndarray:
    dur = 0.085 if not heavy else 0.16
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    click = bp(rng.standard_normal(len(t)), 1400, 6800) * np.exp(-t * (65.0 if not heavy else 38.0)) * (0.36 if not heavy else 0.52)
    plastic = np.sin(2 * np.pi * (420 if not heavy else 210) * t) * np.exp(-t * 48.0) * (0.28 if not heavy else 0.45)
    return click + plastic


def foley_ui_tap(rng: np.random.Generator, bright: bool = False) -> np.ndarray:
    dur = 0.05
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    f = 1320.0 if bright else 860.0
    sig = np.sin(2 * np.pi * f * t) * np.exp(-t * 95.0) * 0.20 + bp(rng.standard_normal(len(t)), 2000, 6000) * np.exp(-t * 120.0) * 0.12
    return sig


def build_audio() -> None:
    tl = json.loads(TIMELINE_PATH.read_text(encoding="utf-8"))
    total_dur = float(tl["duration_seconds"])
    N = int(round(total_dur * SR))
    scenes = {s["id"]: s for s in tl["scenes"]}
    cues = tl["cues"]
    rng = np.random.default_rng(20261126)

    music_l = np.zeros(N, dtype=np.float64)
    music_r = np.zeros(N, dtype=np.float64)
    sfx_l = np.zeros(N, dtype=np.float64)
    sfx_r = np.zeros(N, dtype=np.float64)
    vo_track = np.zeros(N, dtype=np.float64)

    # 1. Load & place all voiceover cues at exact timeline timestamps
    for cue in cues:
        clip = read_wav_mono(ROOT / cue["wav"])
        i0 = int(round(cue["start"] * SR))
        length = min(len(clip), N - i0)
        if length > 0:
            vo_track[i0:i0 + length] += clip[:length]

    # 2. ACT I — Atmospheric Griddle Intro (s01_hero_griddle)
    s01 = scenes["s01_hero_griddle"]
    siz_l, siz_r = foley_griddle_sizzle(rng, s01["end"] - 0.1)
    add_stereo(sfx_l, sfx_r, 0.05, siz_l, siz_r, gain=1.0)
    # Warm intimate opening Rhodes chord & gentle pad
    pl, pr = synth_rhodes_chord([146.83, 220.0, 261.63, 329.63, 392.0], 4.2, vel=0.82)  # Dm11
    add_stereo(music_l, music_r, 0.15, pl, pr, gain=0.95)
    pl2, pr2 = synth_rhodes_chord([196.0, 246.94, 293.66, 349.23, 440.0], 3.2, vel=0.72)  # G9
    add_stereo(music_l, music_r, 4.10, pl2, pr2, gain=0.88)

    # 3. ACT I — Cold Drop on Empty Gate (s02_empty_gate)
    s02 = scenes["s02_empty_gate"]
    add_stereo(sfx_l, sfx_r, s02["start"] - 0.08, foley_shutter_whoosh(rng, 0.22), gain=0.85)
    wl, wr = foley_night_wind(rng, s02["end"] - s02["start"] + 0.2)
    add_stereo(sfx_l, sfx_r, s02["start"], wl, wr, gain=1.15)

    # 4. ACT I — Receipt Dissection Impact (s03_receipt_dissection)
    s03 = scenes["s03_receipt_dissection"]
    add_stereo(sfx_l, sfx_r, s03["start"] - 0.06, foley_shutter_whoosh(rng, 0.20), gain=0.75)
    # Thermal receipt print ticks
    for k in range(6):
        add_stereo(sfx_l, sfx_r, s03["start"] + 0.18 + k * 0.09, foley_ui_tap(rng, False), gain=0.45, pan=-0.25)
    # Left stamp: 溢价 +106%
    add_stereo(sfx_l, sfx_r, s03["start"] + 1.05, foley_stamp_thud(rng, high=False), gain=0.95, pan=-0.30)
    # Right stamp: 原价 · 2分钟取
    add_stereo(sfx_l, sfx_r, s03["start"] + 2.35, foley_stamp_thud(rng, high=True), gain=0.95, pan=0.30)

    # 5. ACT II -> ACT V — Warm 108 BPM Modern Editorial Groove (s04 -> s10)
    groove_start = scenes["s04_brand_map"]["start"]
    groove_end = scenes["s10_handoff_ops"]["end"]
    beat = 60.0 / 108.0  # 0.5556s
    bar = beat * 4.0     # 2.2222s

    # Warm soulful progression: Fmaj9 -> Em7 -> Dm9 -> Cmaj9 (warm, bright, trustworthy, zero cheese)
    prog = [
        ([174.61, 220.00, 261.63, 329.63, 392.00], 87.31),  # Fmaj9 / F2
        ([164.81, 246.94, 293.66, 392.00, 493.88], 82.41),  # Em7 / E2
        ([146.83, 220.00, 261.63, 329.63, 392.00], 73.42),  # Dm9 / D2
        ([130.81, 196.00, 246.94, 293.66, 329.63], 65.41),  # Cmaj9 / C2
    ]

    t_bar = groove_start
    bar_idx = 0
    while t_bar < groove_end - 0.2:
        chords, bass_f = prog[bar_idx % 4]
        dur_bar = min(bar, groove_end - t_bar)
        # Slightly warmer energy during live app showcase (s08-s09)
        in_climax = scenes["s08_student_live"]["start"] <= t_bar <= scenes["s09_merchant_ai"]["end"]
        energy = 1.08 if in_climax else 0.92

        # Rhodes chord on beat 1 and gentle ghost on beat 3.5
        cl, cr = synth_rhodes_chord(chords, dur_bar * 0.98, vel=energy)
        add_stereo(music_l, music_r, t_bar, cl, cr, gain=0.86)
        if dur_bar > beat * 3.0:
            pl, pr = synth_warm_pad([f * 0.5 for f in chords[:3]] + [chords[3]], dur_bar * 1.15, vel=0.72 * energy)
            add_stereo(music_l, music_r, t_bar, pl, pr, gain=0.72)

        # Warm rounded sub-bass pattern (beats 1, 2.5, 4)
        add_stereo(music_l, music_r, t_bar, synth_sub_bass(bass_f, beat * 1.35, vel=energy), gain=0.82)
        if t_bar + beat * 2.5 < groove_end:
            add_stereo(music_l, music_r, t_bar + beat * 2.5, synth_sub_bass(bass_f * 1.5, beat * 0.85, vel=0.75 * energy), gain=0.70)

        # Subtle crisp percussion (soft kick on 1 & 2.75, warm rimshot on 2 & 4, brushed 8th hats)
        for step in range(8):
            st = t_bar + step * (beat * 0.5)
            if st >= groove_end:
                break
            if step in (0, 5):
                add_stereo(music_l, music_r, st, synth_soft_kick(vel=(0.92 if step == 0 else 0.65) * energy), gain=0.68)
            if step in (2, 6):
                add_stereo(music_l, music_r, st, synth_rim(rng, vel=0.82 * energy), gain=0.62, pan=0.14 if step == 2 else -0.14)
            add_stereo(music_l, music_r, st, synth_brush_hat(rng, open_hat=(step == 7), vel=0.75 * energy), gain=0.58, pan=-0.22 if step % 2 == 0 else 0.22)

        t_bar += bar
        bar_idx += 1

    # 6. Scene-specific Frame-Locked Foley across Acts II - V
    # s04 brand map reveal chime
    s04 = scenes["s04_brand_map"]
    add_stereo(sfx_l, sfx_r, s04["start"], synth_plucked_bell(523.25, 1.3, 0.9), gain=0.75, pan=-0.2)
    add_stereo(sfx_l, sfx_r, s04["start"] + 0.14, synth_plucked_bell(659.25, 1.3, 0.9), gain=0.75, pan=0.2)

    # s05 60:00 mechanical split-flap clacks + gate lock
    s05 = scenes["s05_freshness_flap"]
    flap_times = [0.40, 1.05, 1.70, 2.35, 3.00]
    for idx, dt in enumerate(flap_times):
        add_stereo(sfx_l, sfx_r, s05["start"] + dt, foley_split_flap(rng, heavy=(idx == len(flap_times) - 1)), gain=0.95)
    add_stereo(sfx_l, sfx_r, s05["start"] + 3.05, foley_stamp_thud(rng, high=False), gain=0.78)

    # s06 A/B dual-track highway split whoosh
    s06 = scenes["s06_dual_track"]
    add_stereo(sfx_l, sfx_r, s06["start"], foley_shutter_whoosh(rng, 0.25), gain=0.70)
    add_stereo(sfx_l, sfx_r, s06["start"] + 1.0, foley_ui_tap(rng, False), gain=0.60, pan=-0.35)
    add_stereo(sfx_l, sfx_r, s06["start"] + 2.3, foley_ui_tap(rng, True), gain=0.65, pan=0.35)

    # s07 optical glass prism crystalline arpeggio
    s07 = scenes["s07_optical_prism"]
    prism_notes = [523.25, 659.25, 783.99, 987.77, 1174.66]  # C5 E5 G5 B5 D6
    for idx, nf in enumerate(prism_notes):
        add_stereo(sfx_l, sfx_r, s07["start"] + 0.85 + idx * 0.16, synth_plucked_bell(nf, 1.8, 0.85), gain=0.78, pan=-0.4 + idx * 0.2)
    add_stereo(sfx_l, sfx_r, s07["start"] + 4.40, foley_stamp_thud(rng, high=True), gain=0.72)

    # s08 student live app taps + 4-digit pickup code chime
    s08 = scenes["s08_student_live"]
    add_stereo(sfx_l, sfx_r, s08["start"] + 0.2, foley_shutter_whoosh(rng, 0.22), gain=0.65)
    add_stereo(sfx_l, sfx_r, s08["start"] + 2.8, foley_ui_tap(rng, False), gain=0.65, pan=-0.2)
    add_stereo(sfx_l, sfx_r, s08["start"] + 5.2, synth_plucked_bell(783.99, 0.9, 0.85), gain=0.70, pan=0.25)
    add_stereo(sfx_l, sfx_r, s08["start"] + 5.34, synth_plucked_bell(1046.5, 1.1, 0.90), gain=0.72, pan=0.25)

    # s09 merchant oil-hand big button + camera shutter on photo assist
    s09 = scenes["s09_merchant_ai"]
    add_stereo(sfx_l, sfx_r, s09["start"] + 2.65, foley_ui_tap(rng, True), gain=0.75, pan=-0.15)
    add_stereo(sfx_l, sfx_r, s09["start"] + 4.20, foley_split_flap(rng, heavy=False), gain=0.80, pan=0.28)

    # s10 handoff pavilion + /ops console
    s10 = scenes["s10_handoff_ops"]
    add_stereo(sfx_l, sfx_r, s10["start"], foley_shutter_whoosh(rng, 0.24), gain=0.62)
    add_stereo(sfx_l, sfx_r, s10["start"] + 3.8, foley_ui_tap(rng, True), gain=0.60, pan=0.20)

    # 7. ACT VI — Warm Montage & Emotional Finale (s11_montage_finale)
    s11 = scenes["s11_montage_finale"]
    fin_dur = s11["end"] - s11["start"]
    # Four warm culinary beat pulses
    for k in range(4):
        add_stereo(sfx_l, sfx_r, s11["start"] + k * 0.80, synth_soft_kick(0.75), gain=0.55)
    # Lush emotional final chord cadence: Fmaj9 -> G6/9 -> Cmaj9 resolving warmly
    fl1, fr1 = synth_rhodes_chord([174.61, 220.0, 261.63, 329.63, 392.0], 3.8, vel=0.95)
    add_stereo(music_l, music_r, s11["start"], fl1, fr1, gain=0.95)
    fp1, fp1r = synth_warm_pad([174.61, 261.63, 329.63, 392.0], 4.2, vel=0.95)
    add_stereo(music_l, music_r, s11["start"], fp1, fp1r, gain=0.88)

    fl2, fr2 = synth_rhodes_chord([196.0, 246.94, 293.66, 329.63, 440.0], 3.4, vel=0.92)
    add_stereo(music_l, music_r, s11["start"] + 3.4, fl2, fr2, gain=0.92)

    fl3, fr3 = synth_rhodes_chord([130.81, 196.0, 246.94, 293.66, 329.63, 392.0], max(4.5, fin_dur - 6.2), vel=1.0)
    add_stereo(music_l, music_r, s11["start"] + 6.4, fl3, fr3, gain=1.02)
    fp3, fp3r = synth_warm_pad([130.81, 196.0, 261.63, 329.63, 392.0], max(4.8, fin_dur - 6.0), vel=1.05)
    add_stereo(music_l, music_r, s11["start"] + 6.3, fp3, fp3r, gain=0.95)
    # Warm closing chime sparkle
    add_stereo(sfx_l, sfx_r, s11["start"] + 6.6, synth_plucked_bell(783.99, 2.2, 0.72), gain=0.68, pan=-0.2)
    add_stereo(sfx_l, sfx_r, s11["start"] + 6.9, synth_plucked_bell(1046.5, 2.4, 0.78), gain=0.72, pan=0.2)

    # 8. Smooth Sidechain Ducking Envelope driven by actual VO speech activity
    vo_abs = np.abs(vo_track)
    # Smooth envelope via low-pass filter (~6 Hz) + hold
    b_env, a_env = butter(2, 5.5 / (SR * 0.5), btype="low")
    vo_env = np.clip(filtfilt(b_env, a_env, vo_abs) * 14.0, 0.0, 1.0)
    # Duck music bed to 0.36 (-8.8 dB) while speaking, full 1.0 during visual breathers!
    duck_gain = 1.0 - 0.64 * vo_env

    # Subtle overall fade-out on final 1.2s
    tail_n = int(SR * 1.2)
    tail_curve = np.ones(N)
    tail_curve[-tail_n:] = np.linspace(1.0, 0.0, tail_n) ** 1.6

    mix_l = (music_l * duck_gain + sfx_l * (0.72 + 0.28 * (1.0 - vo_env)) + vo_track * 1.05) * tail_curve
    mix_r = (music_r * duck_gain + sfx_r * (0.72 + 0.28 * (1.0 - vo_env)) + vo_track * 1.05) * tail_curve

    # Soft headroom clip protection before EBU R128 loudness mastering
    peak = max(np.max(np.abs(mix_l)), np.max(np.abs(mix_r)), 1e-6)
    scale = 0.88 / peak
    stereo = np.stack([mix_l * scale, mix_r * scale], axis=1)
    pcm = np.clip(stereo * 32767.0, -32768, 32767).astype(np.int16)

    raw_wav = AUDIO_DIR / "soundtrack_raw.wav"
    final_wav = AUDIO_DIR / "soundtrack.wav"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    with wave.open(str(raw_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())

    # Master to broadcast -14.0 LUFS / -1.0 dBTP stereo WAV
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(raw_wav),
            "-af",
            "loudnorm=I=-14.0:TP=-1.0:LRA=7:linear=true",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(final_wav),
        ],
        check=True,
    )
    raw_wav.unlink(missing_ok=True)
    print(f"Mastered cinema soundtrack -> {final_wav} ({total_dur:.2f}s @ 48kHz stereo)")


if __name__ == "__main__":
    build_audio()
