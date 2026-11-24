import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-md px-6 py-16 text-center">
      <h1 className="text-lg font-semibold text-fg">Page not found</h1>
      <p className="mt-2 text-[13.5px] text-fg-muted">There is no such page in the helpdesk console.</p>
      <p className="mt-4">
        <Link href="/" className="text-[13.5px] text-accent underline">
          Back to the task list
        </Link>
      </p>
    </main>
  );
}
