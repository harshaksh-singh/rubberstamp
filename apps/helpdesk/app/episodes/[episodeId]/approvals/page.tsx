import type { Metadata } from "next";

import { ApprovalsView } from "@/components/views/ledgers";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Approvals" };

export default function Page() {
  return <ApprovalsView />;
}
