import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, PageHeader, Tabs } from "../components/ui";
import CanvaTab from "./media/Canva";
import AiImage from "./media/AiImage";
import Create from "./media/Create";
import Gallery from "./media/Gallery";
import Library from "./media/Library";
import Stock from "./media/Stock";
import ThreeD from "./media/ThreeD";
import type { Template } from "./media/shared";

type Tab = "library" | "templates" | "create" | "3d" | "ai" | "stock" | "canva";
type Kind = "image" | "carousel" | "gif" | "animation" | "video" | "document";

export default function MediaStudio() {
  const [tab, setTab] = useState<Tab>(() => {
    try { return (sessionStorage.getItem("media.tab") as Tab) || "library"; } catch { return "library"; }
  });
  const [initial, setInitial] = useState<{ template: string; theme?: string; kind?: Kind } | null>(null);
  const [scene, setScene] = useState<string | undefined>();
  const [params, setParams] = useSearchParams();
  const [canvaNote, setCanvaNote] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  useEffect(() => {
    // Back from Canva sign-in (or ?tab=canva links): open the Canva tab and say how it went.
    const connected = params.get("connected"), error = params.get("error"), t = params.get("tab");
    if (!connected && !error && !t) return;
    if (connected === "canva" || error || t === "canva") change("canva");
    if (connected === "canva") setCanvaNote({ tone: "info", text: "Canva connected. Pick a design to import." });
    if (error) setCanvaNote({ tone: "danger", text: error });
    setParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  const change = (t: Tab) => { setTab(t); try { sessionStorage.setItem("media.tab", t); } catch { /* storage unavailable */ } };
  const useTemplate = (t: Template, theme?: string) => {
    if (t.three_d) { setScene(t.name); change("3d"); return; }
    const kind: Kind = t.kinds.includes("image") ? "image" : t.kinds.includes("carousel") ? "carousel" : "gif";
    setInitial({ template: t.name, theme, kind });
    change("create");
  };
  return (
    <div>
      <PageHeader
        title="Media studio"
        description="Agency-grade branded images, carousels, GIFs, animations, 3D scenes and short videos in each platform's format, plus your media library."
      />
      <Tabs<Tab> value={tab} onChange={change} items={[
        { id: "library", label: "Library" },
        { id: "templates", label: "Templates" },
        { id: "create", label: "Create" },
        { id: "3d", label: "3D" },
        { id: "ai", label: "AI image" },
        { id: "stock", label: "Stock" },
        { id: "canva", label: "From Canva" },
      ]} />
      {canvaNote && tab === "canva" && <div className="mb-3"><Alert tone={canvaNote.tone}>{canvaNote.text}</Alert></div>}
      {tab === "library" && <Library />}
      {tab === "templates" && <Gallery onUse={useTemplate} />}
      {tab === "create" && <Create initial={initial} />}
      {tab === "3d" && <ThreeD initialScene={scene} />}
      {tab === "ai" && <AiImage />}
      {tab === "stock" && <Stock />}
      {tab === "canva" && <CanvaTab />}
    </div>
  );
}
