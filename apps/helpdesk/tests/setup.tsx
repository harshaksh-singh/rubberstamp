/**
 * Runs before every test file. Next's router and <Link> need the App Router mounted, so they are replaced by a
 * recording router (tests/router.ts) and a plain <a>.
 */
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { router } from "./router";

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useParams: () => ({ episodeId: router.episodeId }),
  usePathname: () => router.pathname,
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// jsdom has no matchMedia; the theme toggle asks for prefers-color-scheme.
window.matchMedia ??= (query: string) =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList;

afterEach(() => {
  cleanup();
  router.reset();
});
