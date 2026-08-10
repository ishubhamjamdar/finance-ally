"""Loading `.env` into the process environment.

PLAN.md §5 promises "the backend reads `.env` from the project root", and until
this checkpoint nothing did — `MASSIVE_API_KEY` only ever arrived through the
shell or Docker's `--env-file`. That was survivable while every variable had a
working default. `OPENROUTER_API_KEY` has none, so a developer following the
README would have put the key in `.env` and watched every chat request fail.

The file never overrides what is already set. Docker `--env-file`, a shell
export and a test's `monkeypatch.setenv` all win over the checked-out `.env`,
which is the precedence anyone would assume — and the one that stops a stale
`.env` in a source checkout from quietly redirecting a container.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

from app.paths import REPO_ROOT, is_source_checkout

logger = logging.getLogger(__name__)


def load_env() -> bool:
    """Load `<repo>/.env` into `os.environ`. Returns whether a file was read.

    Absent is normal and silent: the image bakes no `.env`, and PLAN.md §11
    passes the variables in with `--env-file` instead. Only a source checkout
    has a repo root worth looking in at all.
    """
    if not is_source_checkout():
        return False

    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return False

    # override=False: see the module docstring. The environment beats the file.
    load_dotenv(env_file, override=False)
    logger.debug("Loaded environment defaults from %s", env_file)
    return True
