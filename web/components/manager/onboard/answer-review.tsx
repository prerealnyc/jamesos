"use client";

import { useState } from "react";

import { CitationChips } from "@/components/manager/citations";
import { ErrorBox } from "@/components/manager/primitives";
import { Button, Card, Textarea } from "@/components/ui";
import { errorMessage, managerApi, type AutoAnswerDraft } from "@/lib/manager-api";

/** Batch review of AI-drafted answers for every open interview question —
 * the bm2.0 AnswerReview ported onto the merged /manager routes (drafts key
 * on question_id here).
 *
 * Each card mirrors the single-question "Suggest an answer" flow: the AI
 * suggestion visual, rationale, citation chips, and an editable textarea
 * PREFILLED with the suggestion. An AI draft never becomes a user statement
 * on its own — the answer is only recorded when the user clicks Accept (or
 * Accept all), and always uses the textarea's current text.
 *
 * "Accept all" sequentially POSTs an answer for every card not yet handled;
 * a failure keeps that card (and surfaces the error) without stopping the
 * run. Once every card is handled, onDone(savedTotal) lets the parent
 * re-compute the next batch. */
export function AnswerReview({
  drafts,
  onDone,
}: {
  drafts: AutoAnswerDraft[];
  onDone: (saved: number) => void;
}) {
  // Editable text per question, keyed by question_id, seeded from the suggestion.
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(drafts.map((d) => [d.question_id, d.suggestion])),
  );
  // Cards still awaiting a decision (in original order).
  const [pending, setPending] = useState<AutoAnswerDraft[]>(drafts);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [savingAll, setSavingAll] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedCount, setSavedCount] = useState(0);

  const anyBusy = busyId !== null || savingAll;

  function setText(id: string, v: string) {
    setTexts((prev) => ({ ...prev, [id]: v }));
  }

  function drop(id: string, savedSoFar: number) {
    setPending((prev) => {
      const next = prev.filter((d) => d.question_id !== id);
      if (next.length === 0) onDone(savedSoFar);
      return next;
    });
  }

  async function acceptOne(draft: AutoAnswerDraft) {
    const answer = (texts[draft.question_id] ?? "").trim();
    if (!answer || anyBusy) return;
    setBusyId(draft.question_id);
    setError(null);
    try {
      await managerApi.answerQuestion(draft.question_id, answer);
      const saved = savedCount + 1;
      setSavedCount(saved);
      drop(draft.question_id, saved);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  function skipOne(draft: AutoAnswerDraft) {
    if (anyBusy) return;
    drop(draft.question_id, savedCount);
  }

  async function acceptAll() {
    if (anyBusy || pending.length === 0) return;
    setSavingAll(true);
    setError(null);
    const targets = [...pending];
    let saved = savedCount;
    for (let i = 0; i < targets.length; i++) {
      const draft = targets[i];
      setProgress(`Saving ${i + 1}/${targets.length}…`);
      const answer = (texts[draft.question_id] ?? "").trim();
      if (!answer) {
        // Nothing to record for a blanked-out draft — skip it.
        setPending((prev) => prev.filter((d) => d.question_id !== draft.question_id));
        continue;
      }
      try {
        await managerApi.answerQuestion(draft.question_id, answer);
        saved += 1;
        setSavedCount(saved);
        // Remove as we go so a mid-run failure leaves the rest visible.
        setPending((prev) => prev.filter((d) => d.question_id !== draft.question_id));
      } catch (err) {
        // Keep this card and every card after it, surface the error, keep going.
        setError(errorMessage(err));
      }
    }
    setSavingAll(false);
    setProgress(null);
    // If everything landed, pending is empty — let the parent re-compute.
    setPending((prev) => {
      if (prev.length === 0) onDone(saved);
      return prev;
    });
  }

  if (pending.length === 0) {
    return (
      <p className="text-[12px] text-muted-foreground">
        {savedCount > 0
          ? `Saved ${savedCount} ${savedCount === 1 ? "answer" : "answers"} — your profile is updated.`
          : "All drafts handled."}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2.5">
        <div className="text-[13px]">
          <b>{pending.length}</b> AI-drafted {pending.length === 1 ? "answer" : "answers"} to review
          {savedCount > 0 && (
            <span className="text-muted-foreground text-[12px]"> · {savedCount} saved</span>
          )}
        </div>
        <Button className="!px-3 !py-1.5 text-xs" disabled={anyBusy} onClick={acceptAll}>
          {savingAll ? (progress ?? "Saving…") : `Accept all ${pending.length}`}
        </Button>
      </div>

      {error && <ErrorBox message={error} />}

      <div className="flex flex-col gap-3">
        {pending.map((d) => (
          <Card key={d.question_id} className="flex flex-col gap-3">
            <h3 className="text-[15px] font-semibold leading-snug">{d.question}</h3>

            <div className="rounded-md border border-primary/30 bg-primary/5 p-3 flex flex-col gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-[.4px] text-primary">
                AI suggestion {d.grounded ? "· researched" : "· proposed"}
              </span>
              <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{d.suggestion}</p>
              {d.rationale && <p className="text-[12px] text-muted-foreground">{d.rationale}</p>}
              {d.citations.length > 0 && <CitationChips citations={d.citations.slice(0, 5)} />}
            </div>

            <Textarea
              rows={3}
              value={texts[d.question_id] ?? ""}
              onChange={(e) => setText(d.question_id, e.target.value)}
              placeholder="Edit the AI answer, or type your own…"
            />

            <div className="flex items-center gap-2">
              <Button
                className="!px-3 !py-1.5 text-xs"
                disabled={anyBusy || !(texts[d.question_id] ?? "").trim()}
                onClick={() => acceptOne(d)}
              >
                {busyId === d.question_id ? "Saving…" : "Accept"}
              </Button>
              <Button
                variant="ghost"
                className="!px-3 !py-1.5 text-xs"
                disabled={anyBusy}
                onClick={() => skipOne(d)}
              >
                Skip
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
