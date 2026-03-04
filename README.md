# Claude RC Launcher

A web UI to launch and manage [Claude Code](https://docs.anthropic.com/en/docs/claude-code) remote-control sessions from any browser — including your phone.

## Install

**Requirements:** Python 3.8+, tmux, Claude CLI (`claude login` first)

```bash
curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/install.sh | bash
```

This works on both **Linux** and **macOS**. Re-run to update.

### Or run manually

```bash
git clone https://github.com/barjakuzu/claude-rc-launcher.git
cd claude-rc-launcher
cp .env.example .env
python3 app.py
```

Open http://localhost:8200

## Configuration

Edit `.env` (or `~/.config/claude-rc/env` if installed via script):

| Variable | Default | Description |
|---|---|---|
| `RC_HOST` | `0.0.0.0` | Listen address |
| `RC_PORT` | `8200` | Listen port |
| `RC_WORKING_DIR` | `.` | Working directory for sessions |
| `RC_CLAUDE_BIN` | `claude` | Path to Claude CLI |
| `RC_AUTH_USER` / `RC_AUTH_PASS` | *(unset)* | HTTP Basic Auth (set both to enable) |
| `RC_PROJECTS` | *(unset)* | Comma-separated project paths for the project picker |

## Launch Modes

- **Standard (c)** — skip permissions, no approval prompts
- **Teammate (ci)** — skip permissions + teammate mode
- **Safe** — normal permission checks apply

## Remote Access

Click **Share** in the UI to create a public tunnel via [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/). No setup needed — just install cloudflared and click the button.

Other options: SSH tunnel (`ssh -L 8200:localhost:8200 your-server`) or nginx reverse proxy (see `nginx.example.conf`).

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/uninstall.sh | bash
```

## License

[MIT](LICENSE)
