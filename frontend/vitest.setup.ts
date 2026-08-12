import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only auto-cleans when the globals are injected, and this
// project runs without them. An uncleaned mount leaves its `EventSource` and
// its timers alive into the next test, which is precisely the class of bug
// these tests exist to catch — it must not be one they cause.
afterEach(() => {
  cleanup();
});
