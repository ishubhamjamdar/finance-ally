"use client";

/**
 * The AI copilot — PLAN.md §10: a docked, collapsible sidebar with a scrolling
 * conversation, a loading indicator, and executed trades and watchlist changes
 * shown inline as confirmations.
 *
 * ## The turn
 *
 * `POST /api/chat` returns the complete reply — §9 rules out token streaming,
 * because Cerebras answers in well under a second and a loading indicator is
 * enough. So the panel has exactly three states per turn: the message goes up
 * optimistically, an indicator sits under it, and the reply replaces the
 * indicator.
 *
 * The optimistic message is not a guess about what the server will store. It
 * is the same text, and `TerminalProvider.sendChat` appends the authoritative
 * pair from the reply when it lands; this component drops its copy in the same
 * commit. What it buys is that the message the user just typed is never on
 * screen nowhere.
 *
 * ## The failure it must not hide
 *
 * A turn can fail in two quite different ways and they read differently:
 *
 * - **The server refused it** — 503 no feed or no provider, 422 a message the
 *   schema refused. Both are raised before `handle_message` executes anything,
 *   so nothing was said and nothing was stored: the composer keeps the text
 *   and this is worth resending exactly as it was.
 * - **The connection dropped.** This is *not* the same thing, and treating it
 *   as one is how a user is invited to buy twice. `POST /api/chat` commits its
 *   trades and persists the turn **before** it responds, so a reply lost in
 *   transit leaves real fills on the ledger. The panel refreshes the account,
 *   says the outcome is unknown, and does **not** hand the text back — because
 *   handing it back is an invitation to execute it again.
 * - **The model answered badly, or an action it asked for was refused.** That
 *   is a 200. It belongs *in* the transcript, because from the conversation's
 *   point of view it happened — see `ChatActions`.
 */

import { memo, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";

import { ChatActions } from "@/components/ChatActions";
import { ApiError, describeError } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

/** Matches `MAX_CHAT_MESSAGE_CHARS` in `backend/app/api/schemas.py`. */
export const MAX_MESSAGE_CHARS = 2000;

/** What the panel says when it cannot know whether the turn executed. */
export const UNKNOWN_OUTCOME =
  "The connection dropped before the reply arrived. The turn may have executed — " +
  "check the panels, or reload to see the transcript, before sending it again.";

interface ChatPanelProps {
  messages: ChatMessage[];
  /** Resolves when the turn is done; rejects with the backend's reason. */
  onSend: (message: string) => Promise<unknown>;
  /**
   * Re-read the account. Called when a turn's outcome is *unknown*, since a
   * reply lost in transit may still have moved money.
   */
  onRefresh?: () => void;
  collapsed?: boolean;
  onToggle?: () => void;
  loading?: boolean;
  /** A failed *history* read. The transcript may be incomplete, not wrong. */
  error?: string | null;
}

function ChatPanelImpl({
  messages,
  onSend,
  onRefresh,
  collapsed = false,
  onToggle,
  loading = false,
  error = null,
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const transcript = useRef<HTMLDivElement>(null);

  // Follow the conversation down as it grows. `pending` is in the dependencies
  // as well as the message count, so the indicator scrolls into view too.
  useEffect(() => {
    const element = transcript.current;
    if (element !== null) element.scrollTop = element.scrollHeight;
  }, [messages.length, pending]);

  async function send(event: FormEvent) {
    event.preventDefault();
    // Not while a turn is in flight, and not before the stored transcript has
    // landed: a turn sent first could be *included* in a history response that
    // arrives afterwards, and would then render twice — once from the server,
    // once from this session — with different ids, so nothing would dedupe it.
    if (pending !== null || loading) return;

    const text = draft.trim();
    if (text === "") return;

    setPending(text);
    setSendError(null);
    setDraft("");
    try {
      await onSend(text);
    } catch (cause: unknown) {
      // An `ApiError` means the server answered: it refused the request before
      // executing anything, so the text is still the user's and resending it
      // is safe. Anything else is a transport failure, where the turn may have
      // run to completion and only the reply was lost.
      if (cause instanceof ApiError) {
        setSendError(describeError(cause));
        // Only when the box is still empty — a follow-up typed while waiting
        // is the user's newer intent, and must not be overwritten.
        setDraft((current) => (current === "" ? text : current));
      } else {
        setSendError(UNKNOWN_OUTCOME);
        onRefresh?.();
      }
    } finally {
      setPending(null);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter breaks the line — what every chat box does, and
    // a textarea does the opposite by default.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(event as unknown as FormEvent);
    }
  }

  if (collapsed) {
    return (
      <aside
        className="flex h-full flex-col items-center gap-3 rounded border border-edge bg-panel py-3"
        data-testid="chat-panel-collapsed"
      >
        {onToggle !== undefined && (
          <button
            type="button"
            onClick={onToggle}
            aria-label="Expand the assistant"
            aria-expanded={false}
            className="cursor-pointer rounded px-1.5 py-1 text-sm text-muted hover:bg-raised hover:text-ink"
          >
            ‹
          </button>
        )}
        <span
          className="text-[10px] font-semibold tracking-[0.18em] text-muted uppercase"
          // Reads bottom-to-top up the rail, so the collapsed panel is still
          // labelled rather than being an anonymous strip.
          style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}
        >
          Assistant
        </span>
      </aside>
    );
  }

  return (
    <aside
      className="flex h-full min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="chat-heading"
      data-testid="chat-panel"
    >
      <header className="flex items-center justify-between border-b border-edge px-3 py-2">
        <h2
          id="chat-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Assistant
        </h2>
        {onToggle !== undefined && (
          <button
            type="button"
            onClick={onToggle}
            aria-label="Collapse the assistant"
            aria-expanded={true}
            className="cursor-pointer rounded px-1.5 text-sm text-faint hover:bg-raised hover:text-ink"
          >
            ›
          </button>
        )}
      </header>

      {error !== null && (
        <p className="border-b border-edge px-3 py-1.5 text-xs text-down" role="alert">
          {error} — earlier messages may be missing.
        </p>
      )}

      <div
        ref={transcript}
        className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 py-3"
        // `log` rather than `alert`: replies are announced as they arrive
        // without interrupting whatever the user is doing.
        role="log"
        aria-live="polite"
        aria-label="Conversation"
        data-testid="chat-transcript"
      >
        {messages.length === 0 && pending === null && (
          <p className="py-6 text-center text-xs text-faint">
            {loading
              ? "Loading the conversation…"
              : "Ask about your portfolio, or say what to trade. Trades execute immediately."}
          </p>
        )}

        {messages.map((message) => (
          <Turn key={message.id} message={message} />
        ))}

        {pending !== null && (
          <>
            <Bubble role="user">{pending}</Bubble>
            <p
              className="flex items-center gap-2 text-xs text-faint"
              data-testid="chat-loading"
              role="status"
            >
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" aria-hidden />
              Thinking…
            </p>
          </>
        )}
      </div>

      <form className="border-t border-edge p-2" onSubmit={send}>
        {sendError !== null && (
          <p className="pb-2 text-xs text-down" role="alert" data-testid="chat-error">
            {sendError}
          </p>
        )}
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={MAX_MESSAGE_CHARS}
            placeholder="Buy 10 NVDA, or ask what to trim…"
            aria-label="Message the assistant"
            className="min-h-0 flex-1 resize-none rounded border border-edge bg-raised px-2 py-1.5 text-sm text-ink placeholder:text-faint focus:border-brand focus:outline-none"
          />
          <button
            type="submit"
            disabled={pending !== null || loading || draft.trim() === ""}
            className="rounded bg-submit px-3 py-1.5 text-xs font-semibold tracking-wide text-ink uppercase hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </form>
    </aside>
  );
}

/**
 * Memoised, because its parent is not and cannot be: `Workstation` consumes
 * `useMarket()`, whose value changes on every SSE frame. Without this the whole
 * transcript — every turn, every action card — rebuilds twice a second for the
 * life of the session, and it is the part of the page that grows.
 */
export const ChatPanel = memo(ChatPanelImpl);

function Turn({ message }: { message: ChatMessage }) {
  return (
    <div data-testid={`chat-turn-${message.role}`}>
      <Bubble role={message.role}>{message.content}</Bubble>
      {/* Always beneath the message, never instead of it, and never filtered. */}
      {message.actions !== null && message.actions.length > 0 && (
        <ChatActions actions={message.actions} />
      )}
    </div>
  );
}

function Bubble({ role, children }: { role: ChatMessage["role"]; children: string }) {
  const user = role === "user";
  return (
    <p
      className={`rounded px-2.5 py-1.5 text-sm whitespace-pre-wrap ${
        user ? "ml-6 bg-raised text-ink" : "mr-6 bg-terminal text-ink"
      }`}
    >
      <span className="sr-only">{user ? "You said: " : "Assistant said: "}</span>
      {children}
    </p>
  );
}
