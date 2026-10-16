import { useMemo, useState } from "react";
import { Box, Check, Sparkles } from "lucide-react";
import { Alert, Button, Select, Spinner } from "../../components/ui";
import { PREVIEW_SIZE_LABEL, useTemplates, type Template } from "./shared";

const THEME_DOT: Record<string, string> = { dark: "bg-[#062f59]", light: "bg-slate-100 border border-slate-300", brand: "bg-[#075492]" };
const THEME_LABEL: Record<string, string> = { dark: "Dark", light: "Light", brand: "Brand blue" };

function ratioClass(size: string) {
  return ({ "1080x1080": "aspect-square", "1080x1350": "aspect-[4/5]", "1080x1920": "aspect-[9/16]", "1920x1080": "aspect-video",
    "1000x1500": "aspect-[2/3]" } as Record<string, string>)[size] ?? "aspect-[4/5]";
}

/** One template card: a real render of the layout in your brand (cached on the server after the first view). */
export function TemplateCard({ t, size, selected, onPick, theme, onTheme, compact }: {
  t: Template; size: string; selected?: boolean; onPick?: () => void; theme?: string; onTheme?: (th: string) => void; compact?: boolean;
}) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const src = (theme && t.preview_urls[theme]) || t.preview_url;
  return (
    <div className={`group overflow-hidden rounded-xl border bg-surface transition-shadow ${selected ? "border-primary-500 ring-2 ring-primary-500/30" : "border-line hover:shadow-md"}`}>
      <button type="button" onClick={onPick} className={`relative block w-full ${ratioClass(size)} overflow-hidden bg-slate-100`}
        aria-label={`Use ${t.label}`} aria-pressed={selected}>
        {!loaded && !failed && <span className="absolute inset-0 animate-pulse bg-gradient-to-br from-slate-100 to-slate-200" />}
        {failed
          ? <span className="absolute inset-0 grid place-items-center px-3 text-center text-xs text-muted">Preview unavailable</span>
          : <img src={src} alt="" loading="lazy" onLoad={() => setLoaded(true)} onError={() => setFailed(true)}
              className={`absolute inset-0 h-full w-full object-cover transition-opacity ${loaded ? "opacity-100" : "opacity-0"}`} />}
        {t.three_d && <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-slate-900/70 px-2 py-0.5 text-[11px] font-medium text-white"><Box size={11} />3D</span>}
        {selected && <span className="absolute right-2 top-2 grid h-6 w-6 place-items-center rounded-full bg-primary-600 text-white"><Check size={14} /></span>}
      </button>
      <div className={compact ? "px-2.5 py-2" : "px-3 py-2.5"}>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium" title={t.label}>{t.label}</span>
          {onTheme && t.themes.length > 1 && (
            <span className="flex gap-1">
              {t.themes.map((th) => (
                <button key={th} type="button" title={THEME_LABEL[th] ?? th} aria-label={`${THEME_LABEL[th] ?? th} look`}
                  onClick={() => onTheme(th)}
                  className={`h-4 w-4 rounded-full ${THEME_DOT[th] ?? "bg-slate-400"} ${theme === th ? "ring-2 ring-primary-500 ring-offset-1" : ""}`} />
              ))}
            </span>
          )}
        </div>
        {!compact && <p className="mt-0.5 line-clamp-2 text-xs text-muted">{t.description}</p>}
      </div>
    </div>
  );
}

/** Compact picker used inside the Create tab. */
export function TemplatePicker({ kind, value, onChange, size }: { kind: string; value: string; onChange: (name: string) => void; size: string }) {
  const tq = useTemplates(size);
  const [group, setGroup] = useState("All");
  const list = useMemo(() => (tq.data?.templates ?? []).filter((t) => t.kinds.includes(kind) && t.name !== "video_caption_overlay"
    && !t.name.startsWith("carousel_")), [tq.data, kind]);
  const groups = useMemo(() => ["All", ...Array.from(new Set(list.map((t) => t.group)))], [list]);
  const shown = list.filter((t) => group === "All" || t.group === group);
  if (tq.isLoading) return <Spinner label="Loading templates…" />;
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label="Template groups">
        <button type="button" onClick={() => onChange("auto")}
          className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium ${value === "auto" ? "border-primary-500 bg-brand-tint text-brand" : "border-line text-muted hover:text-ink"}`}>
          <Sparkles size={12} />Pick for me
        </button>
        {groups.map((g) => (
          <button key={g} type="button" role="tab" aria-selected={group === g} onClick={() => setGroup(g)}
            className={`rounded-full border px-3 py-1 text-xs font-medium ${group === g ? "border-ink/20 bg-ink/5 text-ink" : "border-line text-muted hover:text-ink"}`}>{g}</button>
        ))}
      </div>
      <div className="grid max-h-[420px] grid-cols-3 gap-2.5 overflow-y-auto pr-1 sm:grid-cols-4">
        {shown.map((t) => <TemplateCard key={t.name} t={t} size={size} compact selected={value === t.name} onPick={() => onChange(t.name)} />)}
      </div>
    </div>
  );
}

/** Templates tab: the whole library with real previews per format. */
export default function Gallery({ onUse }: { onUse: (t: Template, theme?: string) => void }) {
  const [size, setSize] = useState(() => { try { return sessionStorage.getItem("media.gallerySize") || "1080x1350"; } catch { return "1080x1350"; } });
  const tq = useTemplates(size);
  const [group, setGroup] = useState("All");
  const [themes, setThemes] = useState<Record<string, string>>({});
  const list = useMemo(() => (tq.data?.templates ?? []).filter((t) => t.name !== "video_caption_overlay"), [tq.data]);
  const groups = useMemo(() => ["All", ...Array.from(new Set(list.map((t) => t.group)))], [list]);
  const shown = list.filter((t) => group === "All" || t.group === group);
  const changeSize = (v: string) => { setSize(v); try { sessionStorage.setItem("media.gallerySize", v); } catch { /* storage unavailable */ } };

  if (tq.isLoading) return <Spinner label="Loading templates…" />;
  if (tq.error) return <Alert>{(tq.error as Error).message}</Alert>;
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Template groups">
          {groups.map((g) => (
            <button key={g} type="button" role="tab" aria-selected={group === g} onClick={() => setGroup(g)}
              className={`rounded-full border px-3 py-1.5 text-sm font-medium ${group === g ? "border-primary-500 bg-brand-tint text-brand" : "border-line text-muted hover:text-ink"}`}>
              {g}
            </button>
          ))}
        </div>
        <Select aria-label="Preview format" value={size} onChange={(e) => changeSize(e.target.value)} className="!w-56">
          {(tq.data?.preview_sizes ?? []).map((s) => <option key={s} value={s}>{PREVIEW_SIZE_LABEL[s] ?? s}</option>)}
        </Select>
      </div>
      <p className="mb-4 text-sm text-muted">Every preview is a real render in your brand colours and logo. Posts pick a layout automatically from
        their content; choose one here to use it yourself. Templates also animate as GIFs and MP4s.</p>
      <div className={`grid gap-4 ${size === "1920x1080" ? "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3" : "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"}`}>
        {shown.map((t) => (
          <div key={t.name} className="flex flex-col">
            <TemplateCard t={t} size={size} theme={themes[t.name]} onTheme={(th) => setThemes((s) => ({ ...s, [t.name]: th }))}
              onPick={() => onUse(t, themes[t.name])} />
            <Button variant="secondary" className="mt-2" onClick={() => onUse(t, themes[t.name])}>
              {t.three_d ? "Open in 3D" : "Use this template"}
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
