# Claude RC Launcher

A lightweight web UI to launch and manage [Claude Code](https://docs.anthropic.com/en/docs/claude-code) remote-control sessions from any browser -- including your phone.

Start, stop, and monitor Claude sessions with one tap. Sessions run in tmux and connect automatically via `/remote-control`.

<!-- TODO: add screenshot -->
![Screenshot placeholder](docs/screenshot.png)

---

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/USER/claude-rc-launcher/main/install.sh | bash
```

The installer checks dependencies, clones the repo, creates a config file, and sets up a systemd or launchd service for your platform.

To update an existing installation, run the same command again.

## Manual Install

```bash
git clone https://github.com/USER/claude-rc-launcher.git rc-launcher
cd rc-launcher
cp .env.example .env   # edit as needed
python3 app.py
```

Open `http://localhost:8200` in your browser.

### Docker

```bash
cp .env.example .env
# Edit .env -- set RC_WORKING_DIR to your project path
docker compose up -d
```

> The Claude CLI must be installed on your host. The container mounts the binary and `~/.claude` config. Run `claude login` on the host first.

### systemd (manual)

```bash
mkdir -p ~/.config/systemd/user
cp claude-rc.service ~/.config/systemd/user/claude-rc.service
mkdir -p ~/.config/claude-rc
cp .env.example ~/.config/claude-rc/env
# Edit ~/.config/claude-rc/env, then:
systemctl --user daemon-reload
systemctl --user enable --now claude-rc
```

## Configuration

All settings are read from environment variables. Copy `.env.example` to get started.

| Variable | Default | Description |
|---|---|---|
| `RC_HOST` | `0.0.0.0` | Address the server listens on |
| `RC_PORT` | `8200` | Port the server listens on |
| `RC_WORKING_DIR` | `.` | Working directory for Claude sessions |
| `RC_CLAUDE_BIN` | `claude` | Path to the Claude CLI binary |
| `RC_PREFIX` | `rc-` | Tmux session name prefix |
| `RC_AUTH_USER` | *(unset)* | Basic auth username (optional) |
| `RC_AUTH_PASS` | *(unset)* | Basic auth password (optional) |
| `RC_PROJECTS` | *(unset)* | Comma-separated project directories for multi-project picker |
| `CLAUDE_BIN_HOST` | `/usr/local/bin/claude` | Docker only -- host path to Claude binary |

## Launch Modes

| Mode | Flag | Description |
|---|---|---|
| **c** (standard) | `--dangerously-skip-permissions` | Full remote-control session, no approval prompts |
| **ci** (teammate) | `--dangerously-skip-permissions --teammate-mode in-process` | Teammate mode with in-process collaboration |
| **safe** | *(none)* | Standard remote-control with normal permission checks |

All modes include `--verbose` for detailed output.

## Remote Access

### Built-in Share Button (recommended)

The easiest way to access sessions remotely is the **Share** button built into Claude Code itself. Once a session is running, open the session URL and use the native sharing feature -- no extra infrastructure needed.

### Alternatives

**nginx reverse proxy** -- for a permanent setup with your own domain and SSL:

```bash
sudo cp nginx.example.conf /etc/nginx/sites-available/claude-rc
# Edit the file: replace YOUR_DOMAIN, set up htpasswd and certbot
sudo ln -s /etc/nginx/sites-available/claude-rc /etc/nginx/sites-enabled/
sudo certbot --nginx -d YOUR_DOMAIN
sudo systemctl reload nginx
```

See `nginx.example.conf` for the full template.

**SSH tunnel** -- quick one-liner from your local machine:

```bash
ssh -L 8200:localhost:8200 your-server
```

Then open `http://localhost:8200` locally.

## Security

This tool starts Claude Code sessions that can execute arbitrary code. Protect access appropriately:

- **Do not** expose port 8200 to the public internet without authentication.
- Set `RC_AUTH_USER` and `RC_AUTH_PASS` for built-in HTTP Basic Auth.
- For production use, put the launcher behind a reverse proxy with HTTPS and strong auth.
- The `safe` launch mode keeps normal Claude permission prompts active; `c` and `ci` modes skip all permission checks.

## Prerequisites

- Python 3.8+
- tmux
- Claude CLI installed and authenticated (`claude login`)

## Uninstall

If installed via the install script:

```bash
curl -fsSL https://raw.githubusercontent.com/USER/claude-rc-launcher/main/uninstall.sh | bash
```

Or run `uninstall.sh` directly. This stops the service, removes the app directory and wrapper, but leaves your config at `~/.config/claude-rc/` intact.

## License

[MIT](LICENSE)
