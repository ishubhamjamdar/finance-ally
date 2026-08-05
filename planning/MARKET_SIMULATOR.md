# Market Simulator — Approach and Code Structure

How FinAlly generates believable stock prices when no `MASSIVE_API_KEY` is configured. This is the **default** data source: most users, all E2E tests, and all offline development run on it, so it has to look convincing on screen and behave sanely under a portfolio's arithmetic.

Implemented in `backend/app/market/simulator.py` + `seed_prices.py`, behind the `MarketDataSource` interface (`MARKET_INTERFACE.md`).

> **Verification basis.** The volatility, correlation, and shock figures in §3, §5 and §6 were measured by re-running the shipped `step()` logic over full simulated trading days (46,800 ticks × 40 trials per ticker) and by eigenvalue analysis of the correlation matrices the code builds. Numbers below are measured, not estimated.

---

## 1. Why GBM

Geometric Brownian Motion is the model under Black-Scholes and the standard choice for synthetic price paths:

- **Prices can't go negative** — moves are multiplicative, and `exp()` is always positive. Important: a negative price would corrupt portfolio valuation and could produce nonsensical trades.
- **Returns are lognormal**, matching the rough shape of real equity returns.
- **Volatility is a single tunable parameter** per ticker, so "TSLA is jumpier than JPM" is one number, not a special case.
- **It's memoryless** — each tick needs only the current price, so state is one float per ticker and `step()` stays cheap at 2 Hz.

What it deliberately doesn't model: mean reversion, volatility clustering, order books, bid/ask spreads, volume, market hours. None of them affect what the UI shows or what the portfolio math needs.

---

## 2. The math

Exact discretisation of GBM (no Euler error — this is the closed-form solution):

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt  +  σ·√dt·Z )
```

| Term | Meaning |
|---|---|
| `S(t)` | current price |
| `μ` | annualised drift (expected return), e.g. `0.05` = 5%/yr |
| `σ` | annualised volatility, e.g. `0.22` = 22%/yr |
| `dt` | time step as a fraction of a **trading** year |
| `Z` | standard normal draw, correlated across tickers (§5) |

The `−σ²/2` term is the Itô correction: it makes `μ` the drift of the *log* price so that `E[S(t)] = S(0)·e^{μt}`. Dropping it would give every ticker a small unintended upward bias that compounds — over a simulated year at σ=0.5 that's a ~13% drift the config never asked for.

### Deriving `dt`

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # 8.479e-08
```

252 trading days × 6.5 hours × 3600 s, with a 500 ms tick. Scaling to a *trading* year (not a calendar year) is what makes `σ = 0.22` mean the same thing it means on a real quote screen: one simulated tick ≈ one real 500 ms interval of market time.

Per-tick move for σ=0.22: `σ·√dt ≈ 0.22 × 2.9e-4 ≈ 0.0064%` — on a $190 stock, ~1.2 cents. Sub-cent-to-cent jitter that accumulates into visible trends over minutes. That is exactly the texture a trading terminal should have.

---

## 3. Measured output vs. reality

Pure GBM (shocks disabled), 1-day price change over 40 simulated days:

| Ticker | σ | Theory `σ/√252` | **Measured 1-day std** | Real-world ballpark |
|---|---|---|---|---|
| JPM | 0.18 | 1.13% | **1.24%** | ~1.1% |
| AAPL | 0.22 | 1.39% | **1.52%** | ~1.4% |
| TSLA | 0.50 | 3.15% | **3.46%** | ~3.1% |

The implementation matches the closed-form expectation within sampling error, and the per-ticker parameters land in the right neighbourhood of real daily volatility. **The GBM core is correct and well-tuned** — see §6 for the shock process, which currently overrides all of it.

---

## 4. Seed prices and per-ticker parameters

`seed_prices.py` holds only constants — no logic — so parameters can be tuned without touching the engine.

```python
SEED_PRICES = {"AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00,
               "TSLA": 250.00, "NVDA": 800.00, "META": 500.00, "JPM": 195.00,
               "V": 280.00, "NFLX": 600.00}

TICKER_PARAMS = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},   # high vol, low drift
    "NVDA":  {"sigma": 0.40, "mu": 0.08},   # high vol, strong drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},   # low vol (bank)
    "V":     {"sigma": 0.17, "mu": 0.04},   # low vol (payments)
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}

DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}   # for tickers added at runtime
```

Design notes:

- Seed prices are **plausible-but-fixed**, matching PLAN.md §6 ("AAPL ~$190, GOOGL ~$175"). They deliberately do not track reality — a simulator that pretends to be live data invites confusion about which mode is running.
- Prices differ by an order of magnitude across tickers ($175 → $800), which exercises the portfolio-weight and heatmap layout code far better than a uniform set would.
- **Unknown tickers get a random price in `[50, 300)`** via `random.uniform`, plus `DEFAULT_PARAMS`. So the AI assistant can add `PYPL` and get a working ticker instantly — no lookup table, no failure path.
- Deterministic seeds mean a fresh container always opens on the same numbers, which makes E2E assertions and screenshots stable.

> Note: `DEFAULT_PARAMS` must be copied per ticker (`dict(DEFAULT_PARAMS)`), not shared by reference, or tuning one runtime-added ticker would mutate the defaults for all of them. The shipped code does this correctly.

---

## 5. Correlated moves via Cholesky

Real tech stocks move together; independent draws look obviously fake when ten tickers wander in ten directions. So the noise is correlated across tickers.

Given a correlation matrix `C`, factor `C = L·Lᵀ` (Cholesky). For a vector of independent standard normals `z`, the vector `L·z` has covariance `L·Lᵀ = C` — correlated draws with unit variance preserved, so per-ticker σ still means what §3 measured.

```python
z_independent = np.random.standard_normal(n)
z_correlated  = self._cholesky @ z_independent
```

Correlation structure (`seed_prices.py`):

| Pair | ρ |
|---|---|
| Tech ∩ tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | **0.6** |
| Finance ∩ finance (JPM, V) | **0.5** |
| Anything involving TSLA | **0.3** |
| Cross-sector / unknown tickers | **0.3** |

TSLA is checked **first**, before sector membership, so despite being in the tech set it stays weakly correlated — it "does its own thing", which is both realistic and useful for demoing an uncorrelated position in the heatmap.

### The matrix cannot become non-positive-definite

`np.linalg.cholesky` raises `LinAlgError` on a non-PD matrix — a crash risk every time a user adds a ticker, if the structure were ill-formed. It isn't. This block structure is PD for any ticker count, with a floor on the smallest eigenvalue of `1 − ρ_max`:

| Ticker set | n | Measured min eigenvalue | Cholesky |
|---|---|---|---|
| Default watchlist | 10 | +0.400 | OK |
| All 7 tech only | 7 | +0.400 | OK |
| Tech + 40 unknown | 47 | +0.400 | OK |
| 50 unknown (ρ=0.3 throughout) | 50 | +0.700 | OK |
| Default + 90 unknown | 100 | +0.400 | OK |
| 200 unknown | 200 | +0.700 | OK |

Comfortably PD in every case, independent of `n`. **Rule to preserve when tuning:** keep every off-diagonal ρ < 1 and keep the pairwise rule *consistent* (a function of group membership only). Hand-editing individual pairs — say ρ(AAPL,MSFT)=0.9 while ρ(AAPL,NVDA)=0.1 — can break PD and crash the next `add_ticker()`. If arbitrary pairs are ever needed, clamp with a nearest-correlation-matrix projection or fall back to independent draws on `LinAlgError`.

Rebuild cost is O(n²) on add/remove only, never on the hot path; at n<50 it's microseconds.

---

## 6. Random shock events — and a required retune

For drama, each ticker each tick can take a sudden 2–5% jump:

```python
if random.random() < self._event_prob:            # event_probability = 0.001
    shock_magnitude = random.uniform(0.02, 0.05)
    shock_sign = random.choice([-1, 1])
    self._prices[ticker] *= 1 + shock_magnitude * shock_sign
```

### The problem: at `0.001` the shocks swamp everything

`0.001` per ticker **per tick**, at 2 ticks/second, is ~0.002/s → one shock per ticker every ~500 s. Over a 6.5-hour session that is **~47 shocks per ticker per day**, each 2–5%. Measured over 40 simulated days:

| Ticker | σ | 1-day std @ `p=0.001` | 1-day std, shocks off | Real-world |
|---|---|---|---|---|
| JPM | 0.18 | **24.56%** | 1.24% | ~1.1% |
| AAPL | 0.22 | **24.61%** | 1.52% | ~1.4% |
| TSLA | 0.50 | **25.05%** | 3.46% | ~3.1% |

Two conclusions:

1. **Daily volatility is ~17× too high** — a 5th–95th percentile day spans roughly −30% to +54%, with single days losing a third of a position's value.
2. **Per-ticker σ has no effect.** All three tickers converge on ~24.6% regardless of σ, because the shock process contributes `√47 × 3.5% ≈ 24%` and drowns the 1.2–3.5% GBM signal. The carefully tuned `TICKER_PARAMS` table is effectively dead config: JPM is exactly as volatile as TSLA.

This is a plausible-looking constant with an implausible consequence, and it undermines the product: a $10,000 portfolio can swing thousands of dollars in minutes, making the P&L chart, the heatmap colours, and any AI analysis of "risk" meaningless.

### Recommended fix

Target roughly **one shock per ticker per session** — enough that a watching user sees one, rare enough that σ still dominates:

```python
# ~1 event per ticker per 6.5h session (46,800 ticks) instead of ~47
event_probability: float = 2e-5
```

Measured at `2e-5`: AAPL 1-day std **4.25%**, TSLA **5.08%** — elevated versus pure GBM, still recognisably equity-like, and σ ordering is preserved.

Two smaller improvements worth making at the same time:

**Make shocks symmetric.** `×(1+x)` and `×(1−x)` are not mirror images, so repeated random-sign shocks compound with a positive skew — visible in the measured p5/p95 asymmetry (−30% / +54%). Use log-space instead:

```python
self._prices[ticker] *= math.exp(shock_magnitude * shock_sign)
```

**Publish the event.** The shock is currently only `logger.debug`. It's the most interesting thing the simulator does, and the terminal demo already reconstructs it by thresholding price changes. Returning events from `step()` would let the SSE stream flag them so the UI can badge a ticker or the AI can mention them.

If a more dramatic demo is wanted, raise `sigma` — which scales volatility in a principled, per-ticker way — rather than the shock rate, which flattens all tickers to the same behaviour.

---

## 7. Code structure

Two classes, split by responsibility: pure math with no I/O, wrapped in an async loop with no math.

```
backend/app/market/
├── seed_prices.py   # constants only: SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS,
│                    # CORRELATION_GROUPS, INTRA_TECH_CORR, INTRA_FINANCE_CORR,
│                    # CROSS_GROUP_CORR, TSLA_CORR
└── simulator.py     # GBMSimulator        — synchronous price engine
                     # SimulatorDataSource — MarketDataSource impl (async loop)
```

### `GBMSimulator` — the engine

Fully synchronous and deterministic given a seeded RNG, so it can be unit-tested without an event loop.

```python
class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR

    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT,
                 event_probability: float = 0.001) -> None: ...

    def step(self) -> dict[str, float]:   # advance all tickers; {ticker: price}
    def add_ticker(self, ticker: str) -> None      # rebuilds Cholesky
    def remove_ticker(self, ticker: str) -> None   # rebuilds Cholesky
    def get_price(self, ticker: str) -> float | None
    def get_tickers(self) -> list[str]

    # internals
    def _add_ticker_internal(self, ticker) -> None  # no rebuild — batch init
    def _rebuild_cholesky(self) -> None
    @staticmethod
    def _pairwise_correlation(t1, t2) -> float
```

State is three parallel dicts keyed by ticker (`_prices`, `_params`) plus `_tickers` as the **ordered** list that indexes rows of the Cholesky factor. That ordering is load-bearing: `z_correlated[i]` must line up with `_tickers[i]`, so any reordering must rebuild the factor.

`_add_ticker_internal` exists so the constructor can add N tickers and rebuild the matrix **once** instead of N times — O(n²) instead of O(n³) at startup.

`get_tickers()` is public specifically so `SimulatorDataSource` never reaches into `_tickers`.

### `SimulatorDataSource` — the async wrapper

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache,
                 update_interval: float = 0.5,
                 event_probability: float = 0.001) -> None: ...

    async def start(self, tickers):     # build sim, seed cache, spawn task
    async def stop(self):               # cancel task, swallow CancelledError
    async def add_ticker(self, ticker): # sim.add + immediate cache seed
    async def remove_ticker(self, ticker):
    def get_tickers(self): ...

    async def _run_loop(self):
        while True:
            try:
                prices = self._sim.step()
                for ticker, price in prices.items():
                    self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")   # never kill the loop
            await asyncio.sleep(self._interval)
```

Behaviours that matter:

- **Cache is seeded synchronously in `start()`** before the loop spawns, so the first SSE frame and the first trade both have prices — no empty-watchlist flash on load.
- **`add_ticker()` seeds immediately too**, so a ticker added by the user or the AI is priceable at once rather than after up to 500 ms.
- **`try/except` inside the loop, not around it.** A single bad step logs and the loop survives; wrapping the `while` would silently end all price updates for the process lifetime.
- **Cadence is decoupled from the SSE cadence.** Both are 500 ms today, and the cache's `version` counter means an SSE tick with no new data sends nothing.
- The loop steps **all** tickers each tick, so cost scales with the watchlist, not with connected clients.

---

## 8. Testing

Current coverage: `tests/market/test_simulator.py` (17 tests, 98% of `simulator.py`) and `test_simulator_source.py` (10 integration tests).

Properties worth asserting — these are the ones that catch real regressions:

| Property | Why |
|---|---|
| Prices stay > 0 over many thousands of steps | Guards the multiplicative invariant; a negative price breaks portfolio math |
| `step()` returns every tracked ticker, and only those | Catches Cholesky/ticker-list desync |
| Seeded RNG ⇒ identical price paths | Determinism for E2E and screenshots |
| Measured 1-day std ≈ `σ/√252` with shocks disabled | The regression test for §3; would catch a dropped `√dt` or Itô term |
| `add_ticker`/`remove_ticker` keep `len(_tickers) == cholesky.shape[0]` | The invariant behind correlated draws |
| Cholesky succeeds for 1, 2, 10, 100 tickers and after add/remove churn | §5's PD guarantee |
| Removing down to 0 or 1 ticker sets `_cholesky = None` and doesn't raise | Edge case: `cholesky` of a 1×1 is pointless, of 0×0 raises |
| Unknown tickers get a price in `[50, 300)` and default params | The AI-adds-a-ticker path |
| `stop()` is idempotent and safe before `start()` | Lifespan shutdown ordering |
| Empirical pairwise correlation of many steps ≈ configured ρ (±tolerance) | The only test that proves Cholesky is actually applied |

Use a fixed `numpy` `Generator` / `random.Random` seed for statistical assertions and pick tolerances from a preliminary run — otherwise these tests flake.

---

## 9. Demo

`backend/market_data_demo.py` renders a live Rich dashboard — all ten tickers, sparklines, direction arrows, and an event log for notable moves. It's the quickest way to eyeball a parameter change:

```bash
cd backend
uv run market_data_demo.py     # 60s, or Ctrl+C
```

Runs the real `SimulatorDataSource` against a real `PriceCache`, so what it shows is what the SSE stream would send.

---

## 10. Extension points

Ordered by value per unit of effort:

1. **Retune `event_probability` to `2e-5`** and make shocks symmetric (§6). Highest impact, three-line change.
2. **Record `previous_close`** — capture the seed price at `start()` and expose it, so the watchlist's "daily change %" is real for both sources (`MARKET_INTERFACE.md` §6).
3. **Return events from `step()`** so shocks can reach the UI and the AI instead of only DEBUG logs.
4. **Configurable speed** — a `SIM_SPEED` multiplier on `dt` to compress a day into a minute for demos. Keep it env-driven and off by default.
5. **Intraday volume/volatility curve** — scale σ by time of day (U-shaped: busy open and close, quiet midday). Cheap realism.
6. **Market hours** — optionally freeze outside 09:30–16:00 ET to match real-data behaviour. Off by default; a permanently frozen demo is worse than an always-live one.
7. **Sector-wide shocks** — apply one shock across a correlation group rather than a single ticker, so the heatmap lights up as a sector. Visually striking, and closer to how real news moves markets.
