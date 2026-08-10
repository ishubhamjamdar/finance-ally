"""REST API routers.

One module per resource; each exposes a `router` mounted by `app.main`.
"""

from .chat import router as chat_router
from .health import router as health_router
from .portfolio import router as portfolio_router
from .watchlist import router as watchlist_router

__all__ = ["chat_router", "health_router", "portfolio_router", "watchlist_router"]
