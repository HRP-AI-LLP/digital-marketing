import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { PageHeader, Spinner, Tabs } from "../components/ui";
import AuditTab from "./intelligence/Audit";
import CompetitorsTab from "./intelligence/Competitors";
import IdeasTab from "./intelligence/Ideas";
import OverviewTab from "./intelligence/Overview";
import ProfileTab from "./intelligence/Profile";
import SourcesTab from "./intelligence/Sources";
import { ErrorText, JobsProvider, useIntelJobs } from "./intelligence/shared";
import { jobKey, type IntelTab, type Overview } from "./intelligence/types";

const TABS: readonly IntelTab[] = ["overview", "profile", "sources", "competitors", "audit", "ideas"];

function useOverview() {
  return useQuery({
    queryKey: ["intel", "overview"],
    queryFn: () => api<Overview>("/intel/overview"),
    refetchInterval: (q) => ((q.state.data?.running_jobs.length ?? 0) > 0 ? 5000 : false),
  });
}

function Page() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: IntelTab = TABS.find((t) => t === raw) ?? "overview";
  const setTab = (t: IntelTab) => setParams(t === "overview" ? {} : { tab: t }, { replace: true });
  const overview = useOverview();
  const { track } = useIntelJobs();

  // Pick up tasks that are already running (started earlier, in another tab, or by the weekly refresh).
  useEffect(() => {
    for (const j of overview.data?.running_jobs ?? []) track(jobKey(j.kind, j.payload), j.id);
  }, [overview.data, track]);

  const c = overview.data?.counts;
  const items: { id: IntelTab; label: string; count?: number }[] = [
    { id: "overview", label: "Overview" },
    { id: "profile", label: "Company profile", count: c?.entities_total || undefined },
    { id: "sources", label: "Sources", count: c ? (overview.data?.website_source ? 1 : 0) + c.uploads || undefined : undefined },
    { id: "competitors", label: "Competitors", count: c ? (c.competitors.confirmed ?? 0) + (c.competitors.suggested ?? 0) || undefined : undefined },
    { id: "audit", label: "Presence audit" },
    { id: "ideas", label: "Topic ideas", count: c?.ideas.new || undefined },
  ];

  return (
    <div>
      <PageHeader
        title="Company intelligence"
        description="What your website, documents and competitors say about you. Every post and article is grounded in this."
      />
      <Tabs value={tab} onChange={setTab} items={items} />
      {overview.isLoading && <Spinner label="Loading…" />}
      <ErrorText error={overview.error} />
      {overview.data && (
        <>
          {tab === "overview" && <OverviewTab data={overview.data} goTo={setTab} />}
          {tab === "profile" && <ProfileTab overview={overview.data} />}
          {tab === "sources" && <SourcesTab overview={overview.data} />}
          {tab === "competitors" && <CompetitorsTab overview={overview.data} />}
          {tab === "audit" && <AuditTab overview={overview.data} />}
          {tab === "ideas" && <IdeasTab overview={overview.data} />}
        </>
      )}
    </div>
  );
}

export default function Intelligence() {
  return (
    <JobsProvider>
      <Page />
    </JobsProvider>
  );
}
