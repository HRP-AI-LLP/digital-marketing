import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, Film, Search } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Badge, Button, EmptyState, Input, Select, Spinner } from "../../components/ui";
import { ProviderHint, useCaps, type Asset } from "./shared";

type StockItem = {
  provider: string; type: "photo" | "video"; id: string; width: number | null; height: number | null; preview_url: string | null;
  duration: number | null; author: string | null; author_url: string | null; page_url: string | null; alt: string; attribution: string;
};
type SearchResp = { items: StockItem[]; errors: string[]; providers: string[] };

function StockCard({ item, query }: { item: StockItem; query: string }) {
  const qc = useQueryClient();
  const imp = useMutation({
    mutationFn: () => api<Asset>("/media/stock/import", { method: "POST", json: { provider: item.provider, type: item.type, id: item.id, query } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["media"] }),
  });
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className="relative aspect-[4/3] bg-slate-100">
        {item.preview_url && <img src={item.preview_url} alt={item.alt || item.attribution} loading="lazy" referrerPolicy="no-referrer"
          className="h-full w-full object-cover" />}
        {item.type === "video" && (
          <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded bg-slate-900/70 px-1.5 py-0.5 text-xs text-white">
            <Film size={12} />{item.duration ? `${item.duration}s` : "Video"}
          </span>
        )}
      </div>
      <div className="space-y-2 p-2.5">
        <div className="flex items-center justify-between gap-2">
          <Badge>{item.provider}</Badge>
          {item.width && <span className="text-xs text-muted">{item.width} × {item.height}</span>}
        </div>
        <p className="truncate text-xs text-muted" title={item.attribution}>
          {item.page_url ? <a href={item.page_url} target="_blank" rel="noreferrer" className="hover:underline">{item.attribution}</a> : item.attribution}
        </p>
        {imp.error && <Alert>{(imp.error as Error).message}</Alert>}
        <Button variant={imp.isSuccess ? "secondary" : "primary"} className="w-full" onClick={() => imp.mutate()} disabled={imp.isPending || imp.isSuccess}>
          {imp.isSuccess ? <><Check size={15} />In the library</> : imp.isPending ? "Importing…" : <><Download size={15} />Import</>}
        </Button>
      </div>
    </div>
  );
}

export default function Stock() {
  const caps = useCaps();
  const [q, setQ] = useState("");
  const [type, setType] = useState<"photo" | "video">("photo");
  const [orientation, setOrientation] = useState("");
  const [submitted, setSubmitted] = useState<{ q: string; type: string; orientation: string } | null>(null);

  const res = useQuery({
    queryKey: ["stock", submitted],
    queryFn: () => {
      const p = new URLSearchParams({ q: submitted!.q, type: submitted!.type });
      if (submitted!.orientation) p.set("orientation", submitted!.orientation);
      return api<SearchResp>(`/media/stock/search?${p}`);
    },
    enabled: !!submitted,
    staleTime: 300_000,
  });

  if (caps.isLoading) return <Spinner label="Loading…" />;
  if (caps.data && caps.data.stock.length === 0) return <ProviderHint what="stock" />;

  const submit = (e: FormEvent) => { e.preventDefault(); if (q.trim()) setSubmitted({ q: q.trim(), type, orientation }); };
  return (
    <div>
      <form onSubmit={submit} className="mb-5 flex flex-wrap items-end gap-3">
        <div className="relative min-w-[240px] flex-1">
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input aria-label="Search stock media" placeholder="Search photos and clips, e.g. “team meeting”" value={q}
            onChange={(e) => setQ(e.target.value)} className="pl-9" />
        </div>
        <Select aria-label="Type" value={type} onChange={(e) => setType(e.target.value as "photo" | "video")} className="!w-32">
          <option value="photo">Photos</option>
          <option value="video">Videos</option>
        </Select>
        <Select aria-label="Orientation" value={orientation} onChange={(e) => setOrientation(e.target.value)} className="!w-40">
          <option value="">Any shape</option>
          <option value="portrait">Portrait</option>
          <option value="landscape">Landscape</option>
          <option value="square">Square</option>
        </Select>
        <Button type="submit" disabled={!q.trim()}>Search</Button>
      </form>
      <p className="mb-4 text-xs text-muted">Searching {caps.data?.stock.join(", ")}. Credit lines are saved with each import; add them to captions where the licence asks.</p>
      {!submitted ? <EmptyState title="Search free stock photos and clips" body="Imports go to the library and can be used as backgrounds or video scenes." />
        : res.isLoading ? <Spinner label="Searching…" />
        : res.error ? <Alert>{(res.error as Error).message}</Alert>
        : (
          <>
            {res.data?.errors.map((e) => <div key={e} className="mb-2"><Alert tone="warn">{e}</Alert></div>)}
            {res.data?.items.length ? (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                {res.data.items.map((it) => <StockCard key={`${it.provider}-${it.type}-${it.id}`} item={it} query={submitted.q} />)}
              </div>
            ) : <EmptyState title="No results" body="Try fewer or broader words." />}
          </>
        )}
    </div>
  );
}
