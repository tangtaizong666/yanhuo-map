"""Render the original narrative instrumental score for the Yanhuo Map film.

All sounds and notes are synthesized here; no samples or third-party music.
Run: python create_music.py
Requires numpy and scipy. Output: 48 kHz stereo PCM-16 WAV, exactly 60 seconds.
Score cues follow the picture: desire / uncertainty / map / departure / meal.
"""

from pathlib import Path
import json
import wave

import numpy as np
from scipy.signal import butter, sosfilt


SR = 48000
DURATION = 60.0
BPM = 108
BEAT = 60 / BPM
RNG = np.random.default_rng(20261012)
MIX = np.zeros((round(SR * DURATION), 2), dtype=np.float64)


def hz(midi):
    return 440 * 2 ** ((midi - 69) / 12)


def lowpass(signal, cutoff):
    return sosfilt(butter(2, cutoff, fs=SR, output="sos"), signal)


def add(signal, when, level=1.0, pan=0.0):
    start = round(when * SR)
    if start >= len(MIX):
        return
    if start < 0:
        signal = signal[-start:]
        start = 0
    size = min(len(signal), len(MIX) - start)
    angle = (pan + 1) * np.pi / 4
    MIX[start:start + size, 0] += signal[:size] * level * np.cos(angle)
    MIX[start:start + size, 1] += signal[:size] * level * np.sin(angle)


def epiano(note, length=2.7):
    t = np.arange(round(length * SR)) / SR
    f = hz(note)
    # Smooth electric-piano tine with a warm body and a short upper partial.
    attack = 1 - np.exp(-t * 260)
    signal = (
        np.sin(2 * np.pi * f * t) * np.exp(-t / 1.25)
        + 0.22 * np.sin(2 * np.pi * f * 2.003 * t) * np.exp(-t / 0.65)
        + 0.07 * np.sin(2 * np.pi * f * 3.997 * t) * np.exp(-t / 0.17)
        + 0.08 * np.sin(2 * np.pi * f * 0.9992 * t) * np.exp(-t / 1.5)
    )
    signal *= attack * np.minimum(1, (length - t) / 0.15)
    return lowpass(signal, 4200)


def pluck(note, length=1.3):
    t = np.arange(round(length * SR)) / SR
    f = hz(note)
    signal = sum(
        (1 / h ** 1.7) * np.sin(2 * np.pi * f * h * t)
        * np.exp(-t / (0.55 / h ** 0.55))
        for h in range(1, 7)
    )
    return lowpass(signal * (1 - np.exp(-t * 380)), 3800)


def bass(note, length=0.56):
    t = np.arange(round(length * SR)) / SR
    f = hz(note)
    signal = (np.sin(2 * np.pi * f * t)
              + 0.17 * np.sin(4 * np.pi * f * t)
              + 0.035 * np.sin(6 * np.pi * f * t))
    env = (1 - np.exp(-t * 90)) * np.exp(-t / 0.48)
    env *= np.minimum(1, (length - t) / 0.08)
    return signal * env


def kick():
    t = np.arange(round(0.3 * SR)) / SR
    phase = 2 * np.pi * (44 * t + 50 * (1 - np.exp(-t * 33)) / 33)
    return np.sin(phase) * np.exp(-t * 16) * (1 - np.exp(-t * 850))


def brushed_snare():
    t = np.arange(round(0.21 * SR)) / SR
    noise = lowpass(RNG.normal(size=len(t)), 4800)
    noise -= lowpass(noise, 800)
    signal = noise * np.exp(-t * 29) + 0.1 * np.sin(2 * np.pi * 175 * t) * np.exp(-t * 40)
    return signal * (1 - np.exp(-t * 950))


def shaker():
    t = np.arange(round(0.075 * SR)) / SR
    noise = lowpass(RNG.normal(size=len(t)), 7500)
    noise -= lowpass(noise, 3600)
    return noise * (1 - np.exp(-t * 1400)) * np.exp(-t * 66)


def soft_strings(note, length=4.0):
    """Low, softly bowed additive ensemble; no external recording or samples."""
    t = np.arange(round(length * SR)) / SR
    f = hz(note)
    signal = np.zeros_like(t)
    for cents, phase in [(-5, 0.1), (0, 1.0), (5, 2.2)]:
        fundamental = f * 2 ** (cents / 1200)
        vibrato = 0.003 * np.sin(2 * np.pi * 4.6 * t + phase)
        for harmonic in range(1, 5):
            signal += np.sin(2 * np.pi * fundamental * harmonic * t
                             + vibrato * harmonic + phase) / harmonic ** 2.4
    envelope = np.minimum(1, t / 0.45) * np.minimum(1, (length - t) / 0.9)
    return lowpass(signal * envelope / 3, 1800)


CHORDS = [
    (36, [52, 55, 59, 62]),  # Cmaj9
    (33, [48, 52, 55, 59]),  # Am9
    (29, [48, 52, 57, 60]),  # Fmaj9
    (31, [50, 55, 59, 64]),  # G6
]
MELODY = [
    [(0.5, 64), (1.5, 67), (2.5, 69), (3.25, 67)],
    [(0.5, 64), (2, 62), (3.0, 60)],
    [(0.5, 64), (1.5, 65), (2.5, 69), (3.25, 67)],
    [(0.5, 62), (1.75, 64), (2.5, 67)],
]


def chord(when, notes, level=0.070, length=2.7):
    for index, note in enumerate(notes):
        add(epiano(note, length), when + index * 0.018, level,
            -0.26 + index * 0.15)


def phrase(when, notes, level=0.067):
    for index, (beat, note) in enumerate(notes):
        tone = pluck(note)
        pan = -0.16 if index % 2 == 0 else 0.16
        add(tone, when + beat * BEAT, level, pan)
        add(lowpass(tone, 1900), when + (beat + 0.75) * BEAT,
            level * 0.16, -pan)


def groove_bar(when, harmony, energy=1.0, drums=1.0, melody=1.0, strings=0):
    root, notes = CHORDS[harmony % 4]
    chord(when, notes, 0.065 * energy)
    chord(when + 2.5 * BEAT, notes[1:], 0.025 * energy, 1.1)
    for beat, note, level in [(0, root, 0.13), (1.75, root + 12, 0.055),
                              (2.5, root + 7, 0.080), (3.5, root + 12, 0.050)]:
        add(bass(note, BEAT * 0.86), when + beat * BEAT, level * energy)
    if melody:
        phrase(when, MELODY[harmony % 4], 0.075 * melody)
    if drums:
        for beat, level in [(0, 0.13), (2, 0.11), (3.5, 0.052)]:
            add(kick(), when + beat * BEAT, level * drums)
        for beat in [1, 3]:
            add(brushed_snare(), when + beat * BEAT, 0.075 * drums, 0.07)
        for index in range(8):
            beat = index * 0.5 + (0.045 if index % 2 else 0)
            add(shaker(), when + beat * BEAT,
                (0.024 if index % 2 else 0.035) * drums,
                (-1) ** index * 0.3)
    if strings:
        for index, note in enumerate(notes[1:]):
            add(soft_strings(note + 12, 4 * BEAT + 0.45), when,
                0.014 * strings, -0.42 + index * 0.42)


def compose():
    # 0-12 s: the warm appetite motif gradually finds a little movement.
    for bar in range(5):
        start = bar * 4 * BEAT
        root, notes = CHORDS[bar % 4]
        chord(start, notes, 0.060 if bar < 2 else 0.064)
        add(bass(root, 0.9), start, 0.080)
        phrase(start, MELODY[bar % 4], 0.047 if bar < 2 else 0.059)
        if bar >= 2:
            for index in range(4):
                add(shaker(), start + (index + 0.5) * BEAT, 0.026,
                    -0.18 if index % 2 else 0.18)
            add(kick(), start, 0.085)
    # A held, open fifth bridges into the question instead of sustaining a loop.
    chord(11.1, [52, 59, 62], 0.040, 1.8)

    # 12-20 s: stall uncertainty. No groove; unresolved add9 / suspended voicing.
    chord(12.05, [48, 55, 59, 62], 0.034, 3.2)
    add(soft_strings(55, 5.1), 12.0, 0.019, -0.25)
    add(soft_strings(62, 5.1), 12.0, 0.012, 0.25)
    phrase(13.3, [(0, 64), (1.5, 62)], 0.034)
    chord(15.4, [50, 57, 62], 0.030, 2.8)
    phrase(16.1, [(0, 62), (2, 59)], 0.030)
    # Deliberate 18.8-20 s breath: only the fading suspended harmony is audible.
    add(epiano(55, 2.0), 18.1, 0.021, -0.1)

    # 20-27 s: map reveal. A clear C-E-G answer becomes the brand's tiny motif.
    chord(20.0, [48, 55, 60, 64, 67], 0.070, 3.4)
    add(bass(36, 0.9), 20.0, 0.135)
    add(kick(), 20.0, 0.110)
    phrase(20.0, [(0, 60), (0.5, 64), (1, 67), (2.5, 72)], 0.080)
    groove_bar(22.25, 2, energy=0.82, drums=0.44, melody=0.72)
    groove_bar(24.47, 3, energy=0.88, drums=0.63, melody=0.80)

    # 27-37 s: purposeful departure. Light footsteps in bass/drums and rising line.
    for bar in range(5):
        groove_bar(27.0 + bar * 4 * BEAT, bar,
                   energy=0.92 + bar * 0.025, drums=0.77 + bar * 0.045,
                   melody=0.92 + bar * 0.025,
                   strings=0.0 if bar < 3 else 0.35)

    # 38-48 s: food / arrival. The fullest harmony, with a soft string halo.
    for bar in range(4):
        start = 38.111111111 + bar * 4 * BEAT
        groove_bar(start, bar + 1, energy=1.04, drums=1.0,
                   melody=1.08, strings=0.88)
        if bar in (0, 2):
            phrase(start, [(2.75, 72), (3.5, 71 if bar == 0 else 69)], 0.034)

    # 48-53 s: the meal is here. Drum activity recedes while warmth stays.
    groove_bar(47.0, 2, energy=0.95, drums=0.73, melody=0.91, strings=0.7)
    groove_bar(49.222222222, 3, energy=0.88, drums=0.36, melody=0.80, strings=0.5)
    chord(51.45, [50, 55, 60, 62], 0.047, 2.7)
    phrase(51.45, [(0.0, 67), (1.0, 64)], 0.055)

    # 53-60 s: a resolved C6/9 ending for the title; no percussion and no voice.
    chord(53.0, [48, 55, 60, 64, 69], 0.071, 4.5)
    add(bass(36, 1.8), 53.0, 0.106)
    for index, note in enumerate([55, 60, 64]):
        add(soft_strings(note, 5.6), 53.1, 0.019, -0.3 + index * 0.3)
    phrase(53.0, [(0.5, 60), (1, 64), (1.5, 67), (3.0, 72)], 0.061)
    chord(56.2, [52, 55, 60, 62], 0.047, 3.8)
    add(pluck(72, 2.5), 56.9, 0.028, 0.1)


def main():
    MIX.fill(0)
    compose()
    # Quiet, diffuse stereo reflections rather than a metallic feedback reverb.
    dry = MIX.copy()
    for delay, wet in [(0.043, 0.060), (0.079, 0.044), (0.127, 0.033), (0.181, 0.024)]:
        samples = round(delay * SR)
        MIX[samples:] += dry[:-samples, ::-1] * wet
    MIX[:] = np.tanh(MIX * 1.15)
    fade_in = np.linspace(0, 1, SR)
    fade_out = np.linspace(1, 0, SR * 2)
    MIX[:SR] *= fade_in[:, None]
    MIX[-SR * 2:] *= fade_out[:, None]
    MIX[:] *= 0.68 / np.max(np.abs(MIX))
    pcm = (np.clip(MIX, -1, 1) * 32767).astype("<i2")
    output = Path(__file__).resolve().parents[1] / "audio" / "original-bed.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())
    rms = float(np.sqrt(np.mean(MIX ** 2)))
    stats = {
        "path": str(output), "duration_seconds": DURATION,
        "sample_rate": SR, "sample_frames": len(MIX),
        "channels": 2, "sample_width_bits": 16, "bpm": BPM,
        "video_fps": 30, "matching_video_frames": 1800,
        "version": "narrative-v2-no-voice",
        "rms": round(rms, 6), "rms_dbfs": round(20 * np.log10(rms), 2),
        "peak": round(float(np.max(np.abs(MIX))), 6),
        "peak_dbfs": round(20 * np.log10(np.max(np.abs(MIX))), 2),
        "source": "Original procedural composition and synthesis; no third-party samples.",
        "voices": False,
        "fade_in_seconds": 1,
        "fade_out_seconds": 2,
        "cues": [
            {"start": 0, "end": 12, "story": "After class, wanting something warm", "music": "Warm electric piano and a sparse plucked theme; gentle movement grows after 4.4 s"},
            {"start": 12, "end": 20, "story": "Wondering whether the stall is still there", "music": "Drums and bass leave; suspended harmony and isolated notes; a breath at 18.8-20 s"},
            {"start": 20, "end": 27, "story": "Yanhuo Map provides a direction", "music": "C-E-G reveal motif at 20 s, major resolution and a returning pulse"},
            {"start": 27, "end": 38.111111111, "story": "Setting off with a destination", "music": "A steady light groove; bass, melody and percussion build across five bars"},
            {"start": 38.111111111, "end": 48, "story": "Arrival and freshly cooked food", "music": "Fullest arrangement, upper melody answers and a soft string halo"},
            {"start": 48, "end": 53, "story": "Enjoying the meal", "music": "Percussion recedes; warm suspended cadence"},
            {"start": 53, "end": 60, "story": "Warm food and final title", "music": "Resolved C6/9, a last C-E-G motif, sustained piano; two-second fade"},
        ],
    }
    stats["segment_rms_dbfs"] = {
        f"{start}-{end}s": round(20 * np.log10(np.sqrt(np.mean(
            MIX[round(start * SR):round(end * SR)] ** 2))), 2)
        for start, end in [(0, 12), (12, 20), (18.8, 20), (20, 27), (27, 38), (38, 48), (48, 53), (53, 60)]
    }
    output.with_suffix(".json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
