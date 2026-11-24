"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { THEME_KEY as KEY } from "@/lib/theme-script";

type Choice = "system" | "light" | "dark";

const ORDER: Choice[] = ["system", "light", "dark"];

function read(): Choice {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

function apply(choice: Choice) {
  const dark = choice === "dark" || (choice === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

const subscribeNothing = () => () => {};

/** Light, dark, or the system's: a three-way toggle, saved in this browser only. */
export function ThemeToggle() {
  const mounted = useSyncExternalStore(
    subscribeNothing,
    () => true,
    () => false,
  );
  const [choice, setChoice] = useState<Choice>(() => (typeof window === "undefined" ? "system" : read()));

  useEffect(() => {
    if (choice !== "system") return;
    const media = matchMedia("(prefers-color-scheme: dark)");
    const follow = () => apply("system");
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [choice]);

  const cycle = useCallback(() => {
    const next = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length] ?? "system";
    setChoice(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // storage may be unavailable (private mode); the choice still applies to this page
    }
    apply(next);
  }, [choice]);

  const shown = mounted ? choice : "system";
  const label = { system: "System theme", light: "Light theme", dark: "Dark theme" }[shown];
  return (
    <button
      type="button"
      onClick={cycle}
      className="inline-flex h-8 items-center gap-2 rounded-md px-2 text-[13px] text-fg-muted hover:bg-muted hover:text-fg"
      aria-label={`${label}. Change theme`}
      title="Change theme (system, light, dark)"
    >
      <ThemeIcon choice={shown} />
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

function ThemeIcon({ choice }: { choice: Choice }) {
  const common = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2 };
  if (choice === "light") {
    return (
      <svg {...common} aria-hidden>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  if (choice === "dark") {
    return (
      <svg {...common} aria-hidden>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    );
  }
  return (
    <svg {...common} aria-hidden>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  );
}
