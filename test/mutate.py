#!/usr/bin/env python3
"""Gate 3, step 4: break one invariant at a time and prove the suite notices.

Coverage says a line ran. Mutation testing says a line is *pinned*. Every
checkpoint so far has shipped at least one test that passed against deliberately
broken code until this caught it — thirteen of them at Checkpoint 1 — which is
why the step survives being scoped down but must not be skipped.

    test/mutate.py                    # run every mutation for this checkpoint
    test/mutate.py --list             # show them without running anything
    test/mutate.py -k watchlist       # only mutations whose name matches

**It runs in a throwaway `git worktree`, never your working tree.** An earlier
harness edited files in place and restored them in a `finally` — which a
`SIGKILL` skips, so a killed run left a mutant behind, which then provoked a
`git checkout --` that discarded a day of uncommitted work. A worktree makes
that class of accident impossible: the worst case is a stale directory under
`.git/worktrees`, cleaned up on the next run.

Mutations are committed alongside the code they guard. Add one when you add an
invariant; delete one when the invariant goes. Aim for ten to fifteen per
checkpoint, chosen for the rules that are genuinely this checkpoint's — money
maths, atomicity, the tracked set. Mutating request schemas and route wiring
mostly re-proves what ordinary tests already assert.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKTREE = REPO / ".mutation-worktree"

#: Per-mutation timeout. A mutation that hangs is a mutation the suite caught:
#: removing `snapshot_task.cancel()` leaves shutdown awaiting a task that never
#: finishes, and the unmutated suite returns in seconds.
TIMEOUT_SECONDS = 120

#: The project venv, not `sys.executable`. This script runs under whatever
#: python invoked it — the system one, via the shebang — which has no pytest.
VENV_PYTHON = REPO / "backend" / ".venv" / "bin" / "python"

PORTFOLIO = "tests/test_portfolio.py tests/api/test_portfolio_api.py"
WATCHLIST = "tests/test_watchlist.py tests/api/test_watchlist_api.py"
CHAT = "tests/test_chat.py tests/api/test_chat_api.py"
LLM = "tests/llm tests/test_chat.py tests/api/test_chat_api.py"

#: (name, file relative to backend/, snippet to replace, replacement, tests)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # --- Checkpoint 3: money -------------------------------------------
    ("buy: drop the insufficient-cash check", "app/portfolio.py",
     "    if cost > cash:\n        raise TradeError(",
     "    if False:\n        raise TradeError(", PORTFOLIO),
    ("buy: reject a spend of exactly the balance", "app/portfolio.py",
     "    if cost > cash:", "    if cost >= cash:", PORTFOLIO),
    ("buy: unweighted average cost", "app/portfolio.py",
     "    new_avg_cost = (held_basis + cost) / new_quantity",
     "    new_avg_cost = (held.avg_cost + price) / 2 if held else price", PORTFOLIO),
    ("sell: re-average the cost basis", "app/portfolio.py",
     "    return round(cash + proceeds, 2), remaining, held.avg_cost",
     "    return round(cash + proceeds, 2), remaining, price", PORTFOLIO),
    ("sell: drop the oversell check", "app/portfolio.py",
     "    if quantity > held.quantity + QUANTITY_TOLERANCE:", "    if False:", PORTFOLIO),
    ("sell: no tolerance, so a fractional holding never closes", "app/portfolio.py",
     "QUANTITY_TOLERANCE = 1e-9", "QUANTITY_TOLERANCE = 0.0", PORTFOLIO),
    ("fill value: round to the dollar", "app/portfolio.py",
     "    return round(price * quantity, 2)", "    return round(price * quantity, 0)", PORTFOLIO),
    ("validation: allow non-finite quantities", "app/portfolio.py",
     "    if not math.isfinite(quantity):", "    if False:", PORTFOLIO),
    ("pricing: fill a missing price at zero", "app/portfolio.py",
     '        raise TradeError(f"No price available for {ticker} yet. Try again in a moment.")',
     "        return 0.0", PORTFOLIO),

    # --- Checkpoint 3: durability and honesty ---------------------------
    ("trade: per-statement commits instead of one transaction", "app/portfolio.py",
     "    with transaction() as conn:\n        cash = get_cash_balance(conn, user_id)",
     "    with connect() as conn:\n        cash = get_cash_balance(conn, user_id)", PORTFOLIO),
    ("read: value the portfolio in autocommit", "app/portfolio.py",
     "    with read_transaction() as conn:\n        return _value(conn, prices, user_id)",
     "    with connect() as conn:\n        return _value(conn, prices, user_id)", PORTFOLIO),
    ("trade: value the snapshot from a second cache read", "app/portfolio.py",
     "        portfolio = _value(conn, prices, user_id)",
     "        portfolio = _value(conn, price_cache.get_all(), user_id)", PORTFOLIO),
    ("snapshot: record a total that omits unpriced positions", "app/portfolio.py",
     "    if view.unpriced_tickers:", "    if False:", PORTFOLIO),
    ("valuation: mark an unpriced position at zero", "app/portfolio.py",
     "        if update is None:\n            unpriced.append(position.ticker)",
     "        if False:\n            unpriced.append(position.ticker)", PORTFOLIO),
    ("positions: keep a zero-quantity row", "app/db/repository.py",
     "    if quantity == 0:\n        _delete_position(conn, ticker, user_id)\n        return",
     "    if False:\n        _delete_position(conn, ticker, user_id)\n        return", "tests/"),

    # --- Checkpoint 3: the tracked set ----------------------------------
    ("reconcile: never add a wanted ticker", "app/watchlist.py",
     "    for ticker in sorted(wanted - set(source.get_tickers())):\n        await source.add_ticker(ticker)",
     "    for ticker in sorted(set()):\n        await source.add_ticker(ticker)", WATCHLIST),
    ("reconcile: never drop an unwanted ticker", "app/watchlist.py",
     "    for ticker in sorted(set(source.get_tickers()) - wanted):\n        await source.remove_ticker(ticker)",
     "    for ticker in sorted(set()):\n        await source.remove_ticker(ticker)", WATCHLIST),
    ("reconcile: do not re-read after removing", "app/watchlist.py",
     "    wanted = set(await run_in_threadpool(load_tracked_tickers, user_id))\n"
     "    for ticker in sorted(wanted - set(source.get_tickers())):",
     "    for ticker in sorted(wanted - set(source.get_tickers())):", WATCHLIST),
    ("lifespan: never cancel the snapshot task", "app/main.py",
     "        snapshot_task.cancel()", "        pass", "tests/test_main.py"),

    # --- Checkpoint 4: the model is a client, not an authority ----------
    # The invariant this checkpoint owns is that nothing the model returns
    # reaches the ledger unvalidated, and that nothing it does is hidden.
    ("parse: accept an action the schema rejects", "app/llm/schema.py",
     "            valid.append(model.model_validate(item))",
     "            valid.append(model.model_construct(**item))", LLM),
    ("parse: let a model name its own fill price", "app/llm/schema.py",
     '    model_config = ConfigDict(extra="forbid")\n\n'
     "    ticker: Annotated[str, Field(pattern=TICKER_PATTERN)]\n"
     '    side: Literal["buy", "sell"]',
     '    model_config = ConfigDict(extra="allow")\n\n'
     "    ticker: Annotated[str, Field(pattern=TICKER_PATTERN)]\n"
     '    side: Literal["buy", "sell"]', LLM),
    ("parse: admit an infinite order size", "app/llm/schema.py",
     "    quantity: Annotated[float, Field(gt=0, allow_inf_nan=False)]",
     "    quantity: Annotated[float, Field(gt=0)]", LLM),
    ("parse: drop the per-reply action cap", "app/llm/schema.py",
     "        if len(valid) >= cap:", "        if False:", LLM),
    ("parse: treat a reply with no message as usable", "app/llm/schema.py",
     "    if not isinstance(message, str) or not message.strip():",
     "    if False:", LLM),
    ("chat: swallow a refused trade instead of reporting it", "app/chat.py",
     "            logger.info(\"Chat trade refused (%s): %s\", summary, exc)",
     "            logger.info(\"Chat trade refused (%s): %s\", summary, exc)\n            continue",
     CHAT),
    ("chat: swallow a refused watchlist change", "app/chat.py",
     "            logger.info(\"Chat watchlist change refused (%s): %s\", summary, exc)",
     "            logger.info(\"Chat watchlist change refused (%s): %s\", summary, exc)\n            continue",
     CHAT),
    ("chat: raise on a malformed reply instead of answering", "app/chat.py",
     "        logger.warning(\"Discarding an unusable model reply: %s\", exc)",
     "        raise", CHAT),
    ("chat: run removes before trades, killing the price a trade needs", "app/chat.py",
     "    actions = await _apply_watchlist_changes(source, adds, user_id)\n"
     "    actions += await _apply_trades(price_cache, parsed.trades, user_id)\n"
     "    actions += await _apply_watchlist_changes(source, removes, user_id)",
     "    actions = await _apply_watchlist_changes(source, adds + removes, user_id)\n"
     "    actions += await _apply_trades(price_cache, parsed.trades, user_id)", CHAT),
    ("chat: report the model's raw ticker rather than the normalised one", "app/chat.py",
     "        ticker = normalize_ticker(trade.ticker)", "        ticker = trade.ticker", CHAT),
    ("chat: persist the turn before the actions are known", "app/chat.py",
     "        insert_chat_message(conn, \"user\", user_text, None, user_id)",
     "        insert_chat_message(conn, \"user\", user_text, None, user_id)\n        actions = []",
     CHAT),
    ("chat: persist a failed turn the provider never answered", "app/chat.py",
     "    raw = await run_in_threadpool(complete, messages)",
     "    try:\n        raw = await run_in_threadpool(complete, messages)\n"
     "    except Exception:\n        await _finish(text, 'failed', [], price_cache, user_id)\n        raise",
     CHAT),
    ("prompt: replay an assistant turn without what executed", "app/llm/prompt.py",
     "    if message.role != \"assistant\" or not message.actions:",
     "    if True:", LLM),
    ("prompt: put the portfolio context in a user message", "app/llm/prompt.py",
     '        {"role": "system", "content": render_context(portfolio, watchlist)},',
     '        {"role": "user", "content": render_context(portfolio, watchlist)},', LLM),
    ("client: fall back to a live call when LLM_MOCK is set", "app/llm/client.py",
     "    if is_mock_enabled():\n        return mock_completion(messages)", "    pass", LLM),
    ("client: forward the provider's error text to the user", "app/llm/client.py",
     '            f"Could not reach the AI assistant ({type(exc).__name__}). Please try again."',
     '            f"Could not reach the AI assistant ({exc}). Please try again."', LLM),
    ("watchlist: drop the size cap", "app/watchlist.py",
     "        if enforce_cap and count_watchlist(conn, user_id) > MAX_WATCHLIST_SIZE:",
     "        if False:", WATCHLIST),
    ("watchlist: enforce the cap on a compensating restore", "app/watchlist.py",
     "        await run_in_threadpool(_insert_row, ticker, user_id, enforce_cap=False)",
     "        await run_in_threadpool(_insert_row, ticker, user_id)", WATCHLIST),
]


def build_worktree() -> pathlib.Path:
    """A clean checkout of HEAD, isolated from the working tree."""
    if WORKTREE.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(WORKTREE)],
                       cwd=REPO, capture_output=True)
        shutil.rmtree(WORKTREE, ignore_errors=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(WORKTREE), "HEAD"],
                   cwd=REPO, check=True, capture_output=True)
    return WORKTREE / "backend"


def suite_passes(backend: pathlib.Path, tests: str, python: pathlib.Path) -> bool:
    """True if the suite passed — i.e. the mutation went unnoticed."""
    try:
        result = subprocess.run(
            [str(python), "-m", "pytest", "-x", "-q", "--no-header",
             "-p", "no:cacheprovider", *tests.split()],
            cwd=backend, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False  # a hang is the suite noticing; see TIMEOUT_SECONDS
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show mutations without running them")
    parser.add_argument("-k", metavar="SUBSTRING", default="", help="only matching mutations")
    args = parser.parse_args()

    selected = [m for m in MUTATIONS if args.k.lower() in m[0].lower()]
    if args.list:
        for name, path, *_ in selected:
            print(f"{path:28} {name}")
        return 0
    if not selected:
        print(f"No mutation matches {args.k!r}.")
        return 1

    backend = build_worktree()
    python = VENV_PYTHON if VENV_PYTHON.exists() else pathlib.Path(sys.executable)
    if not VENV_PYTHON.exists():
        print("No backend/.venv — run `uv sync --extra dev` in backend/ first.")
        return 1

    print(f"Baseline: the unmutated suite must pass in {backend}")
    if not suite_passes(backend, "tests/", python):
        print("  FAILED — the suite is red before any mutation. Fix that first.")
        return 1
    print("  ok\n")

    survivors: list[str] = []
    for name, relpath, old, new, tests in selected:
        path = backend / relpath
        original = path.read_text()
        if original.count(old) != 1:
            print(f"STALE     {name}: snippet appears {original.count(old)}x in {relpath}")
            survivors.append(name)
            continue
        path.write_text(original.replace(old, new))
        try:
            unnoticed = suite_passes(backend, tests, python)
        finally:
            path.write_text(original)
        print(f"{'SURVIVED ' if unnoticed else 'killed   '} {name}", flush=True)
        if unnoticed:
            survivors.append(name)

    subprocess.run(["git", "worktree", "remove", "--force", str(WORKTREE)],
                   cwd=REPO, capture_output=True)

    print()
    if survivors:
        print(f"{len(survivors)} of {len(selected)} survived — each is a test that cannot fail:")
        for name in survivors:
            print(f"  - {name}")
        return 1
    print(f"All {len(selected)} mutations killed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
