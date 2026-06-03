"use client";

import * as React from "react";

export function FilterChip({
  active,
  onClick,
  children,
  count,
  title,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  count?: number;
  title?: string;
}) {
  const base =
    "inline-flex items-center rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors";
  const tone = active
    ? "bg-primary text-primary-foreground border-primary"
    : "bg-background border-border hover:bg-muted text-foreground";
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className={`${base} ${tone}`}
    >
      {children}
      {typeof count === "number" ? <span> · {count}</span> : null}
    </button>
  );
}
