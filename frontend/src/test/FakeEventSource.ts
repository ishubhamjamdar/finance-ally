/**
 * A test double for `EventSource`, which jsdom does not implement.
 *
 * It is deliberately a real object with real listener bookkeeping rather than
 * a mock that fabricates whatever is asked of it. The backend's own test suite
 * learned this the expensive way — thirteen `MagicMock`-based tests passed
 * against a client that could never have worked (PLAN.md, Checkpoint 1) — and
 * the same trap is open here: a mocked `addEventListener` cannot tell you that
 * the hook forgot to remove its listeners, because a mock accepts every call.
 *
 * What this class refuses to fake is the part under test: `listenerCount` is
 * counted from the listeners actually registered, `close()` really moves
 * `readyState` to CLOSED, and every instance ever constructed stays in
 * `instances` so a test can prove a second connection was never opened.
 */

type Listener = (event: Event) => void;

export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  /** Every instance constructed since the last `reset()`, in order. */
  static instances: FakeEventSource[] = [];

  readonly CONNECTING = FakeEventSource.CONNECTING;
  readonly OPEN = FakeEventSource.OPEN;
  readonly CLOSED = FakeEventSource.CLOSED;

  readyState: number = FakeEventSource.CONNECTING;
  closeCalls = 0;

  private readonly listeners = new Map<string, Listener[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  /** Install as the global `EventSource` and forget earlier instances. */
  static install(): void {
    FakeEventSource.reset();
    (globalThis as { EventSource?: unknown }).EventSource = FakeEventSource;
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  /** The only instance, asserting there is exactly one. */
  static get only(): FakeEventSource {
    if (FakeEventSource.instances.length !== 1) {
      throw new Error(`expected exactly one EventSource, got ${FakeEventSource.instances.length}`);
    }
    return FakeEventSource.instances[0];
  }

  addEventListener(type: string, listener: Listener): void {
    const existing = this.listeners.get(type) ?? [];
    this.listeners.set(type, [...existing, listener]);
  }

  removeEventListener(type: string, listener: Listener): void {
    const existing = this.listeners.get(type) ?? [];
    this.listeners.set(
      type,
      existing.filter((candidate) => candidate !== listener),
    );
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = FakeEventSource.CLOSED;
  }

  /** How many listeners are registered, in total or for one event type. */
  listenerCount(type?: string): number {
    if (type !== undefined) return (this.listeners.get(type) ?? []).length;
    let total = 0;
    for (const listeners of this.listeners.values()) total += listeners.length;
    return total;
  }

  // --- driving the stream from a test ---------------------------------

  /** The connection opens. */
  emitOpen(): void {
    this.readyState = FakeEventSource.OPEN;
    this.dispatch("open", new Event("open"));
  }

  /** A default `data:` frame. */
  emitMessage(data: unknown): void {
    this.dispatch(
      "message",
      new MessageEvent("message", { data: typeof data === "string" ? data : JSON.stringify(data) }),
    );
  }

  /** A named frame — `shock` or `status`. */
  emitNamed(type: string, data: unknown): void {
    this.dispatch(
      type,
      new MessageEvent(type, { data: typeof data === "string" ? data : JSON.stringify(data) }),
    );
  }

  /**
   * The connection drops.
   *
   * `fatal` is the difference the hook has to act on: a real `EventSource`
   * leaves `readyState` at CONNECTING while it retries on its own, and only
   * moves to CLOSED when it has given up for good.
   */
  emitError({ fatal = false }: { fatal?: boolean } = {}): void {
    this.readyState = fatal ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.dispatch("error", new Event("error"));
  }

  private dispatch(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}
