# Claude RC Launcher

Launch and manage [Claude Code](https://docs.anthropic.com/en/docs/claude-code) remote-control sessions from anywhere — your phone, another laptop, wherever you are.

<p align="center">
  <img src="launcher.gif" alt="Claude RC Launcher" width="700">
</p>

## Features

- **Session management** — Launch, stop, restart Claude Code sessions via web UI
- **Remote access** — Built-in Cloudflare Tunnel for public HTTPS URLs
- **Scheduled tasks** — Cron-based scheduler to run Claude sessions on autopilot
- **Browser automation** — Use [`playwright-cli`](https://www.npmjs.com/package/@playwright/cli) for headless browser control in scheduled tasks
- **Resume sessions** — Pick up where you left off with session resume
- **Multi-project** — Browse to any directory or configure project shortcuts

## Install

**Requirements:** Python 3.8+, tmux, Claude CLI (`claude login` first)

```bash
curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/install.sh | bash
```

The installer will:
1. Install cloudflared (for remote access)
2. Set up auth credentials (required)
3. Auto-detect your Claude CLI path
4. Start the service automatically
5. Give you a public URL you can open from anywhere

After install, open **http://localhost:8200** or use the remote URL printed by the installer.

Works on **Linux** and **macOS**.

## Browser Automation

Claude Code sessions launched by the scheduler can automate browsers using `playwright-cli`. This is ideal for tasks like web scraping, form filling, automated testing, or scheduled workflows that interact with websites.

### Setup

Install `playwright-cli`:

```bash
npm install -g @playwright/cli
```

Add browser automation instructions to your global `~/.claude/CLAUDE.md`:

```markdown
## Browser Automation

For ALL browser operations, use `playwright-cli` commands via the Bash tool.

Key patterns:
- Use named sessions: `playwright-cli -s=name <command>`
- Run `snapshot` after navigation to get element refs
- Run `screenshot` after key actions to verify results
- Auth states saved at `~/.playwright/states/<sitename>-auth`
- Before authenticated automation, load saved state with `state-load`
- After successful login, always `state-save` immediately
```

### Common Commands

```bash
# Open browser and navigate
playwright-cli -s=mybrowser open
playwright-cli -s=mybrowser goto "https://example.com"

# Get page structure (element refs for clicking/filling)
playwright-cli -s=mybrowser snapshot

# Interact with elements (refs come from snapshot)
playwright-cli -s=mybrowser click ref123
playwright-cli -s=mybrowser fill ref456 "search text"

# Save/load authentication state
playwright-cli -s=mybrowser state-save ~/.playwright/states/mysite-auth
playwright-cli -s=mybrowser state-load ~/.playwright/states/mysite-auth

# Visual verification
playwright-cli -s=mybrowser screenshot

# Cleanup
playwright-cli -s=mybrowser close
```

### Example: Scheduled Browser Task

Create a schedule via the UI or API that uses browser automation:

```bash
curl -u admin:pass -X POST http://localhost:8200/rc/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "daily-scrape",
    "cron": "0 9 * * *",
    "prompt": "Open a browser session, navigate to https://example.com, take a snapshot, extract the data, save to ~/results.json. Use playwright-cli -s=scraper for all browser commands.",
    "workdir": "/root",
    "mode": "c",
    "model": "2"
  }'
```

The scheduled session will launch Claude Code with the prompt, and Claude will use `playwright-cli` to control a headless browser — no external Chrome process, no MCP servers, no Xvfb needed.

### Auth Persistence

For sites requiring login, authenticate once manually then save the state:

```bash
# Login interactively (one-time)
playwright-cli -s=mysite open --headed
playwright-cli -s=mysite goto "https://mysite.com/login"
# ... fill credentials, click login ...
playwright-cli -s=mysite state-save ~/.playwright/states/mysite-auth
playwright-cli -s=mysite close

# Scheduled tasks load the saved state automatically
# Just include in your schedule prompt:
#   "Load auth state from ~/.playwright/states/mysite-auth before navigating"
```

## Scheduled Tasks

The built-in scheduler runs Claude Code sessions on a cron schedule — fully autonomous, no human in the loop.

### Creating a Schedule

1. Click **"+ New"** on the Scheduled Tasks tab
2. **Write the prompt** — what you want Claude to do (e.g. "Check our website for broken links and report results")
3. **Pick a frequency** — choose from presets like "Every hour", "Daily at 9 AM", "Weekdays at 9 AM", etc., or write the cron expression yourself
4. **Configure options** — set the working directory, launch mode, and task name
5. Save — the schedule appears in the UI immediately and fires automatically on the cron

You can also create and manage schedules by chatting with any Claude Code session that has `mcp_server.py` configured as an MCP server — it exposes `create_schedule`, `list_schedules`, `update_schedule`, `delete_schedule`, and `fire_schedule` tools that proxy to the same HTTP API.

### How It Works

Each scheduled task:
- Spawns a fresh Claude Code session at the scheduled time
- Sends your prompt automatically
- Runs with full tool access (Bash, file editing, browser automation via `playwright-cli`)
- Logs run history in the UI (success/failure per run)
- Can be manually triggered anytime with "Run Now"

You can also create schedules directly via the **Edit** modal (for advanced users who want to write the cron expression and prompt themselves) or the HTTP API.


## Update

```bash
claude-rc update
```

Then restart the service (see below).

## Restart / Stop / Start

**macOS (launchd):**

```bash
# Restart (after update or config change)
launchctl unload ~/Library/LaunchAgents/com.claude-rc.launcher.plist
launchctl load ~/Library/LaunchAgents/com.claude-rc.launcher.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.claude-rc.launcher.plist

# Start
launchctl load ~/Library/LaunchAgents/com.claude-rc.launcher.plist
```

**Linux (systemd):**

```bash
# Restart
systemctl --user restart claude-rc

# Stop
systemctl --user stop claude-rc

# Start
systemctl --user start claude-rc

# View logs
journalctl --user -u claude-rc -f
```

## Configuration

Edit `~/.claude-rc/env`:

| Variable | Default | Description |
|---|---|---|
| `RC_AUTH_USER` / `RC_AUTH_PASS` | *(set during install)* | Login credentials |
| `RC_WORKING_DIR` | `$HOME` | Default working directory for sessions |
| `RC_PROJECTS` | *(unset)* | Comma-separated project paths for quick picker |
| `RC_PORT` | `8200` | Listen port |

## Launch Modes

- **Standard** — skip permissions, no approval prompts
- **Teammate** — skip permissions + teammate mode
- **Safe** — normal permission checks apply
- **Shell** — a plain login shell instead of Claude Code, for running ordinary
  commands from the browser. It has no model, no remote-control URL, no token
  meter and no history view; restarting it just gives you a fresh shell.
  Set `RC_SHELL_BIN` to override which shell is launched (defaults to `$SHELL`,
  then `/bin/bash`).

## Security

- **Login page** with cookie-based sessions (no browser Basic Auth dialogs)
- **CSRF protection** on the login form (double-submit cookie pattern)
- **Rate limiting** — 5 failed login attempts per IP locks out for 15 minutes
- **HttpOnly, SameSite cookies** — session tokens can't be read by JavaScript or sent cross-site
- **Basic Auth fallback** — still works for curl/API access
- Credentials stored in `~/.claude-rc/env` with restricted file permissions (600)
- Always use HTTPS in production (via Cloudflare Tunnel or nginx reverse proxy with SSL)

### Post-Installation Checklist

1. Change default credentials: edit `~/.claude-rc/env` and restart the service
2. Set up HTTPS: use the **Share** button (Cloudflare Tunnel) or put behind an nginx reverse proxy with SSL
3. (Optional) Install [playwright-cli](https://www.npmjs.com/package/@anthropic-ai/claude-code-playwright) for browser automation in scheduled tasks
4. (Optional) Set up [Vaultwarden](https://github.com/dani-garcia/vaultwarden) for secure credential management in automated tasks

## Optional Integrations

### Browser Automation (playwright-cli)

For scheduled tasks that involve browser automation (job applications, web scraping, form filling):

```bash
npm install -g @anthropic-ai/claude-code-playwright
```

The launcher itself does **not** require playwright-cli — only scheduled tasks that automate browsers need it. See the [Browser Automation](#browser-automation) section above for details.

### Credential Management (Vaultwarden / Bitwarden)

For scheduled tasks that need to log into external services, we recommend using a self-hosted credential vault instead of hardcoding passwords:

- **[Vaultwarden](https://github.com/dani-garcia/vaultwarden)** — lightweight self-hosted Bitwarden server
- Use the `bw` CLI to fetch credentials at runtime: `bw get password "ServiceName"`
- This keeps secrets out of task instructions and version control

Not required for the launcher itself — only for automated tasks that interact with authenticated services.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/uninstall.sh | bash
```

## License

[MIT](LICENSE)


## Hook events (fleet visibility)

RC Launcher can show what a session is doing across every device, not
just the ones it launched, by having Claude Code call a tiny spooler
script (`rc-hook`) on a handful of hook events. It is entirely optional
and safe on a device without the launcher installed: every hook command
is guarded, so it's a no-op there, and events only ever arrive from
devices that actually have `rc-hook` installed.

### What's hooked, and why only five events

The block in `docs/hooks/settings.snippet.json` hooks exactly five
events: `StopFailure`, `Notification`, `SubagentStop`, `PreCompact`, and
`SessionEnd`. It deliberately does **not** hook `SessionStart`,
`UserPromptSubmit`, or `Stop`. Session existence and busy/idle state
already come from `claude agents --json`, which the hub polls every 30s
and treats as the source of truth; hooks exist only to capture what
polling can't see — the quiet four plus `SessionEnd`'s `reason` (a poll
only ever sees a session vanish, never why). Adding `rc-hook` to
`SessionStart` or `Stop` would tax every session on every device for
signal we already have from polling, and hooking `UserPromptSubmit`
would spawn a process on every single prompt for a `prompt_len` nobody
needs.

### Installing the block

The file is a standalone, ready-to-paste `hooks` object: merge it as-is
into your (usually shared) `~/.claude/settings.json` under its own
`hooks` key. `install.sh` already places the spooler at
`~/.claude-rc/bin/rc-hook`; nothing else is required.

If `~/.claude/settings.json` already has entries under one of these five
events (today it doesn't), **append** our entry to that event's existing
array — never replace it — so a hook you add there later isn't clobbered
by re-applying this fragment.

Plugin-provided hooks are a separate thing entirely: they live in each
plugin's own `hooks/hooks.json` and are merged in by Claude Code at
runtime, not through `settings.json`. Several plugins already hook
`SessionStart`, `UserPromptSubmit`, `Stop`, and `SessionEnd` this way;
that's independent of this block and needs no action here — Claude Code
runs both sources.

### Timeout

Every hook entry in the snippet carries an explicit `"timeout": 2`
(seconds). Other installed plugins declare hook timeouts of 900s (codex,
on `Stop`), 180s (security-guidance, on `SessionStart`), and 30s
(impeccable, on `Stop`), so a wedged hook can already hold a session for
minutes; `rc-hook`'s 2s cap bounds our contribution to that worst case,
and is why the hook does no network I/O and writes a single line.

### Retention

The spooler never sends anything over the network, never stores prompt
text, and exits successfully even when it can't do anything: a hook must
never block or fail a Claude Code session because of it.

Retention doesn't depend on the launcher running. After every append,
`rc-hook` checks the spool file it just wrote: past 8 MB it's rotated
aside to `<date>.jsonl.1` (clobbering any previous one), and spool files
older than 7 days are deleted. Worst case on disk per device: today's
file (up to 8 MB) + yesterday's rotated `.1` file (up to 8 MB) + up to 7
more same-day files that haven't hit the rotation cap yet. The launcher
itself also prunes old spool files (via `events.prune`, at most once an
hour) as a second, redundant path — the self-limiting behavior above is
what keeps disk bounded even when the launcher process is stopped.
