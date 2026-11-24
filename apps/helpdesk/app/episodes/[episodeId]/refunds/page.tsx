import type { Metadata } from "next";

import { RefundsView } from "@/components/views/ledgers";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Refunds" };

export default function Page() {
  return <RefundsView />;
}
