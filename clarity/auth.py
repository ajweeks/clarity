"""Password + Cloudflare Turnstile login with a signed session token (cookie or Bearer header)."""

from collections import defaultdict, deque
import hashlib
import hmac
import html
import os
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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


def _session_token(request: Request) -> str | None:
    # Some mobile browsers (e.g. DuckDuckGo) never send the session cookie back, so the page
    # also stores the token and sends it as a Bearer header. Prefer that, fall back to the cookie.
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return request.cookies.get(COOKIE_NAME)


def is_authenticated(request: Request) -> bool:
    if AUTH_DISABLED:
        return True
    token = _session_token(request)
    if not token or "." not in token:
        return False
    expiry_str, _ = token.split(".", 1)
    if not expiry_str.isdigit() or int(expiry_str) < time.time():
        return False
    return hmac.compare_digest(token, _sign(int(expiry_str)))


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
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
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
  .password { position: relative; }
  .password input { width: 100%; padding: 10px 64px 10px 12px; font: inherit; color: inherit; background: transparent;
                    border: 1px solid var(--border); border-radius: 8px; }
  .password input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  .password button { position: absolute; top: 0; right: 0; bottom: 0; padding: 0 12px; font: inherit;
                     font-size: 0.85rem; font-weight: 400; color: var(--muted); background: none; }
  .password button:hover, .password button:focus-visible { color: var(--fg); }
  button { padding: 10px; font: inherit; font-weight: 600; color: #fff; background: var(--accent);
           border: 0; border-radius: 8px; cursor: pointer; }
  button:disabled { opacity: 0.6; cursor: default; }
  .error { color: var(--error); }
  .cf-turnstile { min-height: 65px; }
</style>
</head>
<body>
<form id="login" method="post" action="/login">
  <h1>Clarity</h1>
  <p>Enter the password to continue.</p>
  <p class="error" id="error" role="alert"{error_hidden}>{error}</p>
  <div class="password">
    <input id="password" type="password" name="password" placeholder="Password"
           autocomplete="current-password" required autofocus>
    <button type="button" id="toggle" aria-controls="password" aria-pressed="false">Show</button>
  </div>
  <div class="cf-turnstile" data-sitekey="{site_key}"></div>
  <button type="submit" id="submit">Sign in</button>
</form>
<script>
const form = document.getElementById("login");
const pw = document.getElementById("password");
const toggle = document.getElementById("toggle");
const error = document.getElementById("error");
const submit = document.getElementById("submit");

toggle.addEventListener("click", () => {
  const show = pw.type === "password";
  pw.type = show ? "text" : "password";
  toggle.textContent = show ? "Hide" : "Show";
  toggle.setAttribute("aria-pressed", String(show));
  pw.focus();
});

// Sign in via JSON so the session token can be kept in localStorage: some mobile browsers
// (e.g. DuckDuckGo) drop the session cookie, so the app sends the token as a Bearer header too.
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  submit.disabled = true;
  try {
    const captcha = form.querySelector("[name=cf-turnstile-response]");
    const res = await fetch("/api/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw.value, turnstile: captcha ? captcha.value : "" }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `Sign in failed (${res.status})`);
    try { localStorage.setItem("clarity_session", body.token); } catch {}
    location.replace("/");
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
    submit.disabled = false;
    if (window.turnstile) turnstile.reset();
  }
});
</script>
</body>
</html>
"""


LOGIN_SUCCESS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url=/">
<title>Clarity – Signed in</title>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
</style>
</head>
<body>
<p>Signed in. <a href="/">Continue</a></p>
<script>location.replace("/");</script>
</body>
</html>
"""

NO_STORE_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY"}


def _login_page(error: str | None = None, status_code: int = 200) -> HTMLResponse:
    body = (
        LOGIN_PAGE.replace("{error_hidden}", "" if error else " hidden")
        .replace("{error}", html.escape(error or ""))
        .replace("{site_key}", html.escape(TURNSTILE_SITE_KEY, quote=True))
    )
    return HTMLResponse(body, status_code=status_code, headers=NO_STORE_HEADERS)


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


async def _check_login(request: Request, password: str, turnstile_token: str) -> tuple[int, str] | None:
    """Returns (status_code, error) if the sign-in should be rejected, else None."""
    ip = client_ip(request)
    if failed_logins.is_blocked(ip):
        return 429, "Too many failed attempts. Try again later."
    if not await _verify_turnstile(turnstile_token, ip):
        return 400, "Verification failed. Please try again."
    if not hmac.compare_digest(password.encode(), PASSWORD.encode()):
        failed_logins.record_failure(ip)
        return 401, "Incorrect password."
    failed_logins.reset(ip)
    return None


def _new_session_token() -> str:
    return _sign(int(time.time()) + int(SESSION_DAYS * 86400))


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_DAYS * 86400),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )


@router.get("/login", include_in_schema=False)
async def login_form(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return _login_page()


@router.post("/api/session", include_in_schema=False)
async def create_session(request: Request):
    try:
        data = await request.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return JSONResponse({"detail": "Invalid request."}, status_code=400, headers=NO_STORE_HEADERS)

    rejection = await _check_login(request, str(data.get("password", "")), str(data.get("turnstile", "")))
    if rejection:
        status_code, error = rejection
        return JSONResponse({"detail": error}, status_code=status_code, headers=NO_STORE_HEADERS)

    token = _new_session_token()
    response = JSONResponse({"token": token}, headers=NO_STORE_HEADERS)
    _set_session_cookie(response, token)
    return response


@router.post("/login", include_in_schema=False)
async def login(request: Request):
    """Form-post fallback for when JavaScript is unavailable."""
    form = parse_qs((await request.body()).decode(errors="replace"))
    rejection = await _check_login(
        request, form.get("password", [""])[0], form.get("cf-turnstile-response", [""])[0]
    )
    if rejection:
        status_code, error = rejection
        return _login_page(error, status_code=status_code)

    # A 200 page rather than a 303 redirect: some WebViews drop cookies set on redirects.
    response = HTMLResponse(LOGIN_SUCCESS_PAGE, headers=NO_STORE_HEADERS)
    _set_session_cookie(response, _new_session_token())
    return response


@router.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=COOKIE_SECURE, samesite="lax")
    return response
