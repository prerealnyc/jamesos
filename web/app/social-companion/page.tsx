import { PageHeader, NotBuilt } from "@/components/ui";

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Social Companion" sub="Peer analysis" />
      <NotBuilt
        title="Peer analysis engine not built yet"
        what="Track a peer cohort, surface what's working in your space, flag engagement opportunities."
        backendStatus="Needs a social-read connector (the peer cohort itself is also still unlocked per P47 in the founder docs). The connections panel stores handles; the scraping/analysis layer is a separate build. Not faked."
      />
    </div>
  );
}
