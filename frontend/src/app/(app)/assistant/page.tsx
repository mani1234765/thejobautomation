"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Bot, Loader2, MessageSquarePlus, Plug, Trash2, Wrench } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, Skeleton } from "@/components/ui/primitives";
import { Callout, EmptyState, PageHeader } from "@/components/ui/states";
import { api, ApiError } from "@/lib/api";
import { cn, relativeTime } from "@/lib/format";
import { keys, useAction, useAiStatus, useConversations, useMessages } from "@/lib/queries";
import type { ChatMessage, Conversation } from "@/lib/types";

const SUGGESTIONS = [
  "Which of my discovered jobs fit me best, and why?",
  "What skills keep showing up that I'm missing?",
  "Review my resume and tell me the three biggest improvements.",
  "Draft a referral request for my top-matching job.",
];
const TOOL_LABEL: Record<string, string> = {
  get_profile: "Read profile", search_jobs: "Searched jobs", get_job_match: "Checked a match", list_applications: "Read applications",
  get_resume_quality: "Read resume review", get_skill_demand: "Analysed skill demand",
};

/** Minimal, safe Markdown: paragraphs, bullet lists and **bold** (rendered as React nodes, never HTML). */
function Md({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) => p.startsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p);
  return (
    <div className="space-y-2">
      {blocks.map((b, i) => {
        const lines = b.split("\n");
        if (lines.every((l) => /^\s*([-*•]|\d+\.)\s/.test(l))) {
          return <ul key={i} className="list-disc space-y-1 pl-5">{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*([-*•]|\d+\.)\s/, ""))}</li>)}</ul>;
        }
        return <p key={i} className="whitespace-pre-wrap">{inline(b)}</p>;
      })}
    </div>
  );
}

function Bubble({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return <div className="ml-auto max-w-[85%] rounded-xl rounded-br-sm bg-surface-3 px-3.5 py-2.5 text-[13px] whitespace-pre-wrap">{m.content}</div>;
  }
  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-accent-soft text-accent-soft-fg"><Bot className="size-3.5" /></span>
      <div className="min-w-0 flex-1 text-[13px] leading-relaxed">
        {m.tools_used.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1">
            {[...new Set(m.tools_used)].map((t) => <Badge key={t}><Wrench className="size-2.5" />{TOOL_LABEL[t] ?? t}</Badge>)}
          </div>
        )}
        <Md text={m.content} />
      </div>
    </div>
  );
}

export default function AssistantPage() {
  const qc = useQueryClient();
  const { data: ai, isPending: aiPending } = useAiStatus();
  const { data: convos } = useConversations();
  const [active, setActive] = useState<string | null>(null);
  const { data: messages, isPending: msgsPending } = useMessages(active);
  const [text, setText] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }, [messages, sending]);

  const send = async (content: string) => {
    if (!content.trim() || sending) return;
    setError(null);
    setSending(content);
    setText("");
    try {
      let id = active;
      if (!id) {
        const c = await api.post<Conversation>("/assistant/conversations");
        id = c.id;
        setActive(id);
      }
      await api.post(`/assistant/conversations/${id}/messages`, { content });
      await Promise.all([qc.invalidateQueries({ queryKey: keys.messages(id) }), qc.invalidateQueries({ queryKey: keys.conversations })]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The assistant couldn't respond.");
      setText(content);
    } finally {
      setSending(null);
    }
  };
  const remove = useAction((id: string) => api.del(`/assistant/conversations/${id}`), {
    invalidate: [keys.conversations], onSuccess: (_r, id) => { if (id === active) setActive(null); setDeleting(null); },
  });

  if (!aiPending && !ai?.configured) {
    return (
      <>
        <PageHeader title="AI Career Assistant" />
        <Card><EmptyState icon={Plug} title="Connect an AI provider"
          description="The assistant needs an OpenAI-compatible or Anthropic model configured on the server. Everything else in JobPilot keeps working without it."
          action={<Button asChild variant="primary"><Link href="/integrations">View integrations</Link></Button>} /></Card>
      </>
    );
  }

  return (
    <div className="flex h-[calc(100dvh-7.5rem)] flex-col lg:h-[calc(100dvh-3.5rem)]">
      <PageHeader title="AI Career Assistant" description={ai ? `Uses your JobPilot data through read-only tools · ${ai.model}` : undefined}
        actions={<Button onClick={() => setActive(null)}><MessageSquarePlus /> New chat</Button>} />
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Conversations" className="hidden min-h-0 overflow-y-auto lg:block">
          <ul className="flex flex-col gap-0.5">
            {(convos ?? []).map((c) => (
              <li key={c.id} className="group relative">
                <button onClick={() => setActive(c.id)}
                  className={cn("w-full rounded-md px-2 py-1.5 pr-8 text-left", c.id === active ? "bg-surface-3" : "hover:bg-surface-3/60")}>
                  <span className="block truncate text-[13px]">{c.title}</span>
                  <span className="block text-2xs text-fg-subtle">{relativeTime(c.updated_at)}</span>
                </button>
                <Button variant="ghost" size="icon-sm" aria-label={`Delete ${c.title}`} onClick={() => setDeleting(c)}
                  className="absolute top-1.5 right-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"><Trash2 /></Button>
              </li>
            ))}
          </ul>
        </nav>
        <Card className="flex min-h-0 flex-col">
          <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-8" aria-live="polite">
            {!active && !sending ? (
              <div className="mx-auto flex max-w-xl flex-col items-center pt-8 text-center">
                <span className="flex size-10 items-center justify-center rounded-xl bg-accent-soft text-accent-soft-fg"><Bot className="size-5" /></span>
                <h2 className="mt-4 text-base font-semibold">How can I help with your search?</h2>
                <p className="mt-1 text-[13px] text-fg-muted">I can look up your profile, jobs and applications. I can draft — you decide what gets sent or submitted.</p>
                <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => send(s)} className="rounded-lg border border-border px-3 py-2.5 text-left text-[13px] text-fg-muted transition-colors hover:border-border-strong hover:text-fg">{s}</button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto flex max-w-2xl flex-col gap-5">
                {msgsPending && active ? <Skeleton className="h-20" /> : (messages ?? []).map((m) => <Bubble key={m.id} m={m} />)}
                {sending && (
                  <>
                    <Bubble m={{ id: "pending", role: "user", content: sending, tools_used: [], created_at: "" }} />
                    <div className="flex items-center gap-2 text-[13px] text-fg-muted"><Loader2 className="size-4 animate-spin" /> Working — this can take a few seconds while I look things up…</div>
                  </>
                )}
              </div>
            )}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="border-t border-border p-3">
            {error && <Callout tone="danger" className="mb-2">{error}</Callout>}
            <div className="flex items-end gap-2 rounded-lg border border-border bg-surface px-3 py-2 focus-within:border-accent focus-within:ring-3 focus-within:ring-ring">
              <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} maxLength={8000}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
                placeholder="Ask about jobs, your resume, interviews or messages…" aria-label="Message"
                className="max-h-40 min-h-6 flex-1 resize-none bg-transparent text-[13px] outline-none placeholder:text-fg-subtle field-sizing-content" />
              <Tooltip content="Send (Enter)">
                <Button type="submit" size="icon-sm" variant="primary" aria-label="Send" disabled={!text.trim() || !!sending}><ArrowUp /></Button>
              </Tooltip>
            </div>
            <p className="mt-1.5 text-center text-2xs text-fg-subtle">AI can be wrong. Facts about you come from your verified profile; check anything before you use it.</p>
          </form>
        </Card>
      </div>
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} destructive confirmLabel="Delete"
        title="Delete conversation?" description="This removes the conversation permanently." loading={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting.id)} />
    </div>
  );
}
