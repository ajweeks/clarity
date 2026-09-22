from collections import defaultdict, deque
from datetime import UTC, datetime
import itertools
import os
from pathlib import Path
from threading import Lock
import time

from anthropic import Anthropic
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
import openai
from pydantic import BaseModel, Field

from clarity import auth, constants
from clarity.formatting import mk_diff, pair_up_diff
from clarity.llm import ai_stream
from clarity.prompts import DEFAULT_SYSTEM_NAME, DEFAULT_SYSTEM_PROMPT, SYSTEM_PROMPTS

STATIC_DIR = Path(__file__).parent / "static"


class FixRequest(BaseModel):
    text: str = Field(min_length=1, max_length=constants.MAX_CHARS)
    prompt: str | None = Field(default=None, max_length=constants.MAX_CHARS)
    model: str | None = None


class DiffRequest(BaseModel):
    original: str = Field(max_length=constants.MAX_CHARS * 2)
    corrected: str = Field(max_length=constants.MAX_CHARS * 2)


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.events: deque[float] = deque()

    def allow(self, now: float) -> bool:
        cutoff = now - self.window_seconds
        while self.events and self.events[0] <= cutoff:
            self.events.popleft()

        if len(self.events) >= self.limit:
            return False

        self.events.append(now)
        return True


class RequestGuard:
    def __init__(
        self,
        per_ip_interval_seconds: float,
        global_limit_per_minute: int,
        daily_cutoff: int,
    ) -> None:
        self.per_ip_interval_seconds = per_ip_interval_seconds
        self.daily_cutoff = daily_cutoff
        self.global_limiter = SlidingWindowRateLimiter(
            limit=global_limit_per_minute,
            window_seconds=60,
        )
        self.last_request_at: dict[str, float] = defaultdict(float)
        self.daily_count = 0
        self.total_seen_today = 0
        self.current_day = datetime.now(UTC).date()
        self.lock = Lock()

    def allow(self, ip_address: str) -> tuple[bool, str | None, float | None]:
        now = time.monotonic()
        today = datetime.now(UTC).date()

        with self.lock:
            if today != self.current_day:
                self.current_day = today
                self.daily_count = 0
                self.total_seen_today = 0

            self.total_seen_today += 1
            if self.total_seen_today > self.daily_cutoff:
                return False, "Daily request cutoff reached for this server.", None

            last_request = self.last_request_at[ip_address]
            elapsed = now - last_request
            if elapsed < self.per_ip_interval_seconds:
                retry_after = self.per_ip_interval_seconds - elapsed
                return (
                    False,
                    f"Rate limit exceeded: max 1 request every {self.per_ip_interval_seconds:.0f} seconds per IP.",
                    retry_after,
                )

            if not self.global_limiter.allow(now):
                return False, "Server is busy. Please retry shortly.", 60.0

            self.last_request_at[ip_address] = now
            self.daily_count += 1
            return True, None, None


provider = os.getenv("CLARITY_PROVIDER", "anthropic").lower()
if provider == "anthropic":
    client = Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        base_url=os.getenv("API_BASE"),
    )
    default_model = os.getenv("DEFAULT_MODEL", "claude-sonnet-5")
else:
    client = openai.OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("API_BASE"),
    )
    default_model = os.getenv("DEFAULT_MODEL", "gpt-4o-mini")


request_guard = RequestGuard(
    per_ip_interval_seconds=float(os.getenv("PER_IP_INTERVAL_SECONDS", "5")),
    global_limit_per_minute=int(os.getenv("GLOBAL_LIMIT_PER_MINUTE", "120")),
    daily_cutoff=int(os.getenv("DAILY_CUTOFF", "1000")),
)


app = FastAPI(title="Clarity", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router)

protected = [Depends(auth.require_session)]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index(request: Request):
    if not auth.is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/prompts", dependencies=protected)
def prompts() -> dict:
    return {"prompts": SYSTEM_PROMPTS, "default": DEFAULT_SYSTEM_NAME}


@app.post("/api/diff", dependencies=protected)
def diff(payload: DiffRequest) -> dict:
    parts = pair_up_diff(mk_diff(payload.original, payload.corrected))
    # Unchanged text is a string; a change is an [old, new] pair.
    return {"parts": parts}


def _check_rate_limit(request: Request) -> None:
    ok, reason, retry_after = request_guard.allow(auth.client_ip(request))
    if not ok:
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(max(1, int(retry_after)))
        raise HTTPException(status_code=429, detail=reason, headers=headers)


def _start_stream(payload: FixRequest):
    stream = ai_stream(
        payload.prompt or DEFAULT_SYSTEM_PROMPT,
        [dict(role="user", content=payload.text)],
        model=payload.model or default_model,
        client=client,
    )
    # Pull the first chunk eagerly so upstream errors become a proper HTTP error.
    try:
        first = next(stream, "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream LLM request failed: {exc}") from exc
    return itertools.chain([first], stream)


@app.post("/api/fix/stream", dependencies=protected)
def fix_text_stream(payload: FixRequest, request: Request) -> StreamingResponse:
    _check_rate_limit(request)
    return StreamingResponse(
        _start_stream(payload),
        media_type="text/plain; charset=utf-8",
        # no-transform stops Cloudflare from compressing (and therefore buffering) the stream.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/fix", dependencies=protected)
def fix_text(payload: FixRequest, request: Request) -> dict[str, str]:
    _check_rate_limit(request)
    model = payload.model or default_model
    try:
        corrected_text = "".join(_start_stream(payload))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream LLM request failed: {exc}") from exc

    return {
        "corrected_text": corrected_text,
        "model": model,
        "provider": provider,
    }


def serve() -> None:
    import uvicorn

    uvicorn.run(
        "clarity.api:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "9114")),
        workers=1,
    )


if __name__ == "__main__":
    serve()
