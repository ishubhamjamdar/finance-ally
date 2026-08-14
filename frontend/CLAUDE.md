# Frontend — Developer Guide

## Project setup

```bash
cd frontend
npm install
npm run dev        # UI on :3000 — needs NEXT_PUBLIC_API_BASE, see below
npm run build      # static export into out/
npm test           # vitest + Testing Library
npm run lint       # eslint
npm run typecheck  # tsc --noEmit
```

## How this is served

`next.config.ts` sets `output: 'export'`, so `npm run build` writes a static
site to `out/` and there is no Node process in production. FastAPI serves that
directory at `/` **after** every API router, so the UI and the API share one
origin and one port and this project has no CORS configuration anywhere.

The whole app runs from one backend:

```bash
cd frontend && npm run build
cd backend  && uv run uvicorn app.main:app        # finds ../frontend/out on its own
open http://localhost:8000
```

`next dev` is the exception, because it serves on :3000 while the API is on
:8000. Bridge it with `NEXT_PUBLIC_API_BASE`:

```bash
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```

`output: 'export'` ignores `rewrites`, so a dev proxy is not an option — the
base URL is the mechanism. `lib/api.ts` is the only place that reads it.

## The one rule about the price stream

**`usePriceStream` opens an `EventSource` per call, and it is called exactly
once — by `TerminalProvider`.** Components read prices with `useMarket()`:

```tsx
const { prices, sparklines, status, shocks, pricedTickers } = useMarket();
const { portfolio, watchlist, history, refresh, trade, addTicker, removeTicker } = useAccount();
```

A panel that calls `usePriceStream()` for itself gets a second connection to
the same feed, and nothing about the hook's signature stops it. That is the
whole reason the provider exists, and `TerminalProvider.test.tsx` asserts one
connection across two consumers.

Two further things the hook does that are easy to undo by accident:

- **It never constructs a second `EventSource` on error.** The browser
  reconnects on its own, at the cadence the server names in its `retry:` field.
  Opening a fresh one from the error handler is the classic version of this
  bug: the old socket is still retrying, so every blip doubles the number of
  streams the server is feeding.
- **`disconnected` is not the same as `error`.** A dropped connection fires
  `error` and usually recovers a second later. The status goes amber
  immediately and red only after `RECONNECT_GRACE_MS`, so a blip does not look
  like an outage and a dead backend does not stay amber forever.
- **`stalled` is not a connection state at all**, which is why it is a separate
  field. A wedged market source leaves the socket open and healthy and simply
  stops producing, so `EventSource` reports nothing wrong while the grid
  freezes. After `STALL_AFTER_MS` (30 s) without a frame the dot reads amber
  "Stalled" and the feed panel says the values are frozen. A real
  disconnection wins over a stall — red is the more urgent of the two.

  The 30 s threshold must clear the longest legitimate gap between frames: the
  simulator sends one every 500 ms, Massive one per poll. A deployment setting
  `MASSIVE_POLL_INTERVAL` above 30 would make it fire on a healthy feed.

`refresh()` re-reads the portfolio, the watchlist and the snapshot series, and
leaves the stream alone. Reopening the stream would discard every sparkline the
page has accumulated, because there is no history endpoint to rebuild them from.

**The account mutations live on the provider, not in the components that
trigger them.** `trade`, `addTicker` and `removeTicker` each call `refresh()`
on success and reject with the backend's own reason on failure — a failed
mutation refreshes nothing, because nothing changed. What has to happen after a
trade is not local to the trade bar: the header, the positions table, the
heatmap and the P&L chart all have to agree again. Checkpoint 7's chat executes
the same actions through the LLM and needs the same thing to happen, so it adds
`sendChat` here rather than fetching in the panel.

**One loading flag and one error per resource, never merged.** They are read by
different panels, and merging them paints a failed portfolio read across a
watchlist of live streaming prices.

## Layout of `src/`

| Path | What lives there |
|---|---|
| `app/` | `layout.tsx`, `page.tsx`, `globals.css`. The page is a client component; there is no server rendering at request time |
| `state/` | `TerminalProvider` — the single stream, the account, and `refresh()` |
| `hooks/` | `usePriceStream`, `usePriceFlash`, `useApiResource` |
| `components/` | Presentational. They take data as props and hold no fetching of their own |
| `lib/` | `api.ts` (base URL, `getJson`/`sendJson`, `ENDPOINTS`), `types.ts` (the backend's shapes), `format.ts`, `valuation.ts`, `treemap.ts`, `ticker.ts` |

| `test/` | `FakeEventSource`, fixture builders |

The layering runs one way — `lib` ← `hooks` ← `components` ← `state`/`app` — and
`npm run lint` will not catch a violation. No component imports the provider.

Components take props rather than reading context directly, so a test can
render one with fixed data and no provider. `page.tsx` is where context meets
props.

## Nulls are not zeros

`lib/types.ts` mirrors what the backend actually sends, and `null` is
load-bearing in it. A quote with no `previous_close` does not know its daily
change; a position whose ticker has no cached price does not know its value.
`backend/app/api/watchlist.py` says it outright — the frontend renders an em
dash and must not substitute zero.

Every formatter in `lib/format.ts` returns `EM_DASH` for `null`, `undefined`
and any non-finite number. Use them rather than `toFixed` at a call site.
`valuation.ts` follows the same rule for totals: an unpriced position is
excluded from the total **and named**, exactly as `app/portfolio.py` does, so
the header can say so instead of quietly shrinking.

## The flash

`usePriceFlash` returns a direction and a sequence number. The direction picks
`flash-up` / `flash-down`; the sequence is used as the element's React `key`.
Both are needed: without the key, a second move inside the 500 ms window lands
on an element already running the animation and produces no flash at all —
exactly when the market is busiest.

The animation is defined in `globals.css` and disabled under
`prefers-reduced-motion`.

## Styling

Tailwind v4, configured in CSS. The palette is the `@theme` block at the top of
`globals.css`: `--color-panel` generates `bg-panel` / `text-panel` /
`border-panel`. **Add a colour there rather than writing a hex literal in a
component**, so the three brand colours of PLAN.md §2 stay in one place.

There is no webfont. `next/font/google` fetches at build time, which would put
a network dependency inside Checkpoint 8's `docker build`; the system stack
also has no swap flash and already has tabular figures.

## Testing rules for this subsystem

`EventSource` does not exist in jsdom, and **`src/test/FakeEventSource.ts` is a
real object, not a mock** — it counts the listeners actually registered and
`close()` really moves `readyState`. A mocked `addEventListener` accepts every
call, so it cannot tell you the hook forgot to remove its listeners, which is
the bug most worth catching here. The backend suite learned this the expensive
way; see PLAN.md, Checkpoint 1.

Tests import `describe`/`it`/`expect` explicitly — `globals` is off in
`vitest.config.mts`, so there is nothing ambient for `tsc` to have to be told
about. Cleanup is registered in `vitest.setup.ts` for the same reason.

Fixtures come from `src/test/fixtures.ts` and populate **every** field, so a
test cannot pass because it happened to touch only the fields the fixture
bothered to define.

## Charts

`components/LineChart` is the one plot both time series use, and it is SVG by
measurement rather than by preference — see PLAN.md §10. Two things about it
are easy to undo:

- **It needs both `h-full` and `w-full`.** An `svg` is a replaced element with
  an intrinsic aspect ratio taken from its `viewBox`; given only a height, the
  browser computes a *square*. That shipped, and drew the series across 45% of
  a wide panel with the live-end marker stranded to the right of it. jsdom
  performs no layout, so no unit test can see this — the guard in
  `LineChart.test.tsx` asserts the classes, and the real one is Checkpoint 9's
- **The y axis spans the series, never zero.** A portfolio moving between
  10,000 and 10,050 is a flat line on a zero-based axis

The same class of trap applies to the panels: a `section` is a flex *item* of
its column, and a flex item stretches on the cross axis only. Every panel that
should fill its column carries `h-full`, and `page.tsx` owns the ratios.

`lib/treemap.ts` holds the heatmap's geometry as pure arithmetic, deliberately
apart from the component: "do the tile areas match the weights" and "do the
tiles tile the box" are assertable about numbers and not about a DOM tree of
percentage-positioned divs.

## The assistant

`ChatPanel` docks to the right of the grid and collapses to a rail. The first
three grid tracks in `page.tsx` are **identical in both strings**, so collapsing
changes exactly one track: nothing else remounts *or* re-lays-out, and the
accumulated sparklines and the chart's window survive a toggle.

Three things about it are load-bearing:

- **`ChatActions` renders every action, successful or not, in the backend's own
  wording.** The model composes its message before it knows whether anything
  cleared, so a real reply reads "Buying 100000 AAPL" beside an action refused
  for insufficient cash. The sentence stands; the outcome goes directly under
  it. A panel that showed the message alone would be a transcript that lies.
- **A refused *request* and a dropped *connection* are different failures.**
  `POST /api/chat` commits its trades and persists the turn before it responds.
  A 503 or 422 is raised before anything executes, so the text goes back in the
  composer to resend. A transport failure may have run to completion with only
  the reply lost, so the panel refreshes the account, says the outcome is
  unknown, and does **not** hand the text back — handing it back invites the
  user to buy the same thing twice.
- **`refresh()` must never reload the chat history.** `sendChat` appends the
  turn the backend just persisted rather than re-fetching it, so a refresh that
  also re-read the transcript would show every appended turn twice. For the same
  reason nothing may be sent before the history has settled, or a turn could
  appear in a response that arrives after it.

`ChatPanel` is `memo`-wrapped and takes a stable `onToggle`, because its parent
consumes `useMarket()` and therefore re-renders twice a second. Without it the
whole transcript rebuilds on every SSE frame, and the transcript is the part of
the page that grows.

## What is not here yet

Checkpoint 8 packages this into the Docker image; Checkpoint 9 is the
end-to-end suite. Neither adds a component.
