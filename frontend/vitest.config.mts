import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // `@/*` comes from tsconfig.json; Vite resolves it natively.
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // No `globals: true`. Tests import `describe`/`it`/`expect` explicitly, so
    // there is nothing ambient to teach `tsc --noEmit` about and no divergence
    // between what the editor sees and what the runner provides.
    globals: false,
    restoreMocks: true,
  },
});
