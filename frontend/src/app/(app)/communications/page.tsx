"use client";

import { Copy, Inbox, Mail, NotebookPen, PenSquare, RefreshCw, Send, Sparkles } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/controls";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, Input, NativeSelect, Textarea } from "@/components/ui/primitives";
import { Callout, EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { relativeTime, type Tone } from "@/lib/format";
import { keys, useAction, useAiStatus, useApplications, useCommunications, useIntegrations } from "@/lib/queries";

const CATEGORY: Record<string, { label: string; tone: Tone }> = {
  interview_invitation: { label: "Interview", tone: "accent" },
  offer: { label: "Offer", tone: "positive" },
  rejection: { label: "Rejection", tone: "danger" },
  assessment: { label: "Assessment", tone: "info" },
  application_confirmation: { label: "Confirmation", tone: "neutral" },
  recruiter_outreach: { label: "Recruiter", tone: "accent" },
  ats_update: { label: "ATS update", tone: "neutral" },
  follow_up: { label: "Follow-up", tone: "neutral" },
  referral_request: { label: "Referral", tone: "neutral" },
  note: { label: "Note", tone: "neutral" },
};
const KINDS = [
  { value: "follow_up", label: "Follow-up email" },
  { value: "thank_you", label: "Thank-you after interview" },
  { value: "referral_request", label: "Referral request" },
  { value: "recruiter_message", label: "Message to a recruiter" },
  { value: "linkedin_connection", label: "LinkedIn connection note" },
  { value: "linkedin_follow_up", label: "LinkedIn follow-up" },
];

function Composer({ initialKind, initialApp }: { initialKind: string; initialApp: string }) {
  const { data: apps } = useApplications();
  const { data: ai } = useAiStatus();
  const { data: integrations } = useIntegrations();
  const gmail = integrations?.find((i) => i.provider === "gmail");
  const [kind, setKind] = useState(initialKind);
  const [appId, setAppId] = useState(initialApp);
  const [recipient, setRecipient] = useState("");
  const [context, setContext] = useState("");
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const linkedin = kind.startsWith("linkedin");
  const generate = useAction(() => api.post<{ subject: string; body: string; error?: string }>("/communications/draft", {
    kind, application_id: appId || null, recipient_name: recipient || null, context: context || null,
  }), { onSuccess: (r) => { if (r.error) toast.error(r.error); else { setSubject(r.subject); setBody(r.body); } } });
  const toGmail = useAction(() => api.post("/integrations/gmail/drafts", { to, subject, body, application_id: appId || null }), {
    invalidate: [["communications"]], success: "Saved to your Gmail drafts — review and send it from Gmail",
  });
  const log = useAction(() => api.post("/communications", {
    channel: linkedin ? "linkedin" : "email", direction: "outbound", application_id: appId || null, subject: subject || null,
    body, counterpart: recipient || to || null, category: kind === "referral_request" ? "referral_request" : "follow_up",
  }), { invalidate: [["communications"]], success: "Logged as sent" });

  return (
    <Card>
      <CardHeader title="Draft a message" description="JobPilot drafts; you send. Nothing goes out automatically." />
      <div className="grid gap-3 px-4 pb-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Type" htmlFor="kind"><NativeSelect id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</NativeSelect></Field>
          <Field label="Application" htmlFor="app" optional>
            <NativeSelect id="app" value={appId} onChange={(e) => setAppId(e.target.value)}>
              <option value="">None</option>
              {(apps ?? []).map((a) => <option key={a.id} value={a.id}>{a.job.title} — {a.job.company}</option>)}
            </NativeSelect>
          </Field>
          <Field label="Recipient name" htmlFor="rn" optional><Input id="rn" value={recipient} onChange={(e) => setRecipient(e.target.value)} /></Field>
          {!linkedin && <Field label="Recipient email" htmlFor="to" optional><Input id="to" type="email" value={to} onChange={(e) => setTo(e.target.value)} /></Field>}
        </div>
        <Field label="Anything to mention?" htmlFor="ctx" optional><Textarea id="ctx" rows={2} value={context} onChange={(e) => setContext(e.target.value)} placeholder="e.g. we met at the PyCon meetup; interviewer was Priya" /></Field>
        <div>
          <Button onClick={() => generate.mutate(undefined)} loading={generate.isPending} disabled={!ai?.configured}><Sparkles /> Generate draft</Button>
          {!ai?.configured && <span className="ml-3 text-xs text-fg-muted">Needs an AI provider — or write your own below.</span>}
        </div>
        {!linkedin && <Field label="Subject" htmlFor="subj"><Input id="subj" value={subject} onChange={(e) => setSubject(e.target.value)} /></Field>}
        <Field label="Message" htmlFor="body" hint={linkedin && kind === "linkedin_connection" ? `${body.length}/300 characters` : undefined}>
          <Textarea id="body" rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button disabled={!body} onClick={() => { navigator.clipboard.writeText(linkedin ? body : `Subject: ${subject}\n\n${body}`); toast.success("Copied"); }}><Copy /> Copy</Button>
          {!linkedin && (
            <Button disabled={!body || !to || gmail?.status !== "connected"} loading={toGmail.isPending} onClick={() => toGmail.mutate(undefined)}
              title={gmail?.status !== "connected" ? "Connect Gmail in Integrations" : undefined}><Mail /> Save to Gmail drafts</Button>
          )}
          <Button variant="ghost" disabled={!body} loading={log.isPending} onClick={() => log.mutate(undefined)}><Send /> I sent this — log it</Button>
        </div>
      </div>
    </Card>
  );
}

function LogDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { data: apps } = useApplications();
  const [v, setV] = useState({ channel: "email", direction: "inbound", application_id: "", subject: "", body: "", counterpart: "", category: "recruiter_outreach" });
  const save = useAction(() => api.post("/communications", { ...v, application_id: v.application_id || null, subject: v.subject || null, counterpart: v.counterpart || null }), {
    invalidate: [["communications"], keys.dashboard], success: "Logged", onSuccess: () => onOpenChange(false),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Log a message" description="Record a call, LinkedIn message or email that isn't synced." wide
        footer={<Button variant="primary" disabled={!v.body.trim()} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button>}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Channel"><NativeSelect value={v.channel} onChange={(e) => setV({ ...v, channel: e.target.value })}><option value="email">Email</option><option value="linkedin">LinkedIn</option><option value="phone">Phone</option><option value="other">Other</option></NativeSelect></Field>
          <Field label="Direction"><NativeSelect value={v.direction} onChange={(e) => setV({ ...v, direction: e.target.value })}><option value="inbound">Received</option><option value="outbound">Sent</option></NativeSelect></Field>
          <Field label="Type"><NativeSelect value={v.category} onChange={(e) => setV({ ...v, category: e.target.value })}>
            {["recruiter_outreach", "interview_invitation", "assessment", "offer", "rejection", "follow_up", "referral_request", "note"].map((c) => <option key={c} value={c}>{CATEGORY[c].label}</option>)}
          </NativeSelect></Field>
          <Field label="Application" optional><NativeSelect value={v.application_id} onChange={(e) => setV({ ...v, application_id: e.target.value })}><option value="">None</option>{(apps ?? []).map((a) => <option key={a.id} value={a.id}>{a.job.title} — {a.job.company}</option>)}</NativeSelect></Field>
          <Field label="With" optional><Input value={v.counterpart} onChange={(e) => setV({ ...v, counterpart: e.target.value })} placeholder="Name or email" /></Field>
          <Field label="Subject" optional><Input value={v.subject} onChange={(e) => setV({ ...v, subject: e.target.value })} /></Field>
          <Field label="Message or notes" className="sm:col-span-2"><Textarea rows={4} value={v.body} onChange={(e) => setV({ ...v, body: e.target.value })} /></Field>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function CommsInner() {
  const params = useSearchParams();
  const [view, setView] = useState<"inbox" | "compose">(params.get("compose") ? "compose" : "inbox");
  const [category, setCategory] = useState("");
  const [logOpen, setLogOpen] = useState(false);
  const { data, isPending, error, refetch } = useCommunications({ category: category || undefined });
  const { data: integrations } = useIntegrations();
  const gmail = integrations?.find((i) => i.provider === "gmail");
  const sync = useAction(() => api.post<{ scanned: number; stored: number; acknowledged: number }>("/integrations/gmail/sync"), {
    invalidate: [["communications"], keys.applications, keys.dashboard],
    success: (r) => `Scanned ${r.scanned} emails · ${r.stored} job-related${r.acknowledged ? ` · ${r.acknowledged} submissions confirmed` : ""}`,
  });

  return (
    <>
      <PageHeader title="Communications" description="Recruiter replies, interview invites and your outreach — linked to applications."
        actions={<>
          <Button onClick={() => setLogOpen(true)}><NotebookPen /> Log message</Button>
          {gmail?.status === "connected" && <Button onClick={() => sync.mutate(undefined)} loading={sync.isPending}><RefreshCw /> Sync Gmail</Button>}
          <Segmented ariaLabel="View" value={view} onChange={setView} options={[{ value: "inbox", label: "Inbox" }, { value: "compose", label: "Compose" }]} />
        </>} />
      {view === "compose" ? (
        <div className="max-w-3xl"><Composer initialKind={params.get("compose") ?? "follow_up"} initialApp={params.get("application") ?? ""} /></div>
      ) : (
        <div className="flex flex-col gap-4">
          {gmail && gmail.status !== "connected" && (
            <Callout tone="neutral" icon={Mail} title="Connect Gmail to track replies automatically"
              action={<Button asChild size="sm"><Link href="/integrations">Set up</Link></Button>}>
              JobPilot reads only job-related message metadata, links replies to applications and confirms submissions from employer emails.
            </Callout>
          )}
          <div className="flex flex-wrap gap-1.5">
            {["", "interview_invitation", "recruiter_outreach", "assessment", "offer", "rejection", "application_confirmation"].map((c) => (
              <button key={c || "all"} onClick={() => setCategory(c)} aria-pressed={category === c}
                className={`h-7 rounded-full border px-2.5 text-xs font-medium ${category === c ? "border-accent bg-accent-soft text-accent-soft-fg" : "border-border text-fg-muted hover:text-fg"}`}>
                {c ? CATEGORY[c].label : "All"}
              </button>
            ))}
          </div>
          <Card>
            {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isPending ? <ListSkeleton /> : !data.length ? (
              <EmptyState icon={Inbox} title="No messages yet" description="Synced Gmail messages and anything you log will appear here."
                action={<Button onClick={() => setView("compose")}><PenSquare /> Draft a message</Button>} />
            ) : (
              <ul className="divide-y divide-border">
                {data.map((c) => (
                  <li key={c.id} className="flex gap-3 px-4 py-3">
                    <Badge tone={CATEGORY[c.category]?.tone ?? "neutral"} className="mt-0.5 w-24 justify-center">{CATEGORY[c.category]?.label ?? c.category}</Badge>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium">{c.subject ?? (c.direction === "inbound" ? `From ${c.sender ?? "unknown"}` : `To ${c.recipient ?? "unknown"}`)}</p>
                      <p className="line-clamp-2 text-xs text-fg-muted">{c.snippet ?? c.body}</p>
                      <p className="mt-1 text-2xs text-fg-subtle">
                        {c.channel} · {c.direction === "inbound" ? c.sender : c.direction === "draft" ? "draft" : "sent"}
                        {c.application_id && <> · <Link href={`/applications/${c.application_id}`} className="hover:underline">{c.job_title} at {c.company}</Link></>}
                      </p>
                    </div>
                    <span className="shrink-0 text-xs text-fg-subtle">{relativeTime(c.occurred_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
      <LogDialog open={logOpen} onOpenChange={setLogOpen} />
    </>
  );
}

export default function CommunicationsPage() {
  return <Suspense><CommsInner /></Suspense>;
}
