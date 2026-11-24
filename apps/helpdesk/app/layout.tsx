import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";

import { THEME_SCRIPT } from "@/lib/theme-script";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Helpdesk console", template: "%s · Helpdesk console" },
  description:
    "A helpdesk console over the RubberStamp helpdesk world. Every read and write is a tool call through rs-api, " +
    "and each submitted episode is scored by the strict grader and every flawed one.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0e0e11" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        {/* Sets the theme class before the first paint, so a dark page never flashes light. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh bg-bg font-sans text-fg antialiased">{children}</body>
    </html>
  );
}
