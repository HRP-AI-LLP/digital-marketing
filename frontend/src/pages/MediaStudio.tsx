import { useState } from "react";
import { PageHeader, Tabs } from "../components/ui";
import AiImage from "./media/AiImage";
import Create from "./media/Create";
import Library from "./media/Library";
import Stock from "./media/Stock";

type Tab = "library" | "create" | "ai" | "stock";

export default function MediaStudio() {
  const [tab, setTab] = useState<Tab>(() => {
    try { return (sessionStorage.getItem("media.tab") as Tab) || "library"; } catch { return "library"; }
  });
  const change = (t: Tab) => { setTab(t); try { sessionStorage.setItem("media.tab", t); } catch { /* storage unavailable */ } };
  return (
    <div>
      <PageHeader
        title="Media studio"
        description="Branded images, carousels, GIFs and short videos in each platform's format, plus your media library."
      />
      <Tabs<Tab> value={tab} onChange={change} items={[
        { id: "library", label: "Library" },
        { id: "create", label: "Create" },
        { id: "ai", label: "AI image" },
        { id: "stock", label: "Stock" },
      ]} />
      {tab === "library" && <Library />}
      {tab === "create" && <Create />}
      {tab === "ai" && <AiImage />}
      {tab === "stock" && <Stock />}
    </div>
  );
}
