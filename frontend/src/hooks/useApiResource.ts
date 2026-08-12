"use client";

/**
 * A read-only JSON endpoint, fetched once on mount.
 *
 * Deliberately not a polling hook. Everything that changes twice a second
 * arrives on the SSE stream; the REST endpoints hold the things that change
 * when the *user* does something — the watchlist, the cash balance — and those
 * are re-read with `reload()` at that moment. Polling them on a timer would
 * put a database round trip on a clock for data that is usually identical.
 *
 * `loading` is **derived**, not stored: it is true whenever the result on hand
 * belongs to an older request than the one now in flight. That makes it right
 * for a `reload()` and for a changed `path` without either having to remember
 * to flip a flag, and it keeps the effect free of the synchronous `setState`
 * that turns one render into two.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, getJson } from "@/lib/api";

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

export function useApiResource<T>(path: string): ApiResource<T> {
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
        setResult((previous) => ({ key, data: previous.data, error: describe(cause) }));
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [path, key]);

  const reload = useCallback(() => setAttempt((current) => current + 1), []);

  return { data: result.data, error: result.error, loading: result.key !== key, reload };
}

function isAbort(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === "AbortError";
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  // `fetch` rejects with a bare TypeError when the server is not there at all,
  // and "Failed to fetch" tells a user nothing they can act on.
  if (cause instanceof TypeError) return "Cannot reach the server";
  return cause instanceof Error ? cause.message : String(cause);
}
