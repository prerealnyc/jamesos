"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card, CardTitle, PageHeader, Badge } from "@/components/ui";

export default function JpLivePage() {
  const [intg, setIntg] = useState<{ configured: Record<string, boolean>; active: string[] } | null>(null);
  const [scan, setScan] = useState<string | null>(null);
  const [conns, setConns] = useState<{ platform: string; handle: string; enabled: boolean; status: string }[]>([]);

  useEffect(() => {
    api.integrations().then(setIntg).catch(() => {});
    api.lastScan().then((d) => setScan(d.lastScanAt)).catch(() => {});
    api.connections().then(setConns).catch(() => {});
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="JP Live"
        sub="Live brand health — the system status that is genuinely real today (no fabricated metrics)."
      />

      <Card>
        <CardTitle>Integrations</CardTitle>
        {!intg ? (
          <p className="text-muted-foreground text-sm">Loading…</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {Object.entries(intg.configured).map(([k, v]) => (
              <Badge key={k} tone={intg.active.includes(k) ? "ok" : v ? "primary" : "muted"}>
                {k}
                {intg.active.includes(k) ? " · live" : v ? " · configured" : " · —"}
              </Badge>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardTitle>Social connections</CardTitle>
        <div className="flex flex-col gap-1.5">
          {conns.map((c) => (
            <div key={c.platform} className="flex items-center gap-3 text-sm">
              <span className="w-28 capitalize">{c.platform}</span>
              <span className="text-muted-foreground flex-1">{c.handle || "—"}</span>
              <Badge tone={c.status === "configured" ? "primary" : "muted"}>{c.status}</Badge>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardTitle>Memory</CardTitle>
        <p className="text-sm">
          Last ingest event:{" "}
          <b>{scan ? new Date(scan).toLocaleString() : "—"}</b>
        </p>
        <p className="text-muted-foreground text-xs mt-2">
          Engagement KPIs (views, follower growth, reach) are intentionally not shown — there is
          no social-metrics source wired yet. We do not fabricate performance numbers.
        </p>
      </Card>
    </div>
  );
}
