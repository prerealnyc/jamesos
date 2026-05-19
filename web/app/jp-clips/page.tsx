import { PageHeader, NotBuilt } from "@/components/ui";

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="JP Clips" sub="Talking head library" />
      <NotBuilt
        title="Clip library — no clip store yet"
        what="Reusable on-camera clips, tagged by topic, attachable to future posts."
        backendStatus="This was empty in the old dashboard too (its screenshot literally showed 'Library is empty'). It needs a media store + the clip pipeline. Not built — and not faked with an empty shell pretending to be functional."
      />
    </div>
  );
}
