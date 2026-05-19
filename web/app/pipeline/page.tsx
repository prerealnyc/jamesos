import { PageHeader, NotBuilt } from "@/components/ui";

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Pipeline" sub="Podcast → shorts" />
      <NotBuilt
        title="Video pipeline not built yet"
        what="Podcast episode in → transcript (Whisper, already wired) → clip selection → HeyGen/Runway/Descript render → shorts out."
        backendStatus="Whisper transcription is live (audio uploads already become memory). The HeyGen/Runway/Descript keys are configured but inert — the orchestration that drives them is a dedicated subsystem, deliberately not stubbed to look done."
      />
    </div>
  );
}
