"""Runs inside Pyodide. Starts the real FastAPI app against the pre-seeded synthetic SQLite database and exposes an in-memory ASGI
`call(method, target, body)` plus `dispose_engine()` (used to reset the demo). Illustrative synthetic data only."""

import os
import sys

sys.path.insert(0, "/home/pyodide")
os.environ.update(
    DATABASE_URL="sqlite:////tmp/demo.db",
    DEMO_TODAY=DEMO_AS_OF,  # noqa: F821  (injected by worker.js) -- the demo is frozen at the date its data was generated
    ALLOW_RESEED="false",
    DEFAULT_ROLE="approver",
)

import anyio.to_thread  # noqa: E402


async def _inline(func, *args, **kwargs):
    """WebAssembly has no threads: FastAPI's thread-pool offloading of sync endpoints is run inline instead."""
    return func(*args)


anyio.to_thread.run_sync = _inline

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402


def dispose_engine():
    engine.dispose()


async def call(method, target, body=None):
    path, _, query = target.partition("?")
    raw = body.encode() if body else b""
    headers = [(b"content-type", b"application/json")] if raw else []
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query.encode(), "headers": headers, "server": ("demo", 80), "client": ("demo", 1), "root_path": "",
    }  # fmt: skip
    started, chunks = {}, []

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            started["status"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    try:
        await app(scope, receive, send)
    except Exception as exc:  # noqa: BLE001
        return 500, '{"error":"engine_error","message":"%s"}' % str(exc).replace('"', "'")[:300]
    return started.get("status", 500), b"".join(chunks).decode()
