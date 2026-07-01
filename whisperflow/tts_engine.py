"""Text-to-speech engine for the Linux port.

The original macOS ``tts_reader`` spoke via ``NSSpeechSynthesizer`` / the
``say`` command on the *server* machine. In a headless / web deployment
there are no local speakers, so instead we synthesize audio bytes and hand
them to the browser (ws_server broadcasts a ``tts_audio`` message whose
``value`` is base64 audio; jarvis.html decodes it with the Web Audio API
and drives the "speaking" particles from its frequency spectrum).

``synthesize(text) -> bytes`` returns audio (mp3 for OpenAI, wav for
espeak). Backends, in order of preference:

    TTS_BACKEND=openai  → OpenAI /v1/audio/speech  (needs OPENAI_API_KEY)
    TTS_BACKEND=espeak  → espeak-ng CLI (offline, must be installed)

Returns ``b""`` when no backend is available so callers can skip audio
gracefully.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


def _openai_tts(text: str) -> bytes:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return b""
    base_url = (
        os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
    ).rstrip("/")
    model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip()
    voice = os.environ.get("OPENAI_TTS_VOICE", "alloy").strip()
    payload = {
        "model": model,
        "voice": voice,
        "input": text,
        # wav (PCM) so the browser <audio> plays it reliably and the server
        # can parse the envelope for the speaking-particle audio_level stream.
        "response_format": "wav",
    }
    req = urllib.request.Request(
        f"{base_url}/audio/speech",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"[TTS] OpenAI HTTP {e.code}: {body}")
        return b""
    except urllib.error.URLError as e:
        print(f"[TTS] OpenAI connection error: {e.reason}")
        return b""


def _espeak_tts(text: str) -> bytes:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        return b""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        out_path = tf.name
    try:
        # Korean voice if available, else default.
        subprocess.run(
            [exe, "-v", "ko", "-s", "160", "-w", out_path, text],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return Path(out_path).read_bytes()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[TTS] espeak error: {e}")
        return b""
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def synthesize(text: str) -> bytes:
    """Return audio bytes for *text*, or b"" if TTS is unavailable."""
    text = (text or "").strip()
    if not text:
        return b""

    backend = os.environ.get("TTS_BACKEND", "openai").strip().lower()

    if backend == "espeak":
        audio = _espeak_tts(text)
        if audio:
            return audio
        return _openai_tts(text)  # fall back to cloud if espeak missing

    # default: openai, then espeak fallback
    audio = _openai_tts(text)
    if audio:
        return audio
    return _espeak_tts(text)


def available() -> str:
    """Report which backend would be used (for startup logging)."""
    backend = os.environ.get("TTS_BACKEND", "openai").strip().lower()
    if backend == "espeak" and (shutil.which("espeak-ng") or shutil.which("espeak")):
        return "espeak"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak"
    return "none"
