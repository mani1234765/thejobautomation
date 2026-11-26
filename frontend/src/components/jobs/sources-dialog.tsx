"use client";

import { AlertCircle, CheckCircle2, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { Switch } from "@/components/ui/controls";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, DialogContent, Tooltip } from "@/components/ui/overlay";
import { Badge, Field, Input, NativeSelect, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { keys, useAction, useSources } from "@/lib/queries";
import type { JobSource } from "@/lib/types";

const PROVIDERS = [
  { value: "greenhouse", label: "Greenhouse", example: "boards.greenhouse.io/<company>" },
  { value: "lever", label: "Lever", example: "jobs.lever.co/<company>" },
  { value: "ashby", label: "Ashby", example: "jobs.ashbyhq.com/<company>" },
] as const;

export function SourcesDialogContent() {
  const { data: sources, isPending } = useSources();
  const [provider, setProvider] = useState<JobSource["provider"]>("greenhouse");
  const [identifier, setIdentifier] = useState("");
  const [removing, setRemoving] = useState<JobSource | null>(null);
  const invalidate = [keys.sources, ["jobs"], keys.dashboard];

  const add = useAction(() => api.post<JobSource>("/job-sources", { provider, identifier }), {
    invalidate,
    success: (s) => s.last_status === "ok" ? `Following ${s.display_name} — ${s.last_job_count} open roles` : null,
    onSuccess: () => setIdentifier(""),
  });
  const sync = useAction((id: string) => api.post<JobSource>(`/job-sources/${id}/sync`), {
    invalidate, success: (s) => s.last_status === "ok" ? `Synced ${s.display_name}` : `Sync failed: ${s.last_error}`,
  });
  const toggle = useAction((v: { id: string; enabled: boolean }) => api.patch(`/job-sources/${v.id}`, { enabled: v.enabled }), { invalidate });
  const remove = useAction((id: string) => api.del(`/job-sources/${id}`), { invalidate, success: "Board removed", onSuccess: () => setRemoving(null) });

  const detected = PROVIDERS.find((p) => identifier.includes(p.example.split("/")[0]));
  return (
    <DialogContent wide title="Job sources"
      description="Follow company boards on Greenhouse, Lever or Ashby. JobPilot uses their official public APIs only.">
      <form className="flex flex-col gap-3 rounded-lg border border-border bg-surface-2 p-3 sm:flex-row sm:items-end"
        onSubmit={(e) => { e.preventDefault(); if (identifier.trim()) add.mutate(undefined); }}>
        <Field label="Platform" className="sm:w-36">
          <NativeSelect value={detected?.value ?? provider} onChange={(e) => setProvider(e.target.value as JobSource["provider"])}>
            {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </NativeSelect>
        </Field>
        <Field label="Board URL or company identifier" className="flex-1"
          hint={`e.g. ${PROVIDERS.find((p) => p.value === (detected?.value ?? provider))!.example}`}>
          <Input value={identifier} onChange={(e) => {
            setIdentifier(e.target.value);
            const d = PROVIDERS.find((p) => e.target.value.includes(p.example.split("/")[0]));
            if (d) setProvider(d.value);
          }} placeholder="stripe" />
        </Field>
        <Button type="submit" variant="primary" loading={add.isPending} disabled={!identifier.trim()} className="sm:mb-[22px]">Follow</Button>
      </form>

      <div className="mt-4">
        {isPending ? (
          <div className="space-y-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : !sources?.length ? (
          <p className="py-6 text-center text-[13px] text-fg-muted">
            You aren&apos;t following any boards yet. Find a company&apos;s careers page — if it&apos;s hosted on Greenhouse,
            Lever or Ashby, paste its URL above.
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {sources.map((s) => (
              <li key={s.id} className="flex items-center gap-3 px-3 py-2.5">
                {s.last_status === "error" ? (
                  <Tooltip content={s.last_error}><AlertCircle className="size-4 shrink-0 text-danger" aria-label="Last sync failed" /></Tooltip>
                ) : (
                  <CheckCircle2 className="size-4 shrink-0 text-positive" aria-label="Healthy" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium">{s.display_name}
                    <Badge className="ml-2 capitalize">{s.provider}</Badge>
                  </p>
                  <p className="truncate text-xs text-fg-muted">
                    {s.last_status === "error" ? s.last_error
                      : `${s.last_job_count ?? 0} open roles · synced ${relativeTime(s.last_synced_at)}`}
                  </p>
                </div>
                <Tooltip content="Sync now">
                  <Button variant="ghost" size="icon-sm" aria-label={`Sync ${s.display_name}`}
                    onClick={() => sync.mutate(s.id)} disabled={sync.isPending && sync.variables === s.id}>
                    <RefreshCw className={sync.isPending && sync.variables === s.id ? "animate-spin" : undefined} />
                  </Button>
                </Tooltip>
                <Switch checked={s.enabled} aria-label={`Include ${s.display_name} in discovery`}
                  onCheckedChange={(v) => toggle.mutate({ id: s.id, enabled: v })} />
                <Button variant="ghost" size="icon-sm" aria-label={`Remove ${s.display_name}`} onClick={() => setRemoving(s)}>
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <p className="mt-4 text-xs text-fg-muted">
        LinkedIn, Naukri, Indeed and Glassdoor don&apos;t offer candidate-side APIs that permit this, so JobPilot doesn&apos;t
        collect from them.
      </p>
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Stop following ${removing?.display_name}?`}
        description="Its jobs disappear from Discover. Applications you've already started are kept." confirmLabel="Remove" destructive
        loading={remove.isPending} onConfirm={() => removing && remove.mutate(removing.id)} />
    </DialogContent>
  );
}
