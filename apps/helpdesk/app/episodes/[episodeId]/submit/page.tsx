import type { Metadata } from "next";

import { SubmitView } from "@/components/views/submit";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Submit resolution" };

export default function Page() {
  return <SubmitView />;
}
