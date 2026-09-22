"""Password + Cloudflare Turnstile login with a signed session cookie."""

from collections import defaultdict, deque
import hashlib
import hmac
import html
import os
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

from clarity import constants  # noqa: F401  (loads .env)

# Set CLARITY_DISABLE_AUTH=1 for local development only.
AUTH_DISABLED = os.getenv("CLARITY_DISABLE_AUTH", "").lower() in ("1", "true", "yes")


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value and not AUTH_DISABLED:
        raise RuntimeError(
            f"Missing required env var: {name} (set it in .env, or CLARITY_DISABLE_AUTH=1 for local dev)"
        )
    return value


PASSWORD = _require_env("CLARITY_PASSWORD")
SESSION_SECRET = _require_env("CLARITY_SESSION_SECRET")
TURNSTILE_SITE_KEY = _require_env("TURNSTILE_SITE_KEY")
TURNSTILE_SECRET_KEY = _require_env("TURNSTILE_SECRET_KEY")

SESSION_DAYS = float(os.getenv("CLARITY_SESSION_DAYS", "30"))
COOKIE_SECURE = os.getenv("CLARITY_COOKIE_SECURE", "true").lower() != "false"
MAX_FAILED_LOGINS = int(os.getenv("CLARITY_MAX_FAILED_LOGINS", "10"))
FAILED_LOGIN_WINDOW_SECONDS = 15 * 60

COOKIE_NAME = "clarity_session"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Mixing the password into the signing key means changing the password logs everyone out.
_signing_key = hashlib.sha256(f"{SESSION_SECRET}\0{PASSWORD}".encode()).digest()


def _sign(expiry: int) -> str:
    mac = hmac.new(_signing_key, str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{mac}"


def is_authenticated(request: Request) -> bool:
    if AUTH_DISABLED:
        return True
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie or "." not in cookie:
        return False
    expiry_str, _ = cookie.split(".", 1)
    if not expiry_str.isdigit() or int(expiry_str) < time.time():
        return False
    return hmac.compare_digest(cookie, _sign(int(expiry_str)))


def require_session(request: Request) -> None:
    """FastAPI dependency that rejects unauthenticated requests."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not signed in.")


def client_ip(request: Request) -> str:
    # Cloudflare sets CF-Connecting-IP; the server is only reachable through the tunnel.
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        value = request.headers.get(header, "").split(",")[0].strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


class FailedLoginTracker:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, ip: str, now: float) -> deque[float]:
        events = self.failures[ip]
        while events and events[0] <= now - self.window_seconds:
            events.popleft()
        return events

    def is_blocked(self, ip: str) -> bool:
        return len(self._prune(ip, time.monotonic())) >= self.limit

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        self._prune(ip, now).append(now)

    def reset(self, ip: str) -> None:
        self.failures.pop(ip, None)


failed_logins = FailedLoginTracker(MAX_FAILED_LOGINS, FAILED_LOGIN_WINDOW_SECONDS)

router = APIRouter()


LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Clarity – Sign in</title>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<style>
  :root { color-scheme: light dark; --bg: #f6f6f8; --card: #fff; --fg: #1f2328; --muted: #656d76;
          --border: #d0d7de; --accent: #ff4b4b; --error: #cf222e; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #0e1117; --card: #161b22; --fg: #e6edf3; --muted: #8d96a0; --border: #30363d; --error: #ff7b72; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  form { width: 100%; max-width: 360px; background: var(--card); border: 1px solid var(--border);
         border-radius: 12px; padding: 28px; display: grid; gap: 16px; }
  h1 { margin: 0; font-size: 1.6rem; }
  p { margin: 0; color: var(--muted); }
  input[type=password] { width: 100%; padding: 10px 12px; font: inherit; color: inherit; background: transparent;
                         border: 1px solid var(--border); border-radius: 8px; }
  input[type=password]:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  button { padding: 10px; font: inherit; font-weight: 600; color: #fff; background: var(--accent);
           border: 0; border-radius: 8px; cursor: pointer; }
  .error { color: var(--error); }
  .cf-turnstile { min-height: 65px; }
</style>
</head>
<body>
<form method="post" action="/login">
  <h1>Clarity</h1>
  <p>Enter the password to continue.</p>
  {error}
  <input type="password" name="password" placeholder="Password" autocomplete="current-password" required autofocus>
  <div class="cf-turnstile" data-sitekey="{site_key}"></div>
  <button type="submit">Sign in</button>
</form>
</body>
</html>
"""


def _login_page(error: str | None = None, status_code: int = 200) -> HTMLResponse:
    error_html = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    body = LOGIN_PAGE.replace("{error}", error_html).replace(
        "{site_key}", html.escape(TURNSTILE_SITE_KEY, quote=True)
    )
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


async def _verify_turnstile(token: str, ip: str) -> bool:
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                TURNSTILE_VERIFY_URL,
                data={"secret": TURNSTILE_SECRET_KEY, "response": token, "remoteip": ip},
            )
        return bool(resp.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False


@router.get("/login", include_in_schema=False)
async def login_form(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return _login_page()


@router.post("/login", include_in_schema=False)
async def login(request: Request):
    ip = client_ip(request)
    if failed_logins.is_blocked(ip):
        return _login_page("Too many failed attempts. Try again later.", status_code=429)

    form = parse_qs((await request.body()).decode(errors="replace"))
    password = form.get("password", [""])[0]
    token = form.get("cf-turnstile-response", [""])[0]

    if not await _verify_turnstile(token, ip):
        return _login_page("Verification failed. Please try again.", status_code=400)

    if not hmac.compare_digest(password.encode(), PASSWORD.encode()):
        failed_logins.record_failure(ip)
        return _login_page("Incorrect password.", status_code=401)

    failed_logins.reset(ip)
    max_age = int(SESSION_DAYS * 86400)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        _sign(int(time.time()) + max_age),
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )
    return response


@router.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=COOKIE_SECURE, samesite="lax")
    return response
