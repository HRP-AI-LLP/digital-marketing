import { PageHeader } from "../components/ui";
import SystemMap from "./dashboard/SystemMap";

/** Full-screen version of the dashboard's system map. */
export default function SystemView() {
  return (
    <div>
      <PageHeader title="System map"
        description="Every AI agent, the services it relies on and each connected channel, with live status and how work flows between them. Updates every 15 seconds." />
      <SystemMap height={Math.max(640, Math.round(window.innerHeight - 230))} />
    </div>
  );
}
