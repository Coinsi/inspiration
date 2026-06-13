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
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { Avatar } from "@/components/ui/avatar";
import { api, type Project } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
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
  const { me, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const { t, lang, setLang } = useI18n();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === "1");
  const toggleCollapse = () => {
    setCollapsed((c) => {
      localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      return !c;
    });
  };

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const project = projects?.find((p) => p.id === projectId);

  const iconBtn = "h-8 w-8 rounded-md hover:bg-elevated flex items-center justify-center text-muted-foreground shrink-0 transition-colors";

  return (
    <div className="flex h-screen overflow-hidden bg-transparent text-foreground">
      <aside
        className={cn(
          "shrink-0 border-r border-border bg-surface/70 backdrop-blur-xl flex flex-col transition-all duration-200",
          collapsed ? "w-16" : "w-[248px]",
        )}
      >
        {/* 头部 */}
        <div className="h-14 flex items-center gap-2 px-3 border-b border-border">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[10px] bg-gradient-primary text-primary-foreground shadow-glow-sm">
            <Clapperboard className="h-[18px] w-[18px]" />
          </span>
          {!collapsed && <span className="font-semibold tracking-tight flex-1">Inspiration</span>}
          <button onClick={toggleCollapse} className={iconBtn} title={collapsed ? t("shell.expand") : t("shell.collapse")}>
            {collapsed ? <PanelLeft className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </button>
        </div>

        {/* 项目切换 */}
        <NavLink
          to="/projects"
          className={cn(
            "mx-2 mt-3 mb-2 flex items-center gap-2 rounded-md border border-border bg-card hover:border-primary/50 transition-colors",
            collapsed ? "justify-center p-2" : "px-3 py-2",
          )}
          title={project?.name ?? t("shell.selectProject")}
        >
          <Folder className="h-4 w-4 text-muted-foreground shrink-0" />
          {!collapsed && (
            <span className="truncate text-sm">
              <span className="text-muted-foreground text-xs block leading-tight">{t("shell.currentProject")}</span>
              <span className="font-medium">{project?.name ?? t("shell.selectProject")}</span>
            </span>
          )}
        </NavLink>

        {/* 导航 */}
        <nav className="flex-1 overflow-y-auto px-2 pb-3 space-y-3">
          {GROUPS.map((g) => (
            <div key={g.title}>
              {!collapsed && (
                <div className="px-2 mb-1 text-[11px] uppercase tracking-wider text-faint">
                  {t(g.title)}
                </div>
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
                        collapsed ? "justify-center px-0" : "px-2.5",
                        isActive
                          ? "bg-primary/12 text-primary font-medium ring-1 ring-primary/25 before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-full before:bg-primary before:shadow-glow-sm"
                          : "text-muted-foreground hover:text-foreground hover:bg-elevated",
                      )
                    }
                  >
                    <it.icon className="h-4 w-4 shrink-0" />
                    {!collapsed && t(it.label)}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* 底部 */}
        <div className={cn("border-t border-border p-2", collapsed ? "space-y-1" : "")}>
          {!collapsed ? (
            <div className="flex items-center gap-2">
              <Avatar name={me?.user.display_name} size={32} />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{me?.user.display_name}</div>
                <button onClick={logout} className="text-xs text-muted-foreground hover:text-foreground">
                  {t("shell.logout")}
                </button>
              </div>
              <button onClick={() => setLang(lang === "zh" ? "en" : "zh")} className={iconBtn} title={t("shell.language")}>
                <Languages className="h-4 w-4" />
              </button>
              <button onClick={toggle} className={iconBtn} title={t("shell.theme")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-1">
              <Avatar name={me?.user.display_name} size={28} />
              <button onClick={() => setLang(lang === "zh" ? "en" : "zh")} className={iconBtn} title={t("shell.language")}>
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

      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
