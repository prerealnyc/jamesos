"use client";

/**
 * Profile — the signed-in user's settings page. Three sections:
 *
 *   1. Account     — email, display name, tenant id (read-only here;
 *                    rename will move to /workspace later)
 *   2. Password    — change password with current-password gate
 *   3. API keys    — links to /settings where the encrypted key
 *                    store lives. Keeping these on a separate page
 *                    means changing personal info doesn't accidentally
 *                    expose the keys panel.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Badge, Button, Card, Input, PageHeader, Spinner } from "@/components/ui";

type Me = NonNullable<Awaited<ReturnType<typeof api.whoami>>>;

function platformChip(p: string): string {
  return ({ instagram: "IG", tiktok: "TT", youtube: "YT" } as Record<string, string>)[p] || p;
}

export default function ProfilePage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  // Password form
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [pwBusy, setPwBusy] = useState(false);
  const [pwErr, setPwErr] = useState("");
  const [pwOk, setPwOk] = useState(false);

  // Brand accounts mirror (full management at /analytics)
  const [accounts, setAccounts] = useState<{ platform: string; handle: string; name?: string }[]>([]);
  const [newPlatform, setNewPlatform] = useState("instagram");
  const [newHandle, setNewHandle] = useState("");
  const [accountBusy, setAccountBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const m = await api.whoami();
        if (!m) { router.replace("/login"); return; }
        setMe(m);
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

  useEffect(() => {
    api.listBrandAccounts().then((r) => setAccounts(r.accounts)).catch(() => {});
  }, []);

  async function addAccount() {
    const h = newHandle.trim().replace(/^@/, "").toLowerCase();
    if (!h) return;
    setAccountBusy(true);
    try {
      const next = [...accounts, { platform: newPlatform, handle: h }];
      const r = await api.setBrandAccounts(next);
      setAccounts(r.accounts);
      setNewHandle("");
    } finally { setAccountBusy(false); }
  }

  async function removeAccount(target: { platform: string; handle: string }) {
    setAccountBusy(true);
    try {
      const next = accounts.filter(a => !(a.platform === target.platform && a.handle === target.handle));
      const r = await api.setBrandAccounts(next);
      setAccounts(r.accounts);
    } finally { setAccountBusy(false); }
  }

  async function logout() {
    try { await api.logout(); } catch { /* fall through */ }
    window.location.replace("/login");
  }

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    setPwErr(""); setPwOk(false);
    if (pw.next !== pw.confirm) {
      setPwErr("New passwords don't match."); return;
    }
    if (pw.next.length < 10) {
      setPwErr("New password must be at least 10 characters."); return;
    }
    setPwBusy(true);
    try {
      await api.changePassword({
        current_password: pw.current,
        new_password: pw.next,
      });
      setPwOk(true);
      setPw({ current: "", next: "", confirm: "" });
      setTimeout(() => setPwOk(false), 3500);
    } catch (e) {
      setPwErr(e instanceof Error ? e.message : "Could not change password");
    } finally {
      setPwBusy(false);
    }
  }

  if (loading || !me) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Spinner /> Loading…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Profile"
        sub="Your account, your password, and the API key store. Workspace settings live here too."
      />

      {/* Account */}
      <Card className="!p-4 space-y-2">
        <p className="text-[13px] font-medium">Account</p>
        <dl className="text-[12px] grid grid-cols-[120px_1fr] gap-y-1.5">
          <dt className="text-muted-foreground">Email</dt>
          <dd className="font-mono">{me.email}</dd>
          <dt className="text-muted-foreground">Display name</dt>
          <dd>{me.display_name || <span className="text-muted-foreground">(unset)</span>}</dd>
          <dt className="text-muted-foreground">Role</dt>
          <dd>{me.role}</dd>
          <dt className="text-muted-foreground">Workspace id</dt>
          <dd className="font-mono text-[11px] text-muted-foreground">{me.tenant_id}</dd>
        </dl>
        <div className="pt-3 border-t border-border">
          <Button variant="secondary" onClick={logout}>Sign out</Button>
        </div>
      </Card>

      {/* Workspace — Brand accounts (compact mirror of /analytics) */}
      <Card className="!p-4 space-y-3">
        <div>
          <p className="text-[13px] font-medium">Workspace — Brand accounts</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            The handles analytics and autopilot track for this workspace.
          </p>
        </div>
        {accounts.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {accounts.map((a) => (
              <span
                key={`${a.platform}:${a.handle}`}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-muted border border-border text-[12px]"
              >
                <Badge tone="muted">{platformChip(a.platform)}</Badge>
                <span>@{a.handle}</span>
                <button
                  onClick={() => removeAccount(a)}
                  disabled={accountBusy}
                  className="text-muted-foreground hover:text-destructive ml-1"
                  title="Remove this account"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[12px] text-muted-foreground">
            Add your social handles so analytics and autopilot know what to track.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-border">
          <select
            value={newPlatform}
            onChange={(e) => setNewPlatform(e.target.value)}
            className="text-[12px] px-2 py-1.5 rounded border border-border bg-background"
          >
            <option value="instagram">Instagram</option>
            <option value="tiktok">TikTok</option>
            <option value="youtube">YouTube</option>
          </select>
          <Input
            placeholder="handle (no @)"
            value={newHandle}
            onChange={(e) => setNewHandle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") addAccount(); }}
            className="text-[12px] !py-1.5 min-w-[160px]"
          />
          <Button
            onClick={addAccount}
            disabled={!newHandle.trim() || accountBusy}
            className="text-[12px] !px-3 !py-1.5"
          >
            {accountBusy ? <Spinner /> : "+ Add"}
          </Button>
        </div>
        <div>
          <Link
            href="/analytics"
            className="text-[13px] text-primary hover:underline"
          >
            Open full analytics →
          </Link>
        </div>
      </Card>

      {/* Password */}
      <Card className="!p-4 space-y-3">
        <div>
          <p className="text-[13px] font-medium">Password</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            ≥10 characters; mix at least 3 of lowercase / uppercase / digit / symbol.
          </p>
        </div>
        <form onSubmit={submitPassword} className="space-y-3 max-w-sm">
          <label className="block">
            <span className="text-[12px] text-muted-foreground">Current password</span>
            <Input
              type="password"
              autoComplete="current-password"
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
              className="mt-1"
            />
          </label>
          <label className="block">
            <span className="text-[12px] text-muted-foreground">New password</span>
            <Input
              type="password"
              autoComplete="new-password"
              value={pw.next}
              onChange={(e) => setPw({ ...pw, next: e.target.value })}
              className="mt-1"
            />
          </label>
          <label className="block">
            <span className="text-[12px] text-muted-foreground">Confirm new password</span>
            <Input
              type="password"
              autoComplete="new-password"
              value={pw.confirm}
              onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
              className="mt-1"
            />
          </label>
          {pwErr && (
            <p className="text-destructive text-[12px] border border-destructive/40 rounded-md p-2 bg-destructive/5">
              ✗ {pwErr}
            </p>
          )}
          {pwOk && (
            <p className="text-accent text-[12px] border border-accent/40 rounded-md p-2 bg-accent/10">
              ✓ Password changed
            </p>
          )}
          <Button
            type="submit"
            disabled={pwBusy || !pw.current || !pw.next || !pw.confirm}
          >
            {pwBusy ? <Spinner /> : "Change password"}
          </Button>
        </form>
      </Card>

      {/* API keys handoff */}
      <Card className="!p-4 space-y-2">
        <p className="text-[13px] font-medium">API connections</p>
        <p className="text-[12px] text-muted-foreground">
          API keys (OpenAI, Anthropic, HeyGen, Runway, Creatomate, Apify, Perplexity, Google Drive) are stored per-workspace, Fernet-encrypted at rest, and take effect on the next request — no restart.
        </p>
        <div>
          <Link
            href="/settings"
            className="text-[13px] text-primary hover:underline"
          >
            Open API connections →
          </Link>
        </div>
      </Card>
    </div>
  );
}
