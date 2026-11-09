#!/usr/bin/env bash
# Runs MAPIO (labelled demo mode unless you provide real keys in .env.local) and exposes it
# with a free Cloudflare quick tunnel: https://<random>.trycloudflare.com
# Run this on YOUR machine (needs Node 22+ and cloudflared: `brew install cloudflared`).
# HTTPS from the tunnel is what lets the car browser use geolocation.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-3100}"
command -v cloudflared >/dev/null || { echo "Install cloudflared first (brew install cloudflared)"; exit 1; }
[ -d node_modules ] || npm ci
npm run build
set -a; [ -f .env.local ] && . ./.env.local || . ./.env.e2e; set +a
export MAPIO_DEMO_MODE="${MAPIO_DEMO_MODE:-true}" MAPIO_DEV_DB_DIR="${MAPIO_DEV_DB_DIR:-.data/pglite}"
npx next start -p "$PORT" &
APP=$!; trap 'kill $APP 2>/dev/null' EXIT
echo "Opening tunnel… the https link appears below. Open it in the car browser."
cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" 2>&1 | grep --line-buffered -oE 'https://[a-z0-9-]+\.trycloudflare\.com'
