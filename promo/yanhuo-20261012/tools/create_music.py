"""Render an original, deterministic instrumental bed for the Yanhuo Map film.

All sounds and notes are synthesized here; no samples or third-party music.
Run: python create_music.py
Requires numpy and scipy. Output: 48 kHz stereo PCM-16 WAV, exactly 60 seconds.
"""

from pathlib import Path
import json
import wave

import numpy as np
from scipy.signal import butter, sosfilt


SR = 48000
DURATION = 60.0
BPM = 106
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


def compose():
    # Cmaj9 / Am9 / Fmaj9 / G6; middle register leaves room for narration.
    chords = [
        (36, [52, 55, 59, 62]),
        (33, [48, 52, 55, 59]),
        (29, [48, 52, 57, 60]),
        (31, [50, 55, 59, 64]),
    ]
    melody = [
        [(0.5, 67), (2.5, 64), (3.25, 62)],
        [(0.75, 64), (2.5, 67)],
        [(0.5, 69), (2.0, 67), (3.25, 64)],
        [(1.0, 62), (2.5, 59), (3.5, 62)],
    ]
    # Begin immediately with a gentle chord; the groove opens after two bars.
    for bar in range(26):
        origin = bar * 4 * BEAT
        root, notes = chords[bar % 4]
        energy = 0.78 if bar < 2 or 12 <= bar < 14 else 1.0
        if bar >= 24:
            energy = 0.72
        for i, note in enumerate(notes):
            add(epiano(note), origin + i * 0.019, 0.080 * energy, -0.23 + i * 0.15)
        if bar >= 2 and bar < 24:
            for i, note in enumerate(notes[1:]):
                add(epiano(note, 1.45), origin + 2.5 * BEAT + i * 0.014,
                    0.027 * energy, 0.26 - i * 0.13)
        for beat, note, level in [(0, root, 0.125), (1.75, root + 12, 0.062),
                                  (2.5, root + 7, 0.077), (3.5, root + 12, 0.053)]:
            if bar < 2 and beat != 0:
                continue
            add(bass(note, BEAT * 0.82), origin + beat * BEAT, level * energy)
        # Theme is sparse, with lower energy below the explanatory middle.
        theme_level = 0.049 if bar < 8 or bar >= 18 else 0.030
        if bar >= 2:
            for beat, note in melody[bar % 4]:
                pan = -0.15 if int(beat * 4) % 2 == 0 else 0.2
                phrase = pluck(note)
                add(phrase, origin + beat * BEAT, theme_level * energy, pan)
                add(lowpass(phrase, 2200), origin + (beat + 0.75) * BEAT,
                    theme_level * 0.17 * energy, -pan)
        if 2 <= bar < 25:
            for beat in [0, 2, 3.5]:
                add(kick(), origin + beat * BEAT, (0.14 if beat != 3.5 else 0.06) * energy)
            for beat in [1, 3]:
                add(brushed_snare(), origin + beat * BEAT, 0.068 * energy, 0.08)
            for index in range(8):
                swung = index * 0.5 + (0.055 if index % 2 else 0)
                add(shaker(), origin + swung * BEAT,
                    (0.024 if index % 2 else 0.037) * energy, (-1) ** index * 0.3)
    # A resolved C major ending rings into the final fade.
    end_time = 57.15
    for i, note in enumerate([48, 55, 60, 64, 67]):
        add(epiano(note, 2.85), end_time + i * 0.015, 0.055, -0.3 + i * 0.15)
    add(bass(36, 1.6), end_time, 0.10)


def main():
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
    MIX[:] *= 0.56 / np.max(np.abs(MIX))
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
        "sample_rate": SR, "channels": 2, "bpm": BPM,
        "rms": round(rms, 6), "rms_dbfs": round(20 * np.log10(rms), 2),
        "peak": round(float(np.max(np.abs(MIX))), 6),
        "peak_dbfs": round(20 * np.log10(np.max(np.abs(MIX))), 2),
        "source": "Original procedural composition and synthesis; no third-party samples.",
    }
    output.with_suffix(".json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
