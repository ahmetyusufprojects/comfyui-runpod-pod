"""RunPod API package.

Keep package import side-effect free so Windows portable code can import
framework-independent route helpers without installing FastAPI. ASGI app
factories are loaded only when explicitly requested.
"""

__all__ = ["LegacyApps", "create_apps", "create_legacy_app"]


def __getattr__(name):
    if name in __all__:
        from . import app

        return getattr(app, name)
    raise AttributeError(name)
