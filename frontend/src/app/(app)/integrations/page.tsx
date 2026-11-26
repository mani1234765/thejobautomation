"use client";

import { Bot, Copy, Mail, MessageCircle, Plug, ShieldCheck, Terminal } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Badge, Card, Skeleton } from "@/components/ui/primitives";
import { Callout, ErrorState, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { keys, useAction, useIntegrations } from "@/lib/queries";
import type { IntegrationInfo } from "@/lib/types";

const ICON = { gmail: Mail, whatsapp: MessageCircle, ai: Bot } as const;
const DESC: Record<IntegrationInfo["provider"], string> = {
  gmail: "Track recruiter replies and interview invites, confirm submissions from employer emails, and save drafts to Gmail.",
  whatsapp: "Approval requests, submission results, failures and interview reminders on WhatsApp (via the WhatsApp Business Cloud API).",
  ai: "Powers the assistant, resume rewording, answer drafts, cover letters and interview feedback. Matching works without it.",
};

const MCP_CONFIG = `{
  "mcpServers": {
    "jobpilot": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/jobpilot/mcp-server", "python", "jobpilot_mcp.py"],
      "env": {
        "JOBPILOT_API_URL": "http://localhost:8000",
        "JOBPILOT_API_TOKEN": "jp_…"
      }
    }
  }
}`;

function IntegrationCard({ i }: { i: IntegrationInfo }) {
  const Icon = ICON[i.provider];
  const inv = [keys.integrations];
  const connect = useAction(() => api.post<{ authorization_url: string }>("/integrations/gmail/connect"), {
    onSuccess: (r) => { window.location.href = r.authorization_url; },
  });
  const disconnect = useAction(() => api.del(`/integrations/${i.provider}`), { invalidate: inv, success: `${i.name} disconnected` });
  const test = useAction(() => api.post("/integrations/whatsapp/test"), { invalidate: inv, success: "Test message sent" });
  const connected = i.status === "connected";
  return (
    <Card className="flex flex-col p-4">
      <div className="flex items-start gap-3">
        <span className="flex size-9 items-center justify-center rounded-lg border border-border bg-surface-2"><Icon className="size-4" /></span>
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-[13px] font-semibold">{i.name}
            {!i.available ? <Badge tone="warning">Requires setup</Badge> : connected ? <Badge tone="positive" dot>Connected</Badge> : i.status === "error" ? <Badge tone="danger" dot>Error</Badge> : <Badge>Not connected</Badge>}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">{DESC[i.provider]}</p>
        </div>
      </div>
      {i.account && <p className="mt-3 text-xs text-fg-muted">Account: <span className="text-fg">{i.account}</span>{i.last_synced_at ? ` · synced ${relativeTime(i.last_synced_at)}` : ""}</p>}
      {i.last_error && <Callout tone="danger" className="mt-3">{i.last_error}</Callout>}
      {!i.available && i.setup_hint && <Callout tone="neutral" className="mt-3">{i.setup_hint}</Callout>}
      {i.scopes.length > 0 && (
        <ul className="mt-3 space-y-1">{i.scopes.map((s) => <li key={s} className="flex items-center gap-1.5 text-xs text-fg-muted"><ShieldCheck className="size-3 text-positive" />{s}</li>)}</ul>
      )}
      <div className="mt-auto flex gap-2 pt-4">
        {i.provider === "gmail" && i.available && (connected
          ? <Button size="sm" variant="ghost" onClick={() => disconnect.mutate(undefined)} loading={disconnect.isPending}>Disconnect</Button>
          : <Button size="sm" variant="primary" onClick={() => connect.mutate(undefined)} loading={connect.isPending}>Connect Gmail</Button>)}
        {i.provider === "whatsapp" && i.available && (
          i.account ? <Button size="sm" onClick={() => test.mutate(undefined)} loading={test.isPending}>Send test message</Button>
            : <Button asChild size="sm"><Link href="/settings?tab=notifications">Add your number</Link></Button>
        )}
      </div>
    </Card>
  );
}

function IntegrationsInner() {
  const params = useSearchParams();
  const { data, isPending, error, refetch } = useIntegrations();
  useEffect(() => {
    const g = params.get("gmail");
    if (g === "connected") toast.success("Gmail connected");
    else if (g) toast.error(g === "denied" ? "Gmail access was not granted" : "Gmail connection failed — try again");
  }, [params]);
  return (
    <>
      <PageHeader title="Integrations" description="Connections are optional and explicit. Credentials are encrypted at rest and never exposed to the browser." />
      {error ? <Card><ErrorState error={error} onRetry={() => refetch()} /></Card> : (
        <div className="grid gap-4 md:grid-cols-3">
          {isPending ? Array.from({ length: 3 }).map((_, k) => <Skeleton key={k} className="h-48 rounded-lg" />) : data.map((i) => <IntegrationCard key={i.provider} i={i} />)}
        </div>
      )}
      <Card className="mt-6 p-4">
        <div className="flex items-start gap-3">
          <span className="flex size-9 items-center justify-center rounded-lg border border-border bg-surface-2"><Terminal className="size-4" /></span>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-semibold">MCP — use JobPilot from Claude and other AI clients</p>
            <p className="mt-1 text-xs leading-relaxed text-fg-muted">
              The JobPilot MCP server exposes tools like search_jobs, calculate_job_match, tailor_resume and list_applications.
              It uses a personal token with the scopes you choose, so every call goes through the same ownership checks and
              approval rules — MCP clients can never submit applications.
            </p>
            <pre className="mt-3 overflow-x-auto rounded-md border border-border bg-surface-2 p-3 font-mono text-[11.5px] leading-relaxed">{MCP_CONFIG}</pre>
            <div className="mt-3 flex gap-2">
              <Button size="sm" onClick={() => { navigator.clipboard.writeText(MCP_CONFIG); toast.success("Copied"); }}><Copy /> Copy config</Button>
              <Button asChild size="sm" variant="primary"><Link href="/settings?tab=security"><Plug /> Create a token</Link></Button>
            </div>
          </div>
        </div>
      </Card>
    </>
  );
}

export default function IntegrationsPage() {
  return <Suspense><IntegrationsInner /></Suspense>;
}
