#!/usr/bin/env python3
"""Gate 3, step 4: break one invariant at a time and prove the suite notices.

Coverage says a line ran. Mutation testing says a line is *pinned*. Every
checkpoint so far has shipped at least one test that passed against deliberately
broken code until this caught it — thirteen of them at Checkpoint 1 — which is
why the step survives being scoped down but must not be skipped.

    test/mutate.py                    # run every mutation for this checkpoint
    test/mutate.py --list             # show them without running anything
    test/mutate.py -k watchlist       # only mutations whose name matches
    test/mutate.py --project e2e      # one side: backend, frontend, packaging, e2e

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
BACKEND_MUTATIONS: list[tuple[str, str, str, str, str]] = [
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

#: (name, file relative to frontend/, snippet, replacement) — Checkpoint 6.
#:
#: No per-mutation test selection here: the whole vitest suite runs in about two
#: seconds, and a wrongly narrowed selection is a way for a mutation to look
#: killed by a file that never loaded the mutated module.
FRONTEND_MUTATIONS: list[tuple[str, str, str, str]] = [
    # --- the treemap's geometry ----------------------------------------
    ("treemap: do not normalise weights to the box",
     "src/lib/treemap.ts",
     "  const scale = (width * height) / total;",
     "  const scale = 1;"),
    ("treemap: give a null or zero weight a tile of nothing",
     "src/lib/treemap.ts",
     "Number.isFinite(entry.value) && entry.value > 0",
     "Number.isFinite(entry.value) && entry.value >= 0"),
    ("treemap: slice-and-dice instead of squarifying",
     "src/lib/treemap.ts",
     "    if (free.width >= free.height) {",
     "    if (true) {"),
    # --- what a holding is worth ---------------------------------------
    ("valuation: value an unpriced holding at zero",
     "src/lib/valuation.ts",
     "    const marketValue = price === null ? null : price * position.quantity;",
     "    const marketValue = price === null ? 0 : price * position.quantity;"),
    ("valuation: weight positions by cost rather than market value",
     "src/lib/valuation.ts",
     "  const total = marked.reduce((sum, position) => sum + (position.marketValue ?? 0), 0);",
     "  const total = marked.reduce((sum, position) => sum + position.costBasis, 0);"),
    ("heatmap: size an unpriced holding by its cost",
     "src/components/PortfolioHeatmap.tsx",
     "  const tiles = squarify(positions, (position) => position.weight);",
     "  const tiles = squarify(positions, (position) => position.weight ?? position.costBasis);"),
    # --- the plot ------------------------------------------------------
    ("chart: drop the flat-series guard, so a still line vanishes",
     "src/components/LineChart.tsx",
     "  const padding = range === 0 ? Math.max(Math.abs(high) * 0.01, 0.01) : range * 0.08;",
     "  const padding = range * 0.08;"),
    ("chart: start the y axis at zero, flattening every real move",
     "src/components/LineChart.tsx",
     "  const min = low - padding;",
     "  const min = 0;"),
    ("chart: draw a non-finite value instead of dropping it",
     "src/components/LineChart.tsx",
     "  const series = values.filter((value) => Number.isFinite(value));",
     "  const series = [...values];"),
    # --- the order ticket ----------------------------------------------
    ("trade bar: send a quantity of zero or less",
     "src/components/TradeBar.tsx",
     "    if (!Number.isFinite(size) || size <= 0) {",
     "    if (false) {"),
    ("trade bar: swallow the rejection instead of showing it",
     "src/components/TradeBar.tsx",
     "      setError(describeError(cause));",
     "      setError(null);"),
    ("trade bar: revert the symbol to the chart after a fill",
     "src/components/TradeBar.tsx",
     "      setQuantity(\"\");\n    } catch (cause: unknown) {",
     "      setQuantity(\"\");\n      setTypedTicker(null);\n    } catch (cause: unknown) {"),
    ("trade bar: let a second order go while one is in flight",
     "src/components/TradeBar.tsx",
     "    if (pending) return;",
     "    if (false) return;"),
    ("trade bar: skip the symbol shape check",
     "src/components/TradeBar.tsx",
     "    if (!isTicker(symbol)) {",
     "    if (false) {"),
    # --- what happens after an account change --------------------------
    ("provider: refresh before the trade has landed",
     "src/state/TerminalProvider.tsx",
     "      const result = await sendJson<TradeResponse>(ENDPOINTS.trade, \"POST\", order);\n      refresh();",
     "      refresh();\n      const result = await sendJson<TradeResponse>(ENDPOINTS.trade, \"POST\", order);"),
    ("provider: a removal re-reads the list but not the portfolio",
     "src/state/TerminalProvider.tsx",
     "      await sendJson<WatchlistRemoval>(watchlistEntryPath(ticker), \"DELETE\");\n      refresh();",
     "      await sendJson<WatchlistRemoval>(watchlistEntryPath(ticker), \"DELETE\");\n      reloadWatchlist();"),
    ("resource: leak the poll interval past unmount",
     "src/hooks/useApiResource.ts",
     "    return () => clearInterval(timer);",
     "    return;"),
    # --- what the feed panel counts ------------------------------------
    ("stream: count every ticker ever priced, not the last frame's",
     "src/hooks/usePriceStream.ts",
     "          pricedTickers: priced,",
     "          pricedTickers: Object.keys(prices).length,"),
    # --- what may be charted -------------------------------------------
    ("page: decide the charted ticker from the append-only price record",
     "src/app/page.tsx",
     "  const selected = picked !== null && chartable.has(picked) ? picked : (watched[0] ?? null);",
     "  const selected =\n    picked !== null && (chartable.has(picked) || market.prices[picked] !== undefined)\n      ? picked\n      : (watched[0] ?? null);"),
    # --- what a rejection says -----------------------------------------
    ("api: drop the FastAPI 422 detail array",
     "src/lib/api.ts",
     "      if (Array.isArray(body.detail)) {",
     "      if (false) {"),

    # --- Checkpoint 7: the transcript must not lie -----------------------
    # The model writes its message before it knows whether anything cleared,
    # so every one of these is a way for the panel to claim a fill that never
    # happened — or to hide one that did.
    ("chat: hide the actions that failed",
     "src/components/ChatActions.tsx",
     "      {actions.map((action, index) => (",
     "      {actions.filter((action) => action.ok).map((action, index) => ("),
    ("chat: render nothing when every action failed",
     "src/components/ChatActions.tsx",
     "  if (actions.length === 0) return null;",
     "  if (actions.length === 0 || actions.every((action) => !action.ok)) return null;"),
    ("chat: swallow the outcome, showing only what was attempted",
     "src/components/ChatActions.tsx",
     "            {action.detail}",
     "            {action.summary}"),
    ("chat: never say a reply only partly executed",
     "src/components/ChatActions.tsx",
     "  const mixed = failed > 0 && failed < actions.length;",
     "  const mixed = false;"),
    ("chat: drop the actions from the turn entirely",
     "src/components/ChatPanel.tsx",
     "      {message.actions !== null && message.actions.length > 0 && (\n"
     "        <ChatActions actions={message.actions} />\n"
     "      )}",
     "      {false && <ChatActions actions={message.actions ?? []} />}"),

    # --- Checkpoint 7: a dropped connection is not a refusal -------------
    ("chat: treat a dropped connection as proof nothing happened",
     "src/components/ChatPanel.tsx",
     "      if (cause instanceof ApiError) {",
     "      if (true) {"),
    ("chat: skip the refresh when the outcome is unknown",
     "src/components/ChatPanel.tsx",
     "        setSendError(UNKNOWN_OUTCOME);\n        onRefresh?.();",
     "        setSendError(UNKNOWN_OUTCOME);"),
    ("chat: overwrite a follow-up typed while the turn was in flight",
     "src/components/ChatPanel.tsx",
     "        setDraft((current) => (current === \"\" ? text : current));",
     "        setDraft(text);"),

    # --- Checkpoint 7: the transcript's identity -------------------------
    ("chat: send before the stored transcript has landed",
     "src/components/ChatPanel.tsx",
     "    if (pending !== null || loading) return;",
     "    if (pending !== null) return;"),
    ("chat: let a second turn go while one is in flight",
     "src/components/ChatPanel.tsx",
     "    if (pending !== null || loading) return;\n\n    const text = draft.trim();",
     "    if (loading) return;\n\n    const text = draft.trim();"),
    ("chat: send the message untrimmed",
     "src/components/ChatPanel.tsx",
     "    const text = draft.trim();",
     "    const text = draft;"),
    ("chat: send on Shift+Enter too, losing the line break",
     "src/components/ChatPanel.tsx",
     '    if (event.key === "Enter" && !event.shiftKey) {',
     '    if (event.key === "Enter") {'),
    ("chat: ignore the collapsed state",
     "src/components/ChatPanel.tsx",
     "  if (collapsed) {",
     "  if (false) {"),

    # --- Checkpoint 7: what a turn does to the rest of the page ----------
    ("provider: refresh the chat history too, doubling every appended turn",
     "src/state/TerminalProvider.tsx",
     "    reloadPortfolio();\n    reloadWatchlist();\n    reloadHistory();",
     "    reloadPortfolio();\n    reloadWatchlist();\n    reloadHistory();\n    reloadChat();"),
    ("provider: do not re-read the account after a turn",
     "src/state/TerminalProvider.tsx",
     "      refresh();\n      return reply;",
     "      return reply;"),
    # Records the user's half even when the turn failed — a transcript
    # claiming a message was sent that the server never saw.
    ("provider: append a turn the server never answered",
     "src/state/TerminalProvider.tsx",
     "      const reply = await sendJson<ChatReply>(ENDPOINTS.chat, \"POST\", { message });",
     "      const reply = await sendJson<ChatReply>(ENDPOINTS.chat, \"POST\", { message }).catch(\n"
     "        (cause: unknown) => {\n"
     "          setTurns((previous) => [\n"
     "            ...previous,\n"
     "            {\n"
     '              id: "local-failed",\n'
     '              role: "user" as const,\n'
     "              content: message,\n"
     "              actions: null,\n"
     "              created_at: new Date().toISOString(),\n"
     "            },\n"
     "          ]);\n"
     "          throw cause;\n"
     "        },\n"
     "      );"),
    ("provider: drop the stored transcript, keeping only this session",
     "src/state/TerminalProvider.tsx",
     "    () => [...(chatHistory.data?.messages ?? []), ...turns],",
     "    () => [...turns],"),
]


#: (name, file relative to the *repo root*, snippet, replacement) — Checkpoint 8.
#:
#: The packaging files are not code, and the thing that kills these is
#: `tests/test_packaging.py`. That is the point: Checkpoint 8's failures are
#: silent — a baked secret, a database written outside the volume, two front
#: doors with two databases — so the only question worth asking of those tests
#: is whether they can fail at all.
#:
#: `app.paths.REPO_ROOT` resolves to the worktree, so the tests read the mutated
#: copies rather than yours.
#:
#: Three of this checkpoint's invariants are **not** here, because no unit test
#: can see them: that a fresh named volume inherits the runtime user's ownership,
#: that `docker stop` reaches uvicorn, and that the export in the image is the
#: one Node just built. Those are verified against a running container by
#: `test/smoke_docker.sh`, and the Gate 3 notes record the two experiments run
#: by hand to prove the first two fail when reverted.
PACKAGING_MUTATIONS: list[tuple[str, str, str, str]] = [
    # --- the secret must not enter the build context --------------------
    ("dockerignore: let .env into the build context",
     ".dockerignore", "# Secrets\n.env\n", "# Secrets\n"),
    ("dockerignore: take the committed template out with it",
     ".dockerignore", "!.env.example", "# (no exception)"),
    ("dockerfile: inline an API base into every user's bundle",
     "Dockerfile", 'ENV NEXT_PUBLIC_API_BASE=""',
     'ENV NEXT_PUBLIC_API_BASE="http://localhost:8000"'),

    # --- the database must land in the volume ---------------------------
    ("dockerfile: write the database into the image, not the mount",
     "Dockerfile", "ENV DB_PATH=/app/db/finally.db", "ENV DB_PATH=/app/finally.db"),
    ("dockerfile: serve the frontend from somewhere it was not copied",
     "Dockerfile", "    STATIC_DIR=/app/static", "    STATIC_DIR=/app/frontend"),
    ("dockerfile: never copy the export in at all",
     "Dockerfile", "COPY --from=frontend /build/out ./static",
     "RUN mkdir -p ./static"),
    ("dockerfile: create the mount point after handing it over",
     "Dockerfile",
     "    && mkdir -p /app/db \\\n    && chown finally:finally /app/db",
     "    && chown finally:finally /app/db \\\n    && mkdir -p /app/db"),
    ("dockerfile: chown the whole image, duplicating .venv into a layer",
     "Dockerfile", "chown finally:finally /app/db", "chown -R finally:finally /app"),
    ("dockerfile: run as root",
     "Dockerfile", "USER finally", "# USER finally"),

    # --- shutdown must reach the lifespan -------------------------------
    ("dockerfile: wait forever for the open price stream",
     "Dockerfile",
     'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \\\n'
     '     "--timeout-graceful-shutdown", "3"]',
     'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]'),
    ("start_mac: let Docker SIGKILL the shutdown it asked for",
     "scripts/start_mac.sh", '        --stop-timeout "$STOP_TIMEOUT" \\\n', ""),
    ("start_mac: a grace period shorter than the server takes",
     "scripts/start_mac.sh", "STOP_TIMEOUT=15", "STOP_TIMEOUT=1"),
    ("compose: a grace period shorter than the server takes",
     "docker-compose.yml", "stop_grace_period: 15s", "stop_grace_period: 1s"),
    ("start_windows: a grace period shorter than the server takes",
     "scripts/start_windows.ps1", "$StopTimeout = 15", "$StopTimeout = 1"),
    ("dockerfile: shell-form CMD, so /bin/sh is PID 1 and eats the SIGTERM",
     "Dockerfile",
     'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \\\n'
     '     "--timeout-graceful-shutdown", "3"]',
     "CMD uvicorn app.main:app --host 0.0.0.0 --port 8000"),

    # --- one deployment, not two ----------------------------------------
    ("compose: let the project name prefix the volume",
     "docker-compose.yml", "    name: finally-data", "    # unnamed"),
    ("compose: refuse to start without a .env",
     "docker-compose.yml", "        required: false", "        required: true"),
    ("stop_mac: delete the portfolio along with the container",
     "scripts/stop_mac.sh", "docker rm \"$CONTAINER\" >/dev/null",
     "docker rm \"$CONTAINER\" >/dev/null\ndocker volume rm finally-data >/dev/null"),

    # --- the security review's finding ----------------------------------
    ("start_mac: publish on every interface",
     "scripts/start_mac.sh", '--publish "${BIND}:${PORT}:8000"',
     '--publish "${PORT}:8000"'),
    ("start_windows: publish on every interface",
     "scripts/start_windows.ps1", '"--publish", "${Bind}:${Port}:8000",',
     '"--publish", "${Port}:8000",'),
    ("compose: publish on every interface",
     "docker-compose.yml", '- "${FINALLY_BIND:-127.0.0.1}:${FINALLY_PORT:-8000}:8000"',
     '- "${FINALLY_PORT:-8000}:8000"'),
]

#: The packaging mutations are killed by this file alone; running the rest of
#: the suite would only add ten seconds per mutation.
PACKAGING_TESTS = "tests/test_packaging.py"

#: (name, file relative to the *repo root*, snippet, replacement) — Checkpoint 9.
#:
#: The end-to-end suite takes minutes per run, so mutating the application and
#: re-running it is not a mutation *suite* — it is an afternoon. What is
#: mutated here is the harness's own contract, killed by
#: `tests/test_e2e_harness.py` in milliseconds: the settings that would let the
#: suite pass without testing anything (a retry, a stray `.only`), the ones
#: that would make it depend on a key, and the ones that would make a fresh
#: start mean "whatever the last run left behind".
#:
#: The complementary question — does the suite fail when the *app* breaks? —
#: cannot be answered this way and was answered by hand at Gate 3; the log
#: records which mutation was used and what failed.
E2E_MUTATIONS: list[tuple[str, str, str, str]] = [
    # --- the suite must not be able to hide intermittency ----------------
    ("e2e: retry a failing spec until it passes",
     "test/e2e/playwright.config.ts", "retries: 0", "retries: 2"),
    ("e2e: run specs in parallel against one shared account",
     "test/e2e/playwright.config.ts", "workers: 1", "workers: 4"),
    ("e2e: allow a stray .only to shrink the suite to one test",
     "test/e2e/Dockerfile", '"playwright", "test", "--forbid-only"',
     '"playwright", "test"'),

    # --- the suite must need no secrets ----------------------------------
    ("e2e: let the real model answer the chat specs",
     "test/docker-compose.test.yml", 'LLM_MOCK: "true"', 'LLM_MOCK: "false"'),
    ("e2e: inherit the developer's OpenRouter key instead of pinning it empty",
     "test/docker-compose.test.yml", '    OPENROUTER_API_KEY: ""\n', ""),
    ("e2e: inherit a Massive key, so the suite polls a paid API",
     "test/docker-compose.test.yml", '    MASSIVE_API_KEY: ""\n', ""),
    ("e2e: read the developer's .env into the test container",
     "test/docker-compose.test.yml", "  environment:\n    LLM_MOCK:",
     "  env_file:\n    - ../.env\n  environment:\n    LLM_MOCK:"),

    # --- each run must start clean ---------------------------------------
    ("e2e: persist the database between runs",
     "test/docker-compose.test.yml", "  stop_grace_period: 15s",
     "  volumes:\n    - finally-e2e-data:/app/db\n  stop_grace_period: 15s"),
    ("e2e: drop the pristine app, so a fresh start means 'ran first'",
     "test/docker-compose.test.yml", "  app-pristine:\n    <<: *app\n", ""),
    ("e2e: point the fresh-start specs at the shared app",
     "test/docker-compose.test.yml", 'PRISTINE_URL: "http://app-pristine:8000"',
     'BASE_URL_AGAIN: "http://app:8000"'),

    # --- the runner must match its browsers ------------------------------
    ("e2e: float the Playwright dependency off its image tag",
     "test/e2e/package.json", '"@playwright/test": "1.62.1"',
     '"@playwright/test": "^1.62.1"'),
    ("e2e: pull a browser image the library does not match",
     "test/e2e/Dockerfile", "playwright:v1.62.1-noble", "playwright:v1.55.0-noble"),

    # --- and the scenarios must still be there ---------------------------
    ("e2e: lose the SSE reconnection scenario",
     "test/e2e/specs/sse-resilience.spec.ts", "test.describe(", "test.describe.skip("),
    ("e2e: sleep instead of waiting for a condition",
     "test/e2e/specs/trading.spec.ts", "    const cashBefore = await readCash(page);",
     "    await page.waitForTimeout(2000);\n    const cashBefore = await readCash(page);"),
]

#: Same reasoning as PACKAGING_TESTS.
E2E_TESTS = "tests/test_e2e_harness.py"


def build_worktree() -> pathlib.Path:
    """A clean checkout of HEAD, isolated from the working tree.

    Returns the backend directory; `.parent` is the checkout root.
    """
    if WORKTREE.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(WORKTREE)],
                       cwd=REPO, capture_output=True)
        shutil.rmtree(WORKTREE, ignore_errors=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(WORKTREE), "HEAD"],
                   cwd=REPO, check=True, capture_output=True)
    return WORKTREE / "backend"


def backend_passes(backend: pathlib.Path, tests: str, python: pathlib.Path) -> bool:
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


def frontend_passes(frontend: pathlib.Path) -> bool:
    """True if vitest passed — i.e. the mutation went unnoticed."""
    try:
        result = subprocess.run(
            ["npx", "vitest", "run", "--silent"],
            cwd=frontend, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def link_node_modules(frontend: pathlib.Path) -> None:
    """Point the worktree at the installed dependencies rather than reinstalling.

    `npm ci` into a throwaway checkout costs a minute per run for a tree that is
    byte-identical to the one already on disk. The mutations never touch it.
    """
    installed = REPO / "frontend" / "node_modules"
    target = frontend / "node_modules"
    if not installed.exists():
        raise SystemExit("No frontend/node_modules — run `npm install` in frontend/ first.")
    if not target.exists():
        target.symlink_to(installed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show mutations without running them")
    parser.add_argument("-k", metavar="SUBSTRING", default="", help="only matching mutations")
    parser.add_argument(
        "--project",
        choices=("backend", "frontend", "packaging", "e2e"),
        help="only one side",
    )
    args = parser.parse_args()

    # (project, name, path relative to that project, old, new, tests)
    every: list[tuple[str, str, str, str, str, str]] = [
        ("backend", name, path, old, new, tests)
        for name, path, old, new, tests in BACKEND_MUTATIONS
    ] + [
        ("frontend", name, path, old, new, "")
        for name, path, old, new in FRONTEND_MUTATIONS
    ] + [
        # Paths are relative to the repo root; the tests that kill them run
        # from backend/, and read the worktree through app.paths.REPO_ROOT.
        ("packaging", name, path, old, new, PACKAGING_TESTS)
        for name, path, old, new in PACKAGING_MUTATIONS
    ] + [
        ("e2e", name, path, old, new, E2E_TESTS)
        for name, path, old, new in E2E_MUTATIONS
    ]

    selected = [
        m for m in every
        if args.k.lower() in m[1].lower() and args.project in (None, m[0])
    ]
    if args.list:
        for project, name, path, *_ in selected:
            print(f"{project:9} {path:36} {name}")
        return 0
    if not selected:
        print(f"No mutation matches {args.k!r}.")
        return 1

    root = build_worktree().parent
    backend, frontend = root / "backend", root / "frontend"
    python = VENV_PYTHON if VENV_PYTHON.exists() else pathlib.Path(sys.executable)

    wanted = {project for project, *_ in selected}
    if wanted & {"backend", "packaging", "e2e"} and not VENV_PYTHON.exists():
        print("No backend/.venv — run `uv sync --extra dev` in backend/ first.")
        return 1
    if "frontend" in wanted:
        link_node_modules(frontend)

    print("Baseline: the unmutated suites must pass in the worktree")
    if "backend" in wanted and not backend_passes(backend, "tests/", python):
        print("  FAILED — the backend suite is red before any mutation. Fix that first.")
        return 1
    if "packaging" in wanted and not backend_passes(backend, PACKAGING_TESTS, python):
        print("  FAILED — the packaging tests are red in the worktree. Fix that first.")
        return 1
    if "e2e" in wanted and not backend_passes(backend, E2E_TESTS, python):
        print("  FAILED — the e2e harness tests are red in the worktree. Fix that first.")
        return 1
    if "frontend" in wanted and not frontend_passes(frontend):
        print("  FAILED — the frontend suite is red before any mutation. Fix that first.")
        return 1
    print("  ok\n")

    survivors: list[str] = []
    roots = {"backend": backend, "frontend": frontend, "packaging": root, "e2e": root}
    for project, name, relpath, old, new, tests in selected:
        path = roots[project] / relpath
        original = path.read_text()
        if original.count(old) != 1:
            print(f"STALE     {name}: snippet appears {original.count(old)}x in {relpath}")
            survivors.append(name)
            continue
        path.write_text(original.replace(old, new))
        try:
            unnoticed = (
                frontend_passes(frontend)
                if project == "frontend"
                else backend_passes(backend, tests, python)
            )
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
