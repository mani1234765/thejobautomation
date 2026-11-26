"use client";

import { Bell, CheckCheck } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/primitives";
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn, relativeTime } from "@/lib/format";
import { keys, useAction, useNotifications } from "@/lib/queries";

export default function NotificationsPage() {
  const { data, isPending, error, refetch } = useNotifications();
  const read = useAction((ids: string[] | null) => api.post("/notifications/read", { ids }), { invalidate: [keys.notifications] });
  return (
    <>
      <PageHeader title="Notifications" description="Approvals, submissions, new matches and automation results."
        actions={!!data?.unread && <Button onClick={() => read.mutate(null)} loading={read.isPending}><CheckCheck /> Mark all read</Button>} />
      <Card>
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isPending ? <ListSkeleton /> : !data.items.length ? (
          <EmptyState icon={Bell} title="You're all caught up" description="Notifications appear when something needs you or an automation finishes." />
        ) : (
          <ul className="divide-y divide-border">
            {data.items.map((n) => (
              <li key={n.id} className={cn("relative flex gap-3 px-4 py-3", !n.read_at && "bg-accent-soft/20")}>
                <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : "bg-accent")} aria-hidden />
                {!n.read_at && <span className="sr-only">Unread:</span>}
                <div className="min-w-0 flex-1">
                  {n.link ? (
                    <Link href={n.link} onClick={() => !n.read_at && read.mutate([n.id])} className="text-[13px] font-medium after:absolute after:inset-0 hover:underline">{n.title}</Link>
                  ) : <p className="text-[13px] font-medium">{n.title}</p>}
                  {n.body && <p className="mt-0.5 text-xs text-fg-muted">{n.body}</p>}
                </div>
                <span className="shrink-0 text-xs text-fg-subtle">{relativeTime(n.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
