import { defineConfig } from "vitest/config";

/**
 * Unit and component tests (`npm test`): jsdom, React Testing Library, and a fake rs-api behind `fetch`
 * (tests/fake-api.ts), so each test checks the HTTP request the console sends and what it shows for the answer.
 */
export default defineConfig({
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/setup.tsx"],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
