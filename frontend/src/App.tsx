import { lazy } from "react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/auth";
import Login from "@/pages/Login";
import Projects from "@/pages/Projects";

const AssetDetail = lazy(() => import("@/pages/AssetDetail"));
const Assets = lazy(() => import("@/pages/Assets"));
const Skills = lazy(() => import("@/pages/Skills"));
const Agent = lazy(() => import("@/pages/Agent"));
const Canvas = lazy(() => import("@/pages/Canvas"));
const Director = lazy(() => import("@/pages/Director"));
const Evidence = lazy(() => import("@/pages/Evidence"));
const Transcriptions = lazy(() => import("@/pages/Transcriptions"));
const Cuts = lazy(() => import("@/pages/Cuts"));
const Narrative = lazy(() => import("@/pages/Narrative"));
const ProjectMembers = lazy(() => import("@/pages/ProjectMembers"));
const Prompts = lazy(() => import("@/pages/Prompts"));
const ScriptEditor = lazy(() => import("@/pages/ScriptEditor"));
const Scripts = lazy(() => import("@/pages/Scripts"));
const AssetImport = lazy(() => import("@/pages/AssetImport"));
const Settings = lazy(() => import("@/pages/Settings"));
const Shots = lazy(() => import("@/pages/Shots"));
const StoryBible = lazy(() => import("@/pages/StoryBible"));
const Storyboard = lazy(() => import("@/pages/Storyboard"));
const Trash = lazy(() => import("@/pages/Trash"));
const Workbench = lazy(() => import("@/pages/Workbench"));
const Tasks = lazy(() => import("@/pages/Tasks"));
const MediaLibrary = lazy(() => import("@/pages/MediaLibrary"));

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
          <Route path="library" element={<MediaLibrary />} />
          <Route path="assets/import" element={<AssetImport />} />
          <Route path="assets/trash" element={<Trash />} />
          <Route path="assets/:assetId" element={<AssetDetail />} />
          <Route path="shots" element={<Shots />} />
          <Route path="storyboard" element={<Storyboard />} />
          <Route path="prompts" element={<Prompts />} />
          <Route path="skills" element={<Skills />} />
          <Route path="director" element={<Director />} />
          <Route path="evidence" element={<Evidence />} />
          <Route path="transcriptions" element={<Transcriptions />} />
          <Route path="agent" element={<Agent />} />
          <Route path="canvas" element={<Canvas />} />
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
