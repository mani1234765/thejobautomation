import {
  BarChart3, Bell, Bot, Briefcase, FileText, LayoutDashboard, MessagesSquare, Mic, Plug, Search, Settings, Workflow,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { href: string; label: string; icon: LucideIcon; shortcut?: string };

export const MAIN_NAV: NavItem[] = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard, shortcut: "G O" },
  { href: "/jobs", label: "Discover Jobs", icon: Search, shortcut: "G J" },
  { href: "/applications", label: "My Applications", icon: Briefcase, shortcut: "G A" },
  { href: "/resume", label: "Resume Studio", icon: FileText, shortcut: "G R" },
  { href: "/assistant", label: "AI Career Assistant", icon: Bot },
  { href: "/interviews", label: "Interview Preparation", icon: Mic },
  { href: "/communications", label: "Communications", icon: MessagesSquare },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
];

export const SECONDARY_NAV: NavItem[] = [
  { href: "/automation", label: "Automation Centre", icon: Workflow },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/notifications", label: "Notifications", icon: Bell },
  { href: "/settings", label: "Settings", icon: Settings },
];
