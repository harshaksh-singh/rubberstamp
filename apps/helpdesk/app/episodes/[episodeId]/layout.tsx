import { Suspense } from "react";

import { ConsoleShell } from "@/components/console-shell";
import { Spinner } from "@/components/ui";

/**
 * Navigating into an episode waits for its id (a request-time param) and for rs-api, which the browser asks for the
 * episode: the console is client-rendered by design, so it opts out of instant-navigation validation. Moving between
 * views of the same episode re-renders nothing on the server.
 */
export const instant = false;

/** The console for one episode. The episode id is read on the client (useParams), so the shell streams in. */
export default function EpisodeLayout({ children }: LayoutProps<"/episodes/[episodeId]">) {
  return (
    <Suspense
      fallback={
        <div className="p-6">
          <Spinner label="Loading the console" />
        </div>
      }
    >
      <ConsoleShell>{children}</ConsoleShell>
    </Suspense>
  );
}
