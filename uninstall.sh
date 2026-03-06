#!/usr/bin/env bash
# Claude RC Launcher — uninstaller
set -euo pipefail

info() { printf '\033[1;34m=>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m=>\033[0m %s\n' "$*"; }

OS="$(uname -s)"

# Stop and disable service
if [ "$OS" = "Linux" ]; then
    systemctl --user stop claude-rc 2>/dev/null || true
    systemctl --user disable claude-rc 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/claude-rc.service"
    systemctl --user daemon-reload 2>/dev/null || true
    info "Removed systemd service"
elif [ "$OS" = "Darwin" ]; then
    PLIST="$HOME/Library/LaunchAgents/com.claude-rc.launcher.plist"
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    info "Removed launchd plist"
fi

# Remove MCP server registration
if command -v claude > /dev/null 2>&1; then
    claude mcp remove claude-rc-scheduler 2>/dev/null && info "Removed MCP server registration" || true
fi

# Remove application directory and wrapper
rm -rf "$HOME/.local/share/claude-rc"
rm -f "$HOME/.local/bin/claude-rc"
ok "Removed application files"

# Keep config
info "Config preserved at ~/.config/claude-rc/"
info "Remove manually if no longer needed: rm -rf ~/.config/claude-rc"

ok "Uninstall complete"
