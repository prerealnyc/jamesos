"use client";

/**
 * Follow-ups — every play, lever, and radar finding as a trackable action
 * item: accept / mark done / snooze / dismiss / delete, log updates, research
 * a contact path, or put hands on it ("Draft it" → the execution spine).
 * Ported from bm2.0 followups-panel.tsx onto the merged /manager/actions
 * routes (409 on execute = a refused state-machine edge → quiet notice).
 */

import { useState } from "react";

import { Card, Spinner } from "@/components/ui";
import { EmptyState, ErrorBox, Loading, StatusBadge, useLoad } from "@/components/manager/primitives";
import { goToSection } from "@/components/manager/section-nav";
import {
  errorMessage,
  isConflict,
  managerApi,
  type ActionItem,
  type ActionsResponse,
  type ActionStatus,
} from "@/lib/manager-api";
import { ACTION_KIND_TONE, CONFIDENCE_TONE, fmtDateTime, Notice, ToneBadge } from "./bits";

/** Status groups in reading order. Dismissed is hidden behind a toggle. */
const GROUPS: { status: ActionStatus; title: string }[] = [
  { status: "suggested", title: "Suggested" },
  { status: "active", title: "Active" },
  { status: "snoozed", title: "Snoozed" },
  { status: "done", title: "Done" },
];

export function FollowupsPanel({ refreshKey, onMutated }: { refreshKey: number; onMutated: () => void }) {
  const actions = useLoad<ActionsResponse>(() => managerApi.listActions(), [refreshKey]);
  const [showDismissed, setShowDismissed] = useState(false);

  const all = actions.data?.actions ?? [];
  const dismissed = all.filter((a) => a.status === "dismissed");

  return (
    <Card variant="compact">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="m-0 text-xs text-muted-foreground">
          Every play and radar finding is a trackable follow-up — accept it, log what happened, snooze,
          or draft it into The Work.
        </p>
        {actions.loading && actions.data && <Spinner />}
      </div>

      {actions.error && (
        <div className="mb-3">
          <ErrorBox message={actions.error} onRetry={actions.reload} />
        </div>
      )}
      {actions.loading && !actions.data && <Loading label="Loading your follow-ups…" />}

      {actions.data && all.length === 0 && (
        <EmptyState
          title="No follow-ups yet"
          body="Run the radar or plan collaborations above — each finding lands here as a follow-up you can work."
        />
      )}

      {GROUPS.map((g) => {
        const inGroup = all.filter((a) => a.status === g.status);
        if (inGroup.length === 0) return null;
        return (
          <div key={g.status} className="mt-3 first:mt-0">
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-[.4px] text-muted-foreground">
              {g.title} · {inGroup.length}
            </h4>
            <div className="space-y-2">
              {inGroup.map((a) => (
                <FollowupCard key={a.id} action={a} onMutated={onMutated} />
              ))}
            </div>
          </div>
        );
      })}

      {dismissed.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            className="rounded-md px-2.5 py-1 text-xs font-semibold text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => setShowDismissed((v) => !v)}
          >
            {showDismissed ? "Hide" : "Show"} dismissed ({dismissed.length})
          </button>
          {showDismissed && (
            <div className="mt-2 space-y-2">
              {dismissed.map((a) => (
                <FollowupCard key={a.id} action={a} onMutated={onMutated} />
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function ActionButton({
  children,
  variant = "ghost",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" }) {
  const v =
    variant === "primary"
      ? "bg-primary text-primary-foreground hover:bg-primary/90"
      : variant === "danger"
        ? "bg-destructive/10 text-destructive hover:bg-destructive/20"
        : "text-muted-foreground hover:bg-secondary hover:text-foreground";
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center rounded-md px-2.5 py-1 text-xs font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${v}`}
    >
      {children}
    </button>
  );
}

function FollowupCard({ action, onMutated }: { action: ActionItem; onMutated: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const meta = action.meta ?? {};
  const updates = action.updates ?? [];
  const contactPaths = meta.contact_paths ?? [];

  async function run(label: string, fn: () => Promise<unknown>) {
    if (busy) return;
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      onMutated(); // refetch the list (and the daily digest) after each mutation
      setBusy(null);
    } catch (err) {
      if (isConflict(err)) setNotice(errorMessage(err)); // refused edge / already running
      else setError(errorMessage(err));
      setBusy(null);
    }
  }

  const setStatus = (status: ActionStatus, snoozeDays?: number) =>
    run(status, () => managerApi.setActionStatus(action.id, status, snoozeDays));

  function submitNote() {
    const text = note.trim();
    if (!text) return;
    void run("note", async () => {
      await managerApi.addActionNote(action.id, text);
      setNote("");
    });
  }

  function doDelete() {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    void run("delete", () => managerApi.deleteAction(action.id));
  }

  // Put hands on this opportunity — drafts an asset (or routes video/image to
  // production), which surfaces up in The Work above.
  const draftIt = () =>
    run("draft", async () => {
      await managerApi.executeOpportunity(action.id);
      goToSection("work");
    });

  const researchContact = () => run("research", () => managerApi.researchContact(action.id));

  const busyAny = busy !== null;

  return (
    <div className="rounded-md border border-border bg-background p-3" id={`action-${action.id}`}>
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold">{action.title}</div>
          {(meta.peer_handle || action.related_peer) && (
            <div className="text-xs text-muted-foreground">@{meta.peer_handle ?? action.related_peer}</div>
          )}
        </div>
        <span className="flex shrink-0 items-center gap-1.5">
          <ToneBadge tone={ACTION_KIND_TONE[action.kind] ?? "grey"}>{action.kind}</ToneBadge>
          <StatusBadge status={action.status} />
        </span>
      </div>

      {action.detail && <p className="m-0 mb-2 text-sm">{action.detail}</p>}

      {(meta.outreach_path || contactPaths.length > 0) && (
        <div className="mb-2 rounded-md border border-border bg-card px-2.5 py-2">
          {meta.outreach_path && (
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-xs text-muted-foreground">{meta.outreach_path}</span>
              {meta.outreach_confidence && (
                <ToneBadge tone={CONFIDENCE_TONE[meta.outreach_confidence] ?? "grey"}>
                  {meta.outreach_confidence}
                </ToneBadge>
              )}
            </div>
          )}
          {contactPaths.length > 0 && (
            <div className={`flex flex-wrap gap-1.5 ${meta.outreach_path ? "mt-1.5" : ""}`}>
              {contactPaths.map((p, i) => (
                <span
                  key={`${p.type}-${i}`}
                  title={p.source || undefined}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground"
                >
                  {p.type}: <span className="max-w-[220px] truncate">{p.value}</span>
                  <ToneBadge tone={CONFIDENCE_TONE[p.confidence] ?? "grey"}>{p.confidence}</ToneBadge>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* updates log — the "is it going through?" record */}
      {updates.length > 0 && (
        <div className="mb-2 space-y-1 border-l-2 border-border pl-2.5">
          {updates.map((u, i) => (
            <div key={i} className="flex items-baseline gap-2 text-xs">
              <span className="whitespace-nowrap text-muted-foreground">{fmtDateTime(u.ts)}</span>
              <span className="min-w-0">{u.note}</span>
              {u.actor && u.actor !== "user" && (
                <ToneBadge tone="grey">{u.actor.replace(/_/g, " ")}</ToneBadge>
              )}
            </div>
          ))}
        </div>
      )}

      {/* inline add-note */}
      <div className="mb-2 flex gap-2">
        <input
          className="w-full rounded-md border border-input bg-card px-2.5 py-1.5 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
          placeholder="Log an update — e.g. sent a DM…"
          value={note}
          disabled={busyAny}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submitNote();
            }
          }}
        />
        <ActionButton disabled={busyAny || !note.trim()} onClick={submitNote}>
          {busy === "note" ? "Adding…" : "Add note"}
        </ActionButton>
      </div>

      {/* per-card actions */}
      <div className="flex flex-wrap gap-1.5">
        <ActionButton disabled={busyAny} onClick={draftIt}>
          {busy === "draft" ? (
            <>
              <Spinner /> Drafting…
            </>
          ) : (
            "Draft it →"
          )}
        </ActionButton>
        {action.status !== "active" && (
          <ActionButton variant="primary" disabled={busyAny} onClick={() => setStatus("active")}>
            {busy === "active" ? "…" : action.status === "snoozed" ? "Resume" : "Accept"}
          </ActionButton>
        )}
        {action.status !== "done" && (
          <ActionButton disabled={busyAny} onClick={() => setStatus("done")}>
            {busy === "done" ? "…" : "Mark done"}
          </ActionButton>
        )}
        {action.status !== "snoozed" && action.status !== "done" && (
          <ActionButton disabled={busyAny} onClick={() => setStatus("snoozed", 3)}>
            {busy === "snoozed" ? "…" : "Snooze 3d"}
          </ActionButton>
        )}
        {action.status !== "dismissed" && (
          <ActionButton disabled={busyAny} onClick={() => setStatus("dismissed")}>
            {busy === "dismissed" ? "…" : "Dismiss"}
          </ActionButton>
        )}
        <ActionButton disabled={busyAny} onClick={researchContact}>
          {busy === "research" ? (
            <>
              <Spinner /> Researching…
            </>
          ) : (
            "Research contact"
          )}
        </ActionButton>
        <ActionButton
          variant={confirmDelete ? "danger" : "ghost"}
          disabled={busyAny}
          onClick={doDelete}
          onBlur={() => setConfirmDelete(false)}
        >
          {busy === "delete" ? "Deleting…" : confirmDelete ? "Confirm delete" : "Delete"}
        </ActionButton>
      </div>

      {action.snooze_until && action.status === "snoozed" && (
        <p className="m-0 mt-2 text-xs text-muted-foreground">Snoozed until {fmtDateTime(action.snooze_until)}</p>
      )}

      {notice && (
        <div className="mt-2">
          <Notice>{notice}</Notice>
        </div>
      )}
      {error && (
        <p className="m-0 mt-2 text-xs font-medium text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
