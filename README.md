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

### Creating a Schedule (Wizard)

1. Click **"+ New"** on the Scheduled Tasks tab
2. **Describe your task** — write what you want Claude to do (e.g. "Check our website for broken links and report results")
3. **Pick a frequency** — choose from presets like "Every hour", "Daily at 9 AM", "Weekdays at 9 AM", etc.
4. **Configure options** — set the working directory, launch mode, and task name
5. **Create with Claude** — this launches a live Claude Code session marked as a "wizard" session. Open it to refine the task details interactively with Claude — answer questions, clarify requirements, and finalize the schedule prompt together
6. Once finalized, the schedule appears in the UI and fires automatically on the cron

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

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/uninstall.sh | bash
```

## License

[MIT](LICENSE)
