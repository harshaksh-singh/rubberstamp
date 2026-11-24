"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { useEpisode } from "./episode";
import type { JsonObject } from "./types";

export { SLOTS } from "./slots";

/**
 * Open a record in its view: make the read call (recorded like any other), keep its result for the view, navigate.
 * Used by links between records (a ticket's customer, a customer's orders).
 */
export function useOpen() {
  const router = useRouter();
  const { id, run, setSlot } = useEpisode();
  return useCallback(
    async (view: "tickets" | "customers" | "orders", slot: string, tool: string, args: JsonObject) => {
      const call = await run(tool, args);
      setSlot(slot, call);
      router.push(`/episodes/${id}/${view}`);
      return call;
    },
    [id, run, setSlot, router],
  );
}
