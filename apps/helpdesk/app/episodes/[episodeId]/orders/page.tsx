import type { Metadata } from "next";

import { OrdersView } from "@/components/views/orders";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Orders" };

export default function Page() {
  return <OrdersView />;
}
