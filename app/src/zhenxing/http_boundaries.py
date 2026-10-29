"""Bound incoming bytes before JSON parsing; exclude private bodies from errors."""
from uuid import uuid4

from starlette.responses import JSONResponse


JSON_BODY_LIMIT = 64*1024
ATTACHMENT_BODY_LIMIT = 5*1024*1024 + 16384


class RequestBoundaryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        upload = path == "/api/v3/attachments" and method == "POST"
        limit = ATTACHMENT_BODY_LIMIT if upload else JSON_BODY_LIMIT

        async def reject(code, status):
            request_id = scope.get("state", {}).get("request_id") or str(uuid4())
            response = JSONResponse(status_code=status, content={"request_id": request_id,
                "error": {"code": code, "message": "请求体超过安全限制或长度无效，请缩小输入", "retryable": False}},
                headers={"X-Request-Id": request_id, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            await response(scope, receive, send)

        if method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/api/"):
            length = headers.get(b"content-length")
            if length is not None:
                try:
                    count = int(length)
                    if count < 0:
                        raise ValueError
                except ValueError:
                    return await reject("INVALID_CONTENT_LENGTH", 400)
                if count > limit:
                    return await reject("REQUEST_BODY_TOO_LARGE", 413)
            parts, size = [], 0
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                body = event.get("body", b"")
                size += len(body)
                if size > limit:
                    return await reject("REQUEST_BODY_TOO_LARGE", 413)
                parts.append(body)
                if not event.get("more_body", False):
                    break
            buffered = b"".join(parts)
            delivered = False
            original_receive = receive

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": buffered, "more_body": False}
                return await original_receive()

            receive = buffered_receive

        async def secured_send(event):
            if event["type"] == "http.response.start" and path.startswith("/api/"):
                entries = list(event.get("headers", []))
                existing = {key.lower() for key, _ in entries}
                for key, value in ((b"cache-control", b"private, no-store"), (b"x-content-type-options", b"nosniff"),
                                   (b"referrer-policy", b"same-origin")):
                    if key not in existing:
                        entries.append((key, value))
                event = {**event, "headers": entries}
            await send(event)

        await self.app(scope, receive, secured_send)
