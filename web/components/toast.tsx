"use client";

import { useEffect } from "react";
import Link from "next/link";

type ToastKind = "success" | "info" | "error";

export function Toast({
  message,
  kind = "info",
  href,
  hrefLabel,
  onClose,
  durationMs = 4500,
}: {
  message: string;
  kind?: ToastKind;
  href?: string;
  hrefLabel?: string;
  onClose: () => void;
  durationMs?: number;
}) {
  useEffect(() => {
    const t = window.setTimeout(onClose, durationMs);
    return () => window.clearTimeout(t);
  }, [onClose, durationMs]);

  const tone: Record<ToastKind, string> = {
    success: "bg-accent/15 border-accent text-accent",
    info: "bg-primary/10 border-primary text-primary",
    error: "bg-destructive/10 border-destructive text-destructive",
  };

  return (
    <div
      role="status"
      aria-live="polite"
      className={`fixed top-4 right-4 z-50 max-w-sm rounded-md border px-4 py-3 shadow-md ${tone[kind]}`}
    >
      <div className="flex items-start gap-3">
        <div className="flex-1 text-sm text-foreground">
          <div>{message}</div>
          {href && hrefLabel ? (
            <Link
              href={href}
              onClick={onClose}
              className="mt-1 inline-block text-xs font-semibold underline"
            >
              {hrefLabel} →
            </Link>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="ml-2 text-muted-foreground hover:text-foreground text-sm leading-none"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
