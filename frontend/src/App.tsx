import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import Activity from "./pages/Activity";
import ChangePassword from "./pages/ChangePassword";
import ComingSoon from "./pages/ComingSoon";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import OrganizationSettings from "./pages/OrganizationSettings";
import PlatformOrganizations from "./pages/PlatformOrganizations";
import Roles from "./pages/Roles";
import Users from "./pages/Users";
import Integrations from "./pages/Integrations";
import NotificationSettings from "./pages/NotificationSettings";
import Calendar from "./pages/Calendar";
import Approvals from "./pages/Approvals";
import Posts from "./pages/Posts";
import MediaStudio from "./pages/MediaStudio";
import Seo from "./pages/Seo";
import Intelligence from "./pages/Intelligence";
import Analytics from "./pages/Analytics";
import Brand from "./pages/Brand";
import Schedules from "./pages/Schedules";
import SocialAccounts from "./pages/SocialAccounts";
import Inbox from "./pages/inbox/Inbox";
import Research from "./pages/Research";

function Guard({ perm, platform, children }: { perm?: string; platform?: boolean; children: JSX.Element }) {
  const { can, session } = useAuth();
  if (platform && !session?.user.is_platform_admin) return <Navigate to="/" replace />;
  if (perm && !can(perm)) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  const { session, ready } = useAuth();
  if (!ready) return <div className="grid h-full place-items-center text-muted">Loading…</div>;
  if (!session) return <Login />;
  if (session.user.must_change_password) return <ChangePassword forced />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="settings/organization" element={<Guard perm="org.settings.view"><OrganizationSettings /></Guard>} />
        <Route path="settings/users" element={<Guard perm="user.view"><Users /></Guard>} />
        <Route path="settings/roles" element={<Guard perm="role.view"><Roles /></Guard>} />
        <Route path="settings/activity" element={<Guard perm="audit.view"><Activity /></Guard>} />
        <Route path="platform/organizations" element={<Guard platform><PlatformOrganizations /></Guard>} />
        <Route path="settings/integrations" element={<Guard perm="integration.view"><Integrations /></Guard>} />
        <Route path="settings/notifications" element={<NotificationSettings />} />
        <Route path="settings/brand" element={<Guard perm="content.view"><Brand /></Guard>} />
        <Route path="settings/schedules" element={<Guard perm="schedule.view"><Schedules /></Guard>} />
        <Route path="settings/accounts" element={<Guard perm="integration.view"><SocialAccounts /></Guard>} />
        <Route path="calendar" element={<Guard perm="content.view"><Calendar /></Guard>} />
        <Route path="approvals" element={<Guard perm="content.approve"><Approvals /></Guard>} />
        <Route path="posts/*" element={<Guard perm="content.view"><Posts /></Guard>} />
        <Route path="media" element={<Guard perm="media.manage"><MediaStudio /></Guard>} />
        <Route path="seo/*" element={<Guard perm="seo.view"><Seo /></Guard>} />
        <Route path="intelligence" element={<Guard perm="intel.view"><Intelligence /></Guard>} />
        <Route path="research" element={<Guard perm="research.view"><Research /></Guard>} />
        <Route path="analytics" element={<Guard perm="analytics.view"><Analytics /></Guard>} />
        <Route path="inbox" element={<Guard perm="inbox.view"><Inbox /></Guard>} />
        <Route path="account/password" element={<ChangePassword />} />
        <Route path="*" element={<ComingSoon />} />
      </Route>
    </Routes>
  );
}
