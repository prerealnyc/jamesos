import * as React from "react";

function cx(...c: (string | false | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cx("bg-card text-card-foreground border border-border rounded-lg shadow-sm p-5", className)}>
      {children}
    </div>
  );
}

export function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-[13px] uppercase tracking-[1px] text-muted-foreground font-semibold mb-3">
      {children}
    </h2>
  );
}

export function Button({
  children,
  variant = "primary",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" }) {
  const v =
    variant === "primary"
      ? "bg-primary text-primary-foreground hover:bg-primary/90"
      : variant === "secondary"
        ? "bg-secondary text-secondary-foreground hover:bg-secondary/80"
        : "bg-transparent text-foreground hover:bg-secondary";
  return (
    <button
      {...props}
      className={cx(
        "inline-flex items-center justify-center rounded-md px-4 py-2.5 text-sm font-semibold",
        "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:opacity-50 disabled:pointer-events-none",
        v,
        className
      )}
    >
      {children}
    </button>
  );
}

const field =
  "w-full bg-background text-foreground border border-input rounded-md px-3 py-2.5 text-sm " +
  "outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(field, props.className)} />;
}
export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(field, "resize-y", props.className)} />;
}
export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(field, props.className)} />;
}

export function Label({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs text-muted-foreground mt-3 mb-1">{children}</label>;
}

export function Badge({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "primary" | "accent" | "destructive" | "ok";
}) {
  const tones: Record<string, string> = {
    muted: "bg-secondary text-muted-foreground",
    primary: "bg-primary/15 text-primary",
    accent: "bg-accent/20 text-accent",
    ok: "bg-accent/20 text-accent",
    destructive: "bg-destructive/15 text-destructive",
  };
  return (
    <span
      className={cx(
        "inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-[.4px]",
        tones[tone]
      )}
    >
      {children}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" />;
}
