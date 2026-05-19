import { PageHeader, NotBuilt } from "@/components/ui";

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Images" sub="Image library" />
      <NotBuilt
        title="Image library — no image store/generation yet"
        what="Generated and uploaded images, reusable across posts."
        backendStatus="Needs an object store + image generation wiring. Not built. The OpenAI key is present but no image pipeline consumes it yet."
      />
    </div>
  );
}
