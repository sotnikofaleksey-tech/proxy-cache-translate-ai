"""Single-instance guard for Proxy Station (GUI, console and headless modes).

All launch modes bind the same ports, so only one may run per user at a time.
The lock is an OS-level file lock in the per-user data folder: it is released
automatically when the process dies, so there is never a stale lock to clean up.
"""

from __future__ import annotations

import os

from core.config import DATA_DIR

LOCK_FILE = os.path.join(DATA_DIR, "instance.lock")
_handle = None  # kept open for the lifetime of the process


def acquire() -> bool:
    """Try to become the single running instance.  Returns False if one exists."""
    global _handle
    if _handle is not None:
        return True
    os.makedirs(DATA_DIR, exist_ok=True)
    fh = open(LOCK_FILE, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _handle = fh
    return True


def notify_already_running(gui: bool) -> None:
    """Tell the user why this launch is exiting (message box for GUI, print otherwise)."""
    text = (
        "Proxy Station уже запущен.\n\n"
        "Найдите его значок в системном трее (возле часов) или закройте "
        "запущенный экземпляр (stop_all.bat) и повторите запуск.\n\n"
        "Proxy Station is already running - look for its tray icon."
    )
    if gui and os.name == "nt" and not os.environ.get("PROXYSTATION_NO_DIALOG"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "Proxy Station", 0x40)
            return
        except Exception:
            pass
    print(text)
