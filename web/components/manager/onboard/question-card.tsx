"use client";

import { useEffect, useState } from "react";

import { CitationChips } from "@/components/manager/citations";
import { ErrorBox } from "@/components/manager/primitives";
import { Button, Card, Textarea } from "@/components/ui";
import {
  errorMessage,
  managerApi,
  type AnswerSuggestion,
  type InterviewQuestion,
} from "@/lib/manager-api";
import { whyWeAsk } from "./format";

/** One interview question with answer/skip actions — the bm2.0 QuestionCard
 * ported onto the merged /manager routes. The draft answer lives here and
 * resets when the question changes; it is preserved on a failed submit so
 * the user can retry.
 *
 * "✨ Suggest an answer" asks the Answerer agent to research a draft;
 * accepting it just fills the textarea (editable) — the answer is only
 * recorded when the user clicks Answer, so an AI draft never becomes a user
 * statement on its own. */
export function QuestionCard({
  question,
  counter,
  busy,
  error,
  onAnswer,
  onSkip,
  onFinishLater,
}: {
  question: InterviewQuestion;
  /** e.g. "Question 2 of 10" — omit to hide the count line. */
  counter?: string;
  busy: boolean;
  error?: string | null;
  onAnswer: (answer: string) => void;
  onSkip: () => void;
  onFinishLater?: () => void;
}) {
  const [text, setText] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [suggestion, setSuggestion] = useState<AnswerSuggestion | null>(null);
  const [suggestErr, setSuggestErr] = useState<string | null>(null);

  useEffect(() => {
    setText("");
    setSuggestion(null);
    setSuggestErr(null);
  }, [question.id]);

  async function suggest() {
    setSuggesting(true);
    setSuggestErr(null);
    try {
      const s = await managerApi.suggestAnswer(question.id);
      setSuggestion(s);
    } catch (e) {
      setSuggestErr(errorMessage(e));
    } finally {
      setSuggesting(false);
    }
  }

  return (
    <Card className="flex flex-col gap-3">
      {counter && (
        <div className="text-[11px] font-semibold uppercase tracking-[.4px] text-muted-foreground">
          {counter}
        </div>
      )}
      <h2 className="text-lg font-semibold leading-snug">{question.text}</h2>
      <p className="text-[12px] text-muted-foreground -mt-1">
        <b className="text-foreground/80">Why we ask:</b> {whyWeAsk(question.why)}
      </p>

      {suggestion && (
        <div className="rounded-md border border-primary/30 bg-primary/5 p-3 flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[.4px] text-primary">
              AI suggestion {suggestion.grounded ? "· researched" : "· proposed"}
            </span>
            <button
              type="button"
              className="text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => setSuggestion(null)}
            >
              Dismiss
            </button>
          </div>
          <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{suggestion.suggestion}</p>
          {suggestion.rationale && (
            <p className="text-[12px] text-muted-foreground">{suggestion.rationale}</p>
          )}
          {suggestion.citations.length > 0 && (
            <CitationChips citations={suggestion.citations.slice(0, 5)} />
          )}
          <div>
            <Button
              variant="secondary"
              className="!px-3 !py-1.5 text-xs"
              onClick={() => setText(suggestion.suggestion)}
            >
              Use this answer
            </Button>
          </div>
        </div>
      )}

      <Textarea
        rows={3}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Type your answer…"
        autoFocus
      />
      {suggestErr && <ErrorBox message={suggestErr} />}
      {error && <ErrorBox message={error} />}
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={busy || !text.trim()} onClick={() => onAnswer(text.trim())}>
          {busy ? "Saving…" : "Answer"}
        </Button>
        <Button variant="ghost" disabled={busy || suggesting} onClick={suggest}>
          {suggesting ? "Researching…" : suggestion ? "Suggest again" : "✨ Suggest an answer"}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={onSkip}>
          Skip
        </Button>
        {onFinishLater && (
          <button
            type="button"
            className="ml-auto text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            disabled={busy}
            onClick={onFinishLater}
          >
            Finish later
          </button>
        )}
      </div>
    </Card>
  );
}
