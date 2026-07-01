"""Headless entry point for the WhisperFlow Linux port.

Runs the JARVIS web UI + WebSocket server without any macOS-specific
pieces (no rumps menu bar, no pynput global hotkey, no `say`). Wires the
chat pipeline to the API-based AI backend and streams TTS audio + a
synced audio-level envelope to the browser so the "speaking" particles
react to the voice.

    python -m whisperflow.server            # or: whisperflow-server

Environment is read from .env (see .env.example). Serve behind Caddy with
a reverse_proxy to 127.0.0.1:8767 (HTTP + WebSocket share the port).
"""

from __future__ import annotations

import base64
import json
import os
import signal
import threading
import time

from .env_loader import load_env
from .ai_backend import get_provider
from . import tts_engine
from . import audio_envelope


class WhisperFlowServer:
    def __init__(self):
        # Import here so .env is loaded before ws_server/hue read anything.
        from .ws_server import WhisperFlowWSServer

        self.ws = WhisperFlowWSServer()
        # Monotonic generation id: bumping it cancels an in-flight TTS
        # envelope stream (used by tts_interrupt).
        self._tts_gen = 0
        self._tts_lock = threading.Lock()

        # Wire callbacks the ws_server exposes.
        self.ws._on_chat_tts = self._on_chat_tts
        self.ws._on_tts_interrupt = self._on_tts_interrupt

        # Browser mic → server STT (faster-whisper). Imported lazily so the
        # server can start even before the model is downloaded.
        from .stt import stt as _stt
        self.ws._transcribe = _stt.transcribe_bytes

    # ------------------------------------------------------------------
    # TTS: synthesize → play in browser → stream envelope → return to idle
    # ------------------------------------------------------------------

    def _on_chat_tts(self, text: str):
        """Called after a chat response finishes streaming."""
        with self._tts_lock:
            self._tts_gen += 1
            gen = self._tts_gen
        threading.Thread(
            target=self._speak, args=(text, gen), daemon=True
        ).start()

    def _speak(self, text: str, gen: int):
        audio = tts_engine.synthesize(text)
        if not audio or gen != self._tts_gen:
            self.ws.broadcast_state("idle")
            return

        self.ws.broadcast_state("tts_playing")
        b64 = base64.b64encode(audio).decode("ascii")
        self.ws.broadcast_raw(json.dumps({"type": "tts_audio", "value": b64}))

        # Stream the loudness/frequency envelope in real time so the
        # particles vibrate to the voice while the browser plays it.
        env = audio_envelope.frames(audio, fps=30)
        frame_dt = 1.0 / 30.0
        start = time.monotonic()
        for i, f in enumerate(env):
            if gen != self._tts_gen:
                return  # interrupted by a newer utterance
            # Keep wall-clock sync with the browser's playback.
            target = start + i * frame_dt
            drift = target - time.monotonic()
            if drift > 0:
                time.sleep(drift)
            self.ws.broadcast_raw(json.dumps({
                "type": "audio_level",
                "value": f["level"], "low": f["low"],
                "mid": f["mid"], "high": f["high"],
            }))

        if gen == self._tts_gen:
            self.ws.broadcast_raw(json.dumps({"type": "tts_done"}))
            self.ws.broadcast_state("idle")

    def _on_tts_interrupt(self):
        """User interrupted playback → cancel envelope, go to recording."""
        with self._tts_lock:
            self._tts_gen += 1
        self.ws.broadcast_state("recording")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self):
        host = os.environ.get("WHISPERFLOW_HOST", "127.0.0.1")
        port = os.environ.get("WHISPERFLOW_PORT", "8767")
        provider = get_provider().name
        tts = tts_engine.available()

        self.ws.start()
        print("=" * 56)
        print(" WhisperFlow (Linux port) — JARVIS web server")
        print(f"   UI/WS : http://{host}:{port}  (ws shares the port)")
        print(f"   AI    : {provider}")
        print(f"   TTS   : {tts}")
        print(f"   STT   : faster-whisper "
              f"({os.environ.get('WHISPER_MODEL', 'base')})")
        print("   Behind Caddy: reverse_proxy your.domain → "
              f"127.0.0.1:{port}")
        print("   Ctrl+C to stop.")
        print("=" * 56)

        stop = threading.Event()

        def _handle(signum, frame):
            stop.set()

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
        try:
            while not stop.is_set():
                time.sleep(0.5)
        finally:
            print("\n[WhisperFlow] shutting down...")
            self.ws.stop()


def main():
    load_env()
    WhisperFlowServer().run()


if __name__ == "__main__":
    main()
