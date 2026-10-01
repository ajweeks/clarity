#!/usr/bin/env bash
# Start the Clarity server and expose it via a Cloudflare Tunnel.
#
#   clarity-api (127.0.0.1:9114)  <-  cloudflared  <-  https://clarity.ajweeks.com
#
# Secrets live in .env (see .env.example). They are read by the Python server
# via python-dotenv and never passed on the command line or to cloudflared.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

ENV_FILE="$REPO_ROOT/.env"
TUNNEL_NAME="${CLARITY_TUNNEL:-clarity}"
PORT="${PORT:-9114}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.example to .env and fill it in."
  exit 1
fi
chmod 600 "$ENV_FILE"

# Generate a persistent session signing secret on first run.
if ! grep -qE '^CLARITY_SESSION_SECRET=.+' "$ENV_FILE"; then
  sed -i '/^CLARITY_SESSION_SECRET=/d' "$ENV_FILE"
  echo "CLARITY_SESSION_SECRET=$(openssl rand -hex 32)" >> "$ENV_FILE"
  echo "Generated CLARITY_SESSION_SECRET in .env"
fi

for var in CLARITY_PASSWORD TURNSTILE_SITE_KEY TURNSTILE_SECRET_KEY; do
  if ! grep -qE "^${var}=.+" "$ENV_FILE"; then
    echo "Missing $var in .env"
    exit 1
  fi
done
if ! grep -qE '^(ANTHROPIC|OPENAI)_API_KEY=.+' "$ENV_FILE"; then
  echo "Missing ANTHROPIC_API_KEY (or OPENAI_API_KEY) in .env"
  exit 1
fi

for cmd in uv cloudflared curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is not installed."; exit 1; }
done

pids=()
cleanup() {
  trap - EXIT INT TERM
  echo "Shutting down..."
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

uv sync --frozen --quiet

echo "Starting Clarity on 127.0.0.1:$PORT ..."
HOST=127.0.0.1 PORT="$PORT" uv run --frozen clarity-api &
pids+=($!)

healthy=""
for _ in {1..120}; do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then healthy=1; break; fi
  kill -0 "${pids[0]}" 2>/dev/null || break
  sleep 0.5
done
if [[ -z "$healthy" ]]; then
  echo "Clarity did not start. See the output above."
  exit 1
fi

echo "Starting Cloudflare tunnel '$TUNNEL_NAME' ..."
cloudflared tunnel --no-autoupdate run --url "http://127.0.0.1:$PORT" "$TUNNEL_NAME" &
pids+=($!)

echo "Clarity is running at https://clarity.ajweeks.com (Ctrl+C to stop)."
# Exit (and tear everything down) as soon as any process dies.
wait -n
