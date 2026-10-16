import { useLocation } from "react-router-dom";
import { NAV } from "../components/Layout";
import { PageHeader } from "../components/ui";

const PLANS: Record<string, { phase: string; what: string }> = {
  "/calendar": { phase: "Phase 3", what: "See every planned and published post on a calendar, by platform." },
  "/approvals": { phase: "Phase 3", what: "Review drafts, edit or regenerate them, and approve them from here, Telegram or email." },
  "/media": { phase: "Phase 6", what: "Branded images, carousels, GIFs, short videos and animations for each platform's format." },
  "/seo": { phase: "Phase 7", what: "Keyword-targeted articles for your website, stored here until the website is connected." },
  "/intelligence": { phase: "Phase 2", what: "Your company profile, competitors found automatically, a digital-presence audit and topic ideas." },
  "/analytics": { phase: "Phase 7", what: "Engagement for each post, platform and topic, plus Google Analytics and Search Console data." },
  "/settings/integrations": { phase: "Phase 1", what: "Connect AI providers (local Ollama, Gemini, OpenAI, Anthropic, Grok, Hugging Face and more) and social accounts." },
  "/settings/notifications": { phase: "Phase 4", what: "Choose which events reach you in the app, by email or on Telegram." },
};

export default function ComingSoon() {
  const { pathname } = useLocation();
  const item = NAV.flatMap((g) => g.items).find((i) => i.to === pathname);
  const plan = PLANS[pathname];
  return (
    <div>
      <PageHeader title={item?.label ?? "Coming soon"} />
      <div className="max-w-xl rounded-lg border border-dashed border-line bg-surface p-6">
        <p className="text-lg">{plan?.what ?? "This part of the app is planned."}</p>
        {plan && <p className="mt-2 text-muted">Arrives in {plan.phase} of the build plan.</p>}
      </div>
    </div>
  );
}
