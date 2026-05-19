import { PageHeader, NotBuilt } from "@/components/ui";

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Design Studio" sub="Content creation" />
      <NotBuilt
        title="Content-generation engine not built yet"
        what="This is where on-voice drafts (posts, scripts, captions) get generated from the ingested voice corpus and the brand rules, then routed through the protocol/QA gates."
        backendStatus="The substrate it depends on IS live — memory, retrieval, cite-or-refuse, the plug-in rule engine, the approval queue. The generation pipeline that sits on top of them is the next subsystem to build. It is not faked here."
      />
    </div>
  );
}
