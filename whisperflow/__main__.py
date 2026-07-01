"""Module entry point.

`python -m whisperflow` runs the macOS menu-bar app when its native deps
(rumps, etc.) are available; otherwise it falls back to the headless
Linux web server (`python -m whisperflow.server`).
"""

try:
    from .app import main  # macOS menu-bar app (needs rumps/pynput)
    _MODE = "macos"
except Exception:  # ImportError on Linux (no rumps), and friends
    from .server import main  # headless web server
    _MODE = "linux"

if __name__ == "__main__":
    main()
