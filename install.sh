#!/usr/bin/env bash
# Claude RC Launcher — installer / updater
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/install.sh | bash
#
# Re-running this script updates an existing installation via git pull.
set -euo pipefail

RC_HOME="$HOME/.claude-rc"
APP_DIR="$RC_HOME/app"
CONFIG_FILE="$RC_HOME/env"
LOG_DIR="$RC_HOME/logs"
BIN_DIR="$HOME/.local/bin"
BIN_LINK="$BIN_DIR/claude-rc"
SERVICE_NAME="claude-rc"

# ── Helpers ──────────────────────────────────────────────────────────

info()  { printf '\033[1;34m=>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m=>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m=>\033[0m %s\n' "$*"; }
err()   { printf '\033[1;31m=>\033[0m %s\n' "$*" >&2; exit 1; }

# Save stdin fd for prompts — needed when subcommands consume stdin
exec 3<&0

prompt_yn() {
    local answer
    if [ -e /dev/tty ]; then
        printf '\033[1;36m??\033[0m %s ' "$1" > /dev/tty
        read -r answer < /dev/tty
    else
        printf '\033[1;36m??\033[0m %s ' "$1" >&2
        read -r answer <&3 || true
    fi
    answer="${answer:-$2}"
    [[ "$answer" =~ ^[Yy] ]]
}

prompt_value() {
    # $1 = prompt, $2 = default (empty = required)
    local value
    if [ -e /dev/tty ]; then
        printf '\033[1;36m??\033[0m %s ' "$1" > /dev/tty
        read -r value < /dev/tty
    else
        printf '\033[1;36m??\033[0m %s ' "$1" >&2
        read -r value <&3 || true
    fi
    echo "${value:-$2}"
}

need_cmd() {
    command -v "$1" > /dev/null 2>&1 || err "Required command not found: $1"
}

# ── OS detection ─────────────────────────────────────────────────────

OS="$(uname -s)"
case "$OS" in
    Linux)  info "Detected Linux" ;;
    Darwin) info "Detected macOS" ;;
    *)      err "Unsupported OS: $OS" ;;
esac

# ── Dependency checks ────────────────────────────────────────────────

info "Checking dependencies..."

# Python >= 3.8
need_cmd python3
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="${PY_VER##*.}"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
    err "Python 3.8+ required (found $PY_VER)"
fi
ok "python3 $PY_VER"

# tmux
need_cmd tmux
ok "tmux $(tmux -V | awk '{print $2}')"

# Claude CLI — detect full path (needed because tmux doesn't inherit PATH)
need_cmd claude
CLAUDE_PATH="$(command -v claude)"
ok "claude CLI found at $CLAUDE_PATH"

# ── Cloudflared (required for remote access) ─────────────────────────

if ! command -v cloudflared > /dev/null 2>&1; then
    info "cloudflared not found — needed for remote access"
    if prompt_yn "Install cloudflared? (Y/n)" "y"; then
        info "Installing cloudflared..."
        if [ "$OS" = "Linux" ]; then
            ARCH="$(uname -m)"
            case "$ARCH" in
                x86_64)  CF_ARCH="amd64" ;;
                aarch64) CF_ARCH="arm64" ;;
                armv7l)  CF_ARCH="arm"   ;;
                *)       err "Unsupported arch: $ARCH" ;;
            esac
            CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}"
            mkdir -p "$BIN_DIR"
            curl -fsSL "$CF_URL" -o "$BIN_DIR/cloudflared" < /dev/null
            chmod +x "$BIN_DIR/cloudflared"
        elif [ "$OS" = "Darwin" ]; then
            if command -v brew > /dev/null 2>&1; then
                brew install cloudflared </dev/null 2>&1 | while read -r line; do printf '  %s\n' "$line"; done
            else
                err "Homebrew required to install cloudflared on macOS"
            fi
        fi
        ok "cloudflared installed"
    fi
fi

# ── Migrate old installation ─────────────────────────────────────────

OLD_CONFIG="$HOME/.config/claude-rc/env"
OLD_SCHEDULES="$HOME/.config/claude-rc/schedules.json"

if [ -f "$OLD_CONFIG" ] && [ ! -f "$CONFIG_FILE" ]; then
    info "Migrating config from ~/.config/claude-rc/env..."
    mkdir -p "$RC_HOME"
    cp "$OLD_CONFIG" "$CONFIG_FILE"
    ok "Config migrated to $CONFIG_FILE"
fi

if [ -f "$OLD_SCHEDULES" ] && [ ! -f "$RC_HOME/schedules.json" ]; then
    info "Migrating schedules from old location..."
    mkdir -p "$RC_HOME"
    mv "$OLD_SCHEDULES" "$RC_HOME/schedules.json"
    ok "Schedules migrated"
fi

# ── Clone, update, or copy local repo ────────────────────────────────

# LOCAL_SOURCE env var allows installing from a local directory instead of GitHub
LOCAL_SOURCE="${LOCAL_SOURCE:-}"

if [ -n "$LOCAL_SOURCE" ]; then
    info "Installing from local source: $LOCAL_SOURCE"
    mkdir -p "$RC_HOME"
    rm -rf "$APP_DIR"
    cp -r "$LOCAL_SOURCE" "$APP_DIR"
    ok "Copied to $APP_DIR"
elif [ -d "$APP_DIR/.git" ]; then
    info "Existing installation found — updating..."
    git -C "$APP_DIR" pull --ff-only < /dev/null
    ok "Updated to latest"
else
    info "Cloning repository..."
    mkdir -p "$RC_HOME"
    if git clone https://github.com/barjakuzu/claude-rc-launcher.git "$APP_DIR" < /dev/null 2>/dev/null; then
        ok "Cloned to $APP_DIR"
    else
        warn "git clone failed — downloading as tarball..."
        TMP_TAR="$(mktemp -d)"
        curl -fsSL "https://api.github.com/repos/barjakuzu/claude-rc-launcher/tarball/main" < /dev/null | tar xz -C "$TMP_TAR" --strip-components=1
        mv "$TMP_TAR" "$APP_DIR"
        ok "Downloaded to $APP_DIR"
    fi
fi

# Clean up non-runtime files from app directory
rm -f "$APP_DIR/README.md" "$APP_DIR/Dockerfile" "$APP_DIR/docker-compose.yml" \
      "$APP_DIR/install.sh" "$APP_DIR/uninstall.sh" "$APP_DIR/.env.example" \
      "$APP_DIR/claude-rc.service" "$APP_DIR/com.claude-rc.launcher.plist" \
      "$APP_DIR/.gitignore" "$APP_DIR/LICENSE" "$APP_DIR/screenshot.png" \
      "$APP_DIR/launcher.gif" "$APP_DIR/nginx.example.conf"
rm -rf "$APP_DIR/.git"

# ── Create logs directory ────────────────────────────────────────────

mkdir -p "$LOG_DIR"

# ── Authentication setup ─────────────────────────────────────────────

SETUP_AUTH=false
if [ ! -f "$CONFIG_FILE" ]; then
    # Write default env config from embedded template
    cat > "$CONFIG_FILE" <<'ENVTPL'
# Claude RC Launcher — Environment Variables

# ── Server ───────────────────────────────────────────────────────────
# Address and port the launcher listens on.
RC_HOST=0.0.0.0
RC_PORT=8200

# ── Claude ───────────────────────────────────────────────────────────
# Working directory for Claude sessions (absolute path recommended).
RC_WORKING_DIR=.

# Path to the Claude CLI binary, or just "claude" to find it on PATH.
RC_CLAUDE_BIN=claude

# Tmux session name prefix (sessions are named <prefix><your-name>).
RC_PREFIX=rc-

# ── Authentication ───────────────────────────────────────────────────
# Optional HTTP Basic Auth. Both must be set to enable.
# RC_AUTH_USER=admin
# RC_AUTH_PASS=changeme

# ── Multi-project ───────────────────────────────────────────────────
# Comma-separated list of project directories. When set, the UI shows
# a project picker and each session launches in the chosen directory.
# RC_PROJECTS=/home/user/project-a,/home/user/project-b
ENVTPL
    SETUP_AUTH=true
elif ! grep -q '^RC_AUTH_USER=' "$CONFIG_FILE" 2>/dev/null; then
    SETUP_AUTH=true
fi

if [ "$SETUP_AUTH" = true ]; then
    echo ""
    warn "Authentication is REQUIRED for remote access."
    warn "Anyone with your tunnel URL can launch Claude sessions without it."
    echo ""
    AUTH_USER="$(prompt_value "Choose a username [admin]: " "admin")"
    AUTH_PASS=""
    while [ -z "$AUTH_PASS" ]; do
        AUTH_PASS="$(prompt_value "Choose a password (required): " "")"
        [ -z "$AUTH_PASS" ] && warn "Password cannot be empty"
    done

    # Write credentials to config
    if grep -q '^# RC_AUTH_USER' "$CONFIG_FILE" 2>/dev/null; then
        sed -i.bak "s/^# RC_AUTH_USER=.*/RC_AUTH_USER=${AUTH_USER}/" "$CONFIG_FILE"
        sed -i.bak "s/^# RC_AUTH_PASS=.*/RC_AUTH_PASS=${AUTH_PASS}/" "$CONFIG_FILE"
        rm -f "${CONFIG_FILE}.bak"
    elif ! grep -q '^RC_AUTH_USER=' "$CONFIG_FILE" 2>/dev/null; then
        echo "" >> "$CONFIG_FILE"
        echo "RC_AUTH_USER=${AUTH_USER}" >> "$CONFIG_FILE"
        echo "RC_AUTH_PASS=${AUTH_PASS}" >> "$CONFIG_FILE"
    fi
    ok "Auth configured: $AUTH_USER / ****"
else
    info "Config already exists at $CONFIG_FILE (not overwritten)"
fi

# Auto-configure claude binary path (tmux doesn't inherit user PATH)
if grep -q '^RC_CLAUDE_BIN=claude$' "$CONFIG_FILE" 2>/dev/null; then
    sed -i.bak "s|^RC_CLAUDE_BIN=claude$|RC_CLAUDE_BIN=${CLAUDE_PATH}|" "$CONFIG_FILE"
    rm -f "${CONFIG_FILE}.bak"
    ok "Set RC_CLAUDE_BIN=${CLAUDE_PATH}"
elif ! grep -q '^RC_CLAUDE_BIN=/' "$CONFIG_FILE" 2>/dev/null; then
    echo "RC_CLAUDE_BIN=${CLAUDE_PATH}" >> "$CONFIG_FILE"
    ok "Set RC_CLAUDE_BIN=${CLAUDE_PATH}"
fi

# Auto-configure working directory to user's home
if grep -q '^RC_WORKING_DIR=\.$' "$CONFIG_FILE" 2>/dev/null; then
    sed -i.bak "s|^RC_WORKING_DIR=\.\$|RC_WORKING_DIR=${HOME}|" "$CONFIG_FILE"
    rm -f "${CONFIG_FILE}.bak"
fi

# Ensure PATH includes common binary locations (needed for launchd/systemd)
if ! grep -q '^PATH=' "$CONFIG_FILE" 2>/dev/null; then
    if [ "$OS" = "Darwin" ]; then
        echo "PATH=${BIN_DIR}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" >> "$CONFIG_FILE"
    else
        echo "PATH=${BIN_DIR}:/usr/local/bin:/usr/bin:/bin" >> "$CONFIG_FILE"
    fi
fi

# ── Wrapper script ───────────────────────────────────────────────────

mkdir -p "$BIN_DIR"
cat > "$BIN_LINK" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
RC_HOME="$HOME/.claude-rc"
APP_DIR="$RC_HOME/app"
CONFIG="$RC_HOME/env"

if [ "${1:-}" = "update" ]; then
    echo "Updating claude-rc..."
    if [ -d "$APP_DIR/.git" ]; then
        git -C "$APP_DIR" pull --ff-only
        echo "Updated. Restart the service to apply changes:"
        case "$(uname -s)" in
            Darwin) echo "  launchctl unload ~/Library/LaunchAgents/com.claude-rc.launcher.plist"
                    echo "  launchctl load ~/Library/LaunchAgents/com.claude-rc.launcher.plist" ;;
            *)      echo "  systemctl --user restart claude-rc" ;;
        esac
    else
        echo "Not a git install. Re-run the install script to update."
    fi
    exit 0
fi

if [ -f "$CONFIG" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$CONFIG"
    set +a
fi
exec python3 "$APP_DIR/app.py" "$@"
WRAPPER
chmod +x "$BIN_LINK"
ok "Created wrapper at $BIN_LINK"

# ── PATH setup ──────────────────────────────────────────────────────

PATH_ADDED=false
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *)
        if [ "$OS" = "Darwin" ]; then
            SHELL_RC="$HOME/.zshrc"
        else
            SHELL_RC="$HOME/.bashrc"
        fi
        if ! grep -q '\.local/bin' "$SHELL_RC" 2>/dev/null; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            ok "Added ~/.local/bin to PATH in $(basename "$SHELL_RC")"
            PATH_ADDED=true
        fi
        export PATH="$BIN_DIR:$PATH"
        ;;
esac

# ── Service setup ────────────────────────────────────────────────────

if [ "$OS" = "Linux" ]; then
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"
    cat > "$SYSTEMD_DIR/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Claude RC Launcher
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/.claude-rc/env
ExecStart=/usr/bin/env python3 %h/.claude-rc/app/app.py
Restart=on-failure
RestartSec=5
StandardOutput=append:%h/.claude-rc/logs/claude-rc.log
StandardError=append:%h/.claude-rc/logs/claude-rc.log

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now "${SERVICE_NAME}" 2>/dev/null || true
    ok "Started claude-rc service"

elif [ "$OS" = "Darwin" ]; then
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_FILE="$PLIST_DIR/com.claude-rc.launcher.plist"
    mkdir -p "$PLIST_DIR"
    cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-rc.launcher</string>

    <key>ProgramArguments</key>
    <array>
        <string>${BIN_LINK}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${RC_HOME}/logs/claude-rc.log</string>

    <key>StandardErrorPath</key>
    <string>${RC_HOME}/logs/claude-rc.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load "$PLIST_FILE" 2>/dev/null || true
    ok "Started claude-rc service"
fi

# ── Browser Automation (optional) ────────────────────────────────────

# Read auth credentials from config for tunnel URL display
MCP_AUTH_USER=""
MCP_AUTH_PASS=""
if [ -f "$CONFIG_FILE" ]; then
    MCP_AUTH_USER="$(grep '^RC_AUTH_USER=' "$CONFIG_FILE" 2>/dev/null | cut -d= -f2- || true)"
    MCP_AUTH_PASS="$(grep '^RC_AUTH_PASS=' "$CONFIG_FILE" 2>/dev/null | cut -d= -f2- || true)"
fi

echo ""
if prompt_yn "Set up browser automation? (lets Claude control a headless browser via playwright-cli) (y/N)" "n"; then
    if command -v playwright-cli > /dev/null 2>&1; then
        ok "playwright-cli already installed ($(playwright-cli --version 2>/dev/null || echo 'unknown'))"
    else
        info "Installing @playwright/cli..."
        if command -v npm > /dev/null 2>&1; then
            npm install -g @playwright/cli 2>/dev/null && ok "playwright-cli installed" || warn "Could not install @playwright/cli (non-critical)"
        else
            warn "npm not found — install Node.js first, then run: npm install -g @playwright/cli"
        fi
    fi

    echo ""
    info "playwright-cli gives Claude headless browser control — no Chrome, no Xvfb needed."
    info "Usage in schedule prompts:"
    echo "  playwright-cli -s=session open"
    echo "  playwright-cli -s=session goto \"https://example.com\""
    echo "  playwright-cli -s=session snapshot   # get element refs"
    echo "  playwright-cli -s=session click ref1  # interact"
    echo "  playwright-cli -s=session screenshot  # verify"
    echo "  playwright-cli -s=session close"
    echo ""
    info "Save login sessions with: playwright-cli -s=name state-save ~/.playwright/states/site-auth"
    info "Load in scheduled tasks with: playwright-cli -s=name state-load ~/.playwright/states/site-auth"
fi

# ── Start tunnel automatically ──────────────────────────────────────

echo ""
ok "Installation complete!"
echo ""

if command -v cloudflared > /dev/null 2>&1; then
    info "Starting launcher and creating tunnel..."

    # Wait for the service to be ready (up to 10 seconds)
    PORT="${RC_PORT:-8200}"
    AUTH_HEADER=""
    if [ -n "${MCP_AUTH_USER:-}" ] && [ -n "${MCP_AUTH_PASS:-}" ]; then
        AUTH_HEADER="-u ${MCP_AUTH_USER}:${MCP_AUTH_PASS}"
    elif [ -n "${AUTH_USER:-}" ] && [ -n "${AUTH_PASS:-}" ]; then
        AUTH_HEADER="-u ${AUTH_USER}:${AUTH_PASS}"
    fi

    SERVICE_READY=false
    for i in $(seq 1 10); do
        if curl -sf $AUTH_HEADER "http://localhost:${PORT}/rc/sessions" > /dev/null 2>&1; then
            SERVICE_READY=true
            break
        fi
        sleep 1
    done

    if [ "$SERVICE_READY" = true ]; then
        # Start tunnel via API
        curl -sf $AUTH_HEADER -X POST "http://localhost:${PORT}/rc/tunnel/start" > /dev/null 2>&1 || true
        info "Waiting for tunnel URL..."
        for i in $(seq 1 15); do
            sleep 1
            TUNNEL_URL="$(curl -sf $AUTH_HEADER "http://localhost:${PORT}/rc/tunnel/status" 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); u=d.get("url"); print(u if u else "")' 2>/dev/null || true)"
            if [ -n "$TUNNEL_URL" ]; then
                echo ""
                ok "Remote access URL:"
                printf '\n  \033[1;4;36m%s\033[0m\n\n' "$TUNNEL_URL"
                info "Open this URL from anywhere — phone, another computer, etc."
                info "Login with: ${AUTH_USER:-${MCP_AUTH_USER:-admin}} / your password"
                break
            fi
        done
        if [ -z "${TUNNEL_URL:-}" ]; then
            warn "Tunnel is starting... open http://localhost:${PORT} and click Share"
        fi
    else
        warn "Service not yet ready. Run 'claude-rc' manually, then click Share in the UI"
    fi
else
    info "Run:  claude-rc"
    info "Then open http://localhost:${PORT:-8200} and click Share for a remote URL"
fi
