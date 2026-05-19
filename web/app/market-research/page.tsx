import { PageHeader, NotBuilt } from "@/components/ui";

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Market Research" sub="Industry intel" />
      <NotBuilt
        title="Research feeds not built yet"
        what="Industry/news intel relevant to the brand's pillars, surfaced as content opportunities."
        backendStatus="Needs a source allowlist + ingestion feeds. Anything ingested would flow through the same memory + cite-or-refuse substrate (which is live). The feed layer itself is not built."
      />
    </div>
  );
}
