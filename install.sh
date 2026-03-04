#!/usr/bin/env bash
# Claude RC Launcher — installer / updater
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/install.sh | bash
#
# Re-running this script updates an existing installation via git pull.
set -euo pipefail

APP_DIR="$HOME/.local/share/claude-rc"
CONFIG_DIR="$HOME/.config/claude-rc"
BIN_DIR="$HOME/.local/bin"
BIN_LINK="$BIN_DIR/claude-rc"
SERVICE_NAME="claude-rc"

# ── Helpers ──────────────────────────────────────────────────────────

info()  { printf '\033[1;34m=>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m=>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m=>\033[0m %s\n' "$*"; }
err()   { printf '\033[1;31m=>\033[0m %s\n' "$*" >&2; exit 1; }

prompt_yn() {
    # $1 = question, $2 = default (y/n)
    local answer
    printf '\033[1;36m??\033[0m %s ' "$1" > /dev/tty
    read -r answer < /dev/tty
    answer="${answer:-$2}"
    [[ "$answer" =~ ^[Yy] ]]
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

# Claude CLI
need_cmd claude
ok "claude CLI found"

# ── Optional: cloudflared ────────────────────────────────────────────

if ! command -v cloudflared > /dev/null 2>&1; then
    if prompt_yn "Install cloudflared for tunnel access? (y/N)" "n"; then
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
            curl -fsSL "$CF_URL" -o "$BIN_DIR/cloudflared"
            chmod +x "$BIN_DIR/cloudflared"
        elif [ "$OS" = "Darwin" ]; then
            if command -v brew > /dev/null 2>&1; then
                brew install cloudflared
            else
                err "Homebrew required to install cloudflared on macOS"
            fi
        fi
        ok "cloudflared installed"
    fi
fi

# ── Clone or update repo ────────────────────────────────────────────

if [ -d "$APP_DIR/.git" ]; then
    info "Existing installation found — updating..."
    git -C "$APP_DIR" pull --ff-only
    ok "Updated to latest"
else
    info "Cloning repository..."
    mkdir -p "$(dirname "$APP_DIR")"
    git clone https://github.com/barjakuzu/claude-rc-launcher.git "$APP_DIR"
    ok "Cloned to $APP_DIR"
fi

# ── Config file ──────────────────────────────────────────────────────

mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/env" ]; then
    cp "$APP_DIR/.env.example" "$CONFIG_DIR/env"
    info "Created config at $CONFIG_DIR/env — edit it before starting"
else
    info "Config already exists at $CONFIG_DIR/env (not overwritten)"
fi

# ── Wrapper script ───────────────────────────────────────────────────

mkdir -p "$BIN_DIR"
cat > "$BIN_LINK" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
CONFIG="$HOME/.config/claude-rc/env"
if [ -f "$CONFIG" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$CONFIG"
    set +a
fi
exec python3 "$HOME/.local/share/claude-rc/app.py" "$@"
WRAPPER
chmod +x "$BIN_LINK"
ok "Created wrapper at $BIN_LINK"

# ── Service setup ────────────────────────────────────────────────────

if [ "$OS" = "Linux" ]; then
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"
    cp "$APP_DIR/claude-rc.service" "$SYSTEMD_DIR/${SERVICE_NAME}.service"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "Installed systemd user service"
    info "Enable with:  systemctl --user enable --now ${SERVICE_NAME}"

elif [ "$OS" = "Darwin" ]; then
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_FILE="$PLIST_DIR/com.claude-rc.launcher.plist"
    mkdir -p "$PLIST_DIR"
    sed "s|__BIN_LINK__|${BIN_LINK}|g" "$APP_DIR/com.claude-rc.launcher.plist" > "$PLIST_FILE"
    ok "Installed launchd plist"
    info "Load with:  launchctl load $PLIST_FILE"
fi

# ── PATH warning ─────────────────────────────────────────────────────

case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *)
        warn "$BIN_DIR is not in your PATH"
        if [ "$OS" = "Darwin" ]; then
            warn "Add to ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\""
        else
            warn "Add to ~/.bashrc: export PATH=\"\$HOME/.local/bin:\$PATH\""
        fi
        ;;
esac

# ── Done ─────────────────────────────────────────────────────────────

echo ""
ok "Installation complete!"
info "Edit config:  \$EDITOR $CONFIG_DIR/env"
info "Start:        claude-rc"
info "Dashboard:    http://localhost:8200"
