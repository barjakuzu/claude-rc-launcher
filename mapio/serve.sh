#!/usr/bin/env bash
# Serve Mapio locally and expose it with a free Cloudflare quick tunnel
# (https://<random>.trycloudflare.com). Needs python3 and cloudflared.
#   macOS:  brew install cloudflared
#   Linux:  https://github.com/cloudflare/cloudflared/releases
set -euo pipefail
PORT="${PORT:-8300}"
DIR="$(cd "$(dirname "$0")" && pwd)"

command -v cloudflared >/dev/null || { echo "cloudflared not found — install it first"; exit 1; }

python3 -m http.server "$PORT" --bind 127.0.0.1 -d "$DIR" >/dev/null 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT

echo "Mapio on http://localhost:$PORT — opening tunnel (the https link appears below)…"
cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" 2>&1 \
  | grep --line-buffered -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
  | while read -r url; do printf '\n  Mapio: %s\n  Open this in the Tesla browser.\n\n' "$url"; done
