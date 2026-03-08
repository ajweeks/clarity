#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script currently supports Linux servers only."
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd (systemctl) is required for this deployment script."
  exit 1
fi

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Please run as root or install sudo."
    exit 1
  fi
  SUDO="sudo"
fi

RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z "$RUN_HOME" ]]; then
  echo "Could not determine home directory for user '$RUN_USER'."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/srv/clarity}"
SERVICE_NAME="${SERVICE_NAME:-clarity-api}"

CLARITY_PROVIDER="${CLARITY_PROVIDER:-anthropic}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-}"
DEFAULT_MODEL="${DEFAULT_MODEL:-}"
API_BASE="${API_BASE:-}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-9114}"
PER_IP_INTERVAL_SECONDS="${PER_IP_INTERVAL_SECONDS:-5}"
GLOBAL_LIMIT_PER_MINUTE="${GLOBAL_LIMIT_PER_MINUTE:-120}"
DAILY_CUTOFF="${DAILY_CUTOFF:-1000}"

if [[ -z "$ALLOWED_ORIGINS" ]]; then
  echo "Missing required env var: ALLOWED_ORIGINS"
  echo "Example: export ALLOWED_ORIGINS='https://<username>.github.io'"
  exit 1
fi

case "$CLARITY_PROVIDER" in
  openai)
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
      echo "Missing required env var: OPENAI_API_KEY"
      exit 1
    fi
    DEFAULT_MODEL="${DEFAULT_MODEL:-gpt-4o-mini}"
    ;;
  anthropic)
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      echo "Missing required env var: ANTHROPIC_API_KEY"
      exit 1
    fi
    DEFAULT_MODEL="${DEFAULT_MODEL:-claude-sonnet-4-6}"
    ;;
  *)
    echo "CLARITY_PROVIDER must be 'openai' or 'anthropic'."
    exit 1
    ;;
esac

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi

  echo "uv not found. Installing uv for user '$RUN_USER'..."
  $SUDO -u "$RUN_USER" sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

  if ! command -v uv >/dev/null 2>&1; then
    export PATH="$RUN_HOME/.local/bin:$PATH"
  fi

  if ! command -v uv >/dev/null 2>&1; then
    echo "uv installation failed or uv is not on PATH."
    echo "Expected location: $RUN_HOME/.local/bin/uv"
    exit 1
  fi
}

ensure_uv

echo "Syncing repo to $INSTALL_DIR ..."
$SUDO mkdir -p "$INSTALL_DIR"
if command -v rsync >/dev/null 2>&1; then
  $SUDO rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    "$REPO_ROOT/" "$INSTALL_DIR/"
else
  echo "rsync not found; using cp fallback."
  $SUDO rm -rf "$INSTALL_DIR"/*
  $SUDO cp -a "$REPO_ROOT/." "$INSTALL_DIR/"
fi

$SUDO chown -R "$RUN_USER":"$RUN_USER" "$INSTALL_DIR"

echo "Installing Python dependencies with uv ..."
$SUDO -u "$RUN_USER" env "PATH=$RUN_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
  uv sync --directory "$INSTALL_DIR" --frozen

ENV_FILE="$INSTALL_DIR/.env.api"
TMP_ENV_FILE="$(mktemp)"
cat > "$TMP_ENV_FILE" <<EOF
CLARITY_PROVIDER=$CLARITY_PROVIDER
OPENAI_API_KEY=${OPENAI_API_KEY:-}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
DEFAULT_MODEL=$DEFAULT_MODEL
API_BASE=$API_BASE
ALLOWED_ORIGINS=$ALLOWED_ORIGINS
HOST=$HOST
PORT=$PORT
PER_IP_INTERVAL_SECONDS=$PER_IP_INTERVAL_SECONDS
GLOBAL_LIMIT_PER_MINUTE=$GLOBAL_LIMIT_PER_MINUTE
DAILY_CUTOFF=$DAILY_CUTOFF
EOF
$SUDO install -m 600 "$TMP_ENV_FILE" "$ENV_FILE"
rm -f "$TMP_ENV_FILE"
$SUDO chown "$RUN_USER":"$RUN_USER" "$ENV_FILE"

SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
TMP_SERVICE_FILE="$(mktemp)"
cat > "$TMP_SERVICE_FILE" <<EOF
[Unit]
Description=Clarity FastAPI backend for static website integration
After=network.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$RUN_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/env uv run --frozen clarity-api
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
$SUDO install -m 644 "$TMP_SERVICE_FILE" "$SERVICE_FILE"
rm -f "$TMP_SERVICE_FILE"

echo "Starting systemd service: $SERVICE_NAME"
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$SERVICE_NAME"
$SUDO systemctl restart "$SERVICE_NAME"

echo "Waiting for health check..."
for _ in {1..20}; do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "Deployment complete. API is healthy on port $PORT."
    echo "Remember to point your frontend at: https://<your-domain>/api/fix"
    exit 0
  fi
  sleep 1
done

echo "Service did not become healthy in time."
echo "Check logs with: sudo journalctl -u $SERVICE_NAME -n 200 --no-pager"
exit 1
