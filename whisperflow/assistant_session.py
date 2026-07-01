"""
Assistant Session — multi-session chat manager (Linux port).

Originally this drove the Claude Code CLI as a subprocess with
``--resume session_id`` for memory and full tool access. The Linux port
replaces that with a stateless streaming HTTP API (see ai_backend.py):

- Memory is kept in-process as a per-session ``messages`` list and
  resent on every turn (capped), since the HTTP API is stateless.
- ``send_stream`` yields the SAME dict shape the WebSocket server and UI
  already expect, so ws_server.py / jarvis.html are untouched:
      {"type": "assistant", "message": {"content": [{"type": "text",
                                                      "text": <CUMULATIVE>}]}}
  The ``text`` field carries the full accumulated response each chunk
  (matching the old Claude CLI stream-json behaviour), NOT deltas.
- No tool use / no second-brain vault access: this port answers text.

Public interface (session_manager, create_session, send_stream, ...) is
preserved for backward compatibility.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Generator

from .ai_backend import (
    AIError,
    DEFAULT_SYSTEM_PROMPT,
    default_model_alias,
    get_provider,
)

DEFAULT_MODEL = "haiku"

SESSIONS_DIR = Path.home() / ".whisperflow"
SESSIONS_FILE = SESSIONS_DIR / "assistant_sessions.json"

# How many prior messages (user+assistant) to resend as context.
HISTORY_LIMIT = 20


class AssistantSession:
    """A single chat session with its own rolling message history."""

    def __init__(
        self,
        name: str,
        cwd: str | None = None,
        model_alias: str = DEFAULT_MODEL,
        session_id: str | None = None,
        message_count: int = 0,
        created_at: float | None = None,
        total_cost_usd: float = 0.0,
    ):
        self.lock = threading.Lock()
        self.name = name
        self.cwd = cwd or str(Path.home())
        self.model_alias = model_alias
        self.model = get_provider().resolve_model(model_alias)
        self.session_id = session_id
        self.message_count = message_count
        self.created_at = created_at
        self.total_cost_usd = total_cost_usd
        # In-memory conversation history (not persisted across restarts).
        self.messages: list[dict] = []

    @property
    def is_initialized(self) -> bool:
        return self.session_id is not None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "cwd": self.cwd,
            "model": self.model_alias,
            "message_count": self.message_count,
            "created_at": self.created_at,
            "total_cost_usd": self.total_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AssistantSession":
        return cls(
            name=data.get("name", "unnamed"),
            cwd=data.get("cwd"),
            model_alias=data.get("model", DEFAULT_MODEL),
            session_id=data.get("session_id"),
            message_count=data.get("message_count", 0),
            created_at=data.get("created_at"),
            total_cost_usd=data.get("total_cost_usd", 0.0),
        )

    def change_model(self, model_alias: str) -> str:
        """Switch model → start a fresh conversation."""
        self.model_alias = model_alias
        self.model = get_provider().resolve_model(model_alias)
        self.reset()
        return self.model

    def reset(self):
        """Reset conversation (same settings, new context)."""
        self.session_id = None
        self.message_count = 0
        self.created_at = None
        self.total_cost_usd = 0.0
        self.messages = []

    def status(self) -> dict:
        return {
            **self.to_dict(),
            "model_full": self.model,
            "is_initialized": self.is_initialized,
            "uptime_seconds": (
                int(time.time() - self.created_at) if self.created_at else 0
            ),
        }


class SessionManager:
    """Manages multiple chat sessions (tabs)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.sessions: dict[str, AssistantSession] = {}
        self._load_sessions()

    # ── Session CRUD ──

    def create_session(
        self,
        tab_id: str,
        name: str,
        cwd: str | None = None,
        model_alias: str | None = None,
    ) -> AssistantSession:
        with self._lock:
            if tab_id in self.sessions:
                return self.sessions[tab_id]
            session = AssistantSession(
                name=name,
                cwd=cwd,
                model_alias=model_alias or default_model_alias(),
            )
            self.sessions[tab_id] = session
            self._save_sessions_unlocked()
            return session

    def delete_session(self, tab_id: str):
        with self._lock:
            self.sessions.pop(tab_id, None)
            self._save_sessions_unlocked()

    def get_session(self, tab_id: str) -> AssistantSession | None:
        return self.sessions.get(tab_id)

    def list_sessions(self) -> list[dict]:
        return [
            {"tab_id": tid, **s.status()} for tid, s in self.sessions.items()
        ]

    def rename_session(self, tab_id: str, name: str):
        session = self._require_session(tab_id)
        with session.lock:
            session.name = name
        with self._lock:
            self._save_sessions_unlocked()

    def reset_session(self, tab_id: str):
        session = self._require_session(tab_id)
        with session.lock:
            session.reset()
        with self._lock:
            self._save_sessions_unlocked()

    # ── Messaging ──

    def send(self, tab_id: str, text: str, timeout: int = 120) -> str:
        """Synchronous response (collects the stream into a string)."""
        session = self._require_session(tab_id)
        with session.lock:
            parts: list[str] = []
            for chunk in self._stream_locked(session, text):
                if chunk.get("type") == "assistant":
                    blocks = chunk["message"]["content"]
                    if blocks:
                        parts = [blocks[0]["text"]]  # cumulative → replace
                elif chunk.get("type") == "error":
                    return f"Error: {chunk['error']}"
            return parts[0] if parts else "..."

    def send_stream(
        self, tab_id: str, text: str, timeout: int = 120,
        image: str | None = None,
    ) -> Generator[dict, None, None]:
        """Streaming response. Yields cumulative assistant dicts.

        *image* is an optional data URL (e.g. from the browser camera);
        it is sent to the vision model for this turn only.
        """
        session = self._require_session(tab_id)
        with session.lock:
            yield from self._stream_locked(session, text, image)

    # ── Internals ──

    def _require_session(self, tab_id: str) -> AssistantSession:
        session = self.sessions.get(tab_id)
        if session is None:
            raise KeyError(f"Session not found: {tab_id}")
        return session

    def _stream_locked(
        self, session: AssistantSession, text: str, image: str | None = None
    ) -> Generator[dict, None, None]:
        """Core streaming. Caller holds session.lock."""
        if session.session_id is None:
            session.session_id = str(uuid.uuid4())
            session.created_at = time.time()

        if image:
            # OpenAI multimodal content; the image rides along for this
            # turn only (see cleanup below) so history stays cheap.
            session.messages.append({"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image}},
            ]})
        else:
            session.messages.append({"role": "user", "content": text})
        # Cap context window (keep the most recent turns).
        if len(session.messages) > HISTORY_LIMIT:
            session.messages = session.messages[-HISTORY_LIMIT:]

        provider = get_provider()
        model = provider.resolve_model(session.model_alias)
        accumulated = ""

        try:
            for delta in provider.stream(
                model, list(session.messages), DEFAULT_SYSTEM_PROMPT
            ):
                accumulated += delta
                yield {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": accumulated}]
                    },
                }
        except AIError as e:
            self._strip_image(session, text, image)
            yield {"type": "error", "error": str(e)}
            return
        except Exception as e:  # pragma: no cover - defensive
            self._strip_image(session, text, image)
            yield {"type": "error", "error": f"{type(e).__name__}: {e}"}
            return

        # Replace the heavy image payload with a text marker so later turns
        # don't resend hundreds of KB of base64 on every request.
        self._strip_image(session, text, image)

        # Persist assistant turn into history for multi-turn memory.
        session.messages.append({"role": "assistant", "content": accumulated})
        session.message_count += 1
        with self._lock:
            self._save_sessions_unlocked()

        yield {"type": "result", "result": accumulated,
               "session_id": session.session_id}

    @staticmethod
    def _strip_image(session: AssistantSession, text: str, image: str | None):
        """Swap this turn's multimodal content for a cheap text marker."""
        if image and session.messages and isinstance(
            session.messages[-1].get("content"), list
        ):
            session.messages[-1]["content"] = f"{text} [사진 첨부됨]"

    # ── Persistence ──

    def _save_sessions_unlocked(self):
        try:
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            data = {tid: s.to_dict() for tid, s in self.sessions.items()}
            SESSIONS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"[Assistant] Failed to save sessions: {e}")

    def _load_sessions(self):
        if not SESSIONS_FILE.exists():
            return
        try:
            data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
            for tid, sdata in data.items():
                self.sessions[tid] = AssistantSession.from_dict(sdata)
        except Exception as e:
            print(f"[Assistant] Failed to load sessions: {e}")


# Global session manager
session_manager = SessionManager()
