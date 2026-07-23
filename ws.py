"""Minimal RFC6455 WebSocket + tmux control-mode terminal streaming.

Stdlib only. A WS client attaches a `tmux -C attach-session` control client
to the session: pane output streams as %output events (instant, incremental)
instead of the 700ms capture-pane polling snapshots. Keystrokes echo in one
network round-trip.

Wire protocol (JSON text frames):
  server → client: {"type": "data", "data": "<raw terminal bytes>"}
                   {"type": "status", "message": "..."}
  client → server: {"type": "keys", "keys": "<literal text>"}
                   {"type": "special", "special": ["Enter", ...]}
                   {"type": "resize", "cols": N, "rows": N}
"""

import base64
import hashlib
import json
import os
import re
import select
import struct
import subprocess
import threading
import time

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def tunnel_to_device(handler, device):
    """Raw bidirectional tunnel for a WS upgrade request to a remote device.

    urllib can't proxy WebSockets — replay the request over a plain TCP
    connection (Tailscale, http base_url) with the device's Basic auth
    injected, then pump bytes both ways until either side closes.
    """
    import socket as socketlib
    from urllib.parse import urlparse, urlencode, parse_qsl

    u = urlparse(device["base_url"])
    host, port = u.hostname, u.port or 80
    try:
        remote = socketlib.create_connection((host, port), timeout=10)
    except OSError:
        handler.send_error(502)
        return

    # Strip the device query param — on the target, the session is local.
    pu = urlparse(handler.path)
    qs = [(k, v) for k, v in parse_qsl(pu.query) if k != "device"]
    path = pu.path + ("?" + urlencode(qs) if qs else "")

    auth = base64.b64encode(
        f"{device.get('auth_user', '')}:{device.get('auth_pass', '')}".encode()
    ).decode()
    skip = {"authorization", "cookie", "x-rc-device", "host"}
    lines = [f"GET {path} HTTP/1.1",
             f"Host: {host}:{port}",
             f"Authorization: Basic {auth}"]
    for k, v in handler.headers.items():
        if k.lower() not in skip:
            lines.append(f"{k}: {v}")
    try:
        remote.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    except OSError:
        handler.send_error(502)
        return

    client = handler.connection

    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socketlib.SHUT_RDWR)
                except OSError:
                    pass

    t = threading.Thread(target=pump, args=(remote, client), daemon=True)
    t.start()
    pump(client, remote)
    t.join(timeout=5)
    try:
        remote.close()
    except OSError:
        pass

# ── Framing ───────────────────────────────────────────────────────────────────


def accept_key(client_key):
    digest = hashlib.sha1((client_key + _WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def send_frame(sock, payload, opcode=0x1):
    """Send a server→client frame (unmasked)."""
    data = payload.encode() if isinstance(payload, str) else payload
    length = len(data)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    sock.sendall(header + data)


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def read_frame(sock):
    """Read one client→server frame. Returns (opcode, payload bytes)."""
    b1, b2 = _read_exact(sock, 2)
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    length = b2 & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", _read_exact(sock, 2))
    elif length == 127:
        (length,) = struct.unpack("!Q", _read_exact(sock, 8))
    mask = _read_exact(sock, 4) if masked else b""
    payload = _read_exact(sock, length) if length else b""
    if masked:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


# ── tmux control-mode plumbing ────────────────────────────────────────────────

_OCTAL_RE = re.compile(rb"\\([0-7]{3})")


def _unescape_output(data):
    """tmux control mode escapes control bytes as \\ooo octal."""
    return _OCTAL_RE.sub(lambda m: bytes([int(m.group(1), 8)]), data)


def _snapshot(name):
    """Current screen contents + cursor, to seed the terminal on connect."""
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", name, "-e", "-p"],
        capture_output=True, timeout=5,
    )
    out = r.stdout.decode("utf-8", errors="replace")
    if out.endswith("\n"):
        out = out[:-1]
    cur = subprocess.run(
        ["tmux", "display-message", "-p", "-t", name,
         "#{cursor_x} #{cursor_y} #{cursor_flag}"],
        capture_output=True, text=True, timeout=5,
    )
    parts = cur.stdout.split()
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        x, y, visible = int(parts[0]), int(parts[1]), parts[2] == "1"
        out += f"\x1b[{y + 1};{x + 1}H" + ("\x1b[?25h" if visible else "\x1b[?25l")
    return out


def serve_terminal(handler, name):
    """Upgrade the request to a WebSocket and stream the session until close.

    Called from the HTTP handler for GET /sessions/<name>/ws. Takes over the
    connection socket entirely; returns when the client disconnects.
    """
    key = handler.headers.get("Sec-WebSocket-Key", "")
    if not key:
        handler.send_error(400)
        return
    sock = handler.connection
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
    )
    sock.sendall(resp.encode())

    send_lock = threading.Lock()

    def send_json(obj):
        with send_lock:
            send_frame(sock, json.dumps(obj))

    # Attach a control-mode client. Its stdin accepts tmux commands
    # (used for refresh-client resizing); stdout streams %output events.
    proc = subprocess.Popen(
        ["tmux", "-C", "attach-session", "-t", name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0,
    )
    # Follow the most recently active client (native tmux multi-viewer).
    try:
        proc.stdin.write(f"set-option -w -t {name} window-size latest\n".encode())
        proc.stdin.flush()
    except OSError:
        pass

    stop = threading.Event()

    def pump_tmux():
        """tmux control-mode stdout → WS data frames."""
        try:
            buf = b""
            while not stop.is_set():
                r, _, _ = select.select([proc.stdout], [], [], 0.5)
                if not r:
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.startswith(b"%output "):
                        # %output %<pane-id> <escaped bytes>
                        try:
                            _, _, data = line.split(b" ", 2)
                        except ValueError:
                            continue
                        text = _unescape_output(data).decode("utf-8", errors="replace")
                        send_json({"type": "data", "data": text})
                    elif line.startswith(b"%exit"):
                        stop.set()
                        return
                    # %begin/%end/%layout-change/... — ignore
        except (OSError, ConnectionError):
            pass
        finally:
            stop.set()

    pump = threading.Thread(target=pump_tmux, daemon=True)
    pump.start()

    # Seed with the current screen AFTER the pump starts so nothing is lost.
    send_json({"type": "data", "data": _snapshot(name)})

    last_ping = time.time()
    sock.settimeout(1.0)
    try:
        while not stop.is_set():
            if time.time() - last_ping > 30:
                with send_lock:
                    send_frame(sock, b"", opcode=0x9)  # ping
                last_ping = time.time()
            try:
                opcode, payload = read_frame(sock)
            except (TimeoutError, OSError) as e:
                if isinstance(e, OSError) and "timed out" not in str(e).lower():
                    break
                continue
            except ConnectionError:
                break
            if opcode == 0x8:      # close
                break
            if opcode in (0x9,):   # ping → pong
                with send_lock:
                    send_frame(sock, payload, opcode=0xA)
                continue
            if opcode == 0xA:      # pong
                continue
            if opcode != 0x1:      # only text frames carry our protocol
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            mtype = msg.get("type")
            if mtype == "keys" and msg.get("keys"):
                subprocess.run(
                    ["tmux", "send-keys", "-t", name, "-l", str(msg["keys"])],
                    capture_output=True, timeout=5,
                )
            elif mtype == "special" and isinstance(msg.get("special"), list):
                keys = [str(k) for k in msg["special"][:8]]
                subprocess.run(
                    ["tmux", "send-keys", "-t", name, *keys],
                    capture_output=True, timeout=5,
                )
            elif mtype == "resize":
                try:
                    cols = max(40, min(500, int(msg.get("cols"))))
                    rows = max(10, min(200, int(msg.get("rows"))))
                except (TypeError, ValueError):
                    continue
                try:
                    proc.stdin.write(f"refresh-client -C {cols}x{rows}\n".encode())
                    proc.stdin.flush()
                except OSError:
                    pass
    finally:
        stop.set()
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        # If this was the last client, restore the wide layout that the
        # background status/token parsing expects.
        try:
            clients = subprocess.run(
                ["tmux", "list-clients", "-t", name, "-F", "#{client_name}"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if not clients:
                subprocess.run(
                    ["tmux", "resize-window", "-t", name, "-x", "200", "-y", "50"],
                    capture_output=True, timeout=5,
                )
        except (OSError, subprocess.SubprocessError):
            pass
