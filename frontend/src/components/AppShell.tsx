import { useQuery } from "@tanstack/react-query";
import {
  BookMarked,
  Clapperboard,
  FileText,
  Film,
  Folder,
  Images,
  Languages,
  LayoutDashboard,
  LayoutGrid,
  LogOut,
  Moon,
  PanelLeft,
  PanelLeftClose,
  ScrollText,
  Settings as SettingsIcon,
  Sun,
  Type,
  Users,
  X,
  Activity,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useParams } from "react-router-dom";
import { Avatar } from "@/components/ui/avatar";
import { api, type Project } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { useDialogFocus } from "@/components/ui/useDialogFocus";
import { cn } from "@/lib/utils";

const GROUPS = [
  { title: "nav.overview", items: [{ to: "workbench", label: "nav.workbench", icon: LayoutDashboard }] },
  {
    title: "nav.create",
    items: [
      { to: "narrative", label: "nav.narrative", icon: ScrollText },
      { to: "bible", label: "nav.bible", icon: BookMarked },
      { to: "scripts", label: "nav.scripts", icon: FileText },
    ],
  },
  {
    title: "nav.assets",
    items: [
      { to: "assets", label: "nav.assetLibrary", icon: Images },
      { to: "prompts", label: "nav.prompts", icon: Type },
    ],
  },
  {
    title: "nav.production",
    items: [
      { to: "shots", label: "nav.shots", icon: LayoutGrid },
      { to: "storyboard", label: "nav.storyboard", icon: Clapperboard },
      { to: "tasks", label: "nav.tasks", icon: Activity },
    ],
  },
  { title: "nav.film", items: [{ to: "cuts", label: "nav.cuts", icon: Film }] },
  {
    title: "nav.admin",
    items: [
      { to: "members", label: "nav.members", icon: Users },
      { to: "settings", label: "nav.settings", icon: SettingsIcon },
    ],
  },
];

const COLLAPSE_KEY = "inspiration_sidebar_collapsed";

export default function AppShell() {
  const { projectId } = useParams();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const sync = () => {
      setNarrow(media.matches);
      if (!media.matches) setMobileOpen(false);
    };
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  useEffect(() => setMobileOpen(false), [location.pathname]);
  const mobileRef = useDialogFocus(mobileOpen, () => setMobileOpen(false));
  const { me, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const { t, lang, setLang } = useI18n();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === "1");
  const compact = collapsed && !narrow;
  const toggleCollapse = () => {
    setCollapsed((c) => {
      localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      return !c;
    });
  };

  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const project = projects?.find((p) => p.id === projectId);

  const iconBtn =
    "h-8 w-8 rounded-md hover:bg-elevated flex items-center justify-center text-muted-foreground shrink-0 transition-colors";

  return (
    <div className="flex h-dvh overflow-hidden bg-bg text-foreground">
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
        ref={mobileRef}
        role={narrow ? "dialog" : undefined}
        aria-modal={narrow && mobileOpen ? true : undefined}
        aria-label={lang === "zh" ? "项目导航" : "Project navigation"}
        className={cn(
          "z-50 shrink-0 border-r border-border bg-surface flex flex-col md:relative",
          narrow ? (mobileOpen ? "fixed inset-y-0 left-0" : "hidden") : "",
          compact ? "w-[72px]" : "w-[224px]",
        )}
      >
        {/* 头部 */}
        <div className="min-h-16 flex items-center gap-2 px-3 border-b border-border">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-border bg-card text-foreground ">
            <Clapperboard className="h-[18px] w-[18px]" />
          </span>
          {!compact && <span className="font-semibold tracking-tight flex-1">Inspiration</span>}
          <button
            onClick={() => (narrow ? setMobileOpen(false) : toggleCollapse())}
            className={cn(iconBtn, compact && "absolute top-16 left-5")}
            title={compact ? t("shell.expand") : t("shell.collapse")}
          >
            {narrow ? (
              <X className="h-4 w-4" />
            ) : compact ? (
              <PanelLeft className="h-4 w-4" />
            ) : (
              <PanelLeftClose className="h-4 w-4" />
            )}
          </button>
        </div>

        {/* 项目切换 */}
        <NavLink
          to="/projects"
          className={cn(
            "mx-2 mt-3 mb-4 flex items-center gap-2 rounded-md border border-border bg-card hover:border-primary/50 transition-colors",
            compact ? "justify-center p-2 mt-11" : "px-3 py-2",
          )}
          title={project?.name ?? t("shell.selectProject")}
        >
          <Folder className="h-4 w-4 text-muted-foreground shrink-0" />
          {!compact && (
            <span className="truncate text-sm">
              <span className="text-muted-foreground text-xs block leading-tight">
                {t("shell.currentProject")}
              </span>
              <span className="font-medium">{project?.name ?? t("shell.selectProject")}</span>
            </span>
          )}
        </NavLink>

        {/* 导航 */}
        <nav className="flex-1 overflow-y-auto px-2 pb-3 space-y-4">
          {GROUPS.map((g) => (
            <div key={g.title}>
              {!compact && (
                <div className="px-2 mb-1 text-[11px] tracking-wider text-faint">{t(g.title)}</div>
              )}
              <div className="space-y-0.5">
                {g.items.map((it) => (
                  <NavLink
                    key={it.to}
                    to={`/projects/${projectId}/${it.to}`}
                    title={t(it.label)}
                    className={({ isActive }) =>
                      cn(
                        "relative flex items-center gap-2.5 rounded-md py-2 text-sm transition-colors",
                        compact ? "justify-center px-0" : "px-2.5",
                        isActive
                          ? "bg-primary/10 text-primary font-medium"
                          : "text-muted-foreground hover:text-foreground hover:bg-elevated",
                      )
                    }
                  >
                    <it.icon className="h-4 w-4 shrink-0" />
                    {!compact && t(it.label)}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* 底部 */}
        <div className={cn("border-t border-border p-2", compact ? "space-y-1" : "")}>
          {!compact ? (
            <div className="flex items-center gap-2">
              <Avatar name={me?.user.display_name} size={32} />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{me?.user.display_name}</div>
                <button onClick={logout} className="text-xs text-muted-foreground hover:text-foreground">
                  {t("shell.logout")}
                </button>
              </div>
              <button
                onClick={() => setLang(lang === "zh" ? "en" : "zh")}
                className={iconBtn}
                title={t("shell.language")}
              >
                <Languages className="h-4 w-4" />
              </button>
              <button onClick={toggle} className={iconBtn} title={t("shell.theme")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-1">
              <Avatar name={me?.user.display_name} size={28} />
              <button
                onClick={() => setLang(lang === "zh" ? "en" : "zh")}
                className={iconBtn}
                title={t("shell.language")}
              >
                <Languages className="h-4 w-4" />
              </button>
              <button onClick={toggle} className={iconBtn} title={t("shell.theme")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
              <button onClick={logout} className={iconBtn} title={t("shell.logout")}>
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 items-center gap-3 border-b border-border bg-surface px-4 md:hidden">
          <button
            className={iconBtn}
            onClick={() => setMobileOpen(true)}
            aria-label={lang === "zh" ? "打开导航" : "Open navigation"}
            aria-expanded={mobileOpen}
          >
            <PanelLeft className="h-5 w-5" />
          </button>
          <span className="truncate font-medium">{project?.name ?? "Inspiration"}</span>
        </header>
        <main id="main-content" tabIndex={-1} className="app-main min-h-0 min-w-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
