import { ArrowRight, Check, FileText, Radar, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import Link from "next/link";
import { Logo } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/primitives";
import { FactorBar, ScoreRing } from "@/components/ui/score";

const steps = [
  { icon: Radar, title: "Discover", body: "Follow company boards on Greenhouse, Lever and Ashby. New roles are pulled in, de-duplicated and scored against your profile." },
  { icon: Sparkles, title: "Understand fit", body: "Every score is explained: which skills match, which are adjacent, what's missing and why a role might not be eligible." },
  { icon: FileText, title: "Tailor", body: "Suggested edits are applied inside your own DOCX — fonts, tables, margins and bullets stay exactly as you designed them." },
  { icon: Workflow, title: "Apply & track", body: "Prepare answers from your verified profile, approve each application, and track it from submission to offer." },
];

const principles = [
  "Nothing is submitted or sent without your approval",
  "Never invents skills, employers, dates or results",
  "Unknown answers are left for you — not guessed",
  "Submissions count only once they're acknowledged",
  "Only official job-board APIs — no scraping, no CAPTCHA tricks",
  "Your data stays yours: export or delete anytime",
];

export default function Landing() {
  return (
    <div className="min-h-dvh">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Logo />
        <nav className="flex items-center gap-2">
          <Button asChild variant="ghost"><Link href="/login">Sign in</Link></Button>
          <Button asChild variant="primary"><Link href="/register">Get started</Link></Button>
        </nav>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 pt-16 pb-20 sm:px-6 lg:grid-cols-[1.1fr_1fr] lg:pt-24">
          <div>
            <Badge tone="accent" className="mb-5">Your AI-powered career copilot</Badge>
            <h1 className="text-4xl leading-[1.08] font-semibold tracking-[-0.03em] text-balance sm:text-5xl">
              A job search that runs itself — and still asks you first.
            </h1>
            <p className="mt-5 max-w-xl text-base leading-relaxed text-fg-muted text-pretty">
              JobPilot finds roles that genuinely fit, explains why, tailors your own resume without breaking its
              formatting, and prepares every application for your approval. You stay in control; it does the legwork.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button asChild variant="primary" size="lg">
                <Link href="/register">Create your workspace <ArrowRight /></Link>
              </Button>
              <Button asChild variant="secondary" size="lg"><Link href="/login">Sign in</Link></Button>
            </div>
          </div>

          {/* Illustrative example of the match explanation UI (not real data). */}
          <div className="relative" aria-label="Example of a match explanation">
            <div className="absolute -inset-6 -z-10 rounded-[28px] bg-gradient-to-br from-accent-soft via-transparent to-transparent opacity-80" />
            <div className="rounded-xl border border-border bg-surface p-5 shadow-lg">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-2xs font-medium uppercase tracking-wide text-fg-subtle">Example</p>
                  <p className="mt-1 font-semibold">Backend Engineer</p>
                  <p className="text-[13px] text-fg-muted">Payments platform · Bengaluru · Hybrid</p>
                </div>
                <ScoreRing score={84} size={52} />
              </div>
              <div className="mt-5 space-y-3">
                {[["Skills", 0.86, "6 of 7 listed skills matched, 1 adjacent"], ["Experience", 1, "Asks for 2–4 years; you have 3"], ["Role", 0.9, "Closest to your target “Backend Engineer”"], ["Location", 1, "Bengaluru is a preferred location"]].map(([k, v, s]) => (
                  <div key={k as string} className="grid grid-cols-[80px_1fr] items-center gap-3">
                    <span className="text-xs text-fg-muted">{k}</span>
                    <div>
                      <FactorBar value={v as number} />
                      <p className="mt-1 text-xs text-fg-muted">{s}</p>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-5 flex flex-wrap gap-1.5 border-t border-border pt-4">
                {["Python", "PostgreSQL", "AWS", "Docker"].map((s) => <Badge key={s} tone="positive">{s}</Badge>)}
                <Badge tone="warning">Kubernetes · missing</Badge>
              </div>
            </div>
          </div>
        </section>

        <section className="border-y border-border bg-surface">
          <div className="mx-auto grid max-w-6xl gap-px overflow-hidden px-4 sm:px-6 md:grid-cols-4">
            {steps.map((s, i) => (
              <div key={s.title} className="py-10 md:px-6 md:first:pl-0">
                <div className="flex items-center gap-2 text-fg-muted">
                  <s.icon className="size-4" />
                  <span className="tabular text-xs">0{i + 1}</span>
                </div>
                <h2 className="mt-3 font-semibold">{s.title}</h2>
                <p className="mt-2 text-[13px] leading-relaxed text-fg-muted">{s.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mx-auto grid max-w-6xl gap-10 px-4 py-20 sm:px-6 md:grid-cols-[1fr_1.2fr]">
          <div>
            <ShieldCheck className="size-5 text-accent" />
            <h2 className="mt-3 text-2xl font-semibold tracking-[-0.02em]">Automation with guardrails</h2>
            <p className="mt-3 text-[15px] leading-relaxed text-fg-muted">
              The rules aren&apos;t settings you can forget to turn on — they&apos;re enforced by the server, for the
              web app, background automations and AI clients connected over MCP alike.
            </p>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2">
            {principles.map((p) => (
              <li key={p} className="flex items-start gap-2.5 rounded-lg border border-border bg-surface p-3.5 text-[13px]">
                <Check className="mt-0.5 size-4 shrink-0 text-accent" />
                {p}
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-xs text-fg-muted sm:px-6">
          <Logo className="scale-90 origin-left" />
          <span>Works with Greenhouse, Lever, Ashby, Gmail and MCP clients such as Claude.</span>
        </div>
      </footer>
    </div>
  );
}
