import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "@/components/ChatPanel";
import { ApiError } from "@/lib/api";
import type { ChatAction, ChatMessage } from "@/lib/types";

function turn(
  id: string,
  role: ChatMessage["role"],
  content: string,
  actions: ChatAction[] | null = null,
): ChatMessage {
  return { id, role, content, actions, created_at: "2026-08-14T03:22:09+00:00" };
}

const REFUSED: ChatAction = {
  kind: "trade",
  ok: false,
  summary: "buy 100000 AAPL",
  detail: "Insufficient cash: only $8,740.27 is available.",
  ticker: "AAPL",
  action: "buy",
  result: null,
};

/** A send that never settles, so the in-flight state can be observed. */
function neverSettles() {
  let release: (() => void) | null = null;
  const onSend = vi.fn(
    () =>
      new Promise<unknown>((resolve) => {
        release = () => resolve(undefined);
      }),
  );
  return { onSend, release: () => release?.() };
}

function type(text: string) {
  fireEvent.change(screen.getByLabelText("Message the assistant"), { target: { value: text } });
}

describe("ChatPanel", () => {
  it("invites a first message instead of showing an empty box", () => {
    render(<ChatPanel messages={[]} onSend={vi.fn()} />);

    expect(screen.getByTestId("chat-transcript")).toHaveTextContent(/Ask about your portfolio/);
  });

  it("renders the stored conversation, oldest first", () => {
    render(
      <ChatPanel
        messages={[turn("1", "user", "what should I trim?"), turn("2", "assistant", "NVDA is 40%.")]}
        onSend={vi.fn()}
      />,
    );

    expect(screen.getByTestId("chat-turn-user")).toHaveTextContent("what should I trim?");
    expect(screen.getByTestId("chat-turn-assistant")).toHaveTextContent("NVDA is 40%.");
  });

  it("shows a loading indicator while the turn is in flight, then the reply", async () => {
    const { onSend, release } = neverSettles();
    const { rerender } = render(<ChatPanel messages={[]} onSend={onSend} />);

    type("buy 3 MSFT");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // The message goes up at once, with the indicator under it.
    expect(screen.getByTestId("chat-transcript")).toHaveTextContent("buy 3 MSFT");
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();

    release();
    // The provider appends the authoritative pair; this component drops its copy.
    rerender(
      <ChatPanel
        messages={[turn("1", "user", "buy 3 MSFT"), turn("2", "assistant", "Bought.")]}
        onSend={onSend}
      />,
    );

    await waitFor(() => expect(screen.queryByTestId("chat-loading")).toBeNull());
    expect(screen.getByTestId("chat-turn-assistant")).toHaveTextContent("Bought.");
  });

  it("does not leave the typed message on screen nowhere", () => {
    const { onSend } = neverSettles();
    render(<ChatPanel messages={[]} onSend={onSend} />);

    type("what should I trim?");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // Out of the composer and into the transcript in the same commit.
    expect(screen.getByLabelText("Message the assistant")).toHaveValue("");
    expect(screen.getByTestId("chat-transcript")).toHaveTextContent("what should I trim?");
  });

  it("sends the trimmed message", () => {
    const onSend = vi.fn(async () => undefined);
    render(<ChatPanel messages={[]} onSend={onSend} />);

    type("   buy 3 MSFT   ");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("buy 3 MSFT");
  });

  it("will not send an empty or whitespace-only message", () => {
    const onSend = vi.fn(async () => undefined);
    render(<ChatPanel messages={[]} onSend={onSend} />);

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).not.toHaveBeenCalled();

    type("    ");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("will not send a second turn while one is in flight", () => {
    const { onSend } = neverSettles();
    const { container } = render(<ChatPanel messages={[]} onSend={onSend} />);
    const form = container.querySelector("form");
    if (form === null) throw new Error("the panel has no form");

    type("first");
    fireEvent.submit(form);
    expect(onSend).toHaveBeenCalledTimes(1);

    // Submitting the form directly is the path the disabled button does not
    // cover — and a textarea that sends on Enter makes it reachable.
    type("second");
    fireEvent.submit(form);
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("sends on Enter and breaks the line on Shift+Enter", () => {
    const onSend = vi.fn(async () => undefined);
    render(<ChatPanel messages={[]} onSend={onSend} />);
    const box = screen.getByLabelText("Message the assistant");

    type("buy 3 MSFT");
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("buy 3 MSFT");
  });

  it("gives the message back when the request failed", async () => {
    // 503 no feed or no provider: nothing was said and nothing was stored, so
    // the text is still the user's and resending it is the right thing to do.
    render(
      <ChatPanel
        messages={[]}
        onSend={async () => {
          throw new ApiError("No market data source is running", 503);
        }}
      />,
    );

    type("buy 3 MSFT");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByTestId("chat-error")).toHaveTextContent(
      "No market data source is running",
    );
    expect(screen.getByLabelText("Message the assistant")).toHaveValue("buy 3 MSFT");
    // And the optimistic copy is gone, so it does not sit there looking sent.
    expect(screen.queryByTestId("chat-loading")).toBeNull();
  });

  it("reports an unreachable server in words", async () => {
    render(
      <ChatPanel
        messages={[]}
        onSend={async () => {
          throw new TypeError("Failed to fetch");
        }}
      />,
    );

    type("hello");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByTestId("chat-error")).toHaveTextContent("Cannot reach the server");
  });

  it("clears the last send error when the next turn succeeds", async () => {
    const onSend = vi
      .fn<(message: string) => Promise<unknown>>()
      .mockRejectedValueOnce(new ApiError("No provider", 503))
      .mockResolvedValueOnce(undefined);
    render(<ChatPanel messages={[]} onSend={onSend} />);

    type("hello");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByTestId("chat-error");

    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.queryByTestId("chat-error")).toBeNull());
  });

  it("renders a refused action under the message that claimed it", () => {
    // This is the checkpoint's review focus, at the level the panel owns it:
    // the model's sentence stands, and the truth sits directly underneath.
    render(
      <ChatPanel
        messages={[turn("1", "assistant", "Buying 100000 AAPL.", [REFUSED])]}
        onSend={vi.fn()}
      />,
    );

    const assistant = screen.getByTestId("chat-turn-assistant");
    expect(assistant).toHaveTextContent("Buying 100000 AAPL.");
    expect(assistant).toHaveTextContent("Insufficient cash");
    expect(screen.getByTestId("chat-action-failed")).toBeInTheDocument();
  });

  it("says the transcript may be incomplete when the history read failed", () => {
    render(<ChatPanel messages={[]} onSend={vi.fn()} error="Cannot reach the server" />);

    expect(screen.getByRole("alert")).toHaveTextContent("earlier messages may be missing");
  });

  describe("collapsing", () => {
    it("collapses to a labelled rail with a way back", () => {
      const onToggle = vi.fn();
      render(<ChatPanel messages={[]} onSend={vi.fn()} collapsed onToggle={onToggle} />);

      expect(screen.getByTestId("chat-panel-collapsed")).toHaveTextContent("Assistant");
      expect(screen.queryByTestId("chat-transcript")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Expand the assistant" }));
      expect(onToggle).toHaveBeenCalledTimes(1);
    });

    it("expands again", () => {
      const onToggle = vi.fn();
      render(<ChatPanel messages={[]} onSend={vi.fn()} onToggle={onToggle} />);

      fireEvent.click(screen.getByRole("button", { name: "Collapse the assistant" }));
      expect(onToggle).toHaveBeenCalledTimes(1);
    });

    it("reports its state to a screen reader", () => {
      const { rerender } = render(<ChatPanel messages={[]} onSend={vi.fn()} onToggle={vi.fn()} />);
      expect(screen.getByRole("button", { name: "Collapse the assistant" })).toHaveAttribute(
        "aria-expanded",
        "true",
      );

      rerender(<ChatPanel messages={[]} onSend={vi.fn()} collapsed onToggle={vi.fn()} />);
      expect(screen.getByRole("button", { name: "Expand the assistant" })).toHaveAttribute(
        "aria-expanded",
        "false",
      );
    });

    it("offers no toggle at all when there is nothing to toggle", () => {
      render(<ChatPanel messages={[]} onSend={vi.fn()} />);

      expect(screen.queryByRole("button", { name: /the assistant/ })).toBeNull();
    });
  });
});
