"use client";

import { useQuery } from "@tanstack/react-query";
import { Copy, Download, KeyRound, LogOut, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "@/lib/toast";
import { ProfileEditor } from "@/components/settings/profile-editor";
import { Button } from "@/components/ui/button";
import { Checkbox, Switch, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/controls";
import { ConfirmDialog, Dialog, DialogContent } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, Input, Skeleton } from "@/components/ui/primitives";
import { Callout, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { keys, useAction, useAiStatus, useMe, useSettings, useTokens } from "@/lib/queries";
import type { ApiToken, Settings } from "@/lib/types";

function Row({ title, description, children }: { title: string; description?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 border-t border-border px-4 py-3.5 first:border-t-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="max-w-md"><p className="text-[13px] font-medium">{title}</p>{description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}</div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function useSettingsForm() {
  const { data } = useSettings();
  // Local edits layered over server data; cleared after a successful save.
  const [edited, setEdited] = useState<Settings | null>(null);
  const d = edited ?? data ?? null;
  const save = useAction(() => api.put<Settings>("/settings", d), {
    invalidate: [keys.settings, keys.dashboard, ["jobs"]], success: "Settings saved", onSuccess: () => setEdited(null),
  });
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => d && setEdited({ ...d, [k]: v });
  return { d, set, save, dirty: !!edited && JSON.stringify(edited) !== JSON.stringify(data) };
}

function SaveBar({ dirty, loading, onSave }: { dirty: boolean; loading: boolean; onSave: () => void }) {
  return <div className="flex justify-end border-t border-border bg-surface-2 px-4 py-2.5 rounded-b-lg"><Button variant="primary" size="sm" disabled={!dirty} loading={loading} onClick={onSave}>Save changes</Button></div>;
}

function Policies() {
  const { d, set, save, dirty } = useSettingsForm();
  if (!d) return <Skeleton className="h-80" />;
  const num = (v: string) => (v === "" ? 0 : Number(v));
  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Full-time salary policy" description="Applied to every job's eligibility. Disclosed pay is compared in INR; other currencies go to manual review." />
        <Row title="Minimum verified CTC" description="Jobs whose verified maximum is below this are marked outside your criteria.">
          <div className="flex items-center gap-2"><span className="text-xs text-fg-muted">₹</span><Input type="number" min={0} className="w-24" value={d.min_ctc_lpa} onChange={(e) => set("min_ctc_lpa", num(e.target.value))} /><span className="text-xs text-fg-muted">LPA</span></div>
        </Row>
        <Row title="Allow jobs with undisclosed salary" description="Most listings don't publish pay. Turning this off sends them to manual review.">
          <Switch checked={d.allow_undisclosed_salary} onCheckedChange={(v) => set("allow_undisclosed_salary", v)} />
        </Row>
        <Row title="Advertised but unverified pay" description="Pay mentioned only in description text always needs your review."><Badge tone="warning">Manual review</Badge></Row>
      </Card>
      <Card>
        <CardHeader title="Internship stipend policy" description="Separate from full-time CTC rules." />
        <Row title="Minimum monthly stipend" description="Leave empty for no minimum.">
          <div className="flex items-center gap-2"><span className="text-xs text-fg-muted">₹</span><Input type="number" min={0} className="w-28" value={d.min_stipend_monthly_inr ?? ""} onChange={(e) => set("min_stipend_monthly_inr", e.target.value === "" ? null : Number(e.target.value))} /><span className="text-xs text-fg-muted">/ month</span></div>
        </Row>
        <Row title="Allow internships with undisclosed stipend"><Switch checked={d.allow_undisclosed_stipend} onCheckedChange={(v) => set("allow_undisclosed_stipend", v)} /></Row>
      </Card>
      <Card>
        <CardHeader title="Applications" />
        <Row title="Daily application limit" description="Maximum submissions started in any rolling 24 hours. Automations respect it too.">
          <Input type="number" min={1} max={100} className="w-20" value={d.daily_application_limit} onChange={(e) => set("daily_application_limit", Math.max(1, num(e.target.value)))} />
        </Row>
        <Row title="Matching threshold" description="Jobs at or above this score count as matching on your dashboard.">
          <Input type="number" min={0} max={100} className="w-20" value={d.min_match_score} onChange={(e) => set("min_match_score", num(e.target.value))} />
        </Row>
        <Row title="Require approval before submitting" description="Always on. JobPilot never submits without your explicit approval."><Switch checked disabled aria-label="Approval required (always on)" /></Row>
        <SaveBar dirty={dirty} loading={save.isPending} onSave={() => save.mutate(undefined)} />
      </Card>
    </div>
  );
}

function Notifications() {
  const { d, set, save, dirty } = useSettingsForm();
  if (!d) return <Skeleton className="h-60" />;
  return (
    <Card>
      <Row title="In-app notifications" description="Always on — approval requests and submission results must reach you."><Badge tone="positive">On</Badge></Row>
      <Row title="Daily summary" description="A digest of new matches and pending actions."><Switch checked={d.daily_summary} onCheckedChange={(v) => set("daily_summary", v)} /></Row>
      <Row title="WhatsApp notifications" description="Approvals, submission results, failures and interview reminders. Requires WhatsApp to be configured on the server.">
        <Switch checked={d.notify_whatsapp} onCheckedChange={(v) => set("notify_whatsapp", v)} />
      </Row>
      <Row title="WhatsApp number" description="International format, e.g. +919876543210.">
        <Input className="w-48" value={d.whatsapp_number ?? ""} onChange={(e) => set("whatsapp_number", e.target.value || null)} placeholder="+91…" />
      </Row>
      <SaveBar dirty={dirty} loading={save.isPending} onSave={() => save.mutate(undefined)} />
    </Card>
  );
}

function AiSettings() {
  const { data: ai } = useAiStatus();
  const { d, set, save, dirty } = useSettingsForm();
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api.get<{ spend_24h_usd: number; daily_budget_usd: number; by_feature: { feature: string; model: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number | null }[] }>("/ai/usage") });
  return (
    <div className="flex flex-col gap-5">
      <Card>
        <Row title="Provider" description="Configured on the server (environment variables). API keys are never sent to the browser.">
          {ai?.configured ? <Badge tone="positive" dot>{ai.provider} · {ai.model}</Badge> : <Badge tone="warning">Not configured</Badge>}
        </Row>
        <Row title="Model override" description="Optional: use a different model from the same provider for your account.">
          <Input className="w-56" value={d?.ai_model_override ?? ""} onChange={(e) => set("ai_model_override", e.target.value || null)} placeholder={ai?.model ?? "default"} />
        </Row>
        <SaveBar dirty={dirty} loading={save.isPending} onSave={() => save.mutate(undefined)} />
      </Card>
      <Card>
        <CardHeader title="Usage (last 30 days)" description={usage.data ? `$${usage.data.spend_24h_usd.toFixed(3)} spent in the last 24h of a $${usage.data.daily_budget_usd.toFixed(2)} daily budget` : undefined} />
        {!usage.data?.by_feature.length ? <p className="px-4 pb-4 text-[13px] text-fg-muted">No AI usage yet.</p> : (
          <table className="w-full border-t border-border text-[13px]">
            <thead className="text-xs text-fg-muted"><tr><th className="px-4 py-2 text-left font-medium">Feature</th><th className="px-3 text-right font-medium">Calls</th><th className="px-3 text-right font-medium">Tokens</th><th className="px-4 text-right font-medium">Cost</th></tr></thead>
            <tbody className="divide-y divide-border">
              {usage.data.by_feature.map((f) => (
                <tr key={f.feature + f.model}><td className="px-4 py-2">{f.feature.replace(/_/g, " ")} <span className="text-xs text-fg-subtle">{f.model}</span></td>
                  <td className="tabular px-3 text-right">{f.calls}</td><td className="tabular px-3 text-right">{(f.input_tokens + f.output_tokens).toLocaleString()}</td>
                  <td className="tabular px-4 text-right">{f.cost_usd == null ? "—" : `$${f.cost_usd.toFixed(4)}`}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

const SCOPES = [
  { value: "jobs:write", label: "Manage job sources and saved jobs" },
  { value: "resumes:write", label: "Create tailored resume drafts" },
  { value: "applications:write", label: "Create and prepare applications" },
  { value: "approvals:write", label: "Approve or reject applications" },
  { value: "automation:write", label: "Pause and resume automations" },
];

function Security() {
  const router = useRouter();
  const { data: tokens } = useTokens();
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.get<{ id: string; actor: string; action: string; created_at: string }[]>("/audit", { limit: 30 }) });
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [tokenOpen, setTokenOpen] = useState(false);
  const [tokenName, setTokenName] = useState("Claude Desktop");
  const [scopes, setScopes] = useState<string[]>([]);
  const [created, setCreated] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const [logoutAll, setLogoutAll] = useState(false);
  const changePw = useAction(() => api.post("/auth/password", pw), { success: "Password changed. Other sessions were signed out.", onSuccess: () => setPw({ current_password: "", new_password: "" }) });
  const create = useAction(() => api.post<ApiToken>("/auth/tokens", { name: tokenName, scopes }), { invalidate: [keys.tokens], onSuccess: (t) => setCreated(t.token ?? null) });
  const revoke = useAction((id: string) => api.del(`/auth/tokens/${id}`), { invalidate: [keys.tokens], success: "Token revoked", onSuccess: () => setRevoking(null) });
  const signOutAll = useAction(() => api.post("/auth/logout-all"), { onSuccess: () => router.replace("/login") });
  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Password" />
        <form className="grid gap-3 px-4 pb-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); changePw.mutate(undefined); }}>
          <Field label="Current password"><Input type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label="New password"><Input type="password" autoComplete="new-password" minLength={10} value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
          <Button type="submit" loading={changePw.isPending} disabled={!pw.current_password || pw.new_password.length < 10}>Update</Button>
        </form>
      </Card>
      <Card>
        <CardHeader title="API tokens" description="For the JobPilot MCP server and other trusted tools. Tokens can never submit applications or change account settings."
          action={<Button size="sm" onClick={() => { setTokenOpen(true); setCreated(null); }}><KeyRound /> New token</Button>} />
        {!tokens?.length ? <p className="px-4 pb-4 text-[13px] text-fg-muted">No tokens.</p> : (
          <ul className="divide-y divide-border border-t border-border">
            {tokens.map((t) => (
              <li key={t.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium">{t.name} <span className="font-mono text-xs text-fg-subtle">{t.prefix}…</span></p>
                  <p className="text-xs text-fg-muted">{t.scopes.join(", ")} · last used {t.last_used_at ? relativeTime(t.last_used_at) : "never"}</p>
                </div>
                <Button variant="danger-ghost" size="sm" onClick={() => setRevoking(t)}>Revoke</Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card>
        <Row title="Sign out everywhere" description="Ends every session, including this one."><Button onClick={() => setLogoutAll(true)}><LogOut /> Sign out all sessions</Button></Row>
      </Card>
      <Card>
        <CardHeader title="Recent security & activity log" />
        <ul className="divide-y divide-border border-t border-border">
          {(audit.data ?? []).map((e) => (
            <li key={e.id} className="flex items-center gap-3 px-4 py-2 text-[13px]">
              <span className="flex-1 font-mono text-xs">{e.action}</span><Badge>{e.actor}</Badge>
              <span className="w-32 text-right text-xs text-fg-subtle">{formatDateTime(e.created_at)}</span>
            </li>
          ))}
        </ul>
      </Card>
      <Dialog open={tokenOpen} onOpenChange={setTokenOpen}>
        <DialogContent title={created ? "Copy your token" : "New API token"} description={created ? "It won't be shown again." : "Read access is always included. Grant only what you need."}
          footer={created ? <Button variant="primary" onClick={() => setTokenOpen(false)}>Done</Button> : <Button variant="primary" loading={create.isPending} disabled={!tokenName.trim()} onClick={() => create.mutate(undefined)}>Create token</Button>}>
          {created ? (
            <div className="flex gap-2"><Input readOnly value={created} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
              <Button onClick={() => { navigator.clipboard.writeText(created); toast.success("Copied"); }}><Copy /></Button></div>
          ) : (
            <div className="grid gap-3">
              <Field label="Name"><Input value={tokenName} onChange={(e) => setTokenName(e.target.value)} /></Field>
              <fieldset className="grid gap-2">
                <legend className="mb-1 text-xs font-medium">Permissions</legend>
                {SCOPES.map((s) => (
                  <label key={s.value} className="flex items-center gap-2 text-[13px]">
                    <Checkbox checked={scopes.includes(s.value)} onCheckedChange={(v) => setScopes(v ? [...scopes, s.value] : scopes.filter((x) => x !== s.value))} />
                    {s.label} <span className="font-mono text-2xs text-fg-subtle">{s.value}</span>
                  </label>
                ))}
              </fieldset>
              {scopes.includes("approvals:write") && <Callout tone="warning">An AI client with this scope can approve applications when you ask it to. Submission still happens only in JobPilot.</Callout>}
            </div>
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog open={!!revoking} onOpenChange={(o) => !o && setRevoking(null)} destructive confirmLabel="Revoke" title={`Revoke “${revoking?.name}”?`}
        description="Any client using it stops working immediately." loading={revoke.isPending} onConfirm={() => revoking && revoke.mutate(revoking.id)} />
      <ConfirmDialog open={logoutAll} onOpenChange={setLogoutAll} confirmLabel="Sign out all" title="Sign out of every session?" description="You'll need to sign in again." loading={signOutAll.isPending} onConfirm={() => signOutAll.mutate(undefined)} />
    </div>
  );
}

function DataTab() {
  const router = useRouter();
  const { data: me } = useMe();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [password, setPassword] = useState("");
  const [exporting, setExporting] = useState(false);
  const del = useAction(() => api.post("/account/delete", { confirm_email: confirm, password }), { onSuccess: () => router.replace("/") });
  const exportData = async () => {
    setExporting(true);
    try {
      const data = await api.get<object>("/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: `jobpilot-export-${new Date().toISOString().slice(0, 10)}.json` });
      a.click();
      URL.revokeObjectURL(url);
    } finally { setExporting(false); }
  };
  return (
    <Card>
      <Row title="Export your data" description="Profile, resumes metadata, applications, messages, assistant chats and audit log as JSON."><Button loading={exporting} onClick={exportData}><Download /> Export</Button></Row>
      <Row title="Delete account" description="Permanently deletes your account, files and every record. This can't be undone."><Button variant="danger" onClick={() => setOpen(true)}><Trash2 /> Delete account</Button></Row>
      <ConfirmDialog open={open} onOpenChange={setOpen} destructive confirmLabel="Delete everything" title="Delete your JobPilot account?"
        description={<>Type <span className="font-medium text-fg">{me?.email}</span> and your password to confirm.</>} loading={del.isPending}
        onConfirm={() => confirm.trim().toLowerCase() === me?.email ? del.mutate(undefined) : toast.error("The email doesn't match.")}>
        <Input className="mt-3" value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-label="Confirm email" placeholder="Account email" />
        <Input className="mt-2" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} aria-label="Current password" placeholder="Current password" />
      </ConfirmDialog>
    </Card>
  );
}

function SettingsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") ?? "profile";
  return (
    <>
      <PageHeader title="Settings" />
      <Tabs value={tab} onValueChange={(v) => router.replace(`/settings?tab=${v}`, { scroll: false })}>
        <TabsList className="mb-5 overflow-x-auto">
          {[["profile", "Profile"], ["policies", "Job policies"], ["notifications", "Notifications"], ["ai", "AI"], ["security", "Security"], ["data", "Data"]].map(([v, l]) => (
            <TabsTrigger key={v} value={v}>{l}</TabsTrigger>
          ))}
        </TabsList>
        <div className="max-w-4xl">
          <TabsContent value="profile"><ProfileEditor /></TabsContent>
          <TabsContent value="policies"><Policies /></TabsContent>
          <TabsContent value="notifications"><Notifications /></TabsContent>
          <TabsContent value="ai"><AiSettings /></TabsContent>
          <TabsContent value="security"><Security /></TabsContent>
          <TabsContent value="data"><DataTab /></TabsContent>
        </div>
      </Tabs>
    </>
  );
}

export default function SettingsPage() {
  return <Suspense><SettingsInner /></Suspense>;
}
