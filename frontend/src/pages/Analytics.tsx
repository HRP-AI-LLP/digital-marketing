import { useSearchParams } from "react-router-dom";
import { PageHeader, Tabs } from "../components/ui";
import { SearchPanel, SettingsPanel, SocialPanel, SpeedPanel, WebsitePanel } from "./analytics/panels";

type Tab = "social" | "website" | "search" | "speed" | "settings";
const TABS: { id: Tab; label: string }[] = [
  { id: "social", label: "Social" }, { id: "website", label: "Website" }, { id: "search", label: "Search" },
  { id: "speed", label: "Speed" }, { id: "settings", label: "Settings" },
];

export default function Analytics() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") as Tab | null;
  const tab: Tab = raw && TABS.some((t) => t.id === raw) ? raw : "social";
  return (
    <>
      <PageHeader title="Analytics" description="How your posts, website and search presence are performing." />
      <Tabs value={tab} onChange={(t) => setParams(t === "social" ? {} : { tab: t }, { replace: true })} items={TABS} />
      {tab === "social" && <SocialPanel />}
      {tab === "website" && <WebsitePanel />}
      {tab === "search" && <SearchPanel />}
      {tab === "speed" && <SpeedPanel />}
      {tab === "settings" && <SettingsPanel />}
    </>
  );
}
