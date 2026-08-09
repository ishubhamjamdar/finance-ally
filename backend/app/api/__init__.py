"""REST API routers.

One module per resource; each exposes a `router` mounted by `app.main`.
"""

from .health import router as health_router

__all__ = ["health_router"]
