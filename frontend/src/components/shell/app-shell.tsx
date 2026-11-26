"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Command } from "cmdk";
import { Bell, LogOut, Menu as MenuIcon, Monitor, Moon, Plus, Search, Settings, Sun, Upload, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useCallback, useEffect, useState } from "react";
import { LogoMark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Tooltip } from "@/components/ui/overlay";
import { Kbd, Skeleton } from "@/components/ui/primitives";
import { commandMenuShortcutLabel, isCommandMenuShortcut } from "@/lib/shortcuts";
import { api, ApiError } from "@/lib/api";
import { cn, initials } from "@/lib/format";
import { useMe, useNotifications } from "@/lib/queries";
import { MAIN_NAV, SECONDARY_NAV, type NavItem } from "./nav";

type Theme = "light" | "dark" | "system";

function applyTheme(t: Theme) {
  const dark = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  try {
    if (t === "system") localStorage.removeItem("jp-theme");
    else localStorage.setItem("jp-theme", t);
  } catch {}
}

function NavLink({ item, active, onNavigate, badge }: { item: NavItem; active: boolean; onNavigate?: () => void; badge?: number }) {
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group flex h-8 items-center gap-2.5 rounded-md px-2 text-[13px] transition-colors",
        active ? "bg-surface-3 font-medium text-fg" : "text-fg-muted hover:bg-surface-3/70 hover:text-fg",
      )}
    >
      <item.icon className={cn("size-4 shrink-0", active ? "text-fg" : "text-fg-subtle group-hover:text-fg-muted")} aria-hidden />
      <span className="truncate">{item.label}</span>
      {!!badge && (
        <span className="ml-auto rounded-full bg-accent px-1.5 text-2xs font-semibold leading-4 text-accent-fg tabular">{badge > 99 ? "99+" : badge}</span>
      )}
    </Link>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { data: notes } = useNotifications();
  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");
  return (
    <nav className="flex flex-1 flex-col gap-6 overflow-y-auto px-2 py-3" aria-label="Main">
      <div className="flex flex-col gap-0.5">
        {MAIN_NAV.map((i) => <NavLink key={i.href} item={i} active={isActive(i.href)} onNavigate={onNavigate} />)}
      </div>
      <div className="flex flex-col gap-0.5">
        <p className="px-2 pb-1 text-2xs font-medium uppercase tracking-wide text-fg-subtle">Workspace</p>
        {SECONDARY_NAV.map((i) => (
          <NavLink key={i.href} item={i} active={isActive(i.href)} onNavigate={onNavigate}
            badge={i.href === "/notifications" ? notes?.unread : undefined} />
        ))}
      </div>
    </nav>
  );
}

function UserMenu({ onCommand }: { onCommand: () => void }) {
  const { data: me } = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (typeof window !== "undefined" && (localStorage.getItem("jp-theme") as Theme)) || "system";
    } catch {
      return "system";
    }
  });
  const logout = async () => {
    await api.post("/auth/logout").catch(() => undefined);
    qc.clear();
    router.replace("/login");
  };
  const choose = (t: Theme) => {
    setTheme(t);
    applyTheme(t);
  };
  return (
    <div className="flex items-center gap-1 border-t border-border p-2">
      <Menu>
        <MenuTrigger asChild>
          <button className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1.5 text-left hover:bg-surface-3">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-2xs font-semibold text-accent-soft-fg">
              {initials(me?.full_name ?? me?.email)}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[13px] font-medium">{me?.full_name ?? <Skeleton className="h-3 w-20" />}</span>
              <span className="block truncate text-2xs text-fg-subtle">{me?.email}</span>
            </span>
          </button>
        </MenuTrigger>
        <MenuContent align="start" className="w-56">
          <MenuItem onSelect={() => router.push("/settings")}><Settings /> Settings</MenuItem>
          <MenuSeparator />
          <MenuLabel>Theme</MenuLabel>
          {([["light", Sun, "Light"], ["dark", Moon, "Dark"], ["system", Monitor, "System"]] as const).map(([t, Icon, label]) => (
            <MenuItem key={t} onSelect={() => choose(t)}>
              <Icon /> {label}
              {theme === t && <span className="ml-auto size-1.5 rounded-full bg-accent" />}
            </MenuItem>
          ))}
          <MenuSeparator />
          <MenuItem onSelect={logout}><LogOut /> Sign out</MenuItem>
        </MenuContent>
      </Menu>
      <Tooltip content={<span>Command menu <Kbd className="ml-1 border-transparent">{commandMenuShortcutLabel()}</Kbd></span>}>
        <Button variant="ghost" size="icon-sm" aria-label="Open command menu" aria-keyshortcuts="Meta+K Control+K" onClick={onCommand}>
          <Search />
        </Button>
      </Tooltip>
    </div>
  );
}

function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const go = useCallback((href: string) => {
    onOpenChange(false);
    router.push(href);
  }, [onOpenChange, router]);
  const item = "flex h-9 cursor-default items-center gap-2.5 rounded-md px-2.5 text-[13px] text-fg data-[selected=true]:bg-surface-3 [&_svg]:size-4 [&_svg]:text-fg-muted";
  return (
    <Command.Dialog open={open} onOpenChange={onOpenChange} label="Command menu"
      overlayClassName="fixed inset-0 z-50 bg-black/30"
      contentClassName="fixed top-[16vh] left-1/2 z-50 w-[calc(100vw-32px)] max-w-lg -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-surface shadow-lg">
      <DialogPrimitive.Title className="sr-only">Command menu</DialogPrimitive.Title>
      <DialogPrimitive.Description className="sr-only">Search pages and actions</DialogPrimitive.Description>
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Search className="size-4 text-fg-subtle" />
        <Command.Input placeholder="Search pages and actions…" className="h-11 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-subtle" />
        <Kbd>Esc</Kbd>
      </div>
      <Command.List className="max-h-80 overflow-y-auto p-1.5">
        <Command.Empty className="py-8 text-center text-[13px] text-fg-muted">No results.</Command.Empty>
        <Command.Group heading="Actions" className="[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-fg-subtle">
          <Command.Item className={item} onSelect={() => go("/jobs")}><Search /> Search jobs</Command.Item>
          <Command.Item className={item} onSelect={() => go("/jobs?sources=1")}><Plus /> Follow a company job board</Command.Item>
          <Command.Item className={item} onSelect={() => go("/resume?upload=1")}><Upload /> Upload a resume</Command.Item>
          <Command.Item className={item} onSelect={() => go("/applications?view=approvals")}><Bell /> Review approvals</Command.Item>
        </Command.Group>
        <Command.Group heading="Go to" className="[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-fg-subtle">
          {[...MAIN_NAV, ...SECONDARY_NAV].map((n) => (
            <Command.Item key={n.href} className={item} onSelect={() => go(n.href)}>
              <n.icon /> {n.label}
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: me, error, isPending } = useMe();
  const [cmdOpen, setCmdOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [error, router, pathname]);
  useEffect(() => {
    if (me && !me.onboarding_completed) router.replace("/onboarding");
  }, [me, router]);

  useEffect(() => {
    let pendingG = false;
    let timer: ReturnType<typeof setTimeout>;
    const onKey = (e: KeyboardEvent) => {
      if (isCommandMenuShortcut(e)) {
        e.preventDefault();
        setCmdOpen((o) => !o);
        return;
      }
      const t = e.target as HTMLElement;
      if (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (pendingG) {
        const dest = { o: "/overview", j: "/jobs", a: "/applications", r: "/resume" }[e.key.toLowerCase()];
        pendingG = false;
        if (dest) router.push(dest);
      } else if (e.key === "g") {
        pendingG = true;
        clearTimeout(timer);
        timer = setTimeout(() => (pendingG = false), 900);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  if (isPending || !me || !me.onboarding_completed) {
    return (
      <div className="flex min-h-dvh items-center justify-center" aria-busy="true">
        <LogoMark className="size-8 animate-pulse" />
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:shadow-md">
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-dvh w-[232px] shrink-0 flex-col border-r border-border bg-bg lg:flex">
        <Link href="/overview" className="flex h-12 items-center gap-2 px-4">
          <LogoMark className="size-5" />
          <span className="text-[14px] font-semibold tracking-[-0.02em]">JobPilot</span>
        </Link>
        <SidebarContent />
        <UserMenu onCommand={() => setCmdOpen(true)} />
      </aside>

      <DialogPrimitive.Root open={mobileOpen} onOpenChange={setMobileOpen}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/30 lg:hidden" />
          <DialogPrimitive.Content className="fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col border-r border-border bg-bg shadow-lg lg:hidden">
            <DialogPrimitive.Title className="sr-only">Navigation</DialogPrimitive.Title>
            <DialogPrimitive.Description className="sr-only">Main navigation</DialogPrimitive.Description>
            <div className="flex h-12 items-center justify-between px-4">
              <span className="flex items-center gap-2"><LogoMark className="size-5" /><span className="font-semibold">JobPilot</span></span>
              <DialogPrimitive.Close asChild>
                <Button variant="ghost" size="icon-sm" aria-label="Close navigation"><X /></Button>
              </DialogPrimitive.Close>
            </div>
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
            <UserMenu onCommand={() => setCmdOpen(true)} />
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b border-border bg-bg/90 px-3 backdrop-blur lg:hidden">
          <Button variant="ghost" size="icon-sm" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><MenuIcon /></Button>
          <LogoMark className="size-5" />
          <span className="font-semibold">JobPilot</span>
          <Button variant="ghost" size="icon-sm" className="ml-auto" aria-label="Search" onClick={() => setCmdOpen(true)}><Search /></Button>
        </div>
        <main id="main" className="mx-auto w-full max-w-[1240px] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-7">
          {children}
        </main>
      </div>
      <CommandPalette open={cmdOpen} onOpenChange={setCmdOpen} />
    </div>
  );
}
