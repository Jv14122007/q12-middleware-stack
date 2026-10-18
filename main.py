
import asyncio
import math
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="Middleware Stack API")

# Assignment configuration
USER_EMAIL = "25f2006465@ds.study.iitm.ac.in"

ALLOWED_ORIGINS = [
    "https://app-ymvogy.example.com",
    "https://exam.sanand.workers.dev",
]

RATE_LIMIT = 12
WINDOW_SECONDS = 10

# Per-client rolling-window rate-limit buckets
rate_limit_store = defaultdict(deque)
rate_limit_lock = asyncio.Lock()


# Middleware 1: Request context
@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID")

    if not request_id:
        request_id = str(uuid.uuid4())

    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    return response


# Middleware 2: Per-client rate limiting
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # OPTIONS preflight requests do not consume quota.
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "default").strip()
    if not client_id:
        client_id = "default"

    now = time.monotonic()

    async with rate_limit_lock:
        timestamps = rate_limit_store[client_id]

        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT:
            retry_after = max(
                1,
                math.ceil(WINDOW_SECONDS - (now - timestamps[0])),
            )

            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

            # Preserve request context on rate-limit responses too.
            request_id = getattr(
                request.state, "request_id", None
            )
            if request_id:
                response.headers["X-Request-ID"] = request_id

            return response

        timestamps.append(now)

    return await call_next(request)


# Endpoint required by the grader
@app.get("/ping")
async def ping(request: Request):
    return {
        "email": USER_EMAIL,
        "request_id": request.state.request_id,
    }


@app.get("/")
async def root():
    return {"message": "Middleware Stack API is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# Middleware 3: CORS wraps the complete application so that
# CORS headers are also present on HTTP 429 responses.
app = CORSMiddleware(
    app=app,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-Client-Id",
        "X-Request-ID",
    ],
    expose_headers=["X-Request-ID", "Retry-After"],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
