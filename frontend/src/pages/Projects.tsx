import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Trash2,
  Clapperboard,
  FolderOpen,
  Home,
  Plus,
  RefreshCw,
  Search,
  ImagePlus,
} from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { api, blobUrl, type Project } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { AccountMenu } from "@/components/AccountMenu";
import { useToast } from "@/components/ui/toast";
import { StudioScenes } from "@/components/StudioScenes";
import { ProjectTrashDialog } from "@/components/ProjectTrashDialog";
import { useConfirm } from "@/components/ui/confirm";
import { ProjectDialog } from "@/components/ProjectDialog";
import { CreativeLaunch } from "@/components/CreativeLaunch";

export default function Projects() {
  const qc = useQueryClient();
  const { me, refresh } = useAuth();
  const { t: tr, lang } = useI18n();
  const toast = useToast();
  const {
    data: projects,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });

  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("newest");
  const zh = lang === "zh";
  const visible = (projects ?? [])
    .filter((p) =>
      `${p.name} ${p.code} ${p.description ?? ""}`
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase()),
    )
    .sort((a, b) =>
      sort === "name"
        ? a.name.localeCompare(b.name, lang)
        : Date.parse(b.created_at) - Date.parse(a.created_at),
    );
  const [coverProject, setCoverProject] = useState<Project | null>(null);
  const confirm = useConfirm();
  const [trashOpen, setTrashOpen] = useState(false),
    [deleting, setDeleting] = useState("");
  const pendingDelete = useRef(false);
  async function removeProject(p: Project) {
    if (pendingDelete.current) return;
    pendingDelete.current = true;
    setDeleting(p.id);
    try {
      if (
        !(await confirm({
          title: zh ? "删除项目？" : "Delete project?",
          message: zh
            ? `将「${p.name}」移入回收站。项目成员将无法打开它，内容和素材保留，可以恢复。其他项目复用的素材不受影响。已提交的后台任务不会因此自动取消。`
            : `Move “${p.name}” to trash? Members will lose access until it is restored. Content and reused media are kept. Submitted background jobs are not automatically canceled.`,
          confirmText: zh ? "移入回收站" : "Move to trash",
          danger: true,
        }))
      )
        return;
      await api.del(`/projects/${p.id}`);
      qc.setQueryData<Project[]>(["projects"], (old) =>
        old?.filter((x) => x.id !== p.id),
      );
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["project-trash"] });
      toast.push(zh ? "项目已移入回收站" : "Project moved to trash", "success");
    } catch (e) {
      toast.push((e as Error).message, "error");
    } finally {
      pendingDelete.current = false;
      setDeleting("");
    }
  }
  const saved = () => {
    toast.push(zh ? "项目已保存" : "Project saved", "success");
    void qc.invalidateQueries({ queryKey: ["projects"] });
    void refresh();
  };

  return (
    <div className="studio-home">
      <aside className="home-sidebar">
        <Link to="/projects" className="home-brand">
          <span className="studio-brand-mark">
            <Clapperboard size={22} />
          </span>
          <span>
            Inspiration<small>{zh ? "创作工作台" : "Creative studio"}</small>
          </span>
        </Link>
        <button
          className="home-search"
          onClick={() => {
            document.getElementById("project-search")?.focus();
          }}
        >
          <Search size={17} />
          {zh ? "查找项目" : "Find a project"}
        </button>
        <nav aria-label={zh ? "首页导航" : "Home navigation"}>
          <Link to="/projects" className="home-nav active">
            <Home size={18} />
            {zh ? "首页" : "Home"}
          </Link>
          <a href="#project-list" className="home-nav">
            <FolderOpen size={18} />
            {zh ? "我的项目" : "My projects"}
            <span className="ml-auto text-xs text-faint">
              {projects?.length ?? "—"}
            </span>
          </a>
          <button
            className="home-nav w-full"
            onClick={() => {
              setOpen(true);
              document.getElementById("project-list")?.scrollIntoView();
            }}
          >
            <Plus size={18} />
            {tr("proj.new")}
          </button>
        </nav>
        <p className="mt-10 px-3 text-[11px] text-faint">
          {zh ? "最近创建" : "RECENT PROJECTS"}
        </p>
        <div className="mt-3 space-y-1 overflow-y-auto">
          {[...(projects ?? [])]
            .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
            .slice(0, 5)
            .map((p) => (
              <Link
                key={p.id}
                to={`/projects/${p.id}/workbench`}
                className="home-recent"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-faint/50" />
                <span className="truncate">{p.name}</span>
              </Link>
            ))}
        </div>
      </aside>
      <div className="home-main">
        <header className="home-header">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-elevated text-foreground lg:hidden">
              <Clapperboard className="h-[18px] w-[18px]" />
            </span>
            <span className="text-sm text-muted-foreground">
              <span className="lg:hidden">Inspiration · </span>
              {zh ? "创作工作台" : "Creative studio"}
              <span className="mx-3 text-faint">/</span>
              <span className="text-foreground">{zh ? "首页" : "Home"}</span>
            </span>
          </div>
          <AccountMenu />
        </header>

        <main className="home-content">
          <section className="home-hero">
            <StudioScenes />
            <p className="studio-eyebrow">
              INSPIRATION · {zh ? "AI 影视创作工作台" : "AI FILMMAKING STUDIO"}
            </p>
            <h1>
              {zh ? (
                <>
                  把脑海里的画面，<span>拍成你的故事</span>
                </>
              ) : (
                <>
                  Your imagination.<span>Your next story.</span>
                </>
              )}
            </h1>
            <CreativeLaunch
              projects={isError ? undefined : projects}
              onCreate={() => setOpen(true)}
            />
          </section>
          <section id="project-list" className="space-y-6 scroll-mt-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold tracking-tight">
                  {zh ? "继续你的创作" : "Pick up your story"}
                </h2>
                <p className="mt-2 text-xs text-muted-foreground">
                  {zh
                    ? "打开一个项目，继续整理故事、打磨镜头与制作成片。"
                    : "Open a project to develop your story, shape shots and create a film."}
                </p>
              </div>
              <Button onClick={() => setOpen(true)} aria-expanded={open}>
                <Plus className="h-4 w-4 mr-1" /> {tr("proj.new")}
              </Button>
            </div>

            {open && (
              <ProjectDialog onClose={() => setOpen(false)} onSaved={saved} />
            )}
            {trashOpen && (
              <ProjectTrashDialog
                onClose={() => setTrashOpen(false)}
                onRestored={() => {
                  void qc.invalidateQueries({ queryKey: ["projects"] });
                  toast.push(zh ? "项目已恢复" : "Project restored", "success");
                }}
              />
            )}
            {coverProject && (
              <ProjectDialog
                project={coverProject}
                onClose={() => setCoverProject(null)}
                onSaved={saved}
              />
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
              <h2 className="text-sm font-semibold">
                {tr("proj.title")}{" "}
                <span className="ml-2 font-normal text-faint">
                  {!isError && projects ? projects.length : "—"}
                </span>
              </h2>
              <div className="flex flex-wrap gap-2">
                <Button variant="ghost" onClick={() => setTrashOpen(true)}>
                  <Trash2 size={15} />
                  {zh ? "回收站" : "Trash"}
                </Button>
                <label className="flex items-center gap-2 rounded-lg border bg-card px-3">
                  <Search className="h-4 w-4 text-faint" />
                  <input
                    id="project-search"
                    aria-label={zh ? "搜索项目" : "Search projects"}
                    className="min-w-0 bg-transparent py-2 text-sm outline-none"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder={
                      zh ? "搜索项目名称或编号" : "Search name or code"
                    }
                  />
                </label>
                <select
                  aria-label={zh ? "项目排序" : "Sort projects"}
                  value={sort}
                  onChange={(e) => setSort(e.target.value)}
                  className="rounded-lg border bg-card px-3 py-2 text-xs"
                >
                  <option value="newest">
                    {zh ? "最新创建" : "Newest created"}
                  </option>
                  <option value="name">{zh ? "按名称" : "By name"}</option>
                </select>
              </div>
            </div>

            {isError ? (
              <div role="alert" className="studio-empty">
                <p>
                  {zh
                    ? "项目暂时无法加载，请重试。"
                    : "Unable to load your projects. Please try again."}
                </p>
                <Button variant="outline" onClick={() => void refetch()}>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  {zh ? "重试" : "Retry"}
                </Button>
              </div>
            ) : isLoading ? (
              <p className="text-muted-foreground text-sm">
                {tr("common.loading")}
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {visible.map((p) => (
                  <article
                    key={p.id}
                    className="project-card group relative"
                    data-project-id={p.id}
                  >
                    <Link
                      to={`/projects/${p.id}/workbench`}
                      className="block"
                      aria-label={p.name}
                    >
                      <div
                        className="project-card-cover relative flex h-36 items-center justify-center overflow-hidden bg-elevated"
                        data-tone={p.code.length % 3}
                      >
                        {p.cover_blob_hash ? (
                          <img
                            className="project-cover-image"
                            src={blobUrl(p.id, p.cover_blob_hash)}
                            alt={`${p.name} · ${zh ? "项目封面" : "Project cover"}`}
                            loading="lazy"
                          />
                        ) : (
                          <span className="text-4xl font-light tracking-widest text-foreground/60">
                            {p.name.slice(0, 2)}
                          </span>
                        )}
                      </div>
                      <div className="p-5">
                        <div className="truncate font-medium">{p.name}</div>
                        <p className="mt-2 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">
                          {p.description ||
                            (zh
                              ? "整理故事与素材，继续你的创作。"
                              : "Develop your story and bring your references together.")}
                        </p>
                        <div className="mt-4 flex items-center justify-between gap-2 border-t pt-3 text-[11px] text-faint">
                          <span>{zh ? "进入创作" : "Open project"}</span>
                          <span>
                            {new Date(p.created_at).toLocaleDateString(
                              zh ? "zh-CN" : "en-US",
                            )}
                          </span>
                        </div>
                      </div>
                    </Link>
                    {me?.memberships.some(
                      (m) => m.project_id === p.id && m.role === "admin",
                    ) && (
                      <button
                        className="project-card-delete-action"
                        disabled={!!deleting}
                        aria-label={`${zh ? "删除项目" : "Delete project"} · ${p.name}`}
                        title={zh ? "删除项目" : "Delete project"}
                        onClick={() => void removeProject(p)}
                      >
                        <Trash2 size={15} />
                      </button>
                    )}
                    {me?.memberships.some(
                      (m) => m.project_id === p.id && m.role === "admin",
                    ) && (
                      <button
                        className="project-card-cover-action"
                        aria-label={`${zh ? "设置封面" : "Set cover"} · ${p.name}`}
                        onClick={() => setCoverProject(p)}
                      >
                        <ImagePlus size={15} />
                        {p.cover_blob_hash
                          ? zh
                            ? "更换封面"
                            : "Change cover"
                          : zh
                            ? "添加封面"
                            : "Add cover"}
                      </button>
                    )}
                  </article>
                ))}
                {visible.length === 0 && (
                  <div className="studio-empty col-span-full min-h-64">
                    <FolderOpen className="h-8 w-8" strokeWidth={1.5} />
                    <p>
                      {projects?.length
                        ? zh
                          ? "没有匹配的项目"
                          : "No matching projects"
                        : tr("proj.empty")}
                    </p>
                    {projects?.length ? (
                      <Button variant="outline" onClick={() => setSearch("")}>
                        {zh ? "清除搜索" : "Clear search"}
                      </Button>
                    ) : (
                      <Button onClick={() => setOpen(true)}>
                        {tr("proj.new")}
                      </Button>
                    )}
                  </div>
                )}
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
