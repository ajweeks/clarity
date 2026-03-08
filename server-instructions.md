# Server Instructions

Use this guide to host your own API server with a static frontend (for example GitHub Pages).

## Quick path

1. Point a subdomain (for example `api.example.com`) to your home server.
2. Forward ports `80` and `443` from your router to that server.
3. Run `scripts/deploy_api_server.sh`.
4. Run `scripts/setup_https_proxy.sh`.
5. Call `https://api.example.com/api/fix` from your frontend.

## 1) Deploy the API service

From your server (inside this repo):

```bash
export ANTHROPIC_API_KEY=...
export ALLOWED_ORIGINS=https://<your-site-domain>
bash scripts/deploy_api_server.sh
```

Optional overrides:

```bash
export CLARITY_PROVIDER=anthropic
export DEFAULT_MODEL=claude-sonnet-4-6
export PER_IP_INTERVAL_SECONDS=5
export GLOBAL_LIMIT_PER_MINUTE=120
export DAILY_CUTOFF=1000
export PORT=9114
```

What the deploy script does:
- installs `uv` if missing,
- copies repo files to `/srv/clarity`,
- installs dependencies,
- writes `/srv/clarity/.env.api`,
- installs and starts `clarity-api.service`,
- checks `http://127.0.0.1:$PORT/health`.

## 2) Set up HTTPS (Caddy or Nginx)

Caddy:

```bash
export DOMAIN=api.example.com
export PROXY=caddy
bash scripts/setup_https_proxy.sh
```

Nginx + certbot:

```bash
export DOMAIN=api.example.com
export EMAIL=you@example.com
export PROXY=nginx
bash scripts/setup_https_proxy.sh
```

Optional:

```bash
export UPSTREAM=http://127.0.0.1:9114
```

## DNS note

- static home IP: `A` record for `api` -> your public IP.
- dynamic home IP: `CNAME` record for `api` -> your DDNS hostname (for example DuckDNS).

## Verify

```bash
curl -i https://api.example.com/health
```

## Frontend URL

Use this endpoint from your website:

```text
https://api.example.com/api/fix
```

## API endpoint reference

`POST /api/fix`

Request body:

```json
{
  "text": "teh quik brown fox",
  "prompt": "optional custom prompt",
  "model": "optional model override"
}
```

Response body:

```json
{
  "corrected_text": "the quick brown fox",
  "model": "claude-sonnet-4-6",
  "provider": "anthropic"
}
```

Example frontend call:

```html
<script>
  async function fixText() {
    const text = document.querySelector('#input').value;

    const res = await fetch('https://api.example.com/api/fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || 'Request failed');
      return;
    }

    const data = await res.json();
    document.querySelector('#output').value = data.corrected_text;
  }
</script>
```

## Production hardening notes

- Keep HTTPS enabled (Caddy/Nginx).
- Forward real client IP (`X-Forwarded-For`) so per-IP limiting works.
- Current limiter is in-memory (single-server). If you scale out, use Redis.
