"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Copy, Download, KeyRound, LogOut, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "@/lib/toast";
import { ProfileEditor } from "@/components/settings/profile-editor";
import { Button } from "@/components/ui/button";
import { Checkbox, Segmented, Switch, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/controls";
import { ConfirmDialog, Dialog, DialogContent } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, Input, NativeSelect, Skeleton } from "@/components/ui/primitives";
import { Callout, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn, formatDateTime, relativeTime } from "@/lib/format";
import { keys, useAction, useAiStatus, useMe, useSettings, useTokens } from "@/lib/queries";
import { APPRENTICE_CURRENCIES, type ApiToken, type Settings } from "@/lib/types";

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
  // Normalised (out-of-range) stored values can be saved as-is to repair them.
  return { d, set, save, dirty: (!!edited && JSON.stringify(edited) !== JSON.stringify(data)) || !!data?.normalized?.length };
}

function SaveBar({ dirty, loading, onSave }: { dirty: boolean; loading: boolean; onSave: () => void }) {
  return <div className="flex justify-end border-t border-border bg-surface-2 px-4 py-2.5 rounded-b-lg"><Button variant="primary" size="sm" disabled={!dirty} loading={loading} onClick={onSave}>Save changes</Button></div>;
}

// Mirrors the API limits (backend/app/domain/salary_policy.py); the API enforces them.
const MAX_MIN_CTC_LPA = 200;
const MAX_DAILY = 10_000;
const ctcValid = (v: number) => Number.isFinite(v) && v >= 0 && v <= MAX_MIN_CTC_LPA;

function Policies() {
  const { d, set, save, dirty } = useSettingsForm();
  if (!d) return <Skeleton className="h-80" />;
  const num = (v: string) => (v === "" ? 0 : Number(v));
  const normalized = d.normalized ?? [];
  return (
    <div className="flex flex-col gap-5">
      {normalized.length > 0 && (
        <Callout tone="warning" title="Some saved settings were out of range">
          <ul className="list-disc pl-4">
            {normalized.map((n) => <li key={n.field}><span className="font-mono text-2xs">{n.field}</span>: saved {String(n.stored)}, using {String(n.effective)} ({n.reason}).</li>)}
          </ul>
          <p className="mt-1">These values are already in effect. Save to store them.</p>
        </Callout>
      )}
      <Card>
        <CardHeader title="Full-time salary policy" description="Applied to every new eligibility decision. Pay is converted to annual INR first; pay in another currency is only converted with a fresh, documented exchange rate, otherwise it goes to manual review." />
        <Row title="Minimum annual CTC" description={<>A job is eligible on salary only if its <span className="font-medium text-fg">verified minimum</span> is strictly greater than this. ₹ lakh per annum (LPA), total annual CTC; 0–{MAX_MIN_CTC_LPA} LPA, up to 2 decimals.</>}>
          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-2">
              <span className="text-xs text-fg-muted">₹</span>
              <Input id="min-ctc" type="number" inputMode="decimal" min={0} max={MAX_MIN_CTC_LPA} step={0.01} className="w-24" aria-label="Minimum annual CTC in lakh rupees" aria-describedby="min-ctc-hint"
                aria-invalid={!ctcValid(d.min_ctc_lpa)} value={Number.isNaN(d.min_ctc_lpa) ? "" : d.min_ctc_lpa}
                onChange={(e) => set("min_ctc_lpa", e.target.value === "" ? Number.NaN : Number(e.target.value))} />
              <span className="text-xs text-fg-muted">LPA / year</span>
            </div>
            <p id="min-ctc-hint" className={cn("text-2xs", ctcValid(d.min_ctc_lpa) ? "text-fg-subtle" : "text-danger")} role={ctcValid(d.min_ctc_lpa) ? undefined : "alert"}>
              {ctcValid(d.min_ctc_lpa) ? `₹${d.min_ctc_lpa} LPA = ₹${Math.round(d.min_ctc_lpa * 100000).toLocaleString("en-IN")} a year` : `Enter a number from 0 to ${MAX_MIN_CTC_LPA}.`}
            </p>
          </div>
        </Row>
        <p className="border-t border-border px-4 py-3 text-xs text-fg-muted">Changing this applies to new decisions. Jobs you&apos;ve saved or applied to keep the decision they were made with until you choose <span className="font-medium text-fg">Re-evaluate</span> on the job.</p>
        <Row title="Allow jobs with undisclosed salary" description="Most listings don't publish pay. Turning this off sends them to manual review.">
          <Switch aria-label="Allow jobs with undisclosed salary" checked={d.allow_undisclosed_salary} onCheckedChange={(v) => set("allow_undisclosed_salary", v)} />
        </Row>
        <Row title="Advertised but unverified pay" description="Pay mentioned only in description text always needs your review."><Badge tone="warning">Manual review</Badge></Row>
      </Card>
      <Card>
        <CardHeader title="Internship stipend policy" description="Separate from full-time CTC rules." />
        <Row title="Minimum monthly stipend" description="Leave empty for no minimum.">
          <div className="flex items-center gap-2"><span className="text-xs text-fg-muted">₹</span><Input type="number" min={0} className="w-28" aria-label="Minimum monthly stipend in rupees" value={d.min_stipend_monthly_inr ?? ""} onChange={(e) => set("min_stipend_monthly_inr", e.target.value === "" ? null : Number(e.target.value))} /><span className="text-xs text-fg-muted">/ month</span></div>
        </Row>
        <Row title="Allow internships with undisclosed stipend" description="Also applies to apprenticeships whose listing calls the pay a stipend."><Switch aria-label="Allow internships with undisclosed stipend" checked={d.allow_undisclosed_stipend} onCheckedChange={(v) => set("allow_undisclosed_stipend", v)} /></Row>
      </Card>
      <Card>
        <CardHeader title="Apprenticeship stipend policy"
          description="Used only when an apprenticeship listing calls its pay a stipend. Pay called a salary, CTC or wages uses your salary threshold; pay that isn't called either needs your review." />
        <Row title="Minimum stipend" description="Leave empty for no minimum. A stipend that meets or exceeds it passes.">
          <div className="flex items-center gap-2">
            <NativeSelect aria-label="Apprenticeship stipend currency" className="w-20" value={d.apprentice_stipend_currency ?? ""}
              onChange={(e) => set("apprentice_stipend_currency", e.target.value as Settings["apprentice_stipend_currency"])}>
              {d.apprentice_stipend_currency == null && <option value="" disabled>Choose…</option>}
              {APPRENTICE_CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </NativeSelect>
            <Input type="number" min={0} className="w-28" aria-label="Minimum apprenticeship stipend" value={d.apprentice_min_stipend ?? ""}
              onChange={(e) => set("apprentice_min_stipend", e.target.value === "" ? null : Number(e.target.value))} />
            <NativeSelect aria-label="Apprenticeship stipend period" className="w-28" value={d.apprentice_stipend_period}
              onChange={(e) => set("apprentice_stipend_period", e.target.value as Settings["apprentice_stipend_period"])}>
              <option value="month">per month</option><option value="year">per year</option>
            </NativeSelect>
          </div>
        </Row>
        {d.apprentice_stipend_currency == null && (
          <div className="px-4 pb-3"><Callout tone="warning" icon={AlertTriangle}>The saved currency isn&apos;t valid, so the minimum can&apos;t be compared. Choose a currency and save.</Callout></div>
        )}
        <Row title="Stipends stated only in the description" description="Advertised amounts are never shown as employer-verified.">
          <Segmented ariaLabel="Stipends stated only in the description" value={d.apprentice_advertised_stipend}
            onChange={(v) => set("apprentice_advertised_stipend", v)}
            options={[{ value: "compare", label: "Compare with minimum" }, { value: "review", label: "Always review" }]} />
        </Row>
        <Row title="Unverified stipend that meets the minimum" description="Below the minimum is rejected either way.">
          <Segmented ariaLabel="Unverified stipend that meets the minimum" value={d.apprentice_unverified_outcome}
            onChange={(v) => set("apprentice_unverified_outcome", v)}
            options={[{ value: "pass", label: "Pass (marked unverified)" }, { value: "review", label: "Needs review" }]} />
        </Row>
      </Card>
      <Card>
        <CardHeader title="Applications" />
        <Row title="Daily application limit" description={`Maximum submissions started in any rolling 24 hours (1–${MAX_DAILY.toLocaleString("en-IN")}). Automations respect it too. Every application still needs your approval.`}>
          <Input type="number" min={1} max={MAX_DAILY} className="w-24" aria-label="Daily application limit" value={d.daily_application_limit} onChange={(e) => set("daily_application_limit", Math.min(MAX_DAILY, Math.max(1, Math.round(num(e.target.value)))))} />
        </Row>
        <Row title="Matching threshold" description="Jobs at or above this score count as matching on your dashboard.">
          <Input type="number" min={0} max={100} className="w-20" aria-label="Matching threshold score" value={d.min_match_score} onChange={(e) => set("min_match_score", num(e.target.value))} />
        </Row>
        <Row title="Require approval before submitting" description="Always on. Only you can approve, here in JobPilot — AI assistants and API tokens can ask for approval but never grant it."><Switch checked disabled aria-label="Approval required (always on)" /></Row>
        <SaveBar dirty={dirty && ctcValid(d.min_ctc_lpa)} loading={save.isPending} onSave={() => save.mutate(undefined)} />
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
              <p className="text-xs text-fg-muted">No token can approve or submit applications — those always need you, here in JobPilot.</p>
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
