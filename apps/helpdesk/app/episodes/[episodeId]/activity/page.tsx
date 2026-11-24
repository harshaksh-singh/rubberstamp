import type { Metadata } from "next";

import { ActivityView } from "@/components/views/activity";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Activity" };

export default function Page() {
  return <ActivityView />;
}
