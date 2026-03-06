#!/usr/bin/env python3
"""Claude RC Launcher — start/stop claude remote-control sessions."""

import http.server
import subprocess

from config import VERSION, HOST, PORT, WORKING_DIR, CLAUDE_BIN, AUTH_USER
from tunnel import cloudflared_available
from scheduler import start_scheduler
from server import Handler


if __name__ == "__main__":
    print(f"Claude RC Launcher v{VERSION}")
    print(f"Listening on {HOST}:{PORT}")
    print(f"Working directory: {WORKING_DIR}")
    print(f"Claude binary: {CLAUDE_BIN}")
    if AUTH_USER:
        print("Basic auth: enabled")
    if cloudflared_available():
        print("Cloudflared: available")

    # Start the scheduler thread
    start_scheduler()

    server = http.server.HTTPServer((HOST, PORT), Handler)
    server.serve_forever()
