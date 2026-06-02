"use client";

/**
 * Login — email + password. Sets the session cookie on success and
 * redirects to ?next= (or / if not provided). Public route — no
 * auth guard needed.
 */

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button, Card, Input, Spinner } from "@/components/ui";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setErr("");
    try {
      await api.login({ email: email.trim(), password });
      // Hard navigation so the new auth cookie is on every subsequent fetch.
      window.location.replace(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Login failed");
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="text-[20px] font-bold tracking-[.5px] text-primary">JAMES OS</div>
          <p className="text-[12px] text-muted-foreground mt-1">Sign in to your workspace</p>
        </div>

        <Card className="!p-5">
          <form onSubmit={submit} className="space-y-3">
            <label className="block">
              <span className="text-[12px] text-muted-foreground">Email</span>
              <Input
                type="email"
                autoComplete="username"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@brand.com"
                className="mt-1"
              />
            </label>
            <label className="block">
              <span className="text-[12px] text-muted-foreground">Password</span>
              <Input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1"
              />
            </label>

            {err && (
              <p className="text-destructive text-[12px] border border-destructive/40 rounded-md p-2 bg-destructive/5">
                ✗ {err}
              </p>
            )}

            <Button type="submit" disabled={busy || !email || !password} className="w-full">
              {busy ? <Spinner /> : "Sign in"}
            </Button>
          </form>
        </Card>

        <p className="text-center text-[12px] text-muted-foreground">
          No workspace yet?{" "}
          <Link href="/signup" className="text-primary hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center"><Spinner /></div>}>
      <LoginForm />
    </Suspense>
  );
}
