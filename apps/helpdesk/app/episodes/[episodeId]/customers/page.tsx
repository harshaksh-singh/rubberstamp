import type { Metadata } from "next";

import { CustomersView } from "@/components/views/customers";

export const instant = false; // rendered in the browser from rs-api: see ../layout.tsx

export const metadata: Metadata = { title: "Customers" };

export default function Page() {
  return <CustomersView />;
}
