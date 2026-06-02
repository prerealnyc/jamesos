"use client";

/**
 * Signup — first signup ever claims the existing default tenant (so
 * existing data stays accessible). Subsequent signups get their own
 * workspace. Password policy enforced client-side AND server-side;
 * client-side just gives instant feedback before the round-trip.
 */

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button, Card, Input, Spinner } from "@/components/ui";

function passwordError(p: string): string {
  if (p.length < 10) return "At least 10 characters.";
  let classes = 0;
  if (/[a-z]/.test(p)) classes++;
  if (/[A-Z]/.test(p)) classes++;
  if (/\d/.test(p)) classes++;
  if (/[^\w\s]/.test(p)) classes++;
  if (classes < 3) return "Mix at least 3 of: lowercase, uppercase, digit, symbol.";
  return "";
}

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const liveErr =
    password && passwordError(password)
      ? passwordError(password)
      : confirm && confirm !== password
        ? "Passwords don't match."
        : "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || liveErr) return;
    setBusy(true); setErr("");
    try {
      await api.signup({
        email: email.trim(),
        password,
        display_name: displayName.trim(),
      });
      window.location.replace("/");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Signup failed");
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="text-[20px] font-bold tracking-[.5px] text-primary">JAMES OS</div>
          <p className="text-[12px] text-muted-foreground mt-1">Create your workspace</p>
        </div>

        <Card className="!p-5">
          <form onSubmit={submit} className="space-y-3">
            <label className="block">
              <span className="text-[12px] text-muted-foreground">Email</span>
              <Input
                type="email"
                autoComplete="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@brand.com"
                className="mt-1"
              />
            </label>
            <label className="block">
              <span className="text-[12px] text-muted-foreground">Display name</span>
              <Input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="optional"
                className="mt-1"
              />
            </label>
            <label className="block">
              <span className="text-[12px] text-muted-foreground">Password</span>
              <Input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="≥10 chars, 3 of: a-z / A-Z / 0-9 / symbol"
                className="mt-1"
              />
            </label>
            <label className="block">
              <span className="text-[12px] text-muted-foreground">Confirm password</span>
              <Input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="mt-1"
              />
            </label>

            {(liveErr || err) && (
              <p className="text-destructive text-[12px] border border-destructive/40 rounded-md p-2 bg-destructive/5">
                ✗ {err || liveErr}
              </p>
            )}

            <Button
              type="submit"
              disabled={busy || !email || !password || !!liveErr || confirm !== password}
              className="w-full"
            >
              {busy ? <Spinner /> : "Create workspace"}
            </Button>
          </form>
        </Card>

        <p className="text-center text-[12px] text-muted-foreground">
          Already have an account?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
