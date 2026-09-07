#!/usr/bin/env python3
"""Claude RC Launcher — start/stop claude remote-control sessions."""

import http.server
import subprocess

import os

from config import VERSION, HOST, PORT, WORKING_DIR, CLAUDE_BIN, AUTH_USER, RC_HOME
from tunnel import cloudflared_available
from scheduler import start_scheduler
from server import Handler
import store
import fleetpoll
import server as server_module


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

    # Start the hub fleet poller (SQLite store shared with server.py's
    # /api/fleet* routes via server.HUB_STORE). on_change wakes every open
    # /api/fleet/stream SSE connection.
    server_module.HUB_STORE = store.Store(os.path.join(RC_HOME, "hub.db"))
    fleetpoll.FleetPoller(server_module.HUB_STORE, on_change=server_module.notify_fleet_changed).start()

    # ThreadingHTTPServer: a request proxied to a remote device blocks its own
    # handler thread (waiting on the network) without stalling other requests.
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()
