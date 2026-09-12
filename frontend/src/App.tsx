import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/auth";
import AssetDetail from "@/pages/AssetDetail";
import Assets from "@/pages/Assets";
import Cuts from "@/pages/Cuts";
import Login from "@/pages/Login";
import Narrative from "@/pages/Narrative";
import ProjectMembers from "@/pages/ProjectMembers";
import Projects from "@/pages/Projects";
import Prompts from "@/pages/Prompts";
import ScriptEditor from "@/pages/ScriptEditor";
import Scripts from "@/pages/Scripts";
import Settings from "@/pages/Settings";
import Shots from "@/pages/Shots";
import StoryBible from "@/pages/StoryBible";
import Storyboard from "@/pages/Storyboard";
import Trash from "@/pages/Trash";
import Workbench from "@/pages/Workbench";
import Tasks from "@/pages/Tasks";

function Protected() {
  const { me, loading } = useAuth();
  if (loading) return <div className="p-6 text-muted-foreground">加载中…</div>;
  if (!me) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protected />}>
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/:projectId" element={<AppShell />}>
          <Route index element={<Navigate to="workbench" replace />} />
          <Route path="workbench" element={<Workbench />} />
          <Route path="narrative" element={<Narrative />} />
          <Route path="bible" element={<StoryBible />} />
          <Route path="scripts" element={<Scripts />} />
          <Route path="scripts/:scriptId" element={<ScriptEditor />} />
          <Route path="assets" element={<Assets />} />
          <Route path="assets/trash" element={<Trash />} />
          <Route path="assets/:assetId" element={<AssetDetail />} />
          <Route path="shots" element={<Shots />} />
          <Route path="storyboard" element={<Storyboard />} />
          <Route path="prompts" element={<Prompts />} />
          <Route path="cuts" element={<Cuts />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="members" element={<ProjectMembers />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/projects" replace />} />
    </Routes>
  );
}
