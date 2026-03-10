# Claude RC Launcher

Launch and manage [Claude Code](https://docs.anthropic.com/en/docs/claude-code) remote-control sessions from anywhere — your phone, another laptop, wherever you are.

<p align="center">
  <img src="launcher.gif" alt="Claude RC Launcher" width="700">
</p>

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

After install, the launcher runs in the background — just open **http://localhost:8200** or use the remote URL printed by the installer. No need to run anything manually.

Works on **Linux** and **macOS**.

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

**Quick kill (any platform):**

```bash
kill $(lsof -ti :8200)
```

The service auto-restarts if managed by launchd/systemd.

## Multi-Project Setup

To launch sessions in different project directories, edit `~/.config/claude-rc/env`:

```
RC_PROJECTS=/path/to/project-a,/path/to/project-b
```

Restart the service to apply. A project picker will appear in the UI. You can also browse to any folder using the built-in directory browser.

## Configuration

Edit `~/.config/claude-rc/env`:

| Variable | Default | Description |
|---|---|---|
| `RC_AUTH_USER` / `RC_AUTH_PASS` | *(set during install)* | Login credentials |
| `RC_WORKING_DIR` | `$HOME` | Default working directory for sessions |
| `RC_PROJECTS` | *(unset)* | Comma-separated project paths for the picker |
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
