"use client";

import * as React from "react";

export function Skeleton({
  className = "",
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      aria-hidden
      className={`animate-pulse bg-muted/60 rounded ${className}`}
      style={style}
    />
  );
}

export function SkeletonCard({ aspect = "9 / 16" }: { aspect?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <Skeleton className="w-full" style={{ aspectRatio: aspect }} />
      <div className="mt-3 space-y-2">
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
      </div>
      <div className="mt-3 flex gap-2">
        <Skeleton className="h-4 w-12 rounded-full" />
        <Skeleton className="h-4 w-12 rounded-full" />
      </div>
    </div>
  );
}
