"""Local viewer boundary, body limits, request correlation and response hardening."""

from __future__ import annotations

import base64
import hmac
import logging
import time
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from promo_finder.infrastructure.logging import request_id_var

log = logging.getLogger(__name__)
MAX_BODY = 8192
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PUBLIC_PATHS = frozenset({"/readyz", "/healthz"})  # carry no screenplay data


class LocalSecurityMiddleware:
    """Local-only by default. Remote hosting needs BOTH extra hosts and a password."""

    def __init__(
        self,
        app: ASGIApp,
        allowed_hosts: frozenset[str] = frozenset(),
        password: str = "",
    ) -> None:
        if allowed_hosts and not password:
            raise ValueError(
                "PF_ALLOWED_HOSTS requires PF_VIEWER_PASSWORD (refusing to start open)"
            )
        self.app = app
        self.hosts = LOCAL_HOSTS | {h.lower() for h in allowed_hosts}
        self.password = password.encode()
        self.remote = bool(allowed_hosts)

    def _authorized(self, headers: Headers) -> bool:
        if not self.password:
            return True
        header = headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            _, _, given = base64.b64decode(token, validate=True).partition(b":")
        except ValueError:
            return False
        return hmac.compare_digest(given, self.password)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        token = request_id_var.set(request_id)
        started = time.monotonic()
        status = 500

        async def secured_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                h = MutableHeaders(scope=message)
                h.update(
                    {
                        "X-Request-ID": request_id,
                        "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer",
                        "Content-Security-Policy": CSP,
                    }
                )
            await send(message)

        async def reject(code: int, reason: str) -> None:
            await JSONResponse({"error": reason}, status_code=code)(scope, receive, secured_send)

        try:
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            try:
                parsed = urlsplit(f"http://{host}")
                allowed = (parsed.hostname or "").lower() in self.hosts
                allowed = (
                    allowed
                    and not parsed.username
                    and not parsed.password
                    and not parsed.path
                    and not parsed.query
                    and not parsed.fragment
                )
                _ = parsed.port
            except ValueError:
                allowed = False
            public = self.remote and scope["path"] in PUBLIC_PATHS and scope["method"] == "GET"
            if public:
                await self.app(scope, receive, secured_send)
                return
            if not allowed:
                await reject(403, "forbidden host")
                return
            if not self._authorized(headers):
                response = JSONResponse(
                    {"error": "authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Promo Finder", charset="UTF-8"'},
                )
                await response(scope, receive, secured_send)
                return
            if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
                if headers.get("origin") != f"{scope['scheme']}://{host}":
                    await reject(403, "cross-origin or missing Origin: request refused")
                    return
                if (
                    headers.get("content-type", "").split(";")[0].strip().lower()
                    != "application/json"
                ):
                    await reject(415, "Content-Type must be application/json")
                    return
                try:
                    size = int(headers.get("content-length", ""))
                except ValueError:
                    await reject(411, "Content-Length required")
                    return
                if not 0 <= size <= MAX_BODY:
                    await reject(413, "request body too large")
                    return
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_BODY:
                        await reject(413, "request body too large")
                        return
                    if not message.get("more_body", False):
                        break
                if len(body) != size:
                    await reject(400, "Content-Length does not match body")
                    return
                delivered = False

                async def replay() -> Message:
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                await self.app(scope, replay, secured_send)
            else:
                await self.app(scope, receive, secured_send)
        finally:
            # Never log query strings, screenplay text, credentials or request bodies.
            log.info(
                "http_request",
                extra={
                    "method": scope["method"],
                    "status": status,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )
            request_id_var.reset(token)
