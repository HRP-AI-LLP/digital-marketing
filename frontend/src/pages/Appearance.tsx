import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, Globe2, Languages, Search } from "lucide-react";
import { Button, Card, Input, PageHeader, Toggle } from "../components/ui";
import { usePrefs } from "../prefs/PrefsContext";
import { ACCENTS, FONTS, TEXT_SIZES, THEMES, accentSwatch, loadFont, type Prefs } from "../prefs/appearance";
import { LANGUAGES, langInfo, translator, type Lang } from "../prefs/i18n";

/** Settings → Appearance & language (per person, follows them to any device). */
export default function Appearance() {
  const { prefs, update } = usePrefs();
  // Show each font in its own typeface on this page.
  useEffect(() => { FONTS.forEach((f) => loadFont(f.family, f.google)); }, []);
  return (
    <div className="max-w-5xl">
      <PageHeader title="Appearance & language"
        description="Make the studio look and read the way you like. These choices are yours alone and follow you to any device you sign in on." />
      <div className="space-y-5">
        <LanguageCard prefs={prefs} update={update} />
        <Card>
          <H title="Theme" sub="Colours of pages, cards and charts." />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {THEMES.map((t) => {
              const on = (prefs.theme ?? "light") === t.key;
              return (
                <button key={t.key} type="button" onClick={() => update({ theme: t.key })} aria-pressed={on}
                  className={`overflow-hidden rounded-xl border text-left transition ${on ? "border-primary-500 ring-2 ring-primary-500/30" : "border-line hover:border-primary-300"}`}>
                  <div className="flex h-20 gap-1.5 p-2.5" style={{ background: t.swatch[0] }}>
                    <div className="w-1/4 rounded-md bg-[#0b1d33]" />
                    <div className="flex flex-1 flex-col gap-1.5 rounded-md p-2" style={{ background: t.swatch[1], border: "1px solid rgba(127,127,127,.18)" }}>
                      <div className="h-1.5 w-2/3 rounded-full" style={{ background: t.swatch[2] }} />
                      <div className="h-1.5 w-1/2 rounded-full opacity-40" style={{ background: t.swatch[2] }} />
                      <div className="mt-auto flex items-end gap-1">{[40, 70, 55, 90].map((h, i) => <span key={i} className="w-2 rounded-sm" style={{ height: h / 6, background: t.swatch[2], opacity: 0.4 + i * 0.15 }} />)}</div>
                    </div>
                  </div>
                  <div className="flex items-start gap-2 px-3 py-2">
                    <div className="min-w-0 flex-1"><div className="text-sm font-semibold text-ink">{t.name}</div><div className="text-xs text-muted">{t.note}</div></div>
                    {on && <Check size={16} className="mt-0.5 text-primary-700" />}
                  </div>
                </button>
              );
            })}
          </div>
        </Card>
        <Card>
          <H title="Accent colour" sub="Buttons, links, highlights and selections." />
          <div className="flex flex-wrap gap-3">
            {ACCENTS.map((a) => {
              const on = (prefs.accent ?? "blue") === a.key;
              return (
                <button key={a.key} type="button" onClick={() => update({ accent: a.key })} aria-pressed={on}
                  className={`flex items-center gap-2 rounded-full border py-1.5 pl-1.5 pr-3 text-sm ${on ? "border-primary-500 bg-primary-50 font-medium text-ink" : "border-line text-muted hover:text-ink"}`}>
                  <span className="grid h-6 w-6 place-items-center rounded-full" style={{ background: accentSwatch(a.key) }}>{on && <Check size={14} className="text-white" />}</span>
                  {a.name}
                </button>
              );
            })}
          </div>
        </Card>
        <Card>
          <H title="Font" sub="The typeface for the whole studio. For Indian and other scripts, a matching font is added automatically." />
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {FONTS.map((f) => {
              const on = (prefs.font ?? "inter") === f.key;
              return (
                <button key={f.key} type="button" onClick={() => update({ font: f.key })} aria-pressed={on}
                  className={`rounded-xl border px-4 py-3 text-left ${on ? "border-primary-500 bg-primary-50" : "border-line hover:border-primary-300"}`}
                  style={{ fontFamily: `${f.family}, system-ui` }}>
                  <div className="flex items-center gap-2"><span className="text-[15px] font-semibold text-ink">{f.name}</span>
                    {f.note && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-muted">{f.note}</span>}
                    {on && <Check size={15} className="ml-auto text-primary-700" />}</div>
                  <div className="mt-1 truncate text-sm text-muted" translate="no">Schedule 12 posts · Engagement up 8.4%</div>
                </button>
              );
            })}
          </div>
        </Card>
        <Card>
          <H title="Layout" sub="Size, spacing and shape." />
          <div className="grid gap-5 md:grid-cols-3">
            <Segment label="Text size" value={prefs.text_size ?? "m"} onChange={(v) => update({ text_size: v as Prefs["text_size"] })}
              options={TEXT_SIZES.map((s) => ({ key: s.key, name: s.name }))} />
            <Segment label="Spacing" value={prefs.density ?? "comfortable"} onChange={(v) => update({ density: v as Prefs["density"] })}
              options={[{ key: "comfortable", name: "Comfortable" }, { key: "compact", name: "Compact" }]} />
            <Segment label="Corners" value={prefs.radius ?? "rounded"} onChange={(v) => update({ radius: v as Prefs["radius"] })}
              options={[{ key: "rounded", name: "Rounded" }, { key: "soft", name: "Soft" }, { key: "sharp", name: "Square" }]} />
          </div>
          <div className="mt-5"><Toggle checked={!!prefs.reduce_motion} onChange={(v) => update({ reduce_motion: v })} label="Reduce motion (no animations)" /></div>
        </Card>
        <div className="flex justify-end">
          <Button variant="ghost" onClick={() => update({ theme: "light", accent: "blue", font: "inter", text_size: "m", density: "comfortable", radius: "rounded", reduce_motion: false })}>
            Reset look to default
          </Button>
        </div>
      </div>
    </div>
  );
}

function H({ title, sub }: { title: string; sub: string }) {
  return <div className="mb-4"><h2 className="text-lg font-semibold text-ink">{title}</h2><p className="text-sm text-muted">{sub}</p></div>;
}

function Segment({ label, value, options, onChange }: { label: string; value: string; options: { key: string; name: string }[]; onChange: (v: string) => void }) {
  return (
    <div>
      <div className="mb-1.5 text-sm font-medium text-ink">{label}</div>
      <div role="radiogroup" aria-label={label} className="inline-flex flex-wrap rounded-lg border border-line bg-surface p-0.5">
        {options.map((o) => (
          <button key={o.key} type="button" role="radio" aria-checked={value === o.key} onClick={() => onChange(o.key)}
            className={`rounded-md px-3 py-1.5 text-sm ${value === o.key ? "bg-primary-600 font-medium text-white" : "text-muted hover:text-ink"}`}>{o.name}</button>
        ))}
      </div>
    </div>
  );
}

function useTranslatorState() {
  const [s, setS] = useState({ pending: 0, error: null as string | null });
  useEffect(() => translator.subscribe(setS), []);
  return s;
}

function LanguageCard({ prefs, update }: { prefs: Prefs; update: (p: Partial<Prefs>) => void }) {
  const [q, setQ] = useState("");
  const [other, setOther] = useState("");
  const st = useTranslatorState();
  const current = langInfo(prefs.language ?? "en", prefs.language_name);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return LANGUAGES.filter((l) => !t || l.name.toLowerCase().includes(t) || l.native.toLowerCase().includes(t) || l.code.toLowerCase() === t);
  }, [q]);
  const pick = (l: Lang) => update({ language: l.code, language_name: l.name });
  const group = (g: Lang["group"], title: ReactNode) => {
    const items = list.filter((l) => l.group === g);
    if (!items.length) return null;
    return (
      <div>
        <div className="mb-2 mt-3 text-xs font-semibold uppercase tracking-wide text-muted">{title}</div>
        <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
          {items.map((l) => {
            const on = current.code.toLowerCase() === l.code.toLowerCase();
            return (
              <button key={l.code} type="button" onClick={() => pick(l)} aria-pressed={on} translate="no"
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left ${on ? "border-primary-500 bg-primary-50" : "border-line hover:border-primary-300"}`}>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[15px] font-medium text-ink" dir={l.rtl ? "rtl" : undefined}>{l.native}</span>
                  <span className="block truncate text-xs text-muted">{l.name}</span>
                </span>
                {on && <Check size={15} className="shrink-0 text-primary-700" />}
              </button>
            );
          })}
        </div>
      </div>
    );
  };
  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <H title="Language" sub="Every menu, button, message and chart label is shown in the language you choose. Posts and your own content stay as they are." />
        <div className="flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1.5 text-sm text-primary-800" translate="no">
          <Globe2 size={16} /> {current.native}{current.native !== current.name ? ` · ${current.name}` : ""}
        </div>
      </div>
      {st.pending > 0 && <p className="mb-3 flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-muted"><Languages size={15} className="animate-pulse" />
        Translating {st.pending} piece{st.pending === 1 ? "" : "s"} of text on this page…</p>}
      {st.error && current.code !== "en" && <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">{st.error}</p>}
      <div className="relative max-w-md">
        <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search: Hindi, தமிழ், Bhojpuri, Español…" className="pl-9" />
      </div>
      <div className="max-h-[26rem] overflow-y-auto pr-1">
        {group("india", "India: national and regional languages")}
        {group("world", "International")}
        {!list.length && <p className="mt-3 text-sm text-muted">Not in the list? Add it below.</p>}
      </div>
      <form className="mt-4 flex flex-wrap items-end gap-2 border-t border-line pt-4"
        onSubmit={(e) => { e.preventDefault(); const n = other.trim(); if (n) { update({ language: n, language_name: n }); setOther(""); } }}>
        <label className="min-w-[16rem] flex-1">
          <span className="mb-1.5 block text-sm font-medium text-ink">Other language or dialect</span>
          <Input value={other} onChange={(e) => setOther(e.target.value)} maxLength={60} placeholder="For example: Sylheti, Tulu, Haryanvi, Swiss German" />
        </label>
        <Button type="submit" variant="secondary" disabled={!other.trim()}>Use this language</Button>
      </form>
      <p className="mt-3 text-xs text-muted">
        Your AI model translates each piece of text the first time it appears and saves it, so pages open instantly after that and
        everyone in the studio who picks the same language shares the translations.
      </p>
    </Card>
  );
}
