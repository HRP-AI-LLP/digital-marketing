import { useSearchParams } from "react-router-dom";
import { PageHeader, Tabs } from "../components/ui";
import { CompetitorsPanel } from "./analytics/competitors";
import { GrowthPanel, Segmented } from "./analytics/growth";
import { HealthPanel } from "./analytics/health";
import { OverviewPanel } from "./analytics/overview";
import { SearchPanel, SettingsPanel, SpeedPanel, WebsitePanel } from "./analytics/panels";
import { ReportsPanel } from "./analytics/reports";

type Tab = "overview" | "growth" | "competitors" | "health" | "reports" | "website" | "settings";
type Site = "traffic" | "search" | "speed";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "growth", label: "Growth" }, { id: "competitors", label: "Competitors" },
  { id: "health", label: "Health & tips" }, { id: "reports", label: "Reports" }, { id: "website", label: "Website & search" },
  { id: "settings", label: "Settings" },
];
// Old links (?tab=social|search|speed) keep working.
const LEGACY: Record<string, [Tab, Site?]> = { social: ["growth"], search: ["website", "search"], speed: ["website", "speed"] };

export default function Analytics() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") ?? "";
  const [legacyTab, legacySite] = LEGACY[raw] ?? [];
  const tab: Tab = legacyTab ?? (TABS.some((t) => t.id === raw) ? (raw as Tab) : "overview");
  const siteRaw = (params.get("view") as Site | null) ?? legacySite ?? "traffic";
  const site: Site = ["traffic", "search", "speed"].includes(siteRaw) ? siteRaw : "traffic";
  return (
    <>
      <PageHeader title="Analytics" description="How your audience is growing, how you compare with competitors, and what to do next." />
      <Tabs value={tab} onChange={(t) => setParams(t === "overview" ? {} : { tab: t }, { replace: true })} items={TABS} />
      {tab === "overview" && <OverviewPanel />}
      {tab === "growth" && <GrowthPanel />}
      {tab === "competitors" && <CompetitorsPanel />}
      {tab === "health" && <HealthPanel />}
      {tab === "reports" && <ReportsPanel />}
      {tab === "website" && (
        <>
          <div className="mb-5">
            <Segmented label="Website data" value={site} onChange={(v) => setParams({ tab: "website", view: v }, { replace: true })}
              items={[{ id: "traffic", label: "Traffic (Google Analytics)" }, { id: "search", label: "Search (Search Console)" }, { id: "speed", label: "Speed (PageSpeed)" }]} />
          </div>
          {site === "traffic" && <WebsitePanel />}
          {site === "search" && <SearchPanel />}
          {site === "speed" && <SpeedPanel />}
        </>
      )}
      {tab === "settings" && <SettingsPanel />}
    </>
  );
}
