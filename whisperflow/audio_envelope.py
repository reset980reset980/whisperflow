"""Compute a per-frame loudness/frequency envelope from WAV audio.

Used to drive the JARVIS "speaking" particles: while the browser plays a
TTS clip, the server streams ``audio_level`` messages (overall level plus
low/mid/high frequency bands) timed to playback, and jarvis.html animates
the particle cloud from them.

``frames(wav_bytes, fps)`` returns a list of dicts:
    {"level": 0..1, "low": 0..1, "mid": 0..1, "high": 0..1}

Uses numpy for the 3-band FFT split when available; otherwise falls back
to a pure-stdlib RMS-only envelope (level filled, bands = level).
"""

from __future__ import annotations

import io
import math
import struct
import wave

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False


def _read_wav(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return raw, n_channels, sampwidth, framerate


def _to_mono_int16(raw: bytes, n_channels: int, sampwidth: int):
    """Return a list/array of int16 mono samples."""
    if sampwidth != 2:
        # Only 16-bit PCM is expected from OpenAI wav; bail gracefully.
        count = len(raw) // sampwidth
        return [0] * (count // max(1, n_channels))
    total = len(raw) // 2
    samples = struct.unpack("<%dh" % total, raw[: total * 2])
    if n_channels > 1:
        samples = samples[::n_channels]  # take first channel
    return samples


def frames(wav_bytes: bytes, fps: int = 30) -> list[dict]:
    """Envelope frames at *fps*. Empty list if audio can't be parsed."""
    try:
        raw, n_channels, sampwidth, framerate = _read_wav(wav_bytes)
    except (wave.Error, EOFError, struct.error):
        return []

    samples = _to_mono_int16(raw, n_channels, sampwidth)
    if not len(samples):
        return []

    hop = max(1, framerate // fps)

    if _HAS_NUMPY:
        return _frames_numpy(samples, framerate, hop)
    return _frames_stdlib(samples, hop)


def _frames_numpy(samples, framerate, hop) -> list[dict]:
    arr = _np.asarray(samples, dtype=_np.float32) / 32768.0
    out = []
    # Global normalisation reference so quiet clips still animate.
    peak = float(_np.max(_np.abs(arr))) or 1.0
    for start in range(0, len(arr), hop):
        window = arr[start:start + hop]
        if window.size == 0:
            continue
        rms = float(_np.sqrt(_np.mean(window ** 2)))
        level = min(1.0, (rms / peak) * 1.6)

        spectrum = _np.abs(_np.fft.rfft(window * _np.hanning(window.size)))
        freqs = _np.fft.rfftfreq(window.size, 1.0 / framerate)
        low = _band_energy(spectrum, freqs, 0, 500)
        mid = _band_energy(spectrum, freqs, 500, 2000)
        high = _band_energy(spectrum, freqs, 2000, framerate / 2)
        norm = max(low, mid, high, 1e-6)
        out.append({
            "level": round(level, 4),
            "low": round(min(1.0, low / norm) * level, 4),
            "mid": round(min(1.0, mid / norm) * level, 4),
            "high": round(min(1.0, high / norm) * level, 4),
        })
    return out


def _band_energy(spectrum, freqs, lo, hi) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    if not mask.any():
        return 0.0
    return float(spectrum[mask].mean())


def _frames_stdlib(samples, hop) -> list[dict]:
    out = []
    peak = max((abs(s) for s in samples), default=1) or 1
    for start in range(0, len(samples), hop):
        window = samples[start:start + hop]
        if not window:
            continue
        rms = math.sqrt(sum(s * s for s in window) / len(window))
        level = min(1.0, (rms / peak) * 1.6)
        out.append({"level": round(level, 4), "low": round(level, 4),
                    "mid": round(level, 4), "high": round(level, 4)})
    return out
