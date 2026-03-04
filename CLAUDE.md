# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A single-file Python web app that launches and manages Claude CLI remote-control sessions via tmux. The entire application — backend, HTML, CSS, and JS — lives in `app.py`. It's designed for remote access: install on a machine, get a public URL via Cloudflare Tunnel, and manage Claude sessions from anywhere.

## Running

```bash
# Direct (requires env vars or .env sourced)
python3 app.py

# Via wrapper (sources ~/.config/claude-rc/env automatically)
claude-rc

# Update installed version
claude-rc update
```

No build step, no dependencies beyond Python 3.8+ stdlib. Port 8200 by default.

## Architecture

### Single-file design (`app.py`, ~890 lines)

Everything is in one file by design. The structure:

1. **Config & globals** (lines 1-38): Environment variables → constants. All config via `RC_*` env vars.
2. **Cloudflare Tunnel** (lines 40-91): `_start_tunnel()` / `_stop_tunnel()` manage a `cloudflared` subprocess. Tunnel URL is captured from stderr via regex in a daemon thread.
3. **Project helpers** (lines 94-111): `_parse_projects()` reads `RC_PROJECTS` comma-separated paths.
4. **Session management** (lines 114-253): tmux session lifecycle — list, create, setup (trust prompt → wait for prompt → `/remote-control` → `/rename`), stop.
5. **Auth** (lines 255-277): HTTP Basic Auth via `RC_AUTH_USER`/`RC_AUTH_PASS`.
6. **HTML_PAGE** (lines 279-710): Raw string (`r"""..."""`) containing the complete frontend. CSS, HTML, and JS are all inline. Uses inline SVG icons (no external dependencies).
7. **HTTP Handler** (lines 713-877): `http.server.BaseHTTPRequestHandler` subclass. Routes are simple string matching on path.

### Key patterns

- **tmux is the session runtime**: Each Claude session is a tmux session. The app interacts with tmux via `subprocess.run()` calls to send keys, capture pane content, read env vars.
- **Session setup is async**: `_setup_session()` runs in a background thread — it handles the trust prompt, waits for Claude's prompt character (`❯` or `>`), sends `/remote-control`, waits for the URL, then sends `/rename`.
- **HTML uses raw string**: `HTML_PAGE = r"""..."""` — the `r` prefix is important because the JS/SVG content contains backslashes. The only dynamic value injected is `VERSION` via string concatenation.
- **No frontend framework**: Vanilla JS with `fetch()` calls to `/rc/*` API endpoints. UI refreshes every 5 seconds via `setInterval`.
- **Dark neutral color scheme**: All UI colors use grays/blacks (`#0a0a0a`, `#141414`, `#262626`, `#a3a3a3`). No blue/purple/colored accents.

### API routes

All under `/rc/` prefix:
- `GET /` — serves HTML_PAGE
- `GET /sessions` — list running tmux sessions
- `GET /projects` — list configured project directories
- `GET /tunnel/status` — cloudflared tunnel state
- `POST /start` — create new session (params: `name`, `mode`, `workdir`)
- `POST /stop` — stop session by name
- `POST /stop-all` — stop all sessions
- `POST /tunnel/start` / `POST /tunnel/stop` — manage tunnel

### Install infrastructure

- `install.sh` — curl-pipe installer. Clones repo, sets up auth, creates wrapper script, configures launchd (macOS) or systemd (Linux) service. Re-running updates via `git pull`.
- `uninstall.sh` — removes service + app dir, preserves config.
- Config lives at `~/.config/claude-rc/env`, app at `~/.local/share/claude-rc/`.

## Important Gotchas

- **Backslashes in HTML_PAGE**: The raw string prefix `r"""` means Python won't interpret `\n`, `\'` etc. But when generating JS strings that need literal backslashes (e.g., escaping quotes in onclick handlers), use `\\'` not `\'`.
- **tmux command args**: When launching Claude via tmux, pass the binary and flags as separate list args to `tmux new-session`. Do NOT wrap in a shell (`bash -lc "..."`) — it causes argument splitting issues.
- **Claude trust prompt**: Claude shows a "trust this folder" dialog on first run in a directory. The setup code sends Enter to accept it before waiting for the interactive prompt.
- **PATH in services**: launchd/systemd don't inherit user PATH. The installer writes explicit `PATH=` to the config file and detects the full claude binary path via `which claude`.
- **Polling endpoints are silenced**: `log_message()` suppresses logs for `/rc/sessions`, `/rc/tunnel/status`, `/rc/projects` to avoid noise from the 5-second polling.
