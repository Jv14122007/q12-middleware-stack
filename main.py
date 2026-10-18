
import time
import uuid
from collections import defaultdict, deque
from threading import Lock
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

EMAIL = "25f2006465@ds.study.iitm.ac.in"
ALLOWED_ORIGINS = [
    "https://app-ymvogy.example.com",
    # Add the actual exam page origin here if it differs.
]

RATE_LIMIT = 12
WINDOW_SECONDS = 10

request_id_context = ContextVar("request_id", default=None)
client_requests = defaultdict(deque)
lock = Lock()

api = FastAPI()


@api.middleware("http")
async def request_context_middleware(request: Request, call_next):
    supplied_id = request.headers.get("X-Request-ID")
    request_id = supplied_id if supplied_id else str(uuid.uuid4())

    token = request_id_context.set(request_id)
    request.state.request_id = request_id

    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_context.reset(token)


@api.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id")
    if client_id:
        now = time.monotonic()

        with lock:
            timestamps = client_requests[client_id]

            while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
                timestamps.popleft()

            if len(timestamps) >= RATE_LIMIT:
                retry_after = max(
                    1,
                    int(WINDOW_SECONDS - (now - timestamps[0]) + 0.999),
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)

    return await call_next(request)


@api.get("/ping")
async def ping(request: Request):
    return {
        "email": EMAIL,
        "request_id": request.state.request_id,
    }


# CORS wraps the application so headers are applied even to 429 responses.
cors_app = CORSMiddleware(
    app=api,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
    max_age=600,
)


class EnsureRequestIdHeader:
    """Ensure the response always includes the request ID."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = dict(scope.get("headers", []))
        supplied = request_headers.get(b"x-request-id")
        request_id = (
            supplied.decode("latin-1")
            if supplied
            else str(uuid.uuid4())
        )

        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)


app = EnsureRequestIdHeader(cors_app)
