"""Cloudflare Tunnel management."""

import atexit
import re
import shutil
import subprocess
import threading

from config import PORT

_tunnel_proc = None
_tunnel_url = None
_tunnel_lock = threading.Lock()


def cloudflared_available():
    """Check if cloudflared binary is on PATH."""
    return shutil.which("cloudflared") is not None


def start_tunnel():
    """Start a cloudflared quick tunnel pointing at our server."""
    global _tunnel_proc, _tunnel_url
    with _tunnel_lock:
        if _tunnel_proc and _tunnel_proc.poll() is None:
            return  # already running
        _tunnel_url = None
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _tunnel_proc = proc

        def _read_url():
            global _tunnel_url
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace")
                m = re.search(r'(https://[a-z0-9-]+\.trycloudflare\.com)', line)
                if m:
                    _tunnel_url = m.group(1)
                    print(f"  Tunnel URL: {_tunnel_url}")
                    break

        t = threading.Thread(target=_read_url, daemon=True)
        t.start()


def stop_tunnel():
    """Stop the cloudflared tunnel process."""
    global _tunnel_proc, _tunnel_url
    with _tunnel_lock:
        if _tunnel_proc:
            try:
                _tunnel_proc.terminate()
                _tunnel_proc.wait(timeout=5)
            except Exception:
                try:
                    _tunnel_proc.kill()
                except Exception:
                    pass
            _tunnel_proc = None
            _tunnel_url = None


def get_tunnel_status(auth_user, auth_pass):
    """Return tunnel status dict for API responses."""
    global _tunnel_proc, _tunnel_url
    running = _tunnel_proc is not None and _tunnel_proc.poll() is None
    if not running and _tunnel_proc is not None:
        _tunnel_proc = None
        _tunnel_url = None
    return {
        "available": cloudflared_available(),
        "running": running,
        "url": _tunnel_url if running else None,
        "auth_configured": bool(auth_user and auth_pass),
    }


atexit.register(stop_tunnel)
