"""Where things live on disk.

Both the database default and the frontend search path used to count `parents`
from their own module, which meant two independent guesses about the shape of
the source tree — each silently wrong if `app/` ever gains a nesting level, and
both meaningless inside the container, where the package does not sit under a
repo root at all.

One module owns that knowledge now. Every path derived here is a *development*
default: in the image, `DB_PATH` and `STATIC_DIR` are set explicitly.
"""

from __future__ import annotations

from pathlib import Path

#: `backend/` — the uv project root, and the parent of the `app` package.
BACKEND_DIR = Path(__file__).resolve().parents[1]

#: The repo checkout containing `backend/`, `frontend/` and `db/`. Only
#: meaningful in a source checkout; see `is_source_checkout()`.
REPO_ROOT = BACKEND_DIR.parent


def is_source_checkout() -> bool:
    """Whether REPO_ROOT really is the project, rather than wherever an
    installed package happens to sit two directories up."""
    return (REPO_ROOT / "backend" / "app").is_dir()
