"use client";

import Link from "next/link";

import { Button, Notice } from "@/components/ui";
import { messageOf } from "@/lib/api";

/** A page that failed to render (the task list, or the console around an episode). */
export default function PageError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <main className="mx-auto max-w-xl space-y-3 px-6 py-16">
      <Notice tone="bad" title="This page failed to render">
        {messageOf(error)}
      </Notice>
      <div className="flex items-center gap-3">
        <Button onClick={() => retry()}>Try again</Button>
        <Link href="/" className="text-[13.5px] text-accent underline">
          Back to the task list
        </Link>
      </div>
    </main>
  );
}
