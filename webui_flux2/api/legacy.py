"""Bridge ASGI requests to an existing ``BaseHTTPRequestHandler`` class.

The adapter preserves handler-emitted status codes, headers (including
repeated Set-Cookie headers), and response bytes. Request and response bodies
are spooled so backup uploads/downloads do not have to live wholly in memory.
The handler itself runs in a worker thread because its implementation is
synchronous and may perform file or service I/O.
"""

from __future__ import annotations

import tempfile
from email.message import Message
from types import MethodType
from typing import Any, BinaryIO, Iterable, Type

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

SPOOL_MEMORY_LIMIT = 1024 * 1024
STREAM_CHUNK_SIZE = 256 * 1024


class _LegacyServer:
    """Minimal server surface consumed by the existing Handler methods."""

    def __init__(self, port: int, client_host: str):
        self.server_port = port
        self.server_address = ("0.0.0.0", port)
        self.client_host = client_host

    def istek_basladi(self, _connection: Any) -> bool:
        # Legacy parse_request uses this for connection bookkeeping. The ASGI
        # adapter dispatches methods directly and the ASGI server owns sockets.
        return True

    def shutdown_request(self, _connection: Any) -> None:
        return None


class _RequestContext:
    """Populate the attributes expected by BaseHTTPRequestHandler methods."""

    def __init__(
        self,
        *,
        handler_class: Type,
        method: str,
        raw_path: str,
        headers: Message,
        body: BinaryIO,
        response_body: BinaryIO,
        server_port: int,
        client_host: str,
    ):
        # Bypass BaseHTTPRequestHandler.__init__: it owns a socket and parses
        # the request itself, while this adapter supplies an already parsed
        # ASGI request.
        self.handler = handler_class.__new__(handler_class)
        self.status_code: int | None = None
        self.response_headers: list[tuple[str, str]] = []
        self.headers_finished = False
        self.handler.request = None
        self.handler.connection = None
        self.handler.client_address = (client_host, 0)
        self.handler.server = _LegacyServer(server_port, client_host)
        self.handler.command = method
        self.handler.path = raw_path
        self.handler.request_version = "HTTP/1.1"
        self.handler.close_connection = True
        self.handler.headers = headers
        self.handler.rfile = body
        self.handler.wfile = response_body
        self.handler.send_response = MethodType(self._send_response, self.handler)
        self.handler.send_header = MethodType(self._send_header, self.handler)
        self.handler.end_headers = MethodType(self._end_headers, self.handler)

    def _send_response(self, _handler: Any, code: int, _message: str | None = None) -> None:
        self.status_code = int(code)

    def _send_header(self, _handler: Any, key: str, value: str) -> None:
        self.response_headers.append((str(key), str(value)))

    def _end_headers(self, _handler: Any) -> None:
        self.headers_finished = True


def _headers_from_scope(request: Request) -> Message:
    message = Message()
    for key, value in request.scope.get("headers", ()):
        message.add_header(key.decode("latin-1"), value.decode("latin-1"))
    return message


def _legacy_path(request: Request) -> str:
    # BaseHTTPRequestHandler sees the encoded request target. Use ASGI raw_path
    # rather than request.url.path so percent escapes are not decoded early.
    raw_path = request.scope.get("raw_path")
    if raw_path is None:
        path = request.scope.get("path", "/").encode("utf-8")
    else:
        path = raw_path
    target = path.decode("latin-1")
    query = request.scope.get("query_string", b"")
    if query:
        target += "?" + query.decode("latin-1")
    return target


async def _spool_request_body(request: Request) -> BinaryIO:
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_LIMIT, mode="w+b")
    async for chunk in request.stream():
        if chunk:
            spool.write(chunk)
    spool.seek(0)
    return spool


def _response_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    # Raw headers preserve duplicate Set-Cookie values, which a dict-based
    # Response constructor would collapse.
    return [
        (key.encode("latin-1").lower(), value.encode("latin-1"))
        for key, value in headers
    ]


def _body_chunks(body: BinaryIO):
    try:
        while True:
            chunk = body.read(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()


def _dispatch_sync(
    *,
    handler_class: Type,
    method: str,
    raw_path: str,
    headers: Message,
    request_body: BinaryIO,
    response_body: BinaryIO,
    server_port: int,
    client_host: str,
) -> tuple[int, list[tuple[str, str]]]:
    context = _RequestContext(
        handler_class=handler_class,
        method=method,
        raw_path=raw_path,
        headers=headers,
        body=request_body,
        response_body=response_body,
        server_port=server_port,
        client_host=client_host,
    )
    dispatch = getattr(context.handler, f"do_{method}", None)
    if dispatch is None:
        raise RuntimeError(f"Legacy handler does not implement {method}")
    dispatch()
    if context.status_code is None or not context.headers_finished:
        raise RuntimeError("Legacy handler returned without completing an HTTP response")
    response_body.seek(0)
    return context.status_code, context.response_headers


async def dispatch_legacy(
    request: Request,
    *,
    handler_class: Type,
    server_port: int,
) -> StreamingResponse:
    """Dispatch one ASGI request through the supplied legacy handler class."""
    request_body = await _spool_request_body(request)
    response_body = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_LIMIT, mode="w+b")
    client = request.scope.get("client") or ("127.0.0.1", 0)
    client_host = str(client[0])
    try:
        status_code, headers = await run_in_threadpool(
            _dispatch_sync,
            handler_class=handler_class,
            method=request.method.upper(),
            raw_path=_legacy_path(request),
            headers=_headers_from_scope(request),
            request_body=request_body,
            response_body=response_body,
            server_port=server_port,
            client_host=client_host,
        )
    except Exception:
        request_body.close()
        response_body.close()
        raise
    finally:
        request_body.close()

    response = StreamingResponse(_body_chunks(response_body), status_code=status_code)
    response.raw_headers = _response_headers(headers)
    return response
