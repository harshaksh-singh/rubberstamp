"use client";

import { Button, Notice } from "@/components/ui";
import { messageOf } from "@/lib/api";

/** A view that failed to render. The console around it (navigation, activity) stays, and so does the episode. */
export default function EpisodeViewError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <div className="max-w-xl space-y-3">
      <Notice tone="bad" title="This view failed to render">
        {messageOf(error)}
      </Notice>
      <Button onClick={() => retry()}>Try again</Button>
    </div>
  );
}
