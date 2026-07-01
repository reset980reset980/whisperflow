"""Minimal .env loader (stdlib only, no python-dotenv dependency).

Loads KEY=VALUE lines from a .env file into os.environ without
overwriting variables already set in the real environment.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path | None = None) -> bool:
    """Load .env into os.environ. Returns True if a file was read.

    Search order when *path* is None:
      1. $WHISPERFLOW_ENV
      2. ./.env (current working dir)
      3. <repo-root>/.env  (parent of this package)
    Existing environment variables take precedence (not overwritten).
    """
    candidates = []
    if path:
        candidates.append(Path(path))
    else:
        env_override = os.environ.get("WHISPERFLOW_ENV")
        if env_override:
            candidates.append(Path(env_override))
        candidates.append(Path.cwd() / ".env")
        candidates.append(Path(__file__).resolve().parent.parent / ".env")

    for candidate in candidates:
        try:
            if candidate.is_file():
                _parse_into_environ(candidate)
                return True
        except OSError:
            continue
    return False


def _parse_into_environ(file_path: Path) -> None:
    for raw in file_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Quoted value: strip matching quotes, keep contents verbatim.
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        else:
            # Unquoted: strip a trailing inline comment ( value  # note ).
            hash_idx = value.find(" #")
            if hash_idx != -1:
                value = value[:hash_idx]
            value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
