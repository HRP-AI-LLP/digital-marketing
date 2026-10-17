import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  CalendarDays, CheckCheck, ChevronsUpDown, FileText, Gauge, History, Image, KeyRound,
  LineChart, LogOut, Menu, Plug, Coins, Radar, Settings, ShieldCheck, Bell, Users as UsersIcon, X, Globe2, ListChecks,
  Palette, Clock, Share2, Inbox as InboxIcon, Lightbulb,
} from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import NotificationBell from "./NotificationBell";
import InboxBadge from "./InboxBadge";

type Item = { to: string; label: string; icon: typeof Gauge; perm?: string; soon?: boolean; badge?: "inbox" };
type Group = { label: string; items: Item[]; platformOnly?: boolean };

export const NAV: Group[] = [
  { label: "Overview", items: [{ to: "/", label: "Dashboard", icon: Gauge }] },
  {
    label: "Plan and publish",
    items: [
      { to: "/calendar", label: "Calendar", icon: CalendarDays, perm: "content.view" },
      { to: "/approvals", label: "Approvals", icon: CheckCheck, perm: "content.approve" },
      { to: "/posts", label: "All posts", icon: ListChecks, perm: "content.view" },
      { to: "/media", label: "Media studio", icon: Image, perm: "media.manage" },
      { to: "/seo", label: "SEO articles", icon: FileText, perm: "seo.view" },
    ],
  },
  {
    label: "Engage",
    items: [{ to: "/inbox", label: "Inbox", icon: InboxIcon, perm: "inbox.view", badge: "inbox" }],
  },
  {
    label: "Understand",
    items: [
      { to: "/intelligence", label: "Company intelligence", icon: Radar, perm: "intel.view" },
      { to: "/research", label: "Research & ideas", icon: Lightbulb, perm: "research.view" },
      { to: "/analytics", label: "Analytics", icon: LineChart, perm: "analytics.view" },
    ],
  },
  {
    label: "Settings",
    items: [
      { to: "/settings/organization", label: "Organization", icon: Settings, perm: "org.settings.view" },
      { to: "/settings/brand", label: "Brand and topics", icon: Palette, perm: "content.view" },
      { to: "/settings/schedules", label: "Posting schedules", icon: Clock, perm: "schedule.view" },
      { to: "/settings/accounts", label: "Social accounts", icon: Share2, perm: "integration.view" },
      { to: "/settings/integrations", label: "Integrations", icon: Plug, perm: "integration.view" },
      { to: "/settings/ai-usage", label: "AI usage and costs", icon: Coins, perm: "integration.view" },
      { to: "/settings/notifications", label: "Notifications", icon: Bell },
      { to: "/settings/users", label: "Users", icon: UsersIcon, perm: "user.view" },
      { to: "/settings/roles", label: "Roles and access", icon: ShieldCheck, perm: "role.view" },
      { to: "/settings/activity", label: "Activity log", icon: History, perm: "audit.view" },
    ],
  },
  { label: "Platform", platformOnly: true, items: [{ to: "/platform/organizations", label: "All organizations", icon: Globe2 }] },
];

function OrgSwitcher() {
  const { session, switchOrg } = useAuth();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  if (!session) return null;
  const others = session.memberships.filter((m) => m.organization_id !== session.organization?.id);
  const canSwitch = others.length > 0 || session.user.is_platform_admin;
  return (
    <div className="relative">
      <button
        disabled={!canSwitch}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-2.5 rounded-md px-2 py-2 text-left hover:bg-white/10 disabled:hover:bg-transparent"
      >
        {session.organization?.logo_dark_url || session.organization?.logo_url ? (
          <span className={`grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-lg ${session.organization.logo_dark_url ? "" : "bg-white"}`}>
            <img src={session.organization.logo_dark_url || session.organization.logo_url || ""} alt="" className="h-full w-full object-contain p-0.5" />
          </span>
        ) : (
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-blue-700 to-cyan-500 font-bold text-white">
            {(session.organization?.name ?? "P").slice(0, 1)}
          </span>
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold text-white">{session.organization?.name ?? "Platform"}</span>
          <span className="block truncate text-xs text-white/60">
            {session.organization
              ? (session.memberships.find((m) => m.organization_id === session.organization?.id)?.roles.join(", ")
                || (session.user.is_platform_admin ? "Platform admin" : "Member"))
              : "No organization selected"}
          </span>
        </span>
        {canSwitch && <ChevronsUpDown size={16} className="text-white/60" />}
      </button>
      {open && (
        <div className="absolute left-0 right-0 z-20 mt-1 rounded-lg bg-surface py-1 text-ink shadow-xl">
          {others.map((m) => (
            <button
              key={m.organization_id}
              className="block w-full px-3 py-2 text-left text-sm hover:bg-brand-tint"
              onClick={async () => { setOpen(false); await switchOrg(m.organization_id); nav("/"); }}
            >
              {m.organization_name}
              <span className="block text-xs text-muted">{m.roles.join(", ")}</span>
            </button>
          ))}
          {session.user.is_platform_admin && (
            <button
              className="block w-full border-t border-line px-3 py-2 text-left text-sm text-brand hover:bg-brand-tint"
              onClick={() => { setOpen(false); nav("/platform/organizations"); }}
            >
              Manage all organizations
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function Layout() {
  const { session, can, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  if (!session) return null;

  const groups = NAV.filter((g) => !g.platformOnly || session.user.is_platform_admin)
    .map((g) => ({ ...g, items: g.items.filter((i) => !i.perm || can(i.perm)) }))
    .filter((g) => g.items.length);

  const rail = (
    <nav aria-label="Main" className="ai-mesh-dark flex h-full flex-col text-white/80">
      <div className="flex items-center gap-2.5 px-5 pt-5">
        <img src="/logo-mark.png" alt="HRP AI LLP" className="h-9 w-9 rounded-lg bg-white p-0.5" />
        <div className="leading-tight">
          <div className="text-sm font-semibold text-white">HRP AI</div>
          <div className="whitespace-nowrap text-xs text-secondary-300">Marketing Studio</div>
        </div>
        <div className="ml-auto"><NotificationBell dark /></div>
      </div>
      <div className="mx-3 mt-4 rounded-lg border border-white/10 bg-white/5 px-1 pb-0.5 pt-0.5"><OrgSwitcher /></div>
      <div className="scroll-dark flex-1 overflow-y-auto px-3 pb-4">
        {groups.map((g) => (
          <div key={g.label} className="mt-4">
            <div className="px-2 pb-1 text-xs text-white/45">{g.label}</div>
            {g.items.map((i) => (
              <NavLink
                key={i.to}
                to={i.to}
                end={i.to === "/"}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[14px] ${
                    isActive ? "bg-gradient-to-r from-blue-600/40 to-cyan-500/20 text-white font-medium ring-1 ring-inset ring-white/10" : "hover:bg-white/10 hover:text-white"
                  }`
                }
              >
                <i.icon size={16} strokeWidth={1.8} />
                <span className="flex-1">{i.label}</span>
                {i.soon && <span className="text-[11px] text-white/40">soon</span>}
                {i.badge === "inbox" && <InboxBadge />}
              </NavLink>
            ))}
          </div>
        ))}
      </div>
      <div className="border-t border-white/10 px-3 py-3">
        <div className="px-2 text-sm text-white truncate">{session.user.full_name}</div>
        <div className="px-2 text-xs text-white/55 truncate">{session.user.email}</div>
        <div className="mt-2 flex gap-1">
          <NavLink to="/account/password" className="flex items-center gap-1.5 rounded px-2 py-1 text-xs hover:bg-white/10">
            <KeyRound size={13} /> Password
          </NavLink>
          <button onClick={logout} className="flex items-center gap-1.5 rounded px-2 py-1 text-xs hover:bg-white/10">
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </div>
    </nav>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 lg:block">{rail}</aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="w-72">{rail}</div>
          <button aria-label="Close menu" className="flex-1 bg-ink/40" onClick={() => setMobileOpen(false)} />
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2.5 lg:hidden">
          <button aria-label="Open menu" onClick={() => setMobileOpen(true)}>{mobileOpen ? <X /> : <Menu />}</button>
          <img src="/logo-mark.png" alt="" className="h-6 w-6" />
          <span className="font-semibold truncate">{session.organization?.name ?? "Platform"}</span>
        </div>
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1120px] px-4 py-8 sm:px-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
