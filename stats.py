"""Cheap per-device metrics: system load/OS + a token-history ring buffer."""

import os
import platform
import threading

_HISTORY = []      # last N summed-token samples
_MAX = 12
_lock = threading.Lock()

def sample_tokens(total_fn):
    """Append the current summed-token total (from total_fn()) to the ring buffer."""
    try:
        val = int(total_fn())
    except Exception:
        val = 0
    with _lock:
        _HISTORY.append(val)
        if len(_HISTORY) > _MAX:
            del _HISTORY[: len(_HISTORY) - _MAX]

def token_history():
    """Return a copy of the token history (oldest -> newest)."""
    with _lock:
        return list(_HISTORY)

def _os_pretty():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()

def system_stats():
    """Return {loadavg:[1m,5m,15m], cores, os}."""
    try:
        load = list(os.getloadavg())
    except (OSError, AttributeError):
        load = [0.0, 0.0, 0.0]
    return {"loadavg": load, "cores": os.cpu_count() or 1, "os": _os_pretty()}
