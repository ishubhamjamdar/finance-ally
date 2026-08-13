"use client";

/**
 * A read-only JSON endpoint, fetched once on mount.
 *
 * Not a polling hook by default. Everything that changes twice a second
 * arrives on the SSE stream; the REST endpoints hold the things that change
 * when the *user* does something — the watchlist, the cash balance — and those
 * are re-read with `reload()` at that moment. Polling them on a timer would
 * put a database round trip on a clock for data that is usually identical.
 *
 * `refreshMs` is the one exception, and it exists for one endpoint:
 * `/api/portfolio/history` grows on the backend's own 30-second snapshot task,
 * with no user action and no stream frame to announce it. Nothing else here
 * should use it — a trade already calls `reload()` through `refresh()`, which
 * is why the user's own action appears at once rather than on the next tick.
 *
 * `loading` is **derived**, not stored: it is true whenever the result on hand
 * belongs to an older request than the one now in flight. That makes it right
 * for a `reload()` and for a changed `path` without either having to remember
 * to flip a flag, and it keeps the effect free of the synchronous `setState`
 * that turns one render into two.
 */

import { useCallback, useEffect, useState } from "react";

import { describeError, getJson } from "@/lib/api";

export interface ApiResource<T> {
  /** The last successful body. Kept during a reload, so a refresh does not blank the panel. */
  data: T | null;
  /** The backend's own `detail` where it sent one, so the reason survives. */
  error: string | null;
  loading: boolean;
  reload: () => void;
}

interface Result<T> {
  /** Which request produced this — `""` before the first one settles. */
  key: string;
  data: T | null;
  error: string | null;
}

export function useApiResource<T>(path: string, refreshMs?: number): ApiResource<T> {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result<T>>({ key: "", data: null, error: null });

  const key = `${path}#${attempt}`;

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    getJson<T>(path, { signal: controller.signal })
      .then((value) => {
        if (cancelled) return;
        setResult({ key, data: value, error: null });
      })
      .catch((cause: unknown) => {
        // An abort is this hook cancelling itself on unmount, not a failure.
        if (cancelled || isAbort(cause)) return;
        setResult((previous) => ({ key, data: previous.data, error: describeError(cause) }));
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [path, key]);

  const reload = useCallback(() => setAttempt((current) => current + 1), []);

  // A separate effect from the fetch, deliberately: putting the interval in
  // there would restart the clock on every reload, so a series refreshed by a
  // trade would then wait a full period again — and an interval that outlives
  // its mount is the leak this checkpoint's review is looking for.
  useEffect(() => {
    if (refreshMs === undefined) return;
    const timer = setInterval(reload, refreshMs);
    return () => clearInterval(timer);
  }, [refreshMs, reload]);

  return { data: result.data, error: result.error, loading: result.key !== key, reload };
}

function isAbort(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === "AbortError";
}
