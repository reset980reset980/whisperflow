"""Speech-to-text for the Linux port (browser mic → server).

The browser records the microphone (MediaRecorder → webm/opus) and sends
the audio over the WebSocket as base64; here we decode it and run
faster-whisper locally. faster-whisper uses PyAV/ffmpeg to decode the
webm/opus (or wav/mp3) container, so no manual transcoding is needed.

Model + language come from the environment:
    WHISPER_MODEL     tiny/base/small/medium/large-v3   (default: base)
    WHISPER_LANGUAGE  ko/en/ja/zh/auto                   (default: ko)
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Optional


class SpeechToText:
    """Lazy-loaded faster-whisper wrapper."""

    def __init__(self):
        self._model = None
        self._model_size: Optional[str] = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        from faster_whisper import WhisperModel

        size = os.environ.get("WHISPER_MODEL", "base").strip() or "base"
        with self._lock:
            if self._model is None or self._model_size != size:
                self._model_size = size
                # CPU + int8 keeps memory low on a shared server box.
                self._model = WhisperModel(size, device="cpu", compute_type="int8")
            return self._model

    def transcribe_bytes(self, audio: bytes, suffix: str = ".webm") -> str:
        """Transcribe raw audio bytes → text. Returns "" on failure."""
        if not audio:
            return ""
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(audio)
        tmp.close()
        try:
            return self.transcribe_file(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def transcribe_file(self, path: str) -> str:
        if not os.path.exists(path):
            return ""
        model = self._ensure_model()
        lang_cfg = os.environ.get("WHISPER_LANGUAGE", "ko").strip()
        language = None if lang_cfg in ("", "auto") else lang_cfg
        segments, _info = model.transcribe(
            path, language=language, beam_size=5, vad_filter=False
        )
        text = "".join(seg.text for seg in segments).strip()
        # Match the original formatting: newline after sentence enders.
        text = re.sub(r"([.!?])\s*", r"\1\n", text).strip()
        return text


# Global instance
stt = SpeechToText()
