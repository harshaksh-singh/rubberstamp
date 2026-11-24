import type { Metadata } from "next";

import { ToolsView } from "@/components/views/tools";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "All tools" };

export default function Page() {
  return <ToolsView />;
}
