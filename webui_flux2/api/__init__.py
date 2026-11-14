"""ASGI compatibility layer for the existing RunPod HTTP handlers.

The module intentionally does not import ``server``. The caller supplies the
legacy handler class so importing an ASGI app does not start vault watchdogs,
touch storage, or import the ComfyUI/LLM runtimes.
"""

from .app import LegacyApps, create_apps, create_legacy_app

__all__ = ["LegacyApps", "create_apps", "create_legacy_app"]
