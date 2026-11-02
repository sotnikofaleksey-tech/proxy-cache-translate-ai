"""Portable Proxy core.

Importing the package guarantees usable ``sys.stdout`` / ``sys.stderr`` even under
``pythonw.exe`` (windowless launch), where both are ``None`` and any ``print()``,
``logging`` or uvicorn setup would otherwise crash.  Output is redirected to a
log file in the per-user data folder (never into the application folder).
"""

from __future__ import annotations

import os
import sys


def _ensure_std_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = None
    try:
        from core.config import LOG_DIR, LOG_FILE

        os.makedirs(LOG_DIR, exist_ok=True)
        try:  # simple rotation: keep the log below ~2 MB
            if os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except OSError:
            pass
        stream = open(LOG_FILE, "a", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        try:
            stream = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            stream = None
    if stream is not None:
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream


_ensure_std_streams()
