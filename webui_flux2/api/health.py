"""FastAPI router for the two unauthenticated readiness endpoints."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse


def create_health_router(path: str, status_provider: Callable[[], tuple[int, dict[str, Any]]]):
    router = APIRouter()

    @router.get(path, include_in_schema=False)
    def health():
        code, payload = status_provider()
        return JSONResponse(payload, status_code=code,
                            headers={"Cache-Control": "no-store"})

    return router
