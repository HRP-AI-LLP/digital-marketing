// A spatial view of the studio: the AI agents that do the work, the services they rely on and every connected
// account, with live status and animated connections: a real 3D (WebGL) scene you can orbit and zoom, or a flat map.
// Hover for details, click for the full card.
import { useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle, Bot, Brain, CalendarClock, Check, Cloud, FileText, Globe, Hand, Inbox, Lightbulb, Loader2, Mail, Megaphone,
  Pause, PartyPopper, Power, Radar as RadarIcon, Recycle, RotateCcw, Send, Sparkles, TrendingUp, Wand2, Wifi, X,
} from "lucide-react";
import { api } from "../../api/client";
import { TipHead, TipRow, useTip, useWidth, ZoomFrame, ZoomValues } from "../../components/viz";
import { PLATFORM_BADGE, PlatformBadge } from "../analytics/vizkit";
import SystemMap3D, { webglAvailable, type Edge3D, type Node3D } from "./SystemMap3D";

type State = "ok" | "working" | "idle" | "warning" | "error" | "off" | "stopped" | "paused" | "manual";
type Agent = { id: string; name: string; role: string; layer: number; state: State; note: string | null; done_24h: number; failed_24h: number; running: number; queued: number; last_activity: string | null };
type Service = { id: string; name: string; state: State; detail: string; feeds: string[]; from?: string[] };
type Account = { id: string; name: string; handle: string | null; platform: string; platform_label: string; status: string; state: State; note: string | null; posts_7d: number; failed_7d: number; waiting_by_hand: number; last_published_at: string | null; next_post_at: string | null; needs_public_link: boolean };
export type SystemMapData = {
  generated_at: string;
  summary: { agents_working: number; agents_ok: number; agents_attention: number; accounts_ok: number; accounts_attention: number; tasks_24h: number; failed_24h: number; worker_running: boolean; online: boolean; last_seen: string | null };
  agents: Agent[]; flows: { from: string; to: string; label: string }[]; services: Service[]; accounts: Account[];
};

// Status: colour + icon + word, never colour alone.
const STATE: Record<State, { color: string; ring: string; label: string; Icon: typeof Check }> = {
  ok: { color: "#047857", ring: "#34d399", label: "Healthy", Icon: Check },
  working: { color: "#086fb8", ring: "#38bdf8", label: "Working now", Icon: Loader2 },
  idle: { color: "#64748b", ring: "#cbd5e1", label: "Idle", Icon: Pause },
  warning: { color: "#b45309", ring: "#fbbf24", label: "Needs a look", Icon: AlertTriangle },
  error: { color: "#b91c1c", ring: "#f87171", label: "Problem", Icon: X },
  off: { color: "#94a3b8", ring: "#e2e8f0", label: "Switched off", Icon: Power },
  stopped: { color: "#b91c1c", ring: "#fca5a5", label: "Stopped", Icon: Power },
  paused: { color: "#b45309", ring: "#fcd34d", label: "Paused (offline)", Icon: Pause },
  manual: { color: "#6d28d9", ring: "#c4b5fd", label: "Posted by hand", Icon: Hand },
};

const AGENT_ICON: Record<string, typeof Bot> = {
  planner: CalendarClock, writer: Wand2, designer: Sparkles, publisher: Send, blog: FileText, tracker: TrendingUp,
  listening: RadarIcon, inbox: Inbox, research: Lightbulb, evergreen: Recycle, occasions: PartyPopper, advisor: Megaphone,
};
const SERVICE_ICON: Record<string, typeof Bot> = { ai: Brain, tunnel: Cloud, internet: Wifi, email: Mail, website: Globe };

const ago = (iso: string | null) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) { const m = -s / 60; return m < 60 ? `in ${Math.round(m)} min` : m < 1440 ? `in ${Math.round(m / 60)} h` : `in ${Math.round(m / 1440)} d`; }
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};

const P = 1000; // plane size (virtual units)
const C = P / 2;

type Node = { id: string; kind: "hub" | "agent" | "service" | "account"; x: number; y: number; state: State; label: string; sub?: string; data?: Agent | Service | Account };

function layout(d: SystemMapData): { nodes: Node[]; edges: { a: string; b: string; kind: "spoke" | "flow" | "publish" | "feed" | "collect"; label?: string; live: boolean }[] } {
  const nodes: Node[] = [{ id: "hub", kind: "hub", x: C, y: C, state: d.summary.worker_running ? (d.summary.online ? "ok" : "paused") : "stopped", label: "HRP AI Studio", sub: "Orchestrator" }];
  // agents on a ring, grouped by layer (think → make → send → learn) so the flow reads clockwise
  const agents = [...d.agents].sort((a, b) => a.layer - b.layer);
  agents.forEach((a, i) => {
    const ang = -Math.PI / 2 + (i / agents.length) * Math.PI * 2;
    const r = i % 2 ? 330 : 268; // alternate the radius so neighbouring labels don't collide when the plane is tilted
    nodes.push({ id: a.id, kind: "agent", x: C + r * Math.cos(ang), y: C + r * Math.sin(ang), state: a.state, label: a.name, data: a });
  });
  d.services.forEach((s, i) => {
    const ang = -Math.PI / 2 + (i / d.services.length) * Math.PI * 2;
    nodes.push({ id: `svc-${s.id}`, kind: "service", x: C + 168 * Math.cos(ang + Math.PI / d.services.length), y: C + 168 * Math.sin(ang + Math.PI / d.services.length), state: s.state, label: s.name, data: s });
  });
  d.accounts.forEach((a, i) => {
    const ang = -Math.PI / 2 + ((i + 0.5) / Math.max(1, d.accounts.length)) * Math.PI * 2;
    nodes.push({ id: `acc-${a.id}`, kind: "account", x: C + 450 * Math.cos(ang), y: C + 450 * Math.sin(ang), state: a.state, label: a.name, sub: a.platform_label, data: a });
  });
  const edges: ReturnType<typeof layout>["edges"] = [];
  d.agents.forEach((a) => edges.push({ a: "hub", b: a.id, kind: "spoke", live: a.state === "working" }));
  d.flows.forEach((f) => edges.push({ a: f.from, b: f.to, kind: "flow", label: f.label, live: d.agents.find((x) => x.id === f.from)?.state === "working" }));
  d.accounts.forEach((a) => {
    if (a.state !== "manual") edges.push({ a: "publisher", b: `acc-${a.id}`, kind: "publish", live: a.state === "working" });
    edges.push({ a: `acc-${a.id}`, b: "tracker", kind: "collect", live: false });
    if (a.needs_public_link) edges.push({ a: "svc-tunnel", b: `acc-${a.id}`, kind: "feed", live: false });
  });
  d.services.forEach((s) => {
    s.feeds.forEach((t) => edges.push({ a: `svc-${s.id}`, b: t, kind: "feed", live: false }));
    (s.from ?? []).forEach((t) => edges.push({ a: t, b: `svc-${s.id}`, kind: "feed", live: false }));
  });
  return { nodes, edges };
}

export function useSystemMap() {
  return useQuery({ queryKey: ["system-map"], queryFn: () => api<SystemMapData>("/system/map"), refetchInterval: 15000 });
}

export default function SystemMap({ height = 640 }: { height?: number }) {
  const q = useSystemMap();
  const d = q.data;
  const [spin, setSpin] = useState(-18);
  const [tilt, setTilt] = useState(36);
  const [mode3d, setMode3d] = useState(() => webglAvailable());
  const [focus, setFocus] = useState<string | null>(null);
  const [picked, setPicked] = useState<Node | null>(null);
  const drag = useRef<{ x: number; y: number; spin: number; tilt: number; moved: boolean; px: number; py: number } | null>(null);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const t = useTip();
  const [boxRef, boxW] = useWidth<HTMLDivElement>(1000);
  const lay = useMemo(() => (d ? layout(d) : null), [d]);
  if (q.error) return <div className="rounded-xl border border-danger/20 bg-danger-tint px-4 py-3 text-sm text-danger">{(q.error as Error).message}</div>;
  if (!d || !lay) return <div className="grid place-items-center text-sm text-muted" style={{ height }}>Mapping your system…</div>;

  const byId = new Map(lay.nodes.map((n) => [n.id, n]));
  const maxPosts = Math.max(1, ...d.accounts.map((a) => a.posts_7d));
  const nodes3d: Node3D[] = lay.nodes.map((n) => ({
    id: n.id, kind: n.kind, x: n.x, y: n.y, label: n.label, sub: n.sub, ring: STATE[n.state].ring, stateLabel: STATE[n.state].label,
    working: n.state === "working", broken: n.state === "error" || n.state === "stopped",
    icon: n.kind === "agent" ? AGENT_ICON[n.id] ?? Bot : n.kind === "service" ? SERVICE_ICON[(n.data as Service).id] ?? Cloud : undefined,
    badge: n.kind === "account" ? PLATFORM_BADGE[(n.data as Account).platform] ?? { code: (n.data as Account).platform.slice(0, 2).toUpperCase(), bg: "#64748B" } : undefined,
    height: n.kind === "account" ? (n.data as Account).posts_7d / maxPosts : undefined,
  }));
  const edges3d: Edge3D[] = lay.edges.map((e) => {
    const a = byId.get(e.a), b = byId.get(e.b);
    const broken = !!a && !!b && (a.state === "error" || b.state === "error" || a.state === "stopped") && e.kind !== "spoke";
    return { a: e.a, b: e.b, kind: e.kind, live: e.live, broken };
  });
  const related = new Set<string>();
  if (focus) {
    related.add(focus);
    lay.edges.forEach((e) => { if (e.a === focus) related.add(e.b); if (e.b === focus) related.add(e.a); });
  }
  const rotX = mode3d ? tilt : 0;
  const rotZ = mode3d ? spin : 0;
  const billboard: CSSProperties = { transform: `rotateZ(${-rotZ}deg) rotateX(${-rotX}deg)`, transformStyle: "preserve-3d" };

  const tipFor = (n: Node): ReactNode => {
    const st = STATE[n.state];
    if (n.kind === "agent") {
      const a = n.data as Agent;
      return <><TipHead sub={a.role}>{a.name}</TipHead><TipRow color={st.ring} label="State" value={st.label} />
        <TipRow label="Tasks done (24 h)" value={a.done_24h} /><TipRow label="Failed (24 h)" value={a.failed_24h} />
        {(a.running > 0 || a.queued > 0) && <TipRow label="Running / queued" value={`${a.running} / ${a.queued}`} />}
        <TipRow label="Last activity" value={ago(a.last_activity)} />{a.note && <div className="mt-1 text-[11px] text-muted">{a.note}</div>}</>;
    }
    if (n.kind === "account") {
      const a = n.data as Account;
      return <><TipHead sub={`${a.platform_label}${a.handle ? ` · ${a.handle}` : ""}`}>{a.name}</TipHead><TipRow color={st.ring} label="State" value={st.label} />
        <TipRow label="Posts (7 days)" value={a.posts_7d} />{a.failed_7d > 0 && <TipRow label="Failed (7 days)" value={a.failed_7d} />}
        {a.waiting_by_hand > 0 && <TipRow label="Waiting to post by hand" value={a.waiting_by_hand} />}
        <TipRow label="Last post" value={ago(a.last_published_at)} /><TipRow label="Next post" value={a.next_post_at ? ago(a.next_post_at) : "none planned"} />
        {a.note && <div className="mt-1 text-[11px] text-muted">{a.note}</div>}</>;
    }
    if (n.kind === "service") {
      const s = n.data as Service;
      return <><TipHead>{s.name}</TipHead><TipRow color={st.ring} label="State" value={st.label} /><div className="mt-1 text-[11px] text-muted">{s.detail}</div></>;
    }
    return <><TipHead sub="Coordinates every agent">HRP AI Studio</TipHead><TipRow color={st.ring} label="Worker" value={d.summary.worker_running ? "Running" : "Stopped"} />
      <TipRow label="Internet" value={d.summary.online ? "Online" : "Offline"} /><TipRow label="Tasks (24 h)" value={d.summary.tasks_24h} /><TipRow label="Heartbeat" value={ago(d.summary.last_seen)} /></>;
  };

  const edgeStyle = (k: string) => ({
    spoke: { stroke: "#cbd5e1", width: 1.2, dash: "" }, flow: { stroke: "#7dd3fc", width: 2.2, dash: "" },
    publish: { stroke: "#93c5fd", width: 1.6, dash: "" }, collect: { stroke: "#a7f3d0", width: 1.1, dash: "2 6" },
    feed: { stroke: "#ddd6fe", width: 1.2, dash: "4 5" },
  }[k as "spoke"]);

  return (
    <div className="relative">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Stat label="Agents healthy" value={`${d.summary.agents_ok}/${d.agents.length}`} tone={d.summary.agents_attention ? "warn" : "good"} />
        <Stat label="Working now" value={String(d.summary.agents_working)} tone="info" />
        <Stat label="Channels OK" value={`${d.summary.accounts_ok}/${d.accounts.length}`} tone={d.summary.accounts_attention ? "warn" : "good"} />
        <Stat label="Tasks in 24 h" value={String(d.summary.tasks_24h)} tone="info" />
        {d.summary.failed_24h > 0 && <Stat label="Failed in 24 h" value={String(d.summary.failed_24h)} tone="bad" />}
        <span className="ml-auto flex items-center gap-1">
          <button type="button" onClick={() => setMode3d(!mode3d && webglAvailable())} className="rounded-md border border-line bg-white px-2 py-1 font-medium text-ink hover:border-primary-300">{mode3d ? "Flat view" : "3D view"}</button>
          <button type="button" onClick={() => { setSpin(-18); setTilt(36); setFocus(null); setPicked(null); setPan({ x: 0, y: 0 }); }} className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-muted hover:text-ink"><RotateCcw size={12} />Reset</button>
        </span>
      </div>
      <ZoomFrame mode="self" dark wheel={!mode3d} corner="bottom"><ZoomValues>{({ k, fit }) => { const H = Math.round(height * fit); return mode3d ? (
        <div ref={(el) => { (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; }}
          className="relative overflow-hidden rounded-2xl border border-slate-800 bg-[radial-gradient(ellipse_at_50%_35%,#0f2a4a_0%,#081a30_55%,#040c18_100%)] select-none"
          style={{ height: H }} onPointerLeave={() => t.hide()}>
          <SystemMap3D height={H} nodes={nodes3d} edges={edges3d}
            onHover={(id, e) => { setFocus(id); const n = id ? byId.get(id) : null; if (n && e) t.show(e as unknown as React.PointerEvent, tipFor(n)); else t.hide(); }}
            onPick={(id) => { const n = byId.get(id); if (n) setPicked(n); }} />
          <Legends mode3d />
          {t.tip}
          {picked && <Detail node={picked} onClose={() => setPicked(null)} />}
        </div>
      ) : (
      <div ref={(el) => { (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; (boxRef as React.MutableRefObject<HTMLDivElement | null>).current = el; }}
        className="relative overflow-hidden rounded-2xl border border-slate-800 bg-[radial-gradient(ellipse_at_center,#0f2a4a_0%,#081a30_55%,#050f1e_100%)] select-none"
        style={{ height: H, perspective: 1600, cursor: drag.current ? "grabbing" : "grab" }}
        onPointerDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, spin, tilt, moved: false, px: pan.x, py: pan.y }; }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y;
          if (Math.abs(dx) + Math.abs(dy) > 3) drag.current.moved = true;
          if (!mode3d) { if (k > 1 && drag.current.moved) setPan({ x: drag.current.px + dx, y: drag.current.py + dy }); return; }
          setSpin(drag.current.spin + dx * 0.35);
          setTilt(Math.max(0, Math.min(70, drag.current.tilt - dy * 0.25)));
        }}
        onPointerUp={() => { setTimeout(() => { drag.current = null; }, 0); }}
        onPointerLeave={() => { drag.current = null; t.hide(); }}>
        {/* stars */}
        <div className="pointer-events-none absolute inset-0 opacity-50" style={{ backgroundImage: "radial-gradient(1px 1px at 20% 30%, #fff 50%, transparent 51%), radial-gradient(1px 1px at 70% 80%, #fff 50%, transparent 51%), radial-gradient(1px 1px at 40% 70%, #cbd5e1 50%, transparent 51%), radial-gradient(1px 1px at 85% 25%, #fff 50%, transparent 51%)" }} />
        <div className="absolute left-1/2 top-1/2" style={{ width: P, height: P, marginLeft: -P / 2, marginTop: -P / 2, transformStyle: "preserve-3d",
          transform: `translate(${k > 1 ? pan.x : 0}px, ${k > 1 ? pan.y : 0}px) translateY(${mode3d ? -H * 0.015 : 10}px) scale(${k * Math.min(boxW / (P * 1.08), (H - 30) / (P * (mode3d ? 1.0 : 1.14)))}) rotateX(${rotX}deg) rotateZ(${rotZ}deg)`, transition: drag.current ? "none" : "transform .5s ease" }}>
          {/* plane: rings + edges */}
          <svg width={P} height={P} className="absolute inset-0" style={{ overflow: "visible" }}>
            <defs>
              <radialGradient id="sm-floor"><stop offset="0" stopColor="#1e3a5f" stopOpacity="0.55" /><stop offset="1" stopColor="#0b1d33" stopOpacity="0" /></radialGradient>
            </defs>
            <circle cx={C} cy={C} r={500} fill="url(#sm-floor)" />
            {[168, 268, 330, 450].map((r) => <circle key={r} cx={C} cy={C} r={r} fill="none" stroke="#1e3a5f" strokeWidth="1.5" strokeDasharray="3 7" />)}
            {lay.edges.map((e, i) => {
              const a = byId.get(e.a), b = byId.get(e.b);
              if (!a || !b) return null;
              const st = edgeStyle(e.kind);
              const lit = focus ? related.has(e.a) && related.has(e.b) && (e.a === focus || e.b === focus) : e.kind !== "feed" && e.kind !== "collect";
              const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
              const bend = e.kind === "flow" ? 0.18 : 0;
              const cx = mx + (C - mx) * bend, cy = my + (C - my) * bend;
              const path = `M${a.x},${a.y} Q${cx},${cy} ${b.x},${b.y}`;
              const broken = (a.state === "error" || b.state === "error" || a.state === "stopped") && e.kind !== "spoke";
              return (
                <g key={i} opacity={lit ? 1 : focus ? 0.06 : 0.18}>
                  <path d={path} fill="none" stroke={broken ? "#f87171" : st.stroke} strokeWidth={st.width * (lit && focus ? 1.6 : 1)} strokeDasharray={broken ? "6 6" : st.dash} strokeLinecap="round" />
                  {(e.live || (lit && focus && !broken && e.kind !== "collect")) && (
                    <path d={path} fill="none" stroke="#e0f2fe" strokeWidth={st.width + 1} strokeDasharray="2 18" strokeLinecap="round" className="sm-flow" />
                  )}
                </g>
              );
            })}
          </svg>
          {/* nodes (billboarded so they face you while the plane turns) */}
          {lay.nodes.map((n) => {
            const st = STATE[n.state];
            const dim = focus && !related.has(n.id);
            const size = n.kind === "hub" ? 112 : n.kind === "agent" ? 74 : n.kind === "service" ? 56 : 66;
            const Icon = n.kind === "agent" ? AGENT_ICON[n.id] ?? Bot : n.kind === "service" ? SERVICE_ICON[(n.data as Service).id] ?? Cloud : Bot;
            return (
              <div key={n.id} className="absolute" style={{ left: n.x, top: n.y, transformStyle: "preserve-3d", opacity: dim ? 0.25 : 1, transition: "opacity .2s" }}>
                {mode3d && <div className="absolute rounded-full bg-black/40 blur-md" style={{ width: size * 0.9, height: size * 0.9, left: -size * 0.45, top: -size * 0.45 }} />}
                <div style={billboard}>
                  <button type="button" aria-label={`${n.label}: ${st.label}`}
                    className="group absolute flex flex-col items-center outline-none"
                    style={{ left: -85, top: -(size / 2) - (mode3d ? 22 : 0), width: 170 }}
                    onPointerEnter={() => setFocus(n.id)} onPointerLeave={() => { setFocus(null); t.hide(); }}
                    onPointerMove={(e) => { if (!drag.current?.moved) t.show(e, tipFor(n)); }}
                    onFocus={(e) => { setFocus(n.id); t.show(e.currentTarget, tipFor(n)); }} onBlur={() => { setFocus(null); t.hide(); }}
                    onClick={() => { if (!drag.current?.moved) setPicked(n); }}>
                    <span className={`relative grid place-items-center rounded-full shadow-[0_8px_24px_rgba(0,0,0,0.45)] ${n.state === "working" ? "sm-pulse" : ""}`}
                      style={{ width: size, height: size, background: n.kind === "hub" ? "linear-gradient(135deg,#075492,#06a6c7)" : "linear-gradient(160deg,#16314f,#0b1d33)",
                        boxShadow: `0 0 0 3px ${st.ring}, 0 0 22px ${st.ring}55` }}>
                      {n.kind === "account" ? <PlatformBadge platform={(n.data as Account).platform} size={36} /> :
                        n.kind === "hub" ? <img src="/logo-mark.png" alt="" className="h-16 w-16 rounded-xl bg-white/95 object-contain p-1.5" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} /> :
                          <Icon size={n.kind === "service" ? 22 : 30} className="text-sky-100" />}
                      <span className="absolute -bottom-1 -right-1 grid h-6 w-6 place-items-center rounded-full border-2 border-[#081a30]" style={{ background: st.color }}>
                        <st.Icon size={13} className={`text-white ${n.state === "working" ? "animate-spin" : ""}`} />
                      </span>
                    </span>
                    <span className={`mt-1.5 max-w-[170px] truncate rounded-md bg-[#081a30]/90 px-2 py-0.5 text-center font-semibold text-white ${n.kind === "account" || n.kind === "hub" ? "text-[14px] leading-5" : "text-[12.5px] leading-[18px]"}`}>{n.label}</span>
                    {n.sub && <span className="max-w-[170px] truncate text-[12px] leading-4 text-sky-200/85">{n.sub}</span>}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        <Legends mode3d={false} />
        {t.tip}
        {picked && <Detail node={picked} onClose={() => setPicked(null)} />}
      </div>
      ); }}</ZoomValues></ZoomFrame>
      <style>{`
        .sm-flow { animation: smflow 1.2s linear infinite; }
        @keyframes smflow { to { stroke-dashoffset: -40; } }
        .sm-pulse::after { content: ""; position: absolute; inset: -6px; border-radius: 9999px; border: 2px solid #38bdf8; animation: smpulse 1.6s ease-out infinite; }
        @keyframes smpulse { from { opacity: .9; transform: scale(.9); } to { opacity: 0; transform: scale(1.35); } }
        @media (prefers-reduced-motion: reduce) { .sm-flow, .sm-pulse::after { animation: none; } }
      `}</style>
    </div>
  );
}

function Legends({ mode3d }: { mode3d: boolean }) {
  return (
    <>
        <div className="pointer-events-none absolute left-3 top-3 flex max-w-[15rem] flex-col gap-1 rounded-lg bg-[#081a30]/80 px-3 py-2 text-[11px] text-sky-100">
          {(["ok", "working", "idle", "warning", "error", "off", "manual"] as State[]).map((s) => {
            const st = STATE[s];
            return <span key={s} className="inline-flex items-center gap-1"><span className="grid h-3.5 w-3.5 place-items-center rounded-full" style={{ background: st.color }}><st.Icon size={8} className="text-white" /></span>{st.label}</span>;
          })}
        </div>
        <div className="pointer-events-none absolute right-3 top-3 rounded-lg bg-[#081a30]/80 px-3 py-2 text-[11px] text-sky-100/90">
          <div className="flex items-center gap-2"><span className="h-0.5 w-5 rounded bg-sky-300" />Work flowing between agents</div>
          <div className="flex items-center gap-2"><span className="h-0.5 w-5 rounded bg-blue-300" />Publishing to a channel</div>
          {mode3d && <div className="flex items-center gap-2"><span className="h-3 w-2 rounded-sm bg-sky-300/70" />Pillar height: posts in 7 days</div>}
          <div className="flex items-center gap-2"><span className="h-0 w-5 border-t border-dashed border-emerald-300" />Results collected back</div>
          <div className="flex items-center gap-2"><span className="h-0 w-5 border-t border-dashed border-red-400" />Broken connection</div>
          <div className="mt-1 text-sky-200/70">{mode3d ? "Drag to orbit · scroll to zoom · hover to trace · click for details" : "Hover to trace · click for details"}</div>
        </div>
    </>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone: "good" | "warn" | "bad" | "info" }) {
  const cls = { good: "bg-emerald-50 text-emerald-800", warn: "bg-amber-50 text-amber-800", bad: "bg-red-50 text-danger", info: "bg-primary-50 text-primary-800" }[tone];
  return <span className={`rounded-full px-2.5 py-1 ${cls}`}>{label} <b className="tabular-nums">{value}</b></span>;
}

function Detail({ node, onClose }: { node: Node; onClose: () => void }) {
  const st = STATE[node.state];
  const a = node.data as Agent & Account & Service;
  const link = node.kind === "account" ? "/settings/accounts" : node.id === "blog" ? "/seo" : node.id === "listening" ? "/listening"
    : node.id === "inbox" ? "/inbox" : node.id === "research" ? "/research" : node.kind === "service" ? "/settings/integrations" : node.id === "tracker" || node.id === "advisor" ? "/analytics" : "/posts";
  return (
    <div className="absolute right-3 top-28 z-40 w-72 rounded-xl border border-line bg-white p-4 text-sm shadow-2xl" onPointerDown={(e) => e.stopPropagation()}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold text-ink">{node.label}</div>
          <div className="text-xs text-muted">{node.kind === "agent" ? a.role : node.kind === "account" ? a.platform_label : node.kind === "service" ? a.detail : "Coordinates every agent"}</div>
        </div>
        <button type="button" aria-label="Close" onClick={onClose} className="text-muted hover:text-ink"><X size={16} /></button>
      </div>
      <div className="mt-3 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold text-white" style={{ background: st.color }}><st.Icon size={12} />{st.label}</div>
      {a?.note && <p className="mt-2 rounded-lg bg-slate-50 p-2 text-xs text-ink/80">{a.note}</p>}
      {node.kind === "agent" && (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <div><dt className="text-muted">Done (24 h)</dt><dd className="text-lg font-bold tabular-nums">{a.done_24h}</dd></div>
          <div><dt className="text-muted">Failed (24 h)</dt><dd className={`text-lg font-bold tabular-nums ${a.failed_24h ? "text-danger" : ""}`}>{a.failed_24h}</dd></div>
          <div><dt className="text-muted">Running</dt><dd className="font-semibold tabular-nums">{a.running}</dd></div>
          <div><dt className="text-muted">Queued</dt><dd className="font-semibold tabular-nums">{a.queued}</dd></div>
          <div className="col-span-2"><dt className="text-muted">Last activity</dt><dd>{ago(a.last_activity)}</dd></div>
        </dl>
      )}
      {node.kind === "account" && (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <div><dt className="text-muted">Posts (7 days)</dt><dd className="text-lg font-bold tabular-nums">{a.posts_7d}</dd></div>
          <div><dt className="text-muted">Failed (7 days)</dt><dd className={`text-lg font-bold tabular-nums ${a.failed_7d ? "text-danger" : ""}`}>{a.failed_7d}</dd></div>
          <div><dt className="text-muted">Last post</dt><dd>{ago(a.last_published_at)}</dd></div>
          <div><dt className="text-muted">Next post</dt><dd>{a.next_post_at ? ago(a.next_post_at) : "none planned"}</dd></div>
        </dl>
      )}
      <Link to={link} className="mt-3 inline-block text-xs font-medium text-primary-700 hover:underline">Open →</Link>
    </div>
  );
}
