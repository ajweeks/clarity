# Server Instructions

Clarity runs as a single FastAPI server (`clarity-api`) that serves the web page, the login
screen and the `/api/*` endpoints. It listens on `127.0.0.1` only and is exposed to the internet
through a Cloudflare Tunnel, so no ports need to be forwarded.

```text
browser -> https://clarity.ajweeks.com -> Cloudflare -> cloudflared -> 127.0.0.1:9114 (clarity-api)
```

## One-time setup

1. Create the tunnel and point the subdomain at it:

   ```bash
   cloudflared tunnel login          # only if ~/.cloudflared/cert.pem doesn't exist
   cloudflared tunnel create clarity
   cloudflared tunnel route dns clarity clarity.ajweeks.com
   ```

2. Create a Turnstile widget (Cloudflare dashboard > Turnstile > Add widget) with the hostname
   `clarity.ajweeks.com`, and note its site key and secret key.

3. Create `.env` from the template and fill it in:

   ```bash
   cp .env.example .env
   ```

   `.env` is gitignored, and `start.sh` makes it readable only by you (`chmod 600`).
   The session signing secret is generated automatically on first start.

## Start

```bash
make run    # or: bash start.sh
```

This starts `clarity-api` and `cloudflared`, and stops both on Ctrl+C or if either one exits.
API keys are only read by the Python server; they never appear on the command line.

## Verify

```bash
curl -i https://clarity.ajweeks.com/health
```

## Login

- The password is `CLARITY_PASSWORD` in `.env`. Changing it signs everyone out.
- Sessions last `CLARITY_SESSION_DAYS` (default 30) days. Sign out at `/logout`.
- After `CLARITY_MAX_FAILED_LOGINS` (default 10) wrong passwords, an IP is blocked for 15 minutes.

## API endpoints

All `/api/*` endpoints need a signed-in session cookie.

- `POST /api/fix/stream`: `{"text": "...", "prompt": "optional", "model": "optional"}` streams the corrected text as plain text.
- `POST /api/fix`: same body, returns `{"corrected_text", "model", "provider"}`.
- `POST /api/diff`: `{"original", "corrected"}` returns `{"parts": [...]}`, where each part is unchanged text or an `[old, new]` pair.
- `GET /api/prompts`: the built-in prompts, plus the `separator` used by the "Teacher" prompt.
- `GET /health`: no auth needed.

The "Teacher" prompt asks the model for the corrected text, a line containing only `---`, then bullets
explaining each mistake. The page splits on that line: the text above it goes into the diff, and the
bullets are shown under "What was wrong".

Rate limits (env vars): `PER_IP_INTERVAL_SECONDS` (default 5), `GLOBAL_LIMIT_PER_MINUTE` (120), `DAILY_CUTOFF` (1000).
