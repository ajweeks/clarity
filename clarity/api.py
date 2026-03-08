import os
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from threading import Lock

from anthropic import Anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import openai
from pydantic import BaseModel, Field

from clarity import constants
from clarity.llm import ai_stream
from clarity.prompts import DEFAULT_SYSTEM_PROMPT


class FixRequest(BaseModel):
    text: str = Field(min_length=1, max_length=constants.MAX_CHARS)
    prompt: str | None = Field(default=None, max_length=constants.MAX_CHARS)
    model: str | None = None


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
    default_model = os.getenv("DEFAULT_MODEL", "claude-sonnet-4-6")
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


app = FastAPI(title="Clarity API", version="1.0.0")

allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["*"],
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


@app.post("/api/fix")
def fix_text(payload: FixRequest, request: Request) -> dict[str, str]:
    ip_address = _get_client_ip(request)

    ok, reason, retry_after = request_guard.allow(ip_address)
    if not ok:
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(max(1, int(retry_after)))
        raise HTTPException(status_code=429, detail=reason, headers=headers)

    system_prompt = payload.prompt or DEFAULT_SYSTEM_PROMPT
    model = payload.model or default_model

    try:
        corrected_text = "".join(
            ai_stream(
                system_prompt,
                [dict(role="user", content=payload.text)],
                model=model,
                client=client,
            )
        )
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
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "9114")),
        workers=1,
    )


if __name__ == "__main__":
    serve()
