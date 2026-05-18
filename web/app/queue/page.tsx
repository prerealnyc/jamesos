import { Card } from "@/components/ui";

export default function QueuePage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Approval queue</h1>
        <p className="text-muted text-sm mt-1">Next slice of the rebuild.</p>
      </header>
      <Card>
        <p className="text-sm text-muted">
          The approval queue is already live on the backend (the <code>actions</code> table on
          Supabase, with approve/reject). This native page — replacing the old minified
          dashboard&apos;s queue view — is the next page to be built in the greenfield rebuild.
        </p>
      </Card>
    </div>
  );
}
