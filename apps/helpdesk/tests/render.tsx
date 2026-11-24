import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EpisodeProvider, useEpisode } from "@/lib/episode";

import { EPISODE_ID, FakeApi } from "./fake-api";
import { router } from "./router";

const LOADING = "Loading the episode";

function Loaded({ children }: { children: React.ReactNode }) {
  const { phase, error } = useEpisode();
  if (phase === "loading") return <p>{LOADING}</p>;
  if (phase !== "ready") return <p>{`Episode ${phase}: ${error}`}</p>;
  return <>{children}</>;
}

/** Render `ui` inside an episode served by `fake` (installed as `fetch`), once the episode has loaded. */
export async function renderEpisode(ui: React.ReactNode, fake: FakeApi = new FakeApi()) {
  fake.install();
  router.episodeId = EPISODE_ID;
  const user = userEvent.setup();
  const view = render(
    <EpisodeProvider id={EPISODE_ID}>
      <Loaded>{ui}</Loaded>
    </EpisodeProvider>,
  );
  await waitFor(() => {
    if (screen.queryByText(LOADING)) throw new Error("the episode is still loading");
  });
  return { ...view, user, fake };
}
