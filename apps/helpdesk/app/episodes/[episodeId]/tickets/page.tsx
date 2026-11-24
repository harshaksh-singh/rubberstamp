import type { Metadata } from "next";

import { TicketsView } from "@/components/views/tickets";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Tickets" };

export default function Page() {
  return <TicketsView />;
}
