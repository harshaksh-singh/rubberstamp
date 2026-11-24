import type { Metadata } from "next";

import { PoliciesView } from "@/components/views/policies";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Policies" };

export default function Page() {
  return <PoliciesView />;
}
