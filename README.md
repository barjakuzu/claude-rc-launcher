# Claude RC Launcher

Launch and manage [Claude Code](https://docs.anthropic.com/en/docs/claude-code) remote-control sessions from anywhere — your phone, another laptop, wherever you are.

## Install

**Requirements:** Python 3.8+, tmux, Claude CLI (`claude login` first)

```bash
curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/install.sh | bash
```

The installer will:
1. Install cloudflared (for remote access)
2. Set up auth credentials (required)
3. Start the service
4. Give you a public URL you can open from anywhere

Works on **Linux** and **macOS**. Re-run to update.

## Configuration

Edit `~/.config/claude-rc/env`:

| Variable | Default | Description |
|---|---|---|
| `RC_AUTH_USER` / `RC_AUTH_PASS` | *(set during install)* | Login credentials |
| `RC_WORKING_DIR` | `.` | Working directory for sessions |
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
