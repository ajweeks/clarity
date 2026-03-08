#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script currently supports Linux only."
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

PROXY="${PROXY:-caddy}"            # caddy | nginx
DOMAIN="${DOMAIN:-}"
UPSTREAM="${UPSTREAM:-http://127.0.0.1:9114}"
EMAIL="${EMAIL:-}"

if [[ -z "$DOMAIN" ]]; then
  echo "Missing required env var: DOMAIN"
  echo "Example: export DOMAIN=api.example.com"
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd is required."
  exit 1
fi

install_pkg() {
  local pkg="$1"

  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "$pkg"
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y "$pkg"
  elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y "$pkg"
  else
    echo "No supported package manager found (apt, dnf, yum)."
    exit 1
  fi
}

ensure_curl() {
  if ! command -v curl >/dev/null 2>&1; then
    install_pkg curl
  fi
}

setup_caddy() {
  ensure_curl

  if ! command -v caddy >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      install_pkg gpg
      install_pkg debian-keyring
      install_pkg debian-archive-keyring
      install_pkg apt-transport-https
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | $SUDO gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | $SUDO tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
      $SUDO apt-get update -y
      DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y caddy
    else
      install_pkg caddy
    fi
  fi

  local tmp_file
  tmp_file="$(mktemp)"

  if [[ -n "$EMAIL" ]]; then
    cat > "$tmp_file" <<EOF
{
  email $EMAIL
}

$DOMAIN {
  encode gzip
  reverse_proxy $UPSTREAM
}
EOF
  else
    cat > "$tmp_file" <<EOF
$DOMAIN {
  encode gzip
  reverse_proxy $UPSTREAM
}
EOF
  fi

  $SUDO install -m 644 "$tmp_file" /etc/caddy/Caddyfile
  rm -f "$tmp_file"

  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now caddy
  $SUDO systemctl restart caddy

  echo "Caddy configured for https://$DOMAIN -> $UPSTREAM"
  echo "Logs: sudo journalctl -u caddy -n 200 --no-pager"
}

setup_nginx() {
  if [[ -z "$EMAIL" ]]; then
    echo "EMAIL is required for Nginx + Let's Encrypt (certbot)."
    echo "Example: export EMAIL=you@example.com"
    exit 1
  fi

  ensure_curl

  if ! command -v nginx >/dev/null 2>&1; then
    install_pkg nginx
  fi

  if ! command -v certbot >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      install_pkg certbot
      install_pkg python3-certbot-nginx
    else
      install_pkg certbot
    fi
  fi

  local conf_file="/etc/nginx/sites-available/clarity-api.conf"
  local link_file="/etc/nginx/sites-enabled/clarity-api.conf"
  local tmp_file
  tmp_file="$(mktemp)"

  cat > "$tmp_file" <<EOF
server {
  listen 80;
  server_name $DOMAIN;

  location / {
    proxy_pass $UPSTREAM;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
  }
}
EOF

  $SUDO install -m 644 "$tmp_file" "$conf_file"
  rm -f "$tmp_file"

  if [[ -f /etc/nginx/sites-enabled/default ]]; then
    $SUDO rm -f /etc/nginx/sites-enabled/default
  fi

  $SUDO ln -sf "$conf_file" "$link_file"
  $SUDO nginx -t
  $SUDO systemctl enable --now nginx
  $SUDO systemctl restart nginx

  $SUDO certbot --nginx \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --redirect \
    -d "$DOMAIN"

  $SUDO systemctl restart nginx

  echo "Nginx configured for https://$DOMAIN -> $UPSTREAM"
  echo "Logs: sudo journalctl -u nginx -n 200 --no-pager"
}

case "$PROXY" in
  caddy)
    setup_caddy
    ;;
  nginx)
    setup_nginx
    ;;
  *)
    echo "Invalid PROXY='$PROXY'. Use 'caddy' or 'nginx'."
    exit 1
    ;;
esac

echo "Done."
echo "Test: curl -i https://$DOMAIN/health"
