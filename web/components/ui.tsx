import * as React from "react";

function cx(...c: (string | false | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cx("bg-panel border border-border rounded-xl p-5", className)}>{children}</div>
  );
}

export function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-[13px] uppercase tracking-[1px] text-muted font-semibold mb-3">
      {children}
    </h2>
  );
}

export function Button({
  children,
  variant = "primary",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  return (
    <button
      {...props}
      className={cx(
        "rounded-lg px-4 py-2.5 text-sm font-semibold transition-opacity disabled:opacity-50 disabled:cursor-not-allowed",
        variant === "primary" ? "bg-accent text-white hover:opacity-90" : "bg-panel2 text-ink border border-border hover:opacity-90",
        className
      )}
    >
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx(
        "w-full bg-bg text-ink border border-border rounded-lg px-3 py-2.5 text-sm outline-none focus:border-accent",
        props.className
      )}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(
        "w-full bg-bg text-ink border border-border rounded-lg px-3 py-2.5 text-sm outline-none focus:border-accent resize-y",
        props.className
      )}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx(
        "w-full bg-bg text-ink border border-border rounded-lg px-3 py-2.5 text-sm outline-none focus:border-accent",
        props.className
      )}
    />
  );
}

export function Label({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs text-muted mt-3 mb-1">{children}</label>;
}

export function Badge({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "ok" | "warn" | "accent" }) {
  const tones: Record<string, string> = {
    muted: "bg-panel2 text-muted",
    ok: "bg-[#13351f] text-ok",
    warn: "bg-[#3a2e12] text-warn",
    accent: "bg-[#16304f] text-accent",
  };
  return (
    <span className={cx("inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-[.4px]", tones[tone])}>
      {children}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" />;
}
