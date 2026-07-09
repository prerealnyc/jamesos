"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { managerApi } from "@/lib/manager-api";
import { Spinner } from "@/components/ui";

/**
 * The front door (the 2.0 journey, Roy's call 2026-07-09): a brand that
 * hasn't been onboarded lands in the onboarding wizard; an onboarded brand
 * lands in Mission Control. The production studio (create/video/libraries)
 * is the backend workshop, reachable from the rail — never the landing.
 * If the manager layer is off for this tenant (manager_v2 gate), fall back
 * to the Ask console so the pre-merge experience still has a home.
 */
export default function FrontDoor() {
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const steps = await managerApi.nextSteps();
        if (cancelled) return;
        const research = steps.find((s) => s.step === "research_profile");
        const onboarded = research?.state === "done" || research?.state === "running";
        router.replace(onboarded ? "/manager" : "/intake");
      } catch {
        // manager_v2 off (404) or the API is unreachable — classic home
        if (!cancelled) router.replace("/ask");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="flex items-center justify-center py-24 text-muted-foreground gap-3">
      <Spinner /> <span className="text-sm">Finding your front door…</span>
    </div>
  );
}
