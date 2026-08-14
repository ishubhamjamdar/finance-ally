"use client";

/**
 * What the assistant actually did, rendered beside what it said.
 *
 * ## This is the honest half of the transcript
 *
 * The model composes its message *before* it knows whether anything cleared.
 * A real reply from this system reads:
 *
 *     message: "Buying 100000 AAPL."
 *     actions: [{ ok: false, detail: "Insufficient cash: … only $8,740.27 …" }]
 *
 * So the prose claims a fill that never happened. Nothing here can rewrite the
 * model's sentence, and nothing should try — but the outcome goes directly
 * underneath it, in red, saying what the account really did. A panel that
 * showed the message alone would be a transcript that lies, and the user's
 * next question would be built on it.
 *
 * Three rules follow, and each is tested:
 *
 * 1. **Every action is rendered, successful or not.** Never a filtered list,
 *    never a count that hides the failures inside it.
 * 2. **The wording is the backend's.** `summary` and `detail` are composed by
 *    `app/chat.py`, which is the only thing that knows what the fill price
 *    was. Re-deriving them here would be a second implementation of the trade,
 *    drifting from the first.
 * 3. **A partial result says so.** Three trades of which one failed is the
 *    case most easily misread as success, so a mixed reply gets an explicit
 *    count. A uniform one does not — "3 of 3 executed" is noise.
 */

import type { ChatAction } from "@/lib/types";

export function ChatActions({ actions }: { actions: ChatAction[] }) {
  if (actions.length === 0) return null;

  const failed = actions.filter((action) => !action.ok).length;
  const mixed = failed > 0 && failed < actions.length;

  return (
    <div className="mt-1.5 flex flex-col gap-1" data-testid="chat-actions">
      {mixed && (
        <p className="font-mono text-[10px] tracking-[0.08em] text-accent uppercase">
          {actions.length - failed} of {actions.length} executed
        </p>
      )}

      {actions.map((action, index) => (
        <div
          // Nothing in an action is unique — the same reply may hold two
          // identical `buy 1 AAPL` items, and both must be drawn. The index is
          // the identity here because the list is rendered whole and never
          // reordered or filtered.
          key={index}
          className={`rounded border-l-2 py-1 pr-2 pl-2 text-[11px] ${
            action.ok ? "border-up bg-up/5" : "border-down bg-down/5"
          }`}
          data-testid={`chat-action-${action.ok ? "ok" : "failed"}`}
        >
          <p className="flex items-baseline gap-1.5">
            <span aria-hidden className={action.ok ? "text-up" : "text-down"}>
              {action.ok ? "✓" : "✕"}
            </span>
            <span className="font-mono font-semibold text-ink uppercase">{action.summary}</span>
            <span className="ml-auto shrink-0 text-[10px] tracking-[0.1em] text-faint uppercase">
              {action.kind}
            </span>
          </p>
          {/* The screen-reader name for the state, since the glyph is decorative
              and the colour is not available to everyone. */}
          <p className={action.ok ? "text-muted" : "text-down"}>
            <span className="sr-only">{action.ok ? "Executed: " : "Refused: "}</span>
            {action.detail}
          </p>
        </div>
      ))}
    </div>
  );
}
