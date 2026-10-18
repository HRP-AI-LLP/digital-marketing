import { useSearchParams } from "react-router-dom";
import { PageHeader, Tabs } from "../components/ui";
import { GapTab } from "./keywords/Gap";
import { ListsTab } from "./keywords/Lists";
import { RankingsTab } from "./keywords/Rankings";
import { ResearchTab } from "./keywords/Research";
import { SettingsTab } from "./keywords/SettingsTab";

type Tab = "research" | "gap" | "rankings" | "lists" | "settings";
const TABS: { id: Tab; label: string }[] = [
  { id: "research", label: "Research" }, { id: "gap", label: "Competitor gap" }, { id: "rankings", label: "Rank tracking" },
  { id: "lists", label: "Lists & clusters" }, { id: "settings", label: "Settings" },
];

export default function Keywords() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: Tab = TABS.some((t) => t.id === raw) ? (raw as Tab) : "research";
  return (
    <>
      <PageHeader title="Keywords & rankings" description="Find what your customers search for, see where competitors are ahead, and track your Google positions every day." />
      <Tabs value={tab} onChange={(t) => setParams(t === "research" ? {} : { tab: t }, { replace: true })} items={TABS} />
      {tab === "research" && <ResearchTab />}
      {tab === "gap" && <GapTab />}
      {tab === "rankings" && <RankingsTab />}
      {tab === "lists" && <ListsTab />}
      {tab === "settings" && <SettingsTab />}
    </>
  );
}
