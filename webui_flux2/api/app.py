"""FastAPI applications which temporarily adapt the existing Handler API.

This is a migration seam, not a replacement for application services. The
handler class remains the temporary source of route, auth, and response
behavior until each route family is moved behind native FastAPI routers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from fastapi import FastAPI, Request
from collections.abc import Callable
from typing import Any

from .health import create_health_router
from .legacy import dispatch_legacy

MAIN_PORT = 8189
SETUP_PORT = 8192
SUPPORTED_PORTS = frozenset((MAIN_PORT, SETUP_PORT))


@dataclass(frozen=True)
class LegacyApps:
    """Separate ASGI entry points matching the two existing RunPod ports."""

    main: FastAPI
    setup: FastAPI


def create_legacy_app(handler_class: Type, *, port: int, title: str | None = None,
                      health_provider: Callable[[], tuple[int, dict[str, Any]]] | None = None) -> FastAPI:
    """Create a catch-all ASGI adapter around a BaseHTTPRequestHandler class.

    Only GET and POST are registered, matching the methods implemented by the
    legacy Handler. OpenAPI and interactive docs are disabled so this
    transitional adapter does not add public endpoints.
    """
    if port not in SUPPORTED_PORTS:
        raise ValueError(f"Unsupported legacy app port: {port}")

    app = FastAPI(
        title=title or ("RunPod setup" if port == SETUP_PORT else "RunPod WebUI"),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.legacy_handler_class = handler_class
    app.state.legacy_server_port = port

    if health_provider is not None:
        health_path = "/api/setup-state" if port == SETUP_PORT else "/api/sunucu-hazir"
        app.include_router(create_health_router(health_path, health_provider))

    async def dispatch(request: Request):
        return await dispatch_legacy(
            request,
            handler_class=handler_class,
            server_port=port,
        )

    app.add_api_route("/", dispatch, methods=["GET", "POST"], include_in_schema=False)
    app.add_api_route("/{path:path}", dispatch, methods=["GET", "POST"], include_in_schema=False)
    return app


def create_apps(handler_class: Type, *, main_health=None, setup_health=None) -> LegacyApps:
    """Build main (8189) and setup (8192) apps over the same handler class."""
    return LegacyApps(
        main=create_legacy_app(handler_class, port=MAIN_PORT,
                               health_provider=main_health),
        setup=create_legacy_app(handler_class, port=SETUP_PORT,
                                health_provider=setup_health),
    )
