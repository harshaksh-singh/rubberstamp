"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { messageOf } from "./api";
import { useEpisode } from "./episode";
import type { JsonObject } from "./types";

export { SLOTS } from "./slots";

/**
 * Open a record in its view: make the read call (recorded like any other), keep its result for the view, navigate.
 * Used by links between records (a ticket's customer, a customer's orders). When the API does not take the call,
 * `failure` says why and the page stays where it is. `disabled` is true once the episode ended (a call would get 409).
 */
export function useOpen() {
  const router = useRouter();
  const { id, run, setSlot, submitted } = useEpisode();
  const [failure, setFailure] = useState<string | null>(null);
  const open = useCallback(
    async (view: "tickets" | "customers" | "orders", slot: string, tool: string, args: JsonObject) => {
      setFailure(null);
      try {
        const call = await run(tool, args);
        setSlot(slot, call);
        router.push(`/episodes/${id}/${view}`);
        return call;
      } catch (exc) {
        setFailure(messageOf(exc));
        return null;
      }
    },
    [id, run, setSlot, router],
  );
  return { open, failure, disabled: submitted };
}
