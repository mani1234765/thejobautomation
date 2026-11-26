"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Logo } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/primitives";
import { Callout } from "@/components/ui/states";
import { api, ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";

// Zod's JIT probes `new Function`, which the Content-Security-Policy (correctly) forbids.
z.config({ jitless: true });

const login = z.object({
  email: z.email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});
const register = login.extend({
  full_name: z.string().trim().min(1, "Enter your name."),
  password: z
    .string()
    .min(10, "Use at least 10 characters.")
    .refine((v) => [/[a-z]/i, /\d/, /[^a-z0-9]/i].filter((r) => r.test(v)).length >= 2, "Mix letters with numbers or symbols."),
});
type Values = z.infer<typeof register>;

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver((mode === "login" ? login : register) as typeof register),
    defaultValues: { email: "", password: "", full_name: "" },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      const me = await api.post<Me>(mode === "login" ? "/auth/login" : "/auth/register",
        mode === "login" ? { email: values.email, password: values.password } : values);
      qc.setQueryData(["me"], me);
      const next = params.get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : null;
      router.replace(!me.onboarding_completed ? "/onboarding" : safeNext ?? "/overview");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Try again.");
    }
  });

  return (
    <div className="flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="w-full max-w-[360px]">
        <Link href="/" className="mb-8 inline-flex">
          <Logo />
        </Link>
        <h1 className="text-xl font-semibold tracking-[-0.015em]">
          {mode === "login" ? "Welcome back" : "Create your workspace"}
        </h1>
        <p className="mt-1 text-[13px] text-fg-muted">
          {mode === "login" ? "Sign in to continue your search." : "Free while in early access. Takes about two minutes."}
        </p>
        <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4" noValidate>
          {error && <Callout tone="danger">{error}</Callout>}
          {mode === "register" && (
            <Field label="Full name" htmlFor="full_name" error={errors.full_name?.message}>
              <Input id="full_name" autoComplete="name" aria-invalid={!!errors.full_name} {...form.register("full_name")} />
            </Field>
          )}
          <Field label="Email" htmlFor="email" error={errors.email?.message}>
            <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} {...form.register("email")} />
          </Field>
          <Field label="Password" htmlFor="password" error={errors.password?.message}
            hint={mode === "register" ? "10+ characters, mixing letters with numbers or symbols." : undefined}>
            <Input id="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"}
              aria-invalid={!!errors.password} {...form.register("password")} />
          </Field>
          <Button type="submit" variant="primary" size="lg" loading={isSubmitting} className="mt-1">
            {mode === "login" ? "Sign in" : "Create account"}
          </Button>
        </form>
        <p className="mt-6 text-center text-[13px] text-fg-muted">
          {mode === "login" ? (
            <>New to JobPilot? <Link href="/register" className="font-medium text-fg hover:underline">Create an account</Link></>
          ) : (
            <>Already have an account? <Link href="/login" className="font-medium text-fg hover:underline">Sign in</Link></>
          )}
        </p>
      </div>
    </div>
  );
}
