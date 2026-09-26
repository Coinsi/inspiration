import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
const AppShell = lazy(() => import("@/components/AppShell"));
import { useAuth } from "@/lib/auth";
const Login = lazy(() => import("@/pages/Login"));
const Projects = lazy(() => import("@/pages/Projects"));
const Landing = lazy(() => import("@/pages/Landing"));
const WebsiteAdmin = lazy(() => import("@/pages/WebsiteAdmin"));
const Blog = lazy(() => import("@/pages/Blog"));
const BlogArticle = lazy(() => import("@/pages/BlogArticle"));
const BlogAdmin = lazy(() => import("@/pages/BlogAdmin"));

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
  const location = useLocation();
  if (loading) return <div className="p-6 text-muted-foreground">加载中…</div>;
  if (!me)
    return (
      <Navigate
        to={`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`}
        replace
      />
    );
  return <Outlet />;
}

export default function App() {
  return (
    <Suspense fallback={<div className="p-8">正在加载…</div>}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/blog" element={<Blog />} />
        <Route path="/blog/:slug" element={<BlogArticle />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Login />} />
        <Route element={<Protected />}>
          <Route path="/admin/website" element={<WebsiteAdmin />} />
          <Route path="/admin/blog" element={<BlogAdmin />} />
          <Route path="/admin/blog/:postId" element={<BlogAdmin />} />
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
    </Suspense>
  );
}
