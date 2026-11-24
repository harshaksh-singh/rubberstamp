import type { Metadata } from "next";

import { OverviewView } from "@/components/views/overview";

export const instant = false; // rendered in the browser from rs-api: see ./layout.tsx

export const metadata: Metadata = { title: "Overview" };

export default function Page() {
  return <OverviewView />;
}
