import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { api } from "../../api/client";
import { upload } from "../../api/hooks";
import { Alert, Button, Card, Field, Input } from "../../components/ui";

type Look = {
  colors: Record<string, string>;
  logo_asset_id: string | null;
  logo_url: string | null;
  look: { display_name: string | null; website: string | null; handle: string | null };
};

const DEFAULTS: Record<string, string> = { primary: "#075492", secondary: "#06a6c7", dark: "#062f59", light: "#f8fafc" };
const LABELS: Record<string, string> = {
  primary: "Main colour", secondary: "Accent colour", dark: "Dark background", light: "Light background",
};

export default function BrandLook({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brand"], queryFn: () => api<Look>("/content/brand") });
  const [colors, setColors] = useState<Record<string, string>>(DEFAULTS);
  const [logo, setLogo] = useState<{ id: string | null; url: string | null }>({ id: null, url: null });
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [handle, setHandle] = useState("");
  const [saved, setSaved] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!q.data) return;
    setColors({ ...DEFAULTS, ...q.data.colors });
    setLogo({ id: q.data.logo_asset_id, url: q.data.logo_url });
    setName(q.data.look.display_name ?? "");
    setWebsite(q.data.look.website ?? "");
    setHandle(q.data.look.handle ?? "");
  }, [q.data]);

  const up = useMutation({
    mutationFn: (f: File) => {
      const form = new FormData();
      form.append("files", f, f.name);
      return upload<{ items: { id: string; url: string }[]; errors: string[] }>("/media/upload", form);
    },
    onSuccess: (r) => { if (r.items[0]) setLogo({ id: r.items[0].id, url: r.items[0].url }); },
  });
  const save = useMutation({
    mutationFn: () => api("/content/brand/look", {
      method: "PUT",
      json: { colors, logo_asset_id: logo.id, display_name: name || null, website: website || null, handle: handle || null },
    }),
    onSuccess: () => { setSaved(true); qc.invalidateQueries({ queryKey: ["brand"] }); },
  });
  const err = (save.error || up.error) as Error | null;

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <div className="space-y-6">
        <Card>
          <h2 className="font-semibold">Colours</h2>
          <p className="text-sm text-muted">Used on every generated image, carousel and video. Defaults match hrpai.in.</p>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {Object.keys(DEFAULTS).map((k) => (
              <Field key={k} label={LABELS[k]}>
                <div className="flex items-center gap-2">
                  <input type="color" aria-label={LABELS[k]} disabled={!canEdit} value={colors[k]}
                    onChange={(e) => setColors({ ...colors, [k]: e.target.value })}
                    className="h-10 w-12 cursor-pointer rounded-lg border border-line bg-surface p-1" />
                  <Input className="w-32 font-mono" disabled={!canEdit} value={colors[k]}
                    onChange={(e) => setColors({ ...colors, [k]: e.target.value })} />
                </div>
              </Field>
            ))}
          </div>
          <button className="mt-3 text-sm text-primary-700 hover:underline disabled:opacity-50" disabled={!canEdit} onClick={() => setColors(DEFAULTS)}>
            Reset to hrpai.in colours
          </button>
        </Card>
        <Card>
          <h2 className="font-semibold">Logo and name</h2>
          <p className="text-sm text-muted">Shown in the corner of images and at the end of videos. A square PNG with a transparent background works best.</p>
          <div className="mt-4 flex items-center gap-4">
            <div className="grid h-20 w-20 place-items-center overflow-hidden rounded-xl border border-line bg-white">
              {logo.url ? <img src={logo.url} alt="Logo" className="h-full w-full object-contain p-1" /> : <span className="text-xs text-muted">Default</span>}
            </div>
            {canEdit && (
              <div className="flex gap-2">
                <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) up.mutate(f); e.target.value = ""; }} />
                <Button variant="secondary" onClick={() => fileRef.current?.click()} disabled={up.isPending}><Upload size={15} /> {up.isPending ? "Uploading…" : "Upload logo"}</Button>
                {logo.id && <Button variant="ghost" onClick={() => setLogo({ id: null, url: null })}>Use default</Button>}
              </div>
            )}
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <Field label="Name on images"><Input disabled={!canEdit} value={name} onChange={(e) => setName(e.target.value)} placeholder="HRP AI" /></Field>
            <Field label="Website on images"><Input disabled={!canEdit} value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="hrpai.in" /></Field>
            <Field label="Handle (optional)"><Input disabled={!canEdit} value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@hrpai" /></Field>
          </div>
        </Card>
        {err && <Alert>{err.message}</Alert>}
        {saved && !save.isPending && <Alert tone="info">Saved. New images use these settings.</Alert>}
        {canEdit && <Button onClick={() => { setSaved(false); save.mutate(); }} disabled={save.isPending}>Save look</Button>}
      </div>

      <aside>
        <div className="text-sm text-muted">Preview</div>
        <div className="mt-2 aspect-square overflow-hidden rounded-xl p-6 text-white shadow-lg"
          style={{ background: `linear-gradient(140deg, ${colors.dark}, ${colors.primary})` }}>
          <div className="flex items-center gap-2">
            <div className="grid h-9 w-9 place-items-center overflow-hidden rounded-lg bg-white">
              {logo.url ? <img src={logo.url} alt="" className="h-full w-full object-contain p-0.5" /> : <img src="/logo-mark.png" alt="" className="h-full w-full object-contain p-0.5" />}
            </div>
            <span className="text-sm font-semibold">{name || "HRP AI"}</span>
          </div>
          <div className="mt-16 text-3xl font-bold leading-tight">Your headline sits here.</div>
          <div className="mt-6 h-1 w-16 rounded-full" style={{ background: colors.secondary }} />
          <div className="mt-10 text-right text-xs opacity-80">{website || "hrpai.in"}</div>
        </div>
      </aside>
    </div>
  );
}
