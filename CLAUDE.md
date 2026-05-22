# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python web app that launches and manages Claude CLI remote-control sessions via tmux, with built-in cron scheduling. Designed for remote access: install on a machine, get a public URL via Cloudflare Tunnel, and manage Claude sessions from anywhere.

## Running

```bash
# Direct (requires env vars or .env sourced)
python3 app.py

# Via wrapper (sources ~/.claude-rc/env automatically)
claude-rc

# Update installed version
claude-rc update
```

Runtime has no dependencies beyond Python 3.8+ stdlib, and target devices need no build
step (the frontend is pre-built into `static/dist/` and committed). The React frontend is
built at dev time with Vite (Node only needed on the dev machine). Port 8200 by default.

## Architecture

### Modular design

```
app.py          — Entry point: config loading, server startup, scheduler init
config.py       — Shared constants (env vars → RC_*, VERSION, paths)
server.py       — HTTP handler, routing, auth, project helpers
sessions.py     — tmux session lifecycle (list, create, setup, stop)
tunnel.py       — Cloudflare tunnel management
scheduler.py    — Cron expression parser + scheduler thread
schedules.py    — Schedule storage CRUD (JSON file at ~/.claude-rc/schedules.json)
mcp_server.py   — MCP server for chat-based schedule management (standalone)
stats.py        — Per-device metrics: token-history ring buffer + system load/OS
overview.py     — Hub aggregator: fans out to devices, builds grid cards (/rc/overview)
frontend/       — React + TypeScript SPA (Vite), the V4 "Ops Console" UI — SOURCE
static/dist/    — Built frontend (committed) — served at `/`. Run `npm run build` before commit
static/index.html — LEGACY vanilla-JS UI (app.js + style.css), served at `/legacy`
docs/design-reference/ — frozen V4 prototype the SPA was ported from
```

### Frontend (React/TS V4 — current)

The primary UI is a React + TypeScript SPA in `frontend/`, built with Vite to
`static/dist/` (committed, so target devices need **no Node** — `git pull` + restart
still works). The server serves the SPA at `/` and the old vanilla UI at `/legacy`.

- **Build before committing UI changes:** `cd frontend && npm run build` (outputs to
  `../static/dist`, Vite `base: '/static/dist/'`). Backend tests: `python3 -m unittest discover tests`.
- **Multi-device dashboard:** the V4 UI is a device grid (from `/rc/overview`) → drill
  into a side panel (launcher + Sessions/Scheduled/Logs). The machine selector in the
  header is primary nav. Polls `/rc/overview` every 5s; the open panel polls that device.
- **Design source of truth:** `docs/design-reference/variant-ops-refined.jsx` — match it
  pixel-for-pixel. Dark OKLCH palette (`RT` tokens in `frontend/src/tokens.ts`); device
  cards color-coded by a stable hue hashed from device id.
- A device shows **online** when its `/rc/sessions` is reachable; `/rc/stats` (load/OS/
  sparkline) is best-effort, so a device on older code still appears online.

### Key patterns

- **tmux is the session runtime**: Each Claude session is a tmux session. The app interacts with tmux via `subprocess.run()` calls to send keys, capture pane content, read env vars.
- **Session setup is async**: `setup_session()` runs in a background thread — it handles the trust prompt, waits for Claude's prompt character (`❯` or `>`), sends `/remote-control`, waits for the URL, then sends `/rename`.
- **No frontend framework**: Vanilla JS with `fetch()` calls to `/rc/*` API endpoints. UI refreshes every 5 seconds via `setInterval`.
- **Dark neutral color scheme**: All UI colors use grays/blacks (`#0a0a0a`, `#141414`, `#262626`, `#a3a3a3`). No blue/purple/colored accents.
- **Scheduler is Python, not a Claude session**: The scheduler thread in app.py checks cron expressions every 60 seconds. When a schedule fires, it spawns a fresh independent Claude Code session — no persistent "team lead" session needed.

### API routes

All under `/rc/` prefix:
- `GET /` — serves static/index.html
- `GET /sessions` — list running tmux sessions
- `GET /projects` — list configured project directories
- `GET /version` — returns `{version: "..."}`
- `GET /tunnel/status` — cloudflared tunnel state
- `GET /schedules` — list all schedules with computed `next_run`
- `POST /start` — create new session (params: `name`, `mode`, `workdir`)
- `POST /stop` — stop session by name
- `POST /stop-all` — stop all sessions
- `POST /tunnel/start` / `POST /tunnel/stop` — manage tunnel
- `POST /schedules` — create schedule
- `POST /schedules/update` — update schedule by id
- `POST /schedules/delete` — delete schedule by id
- `POST /schedules/fire` — manually trigger a schedule

### Scheduler flow

1. Scheduler thread sleeps until next minute boundary
2. Loads schedules from JSON, checks each enabled schedule with `cron_matches()`
3. If cron matches and `last_run` is not the same minute → fires the schedule
4. Fire: creates a new tmux session, runs `setup_session()`, then sends the task prompt via `tmux send-keys`
5. Each scheduled task gets its own fresh session with clean context + remote control

### MCP server

`mcp_server.py` is a standalone MCP server (stdio transport) that proxies to the launcher's HTTP API. Users can create/manage schedules by chatting with any Claude Code session that has this MCP configured. Tools: `list_schedules`, `create_schedule`, `update_schedule`, `delete_schedule`, `fire_schedule`.

### Multi-device / hub mode

One instance can act as a **hub** that manages Claude sessions on multiple machines. Every machine runs the same unchanged rc-launcher app; the hub just proxies to them.

- **Registry**: `devices.py` reads `~/.claude-rc/devices.json` — a list of `{id, name, base_url, auth_user, auth_pass}` for each remote device. The hub's own machine is the implicit `local` device (never listed). The file holds credentials → `chmod 600`. `list_devices_public()` strips creds for the UI.
- **Proxy**: `server.py` reads the target device from the `X-RC-Device` request header (fallback `?device=`). If `local`/absent → handled normally. Otherwise `_proxy_to_device()` forwards the request to that device's `base_url` over `urllib`, injecting the device's own Basic Auth and stripping the hub cookie. `/login`, `/logout`, `/static/*`, and `/devices` are never proxied. Unreachable device → `502`; unknown id → `404`.
- **Frontend**: a device dropdown (`#device-select`) populated from `GET /rc/devices`, persisted in `localStorage.rc_device`. `api()` attaches `X-RC-Device` to every call; changing it re-runs `refresh()`. The response shape is identical per device, so no other UI logic changes.
- **Connectivity**: remote devices are reached over Tailscale (use the MagicDNS name in `base_url` so it survives IP changes). The hub is the only publicly-exposed instance (Cloudflare tunnel); remote devices stay private behind the tailnet.
- **Schedules stay per-device**: each device's own scheduler runs its own `schedules.json`. Managing a device's schedules in the UI works transparently because the schedule routes are proxied like everything else. There is no hub-level cross-device scheduler.

### Install infrastructure

- `install.sh` — curl-pipe installer. Clones repo, sets up auth, creates wrapper script, configures launchd (macOS) or systemd (Linux) service. Re-running updates via `git pull`.
- `uninstall.sh` — removes service + app dir, preserves config.
- Everything lives under `~/.claude-rc/`: config at `env`, app code at `app/`, logs at `logs/`, schedules at `schedules.json`.

## Important Gotchas

- **tmux command args**: When launching Claude via tmux, pass the binary and flags as separate list args to `tmux new-session`. Do NOT wrap in a shell (`bash -lc "..."`) — it causes argument splitting issues.
- **Claude trust prompt**: Claude shows a "trust this folder" dialog on first run in a directory. The setup code sends Enter to accept it before waiting for the interactive prompt.
- **PATH in services**: launchd/systemd don't inherit user PATH. The installer writes explicit `PATH=` to the config file and detects the full claude binary path via `which claude`.
- **Polling endpoints are silenced**: `log_message()` suppresses logs for `/rc/sessions`, `/rc/tunnel/status`, `/rc/projects`, `/rc/schedules`, `/rc/version` to avoid noise from the 5-second polling.
- **Module import order**: `config.py` is the root — all other modules import from it. No circular imports: `app.py` → `server.py` → `sessions.py`, `tunnel.py`, `scheduler.py`, `schedules.py`.
