import { StudioSelect } from "@/components/ui/studio-select";
import { useQuery } from "@tanstack/react-query";
import {
  Clapperboard,
  Folder,
  PanelLeft,
  PanelLeftClose,
  PanelLeftOpen,
  X,
  Search,
  ChevronRight,
  ChevronDown,
  Settings2,
  Users,
  Activity,
  Server,
  SlidersHorizontal,
} from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import {
  NavLink,
  Outlet,
  useLocation,
  useParams,
  useNavigate,
} from "react-router-dom";
import { AccountMenu } from "@/components/AccountMenu";
import { HeaderMenu, headerMenuItem } from "@/components/ui/header-menu";
import { api, type Project } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useDialogFocus } from "@/components/ui/useDialogFocus";
import { cn } from "@/lib/utils";
import { WORKSPACE_DESTINATIONS } from "@/lib/workspace-navigation";
import { WorkspaceNavigator } from "@/components/WorkspaceNavigator";
import { WORKSPACE_STAGES } from "@/lib/workspace-stages";
import { ConversationNavigationHost } from "@/lib/conversation-navigation";

const COLLAPSE_KEY = "inspiration_sidebar_collapsed";

export default function AppShell() {
  const { projectId } = useParams();
  const location = useLocation();
  const taskReturnParams = new URLSearchParams(location.search);
  taskReturnParams.delete("message");
  taskReturnParams.delete("ordinal");
  const taskReturnPath = `${location.pathname}?${taskReturnParams}`;
  const [mobileOpen, setMobileOpen] = useState(false);
  const [navigatorOpen, setNavigatorOpen] = useState(false);
  const navigate = useNavigate();
  const [expandedStage, setExpandedStage] = useState<string | null>(null);
  const [historyHost, setHistoryHost] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        if (document.querySelector('[aria-modal="true"]') && !navigatorOpen)
          return;
        event.preventDefault();
        setNavigatorOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [navigatorOpen]);
  const [narrow, setNarrow] = useState(
    () => window.matchMedia("(max-width: 767px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const sync = () => {
      setNarrow(media.matches);
      if (!media.matches) setMobileOpen(false);
    };
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  useEffect(() => setMobileOpen(false), [location.pathname, location.search]);
  const mobileRef = useDialogFocus(mobileOpen, () => setMobileOpen(false));
  const { t, lang } = useI18n();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const canvasWorkspace = location.pathname.endsWith("/canvas");
  const directorWorkspace = location.pathname.endsWith("/director");
  const cutsWorkspace = location.pathname.endsWith("/cuts");
  const writingWorkspace = /\/scripts\/[^/]+$/.test(location.pathname);
  const editingWorkspace = writingWorkspace || cutsWorkspace || location.pathname.endsWith("/storyboard");
  const [editorNavigationExpanded, setEditorNavigationExpanded] = useState(false);
  const compact = (editingWorkspace ? !editorNavigationExpanded : collapsed) && !narrow;
  const toggleCollapse = () => {
    if (editingWorkspace) {
      setEditorNavigationExpanded((value) => !value);
      return;
    }
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        /* keep usable without storage */
      }
      return !c;
    });
  };

  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const project = projects?.find((p) => p.id === projectId);
  const destination = [...WORKSPACE_DESTINATIONS]
    .sort((a, b) => b.to.length - a.to.length)
    .find(
      (item) =>
        location.pathname === `/projects/${projectId}/${item.to}` ||
        location.pathname.startsWith(`/projects/${projectId}/${item.to}/`),
    );
  const pageTitle =
    destination?.to === "shots" &&
    new URLSearchParams(location.search).has("shot")
      ? lang === "zh"
        ? "镜头创作"
        : "Shot creation"
      : destination?.to === "assets/trash"
        ? lang === "zh"
          ? "回收站"
          : "Trash"
        : destination
          ? t(destination.label)
          : "Inspiration";

  const iconBtn =
    "h-8 w-8 rounded-md hover:bg-elevated flex items-center justify-center text-muted-foreground shrink-0 transition-colors";
  const activeStage = WORKSPACE_STAGES.find((stage) =>
    stage.routes.includes(
      ["assets", "assets/trash", "library", "evidence", "generated"].includes(
        destination?.to ?? "",
      )
        ? "materials"
        : (destination?.to ?? ""),
    ),
  );
  useEffect(() => {
    setExpandedStage(activeStage?.id ?? null);
  }, [activeStage?.id]);

  return (
    <ConversationNavigationHost.Provider value={historyHost}>
    <div
      className={`studio-shell flex h-dvh overflow-hidden bg-bg text-foreground ${canvasWorkspace ? "is-canvas-workspace" : ""} ${directorWorkspace ? "is-director-workspace" : ""} ${cutsWorkspace ? "is-cuts-workspace" : ""}`}
    >
      <a href="#main-content" className="skip-link">
        {lang === "zh" ? "跳至内容" : "Skip to content"}
      </a>
      {mobileOpen && (
        <button
          tabIndex={-1}
          aria-label={lang === "zh" ? "关闭导航" : "Close navigation"}
          onClick={() => setMobileOpen(false)}
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
        />
      )}
      <aside
        id="workspace-sidebar"
        ref={mobileRef}
        role={narrow ? "dialog" : undefined}
        aria-modal={narrow && mobileOpen ? true : undefined}
        aria-label={lang === "zh" ? "项目导航" : "Project navigation"}
        className={cn(
          "studio-sidebar z-50 shrink-0 bg-surface flex flex-col md:relative",
          narrow ? (mobileOpen ? "fixed inset-y-0 left-0" : "hidden") : "",
          compact ? "w-[88px]" : "w-[244px]",
        )}
      >
        {/* 头部 */}
        <div className="studio-sidebar-header">
          <button
            type="button"
            onClick={() => (narrow ? setMobileOpen(false) : toggleCollapse())}
            className="studio-sidebar-toggle"
            title={
              narrow
                ? lang === "zh"
                  ? "关闭导航"
                  : "Close navigation"
                : compact
                  ? t("shell.expand")
                  : t("shell.collapse")
            }
            aria-label={
              narrow
                ? lang === "zh"
                  ? "关闭导航"
                  : "Close navigation"
                : compact
                  ? t("shell.expand")
                  : t("shell.collapse")
            }
            aria-controls="workspace-sidebar"
            aria-expanded={narrow ? mobileOpen : !compact}
          >
            {narrow ? (
              <X size={18} aria-hidden="true" />
            ) : compact ? (
              <PanelLeftOpen size={18} aria-hidden="true" />
            ) : (
              <PanelLeftClose size={18} aria-hidden="true" />
            )}
          </button>
          {!compact && (
            <span className="studio-sidebar-brand">
              <span className="studio-brand-mark" aria-hidden="true">
                <Clapperboard size={27} />
              </span>
              <span>Inspiration</span>
            </span>
          )}
        </div>

        <NavLink
          to="/projects"
          title={lang === "zh" ? "所有项目" : "All projects"}
          className={cn(
            "mx-3 mb-4 flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-elevated",
            compact && "flex-col justify-center gap-1 px-0 text-[11px]",
          )}
        >
          <Folder size={17} />
          <span>{lang === "zh" ? "项目" : "All projects"}</span>
        </NavLink>

        <button
          onClick={() => {
            setNavigatorOpen(true);
          }}
          title={lang === "zh" ? "快速导航" : "Quick navigation"}
          className={cn(
            "mx-3 mb-5 flex items-center gap-2 rounded-xl bg-elevated/60 px-3 py-3 text-xs text-muted-foreground hover:bg-elevated",
            compact && "flex-col justify-center gap-1 px-0 text-[11px]",
          )}
        >
          <Search className="h-4 w-4 shrink-0" />
          {compact && <span>{lang === "zh" ? "搜索" : "Search"}</span>}
          {!compact && (
            <>
              <span className="flex-1 text-left">
                {lang === "zh" ? "导航" : "Quick navigation"}
              </span>
              <kbd className="text-[10px] text-faint">⌘ / Ctrl K</kbd>
            </>
          )}
        </button>

        {/* 导航 */}
        <nav
          className="flex-1 overflow-y-auto px-2 pb-3 space-y-1"
          aria-label={lang === "zh" ? "工作区功能" : "Workspace tools"}
        >
          {WORKSPACE_STAGES.map((stage) => {
            const first = stage.items[0],
              active = activeStage?.id === stage.id,
              expanded = expandedStage === stage.id;
            const Icon = first.icon;
            return (
              <div key={stage.id}>
                <div className="flex items-center gap-1">
                  <NavLink
                    to={
                      stage.id === "media"
                        ? `/projects/${projectId}/materials${activeStage?.id !== "media" ? `?return=${encodeURIComponent(location.pathname + location.search)}` : ""}`
                        : `/projects/${projectId}/${active ? destination!.to : first.to}`
                    }
                    title={lang === "zh" ? stage.zh : stage.en}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex flex-1 min-w-0 rounded-xl transition-colors",
                      compact
                        ? "flex-col items-center justify-center gap-1 py-2 text-[10px]"
                        : "items-center gap-3 px-3 py-2.5 text-sm",
                      active
                        ? "bg-elevated text-foreground font-medium"
                        : "text-muted-foreground hover:bg-elevated",
                    )}
                  >
                    <Icon size={18} className="shrink-0" />
                    {lang === "zh" ? stage.zh : stage.en}
                  </NavLink>
                  {!compact && (stage.items.length > 1 || (stage.id === "home" && active)) && (
                    <button
                      aria-label={`${lang === "zh" ? "展开" : "Expand"}${lang === "zh" ? stage.zh : stage.en}`}
                      aria-expanded={expanded}
                      onClick={() =>
                        setExpandedStage(expanded ? null : stage.id)
                      }
                      className="p-2 rounded-lg hover:bg-elevated"
                    >
                      <ChevronRight
                        size={13}
                        className={expanded ? "rotate-90" : ""}
                      />
                    </button>
                  )}
                </div>
                {stage.id === "home" && active && !compact && (
                  <div hidden={!expanded} className="shell-conversations" ref={setHistoryHost}>
                    {new URLSearchParams(location.search).get("view") === "overview" && (
                      <NavLink className="block px-2 py-2 text-sm text-muted-foreground" to={`/projects/${projectId}/workbench?view=chat`}>
                        {lang === "zh" ? "返回对话" : "Back to conversations"}
                      </NavLink>
                    )}
                  </div>
                )}
                {!compact && expanded && stage.items.length > 1 && (
                  <div className="ml-5 mt-1 pl-3 border-l space-y-0.5">
                    {stage.items.map((it) => (
                      <NavLink
                        key={it.to}
                        end
                        to={`/projects/${projectId}/${it.to}${it.to === "shots" ? "?view=status" : ""}`}
                        className={() =>
                          cn(
                            "flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px]",
                            (destination?.to === "shots" &&
                            new URLSearchParams(location.search).has("shot") &&
                            new URLSearchParams(location.search).get("view") !==
                              "status"
                              ? "storyboard"
                              : destination?.to) === it.to
                              ? "bg-primary/10 text-primary"
                              : "text-muted-foreground hover:bg-elevated",
                          )
                        }
                      >
                        <it.icon size={16} className="shrink-0" aria-hidden="true" />
                        <span>{t(it.label)}</span>
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-16 shrink-0 items-center gap-1.5 sm:gap-3 bg-bg px-3 md:px-8">
          <button
            className={cn(iconBtn, "md:hidden")}
            onClick={() => setMobileOpen(true)}
            aria-label={lang === "zh" ? "打开导航" : "Open navigation"}
            aria-controls="workspace-sidebar"
            aria-expanded={mobileOpen}
          >
            <PanelLeft className="h-5 w-5" />
          </button>
          <NavLink
            to="/projects"
            className="hidden text-xs text-muted-foreground hover:text-foreground md:block"
          >
            {lang === "zh" ? "所有项目" : "All projects"}
          </NavLink>
          <ChevronRight className="hidden h-3 w-3 text-faint md:block" />
          <HeaderMenu
            align="left"
            className="max-w-[38vw] sm:max-w-64"
            label={lang === "zh" ? "项目菜单" : "Project menu"}
            trigger={
              <>
                <span className="truncate text-sm">
                  {project?.name ?? "Inspiration"}
                </span>
                <ChevronDown size={13} className="shrink-0" />
              </>
            }
          >
            <div className="border-b px-3 py-3 mb-1">
              <p className="truncate text-sm font-medium">{project?.name}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {lang === "zh" ? "当前项目" : "Current project"}
              </p>
            </div>
            {[
              [
                "settings?section=project",
                lang === "zh" ? "项目资料与封面" : "Project details",
                Settings2,
              ],
              [
                "settings?section=creative",
                lang === "zh" ? "创作默认项" : "Creative defaults",
                SlidersHorizontal,
              ],
              [
                "settings?section=channels",
                lang === "zh" ? "渠道与模型" : "Channels & models",
                Server,
              ],
              [
                "members",
                lang === "zh" ? "成员与权限" : "Members & permissions",
                Users,
              ],
              [
                "settings?section=quota",
                lang === "zh" ? "更多项目设置" : "More project settings",
                Settings2,
              ],
            ].map(([to, label, Icon]) => {
              const MenuIcon = Icon as typeof Settings2;
              return (
                <NavLink
                  role="menuitem"
                  className={headerMenuItem}
                  key={to as string}
                  to={`/projects/${projectId}/${to}`}
                >
                  <MenuIcon size={16} />
                  {label as string}
                </NavLink>
              );
            })}
            <div className="border-t mt-1 pt-1">
              <NavLink
                role="menuitem"
                className={headerMenuItem}
                to="/projects"
              >
                <Folder size={16} />
                {lang === "zh" ? "切换项目" : "Switch project"}
              </NavLink>
            </div>
          </HeaderMenu>
          <ChevronRight className="hidden sm:block h-3 w-3 shrink-0 text-faint" />
          {!(compact && activeStage && activeStage.items.length > 1) && (
            <span className="hidden sm:block truncate text-xs font-medium">
              {pageTitle}
            </span>
          )}
          {compact && activeStage && activeStage.items.length > 1 && (
            <StudioSelect
              aria-label={lang === "zh" ? "切换相关工具" : "Related tools"}
              className="ml-2 max-w-32 rounded-lg border bg-card px-2 py-1 text-xs"
              value={destination?.to}
              onValueChange={(selectedValue) =>
                navigate(`/projects/${projectId}/${selectedValue}`)
              }
            >
              {activeStage.items.map((it) => (
                <option key={it.to} value={it.to}>
                  {t(it.label)}
                </option>
              ))}
            </StudioSelect>
          )}
          <button
            className={cn(iconBtn, "ml-auto")}
            onClick={() => setNavigatorOpen(true)}
            aria-label={lang === "zh" ? "查找功能" : "Find a tool"}
          >
            <Search className="h-4 w-4" />
          </button>
          <NavLink
            to={`/projects/${projectId}/tasks${location.pathname.endsWith("/workbench") ? `?return=${encodeURIComponent(taskReturnPath)}` : ""}`}
            title={lang === "zh" ? "任务中心" : "Task center"}
            aria-label={lang === "zh" ? "任务中心" : "Task center"}
            className={cn(
              iconBtn,
              destination?.to === "tasks" && "bg-primary/10 text-primary",
            )}
          >
            <Activity size={18} />
          </NavLink>
          <AccountMenu />
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className="app-main min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
          <Suspense
            fallback={
              <div
                role="status"
                className="studio-page text-sm text-muted-foreground"
              >
                {lang === "zh" ? "正在打开工作区…" : "Opening workspace…"}
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </main>
      </div>
      {navigatorOpen && projectId && (
        <WorkspaceNavigator
          key={projectId}
          projectId={projectId}
          onClose={() => setNavigatorOpen(false)}
        />
      )}
    </div>
    </ConversationNavigationHost.Provider>
  );
}
