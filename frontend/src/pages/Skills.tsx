import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  Archive,
  BookOpen,
  FileUp,
  Plus,
  Save,
  Sparkles,
  Search,
  Heart,
  Library,
  UserRound,
  Clapperboard,
  Scissors,
  Layers,
  ArrowUpRight,
  Pencil,
  RotateCcw,
} from "lucide-react";
import "./skills-workspace.css";
import { SkillOverlay } from "@/components/SkillOverlay";
import { SkillInstall, SkillInstallCard } from "@/components/SkillInstall";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import type { Skill, SkillCard } from "@/lib/skills";
import { categories } from "@/lib/skills";
import {
  SkillReader,
  SkillFilesEditor,
  exportSkill,
} from "@/components/SkillPackage";
const blank: Skill = {
  name: "",
  description: "",
  instructions: "",
  required_tools: [],
  source: "手动编写",
};
const field =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";
export default function Skills() {
  const { projectId } = useParams();
  const user = useAuth().me?.user.id;
  return (
    <SkillWorkspace
      key={`${projectId}:${user}`}
      projectId={projectId!}
      userId={user ?? "anonymous"}
    />
  );
}
function SkillWorkspace({
  projectId,
  userId,
}: {
  projectId: string;
  userId: string;
}) {
  const role = useAuth().me?.memberships.find(
    (m) => m.project_id === projectId,
  )?.role;
  const canEdit = ["admin", "director", "artist", "writer"].includes(
    role ?? "",
  );
  const base = `/projects/${projectId}`,
    qc = useQueryClient(),
    confirm = useConfirm();
  const [search, setSearch] = useState(""),
    [category, setCategory] = useState(""),
    [scope, setScope] = useState("project"),
    [sort, setSort] = useState("updated");
  const [installOpen, setInstallOpen] = useState(false);
  const [reading, setReading] = useState<
    (Skill & { project_id?: string; favorite?: boolean }) | null
  >(null);
  const [archived, setArchived] = useState(false),
    [offset, setOffset] = useState(0),
    [historyOffset, setHistoryOffset] = useState(0);
  const draftKey = `skill-draft:${userId}:${projectId}`;
  const [recovered] = useState<{ draft: Skill; baseline: Skill | null } | null>(
    () => {
      try {
        const saved = JSON.parse(localStorage.getItem(draftKey) ?? "null");
        const valid = (s: Skill) =>
          s &&
          typeof s.name === "string" &&
          typeof s.description === "string" &&
          typeof s.instructions === "string" &&
          s.instructions.length <= 20000 &&
          typeof s.source === "string" &&
          Array.isArray(s.required_tools) &&
          s.required_tools.every((t) => typeof t === "string");
        return saved &&
          valid(saved.draft) &&
          (!saved.baseline || valid(saved.baseline))
          ? saved
          : null;
      } catch {
        return null;
      }
    },
  );
  const [baseline, setBaseline] = useState<Skill | null>(
    recovered?.baseline ?? null,
  );
  const [recoveredNotice, setRecoveredNotice] = useState(!!recovered);
  const [editorTab, setEditorTab] = useState("content");
  const [storageError, setStorageError] = useState(false);
  const [draft, setDraft] = useState<Skill | null>(recovered?.draft ?? null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const busyRef = useRef(false);
  const dirty = !!draft && JSON.stringify(draft) !== JSON.stringify(baseline);
  useEffect(() => {
    try {
      if (dirty)
        localStorage.setItem(draftKey, JSON.stringify({ draft, baseline }));
      else localStorage.removeItem(draftKey);
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }, [draftKey, draft, baseline, dirty]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  function acceptDraft(value: Skill) {
    setDraft(value);
    setBaseline(value.id ? value : null);
    setRecoveredNotice(false);
    setHistoryOffset(0);
    setEditorTab("content");
    setReading(null);
  }
  async function choose(value: Skill) {
    if (busyRef.current) return;
    if (
      dirty &&
      !(await confirm({
        message: "切换会替换当前未保存的技能草稿。确定继续？",
      }))
    )
      return;
    acceptDraft(value);
    setError("");
    setMessage("");
  }
  const list = useQuery({
    enabled: scope !== "templates",
    queryKey: [
      "skills",
      projectId,
      archived,
      offset,
      search,
      category,
      scope,
      sort,
    ],
    queryFn: () =>
      api.get<{ items: SkillCard[]; total: number }>(
        `${base}/skills/catalog?archived=${archived}&offset=${offset}&q=${encodeURIComponent(search)}&category=${category}&scope=${scope}&sort=${sort}`,
      ),
  });
  const templates = useQuery({
    queryKey: ["skill-templates", projectId],
    queryFn: () => api.get<Skill[]>(`${base}/skills/templates`),
  });
  const tools = useQuery({
    queryKey: ["agent-tools", projectId],
    queryFn: () =>
      api.get<
        { name: string; description: string; requires_review: boolean }[]
      >(`${base}/agent/tools`),
  });
  const history = useQuery({
    queryKey: ["skill-history", draft?.id, historyOffset],
    queryFn: () =>
      api.get<{ revision: number; document: Skill; created_at: string }[]>(
        `${base}/skills/${draft?.id}/history?offset=${historyOffset}`,
      ),
    enabled: !!draft?.id,
  });
  async function perform(fn: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["skills", projectId] });
      await qc.invalidateQueries({ queryKey: ["skill-history"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function save() {
    if (!draft) return;
    const { id, revision, archived: _, ...body } = draft;
    const result = id
      ? await api.put<Skill>(`${base}/skills/${id}`, { ...body, revision })
      : await api.post<Skill>(`${base}/skills`, body);
    acceptDraft(result);
    setMessage(`已保存版本 ${result.revision}`);
  }
  function edit(change: Partial<Skill>) {
    setDraft((d) => (d ? { ...d, ...change } : d));
  }
  const catalogItems =
    scope === "templates"
      ? (templates.data ?? [])
          .map(
            (s) =>
              ({
                ...s,
                category: s.name.includes("镜头")
                  ? "shot"
                  : s.name.includes("角色")
                    ? "character"
                    : "general",
                project_id: projectId,
                file_count: 1,
              }) as SkillCard,
          )
          .filter(
            (s) =>
              (!category || s.category === category) &&
              (!search.trim() ||
                `${s.name} ${s.description}`
                  .toLowerCase()
                  .includes(search.trim().toLowerCase())),
          )
      : (list.data?.items ?? []);
  const total =
    scope === "templates" ? catalogItems.length : (list.data?.total ?? 0);
  const loading = scope === "templates" ? templates.isLoading : list.isLoading;
  const groups = Object.entries(categories)
    .map(([key, label]) => ({
      key,
      label,
      items: catalogItems.filter((s) => (s.category ?? "general") === key),
    }))
    .filter((g) => g.items.length);
  const categoryIcons = {
    general: Layers,
    story: BookOpen,
    character: UserRound,
    shot: Clapperboard,
    edit: Scissors,
  };
  const scopes = [
    { id: "project", label: "项目技能", icon: Library },
    { id: "mine", label: "我创建的", icon: UserRound },
    { id: "favorites", label: "我的收藏", icon: Heart },
    { id: "templates", label: "方法模板", icon: Sparkles },
  ];
  const hasFilters = !!search || !!category || archived;
  function resetFilters() {
    setSearch("");
    setCategory("");
    setArchived(false);
    setOffset(0);
  }
  async function closeEditor() {
    if (busyRef.current) return;
    if (
      dirty &&
      !(await confirm({
        message: "关闭编辑会放弃当前未保存的修改。确定继续？",
      }))
    )
      return;
    setDraft(null);
    setBaseline(null);
    setRecoveredNotice(false);
    setError("");
    setMessage("");
  }
  async function openDetail(s: SkillCard) {
    if (!s.id) {
      const { file_count: _, project_id: __, favorite: ___, ...doc } = s;
      setReading({ ...doc, project_id: projectId });
      return;
    }
    await perform(async () =>
      setReading({
        ...(await api.get<Skill>(`/projects/${s.project_id}/skills/${s.id}`)),
        project_id: s.project_id,
        favorite: s.favorite,
      }),
    );
  }
  async function favorite(s: SkillCard) {
    await perform(async () => {
      await api.put(`/projects/${s.project_id}/skills/${s.id}/favorite`, {
        favorite: !s.favorite,
      });
      setReading((current) =>
        current && current.id === s.id
          ? { ...current, favorite: !s.favorite }
          : current,
      );
    });
  }
  async function copy(s: SkillCard) {
    await perform(async () => {
      const result = await api.post<Skill>(`${base}/skills/copy`, {
        source_project_id: s.project_id,
        source_skill_id: s.id,
        revision: s.revision,
      });
      setReading(null);
      setScope("project");
      setCategory("");
      setSearch("");
      setOffset(0);
      setMessage(`已将「${result.name}」复制到当前项目`);
    });
  }
  const notices = (
    <>
      {error && (
        <p role="alert" className="knowledge-notice is-error">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="knowledge-notice">
          {message}
        </p>
      )}
      {recoveredNotice && (
        <p role="status" className="knowledge-notice">
          已恢复本机未保存的技能草稿，核对后即可继续保存。
        </p>
      )}
      {storageError && (
        <p role="alert" className="knowledge-notice is-error">
          本机草稿暂时无法保存，请及时保存技能。
        </p>
      )}
    </>
  );
  return (
    <div className="knowledge-page">
      <section className="knowledge-hero" aria-labelledby="knowledge-title">
        <span className="knowledge-hero-badge">
          <Sparkles size={13} /> 创作工具箱
        </span>
        <h1 id="knowledge-title">技能库</h1>
        <p>把灵感、经验与创作方法，变成随时可用的技能。</p>
        <span className="knowledge-hero-note">
          <span /> 从故事构思，到每一个镜头
        </span>
      </section>
      <div className="knowledge-toolbar">
        <div className="knowledge-tabs" role="tablist" aria-label="技能库范围">
          {scopes.map((s, i) => {
            const Icon = s.icon;
            return (
              <button
                role="tab"
                id={`skill-tab-${s.id}`}
                aria-controls="skill-catalog"
                tabIndex={scope === s.id ? 0 : -1}
                aria-selected={scope === s.id}
                key={s.id}
                onClick={() => {
                  setScope(s.id);
                  setOffset(0);
                  setArchived(false);
                }}
                onKeyDown={(e) => {
                  if (
                    ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
                  ) {
                    e.preventDefault();
                    const next =
                      e.key === "Home"
                        ? 0
                        : e.key === "End"
                          ? scopes.length - 1
                          : (i +
                              (e.key === "ArrowRight" ? 1 : -1) +
                              scopes.length) %
                            scopes.length;
                    setScope(scopes[next].id);
                    setOffset(0);
                    setArchived(false);
                    document
                      .getElementById(`skill-tab-${scopes[next].id}`)
                      ?.focus();
                  }
                }}
              >
                <Icon size={15} />
                {s.label}
              </button>
            );
          })}
        </div>
        <div className="knowledge-toolbar-actions">
          <label className="knowledge-search">
            <Search size={16} />
            <input
              aria-label="搜索技能"
              value={search}
              maxLength={200}
              placeholder="搜索技能或创作方法…"
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
            {search && (
              <button
                aria-label="清除搜索"
                onClick={() => {
                  setSearch("");
                  setOffset(0);
                }}
              >
                ×
              </button>
            )}
          </label>
          <Button
            variant="outline"
            disabled={busy || !canEdit}
            onClick={() => setInstallOpen(true)}
          >
            <Plus size={16} />
            安装技能
          </Button>
          <Button
            disabled={busy || !canEdit}
            onClick={() => void choose({ ...blank })}
          >
            <Plus size={16} />
            新建技能
          </Button>
        </div>
      </div>
      <div className="knowledge-filterbar">
        <div className="knowledge-categories" aria-label="技能分类">
          {[["", "全部分类"], ...Object.entries(categories)].map(([k, v]) => (
            <button
              key={k}
              aria-pressed={category === k}
              onClick={() => {
                setCategory(k);
                setOffset(0);
              }}
            >
              {v}
            </button>
          ))}
        </div>
        <div className="knowledge-filter-options">
          {scope !== "templates" && (
            <>
              <button
                aria-label="查看归档技能"
                aria-pressed={archived}
                className={archived ? "is-active" : ""}
                onClick={() => {
                  setArchived(!archived);
                  setOffset(0);
                }}
              >
                <Archive size={14} />
                {archived ? "已归档" : "归档"}
              </button>
              <select
                aria-label="技能排序"
                value={sort}
                onChange={(e) => {
                  setSort(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="updated">最近更新</option>
                <option value="name">名称排序</option>
              </select>
            </>
          )}
          {hasFilters && (
            <button
              title="清除筛选"
              aria-label="清除筛选"
              onClick={resetFilters}
            >
              <RotateCcw size={14} />
            </button>
          )}
        </div>
      </div>
      {!draft && !reading && notices}
      <div
        id="skill-catalog"
        role="tabpanel"
        aria-labelledby={`skill-tab-${scope}`}
        className="knowledge-catalog"
        aria-busy={loading}
      >
        {(scope === "templates" ? templates.error : list.error) ? (
          <div className="knowledge-empty">
            <BookOpen />
            <h2>技能暂时未能载入</h2>
            <p>你的内容仍保留在项目中，请稍后重试。</p>
            <Button
              variant="outline"
              onClick={() =>
                void (scope === "templates" ? templates : list).refetch()
              }
            >
              重新读取技能
            </Button>
          </div>
        ) : loading ? (
          <div className="knowledge-grid">
            {Array.from({ length: 6 }, (_, i) => (
              <div className="knowledge-skeleton" key={i}>
                <span />
                <div />
                <div />
                <div />
              </div>
            ))}
          </div>
        ) : groups.length ? (
          groups.map((g, index) => {
            const Icon = categoryIcons[g.key as keyof typeof categoryIcons];
            return (
              <section className="knowledge-group" key={g.key}>
                <div className="knowledge-group-heading">
                  <h2>
                    <Icon size={17} />
                    {g.label}
                  </h2>
                  <span>{g.items.length} 项</span>
                </div>
                <div className="knowledge-grid">
                  {index === 0 && !archived && canEdit && (
                    <SkillInstallCard
                      disabled={busy}
                      onClick={() => setInstallOpen(true)}
                    />
                  )}{" "}
                  {g.items.map((s) => (
                    <article
                      className="knowledge-card"
                      data-skill-id={s.id}
                      key={s.id ?? s.name}
                    >
                      <div className="knowledge-card-top">
                        <span className={`knowledge-tile tone-${g.key}`}>
                          <Icon size={23} />
                        </span>
                        <div className="knowledge-card-meta">
                          <span>{s.id ? `v${s.revision}` : "内置模板"}</span>
                          {s.id && (
                            <button
                              className={`knowledge-icon-button ${s.favorite ? "is-liked" : ""}`}
                              aria-label={`${s.favorite ? "取消收藏" : "收藏"} ${s.name}`}
                              title={s.favorite ? "取消收藏" : "收藏"}
                              disabled={busy}
                              onClick={() => void favorite(s)}
                            >
                              <Heart
                                size={16}
                                fill={s.favorite ? "currentColor" : "none"}
                              />
                            </button>
                          )}
                        </div>
                      </div>
                      <button
                        className="knowledge-card-main"
                        disabled={busy}
                        onClick={() => void openDetail(s)}
                        aria-label={`查看技能 ${s.name}`}
                      >
                        <h3>{s.name}</h3>
                        <p>{s.description}</p>
                      </button>
                      <div className="knowledge-card-footer">
                        <span title={s.source}>
                          {s.source_metadata?.kind === "github"
                            ? "GitHub 技能"
                            : s.source_metadata?.kind === "copy"
                              ? "项目复用"
                              : !s.id
                                ? "Inspiration"
                                : s.source_metadata?.kind === "file"
                                  ? "文件导入"
                                  : "项目方法"}
                          <i /> {s.file_count} 个文件
                        </span>
                        {!s.id ? (
                          <button
                            disabled={busy || !canEdit}
                            onClick={() =>
                              void choose({
                                name: s.name,
                                description: s.description,
                                instructions: s.instructions,
                                source: s.source,
                                required_tools: s.required_tools,
                                category: s.category,
                              })
                            }
                          >
                            使用模板
                            <ArrowUpRight size={13} />
                          </button>
                        ) : s.project_id === projectId ? (
                          <button
                            aria-label={`编辑技能 ${s.name}`}
                            disabled={busy || !canEdit}
                            onClick={() =>
                              void perform(async () =>
                                acceptDraft(
                                  await api.get<Skill>(
                                    `${base}/skills/${s.id}`,
                                  ),
                                ),
                              )
                            }
                          >
                            <Pencil size={13} />
                            <span>编辑</span>
                          </button>
                        ) : (
                          <button
                            disabled={busy || !canEdit}
                            onClick={() => void copy(s)}
                          >
                            加入项目
                            <Plus size={13} />
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            );
          })
        ) : (
          <div className="knowledge-empty-layout">
            {canEdit && !hasFilters && (
              <SkillInstallCard
                disabled={busy}
                onClick={() => setInstallOpen(true)}
              />
            )}
            <div className="knowledge-empty">
              <span className="knowledge-empty-icon">
                {scope === "favorites" ? (
                  <Heart size={25} />
                ) : (
                  <BookOpen size={25} />
                )}
              </span>
              <h2>
                {hasFilters
                  ? "没有找到匹配的技能"
                  : scope === "favorites"
                    ? "收藏你常用的创作方法"
                    : "从第一个创作技能开始"}
              </h2>
              <p>
                {hasFilters
                  ? "换个关键词，或试试其他分类。"
                  : scope === "favorites"
                    ? "在技能卡片上点一下爱心，下次在这里快速找到。"
                    : "安装已有技能，或从内置方法模板中获得灵感。"}
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  if (hasFilters) resetFilters();
                  else {
                    setScope("templates");
                    setOffset(0);
                  }
                }}
              >
                {hasFilters ? "清除筛选" : "浏览方法模板"}
              </Button>
            </div>
          </div>
        )}
        {!loading && (
          <div className="knowledge-pagination">
            <span>
              共 {total} 项{archived ? "归档技能" : "技能"}
              {total > 24 && scope !== "templates"
                ? ` · 第 ${offset / 24 + 1} / ${Math.ceil(total / 24)} 页`
                : ""}
            </span>
            {scope !== "templates" && total > 24 && (
              <div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 24))}
                >
                  上一页
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={offset + 24 >= total}
                  onClick={() => setOffset(offset + 24)}
                >
                  下一页
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
      <footer className="knowledge-page-footer">
        <span>
          <BookOpen size={14} />
          让好方法留在项目里。
        </span>
        <div>
          <Link to={`${base}/agent`}>
            去创作助理
            <ArrowUpRight size={12} />
          </Link>
          <Link to={`${base}/assets/import`}>导入外部目录 ↗</Link>
          <Link to={`${base}/settings?section=connections`}>
            外部 AI 连接 ↗
          </Link>
        </div>
      </footer>
      {installOpen && (
        <SkillInstall
          base={base}
          onClose={() => setInstallOpen(false)}
          onManualCreate={() => {
            setInstallOpen(false);
            void choose({ ...blank });
          }}
          onInstalled={(skill) => {
            setInstallOpen(false);
            setScope("project");
            resetFilters();
            setMessage(`「${skill.name}」已安装，可在创作中选用`);
            setReading({ ...skill, project_id: projectId, favorite: false });
            void qc.invalidateQueries({ queryKey: ["skills"] });
          }}
        />
      )}
      <SkillOverlay
        open={!!draft}
        drawer
        dismissDisabled={busy}
        title={draft?.id ? `编辑技能 · 版本 ${draft.revision}` : "创建技能"}
        subtitle="整理你的创作方法"
        onClose={() => void closeEditor()}
      >
        <div className="knowledge-editor-notices">{notices}</div>
        <div
          className="knowledge-editor-tabs"
          role="tablist"
          aria-label="技能编辑分区"
        >
          {[
            ["content", "方法说明"],
            ["files", "配套文件"],
            ["tools", "工具依赖"],
            ["history", "版本历史"],
          ].map(([k, v]) => (
            <button
              role="tab"
              aria-selected={editorTab === k}
              id={`skill-editor-tab-${k}`}
              aria-controls={`skill-editor-panel-${k}`}
              tabIndex={editorTab === k ? 0 : -1}
              key={k}
              onClick={() => setEditorTab(k)}
              onKeyDown={(event) => {
                const tabs = ["content", "files", "tools", "history"];
                if (
                  !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                    event.key,
                  )
                )
                  return;
                event.preventDefault();
                const index =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? 3
                      : (tabs.indexOf(k) +
                          (event.key === "ArrowRight" ? 1 : -1) +
                          tabs.length) %
                        tabs.length;
                setEditorTab(tabs[index]);
                document
                  .getElementById(`skill-editor-tab-${tabs[index]}`)
                  ?.focus();
              }}
            >
              {v}
              {k === "files" && draft?.files?.length ? (
                <span>{draft.files.length}</span>
              ) : null}
            </button>
          ))}
        </div>
        {draft && (
          <fieldset
            disabled={busy || !canEdit}
            className="knowledge-editor-form"
          >
            <div
              hidden={editorTab !== "content"}
              role="tabpanel"
              id="skill-editor-panel-content"
              aria-labelledby="skill-editor-tab-content"
              className="knowledge-editor-section"
            >
              <div className="knowledge-editor-section-top">
                <h3>方法与说明</h3>
                <label className="studio-link cursor-pointer text-xs">
                  <FileUp size={14} />
                  导入 Markdown
                  <input
                    className="sr-only"
                    type="file"
                    accept=".md,.txt,text/plain,text/markdown"
                    aria-label="导入技能文件"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (!file) return;
                      void perform(async () => {
                        if (file.size > 80000)
                          throw new Error("技能文件最大80KB");
                        const text = await file.text();
                        if (text.length > 20000)
                          throw new Error("技能说明最多20000字");
                        edit({
                          instructions: text,
                          name:
                            draft.name ||
                            file.name.replace(/\.(md|txt)$/i, "").slice(0, 120),
                          source: `文件导入：${file.name}`,
                        });
                      });
                    }}
                  />
                </label>
              </div>
              <p role="status" className="text-xs text-muted-foreground">
                {dirty
                  ? storageError
                    ? "有未保存修改 · 本机草稿保存失败"
                    : "有未保存修改 · 已保留本机草稿"
                  : "已与保存版本同步"}
              </p>
              <label className="block text-xs space-y-2">
                <span>技能名称</span>
                <input
                  className={field}
                  value={draft.name}
                  maxLength={120}
                  onChange={(e) => edit({ name: e.target.value })}
                />
              </label>
              <label className="block text-xs space-y-2">
                <span>适用任务</span>
                <textarea
                  aria-label="适用任务"
                  disabled={busy}
                  className={field}
                  value={draft.description}
                  maxLength={1000}
                  onChange={(e) => edit({ description: e.target.value })}
                />
              </label>
              <label className="block text-xs space-y-2">
                <span>方法与步骤</span>
                <textarea
                  aria-label="方法与步骤"
                  disabled={busy}
                  className={`${field} min-h-64 resize-y leading-7`}
                  value={draft.instructions}
                  maxLength={20000}
                  onChange={(e) => edit({ instructions: e.target.value })}
                />
              </label>
              <label className="block text-xs space-y-2">
                <span>来源与作者</span>
                <input
                  className={field}
                  value={draft.source}
                  maxLength={1000}
                  onChange={(e) => edit({ source: e.target.value })}
                />
              </label>
            </div>
            <div
              hidden={editorTab !== "files"}
              role="tabpanel"
              id="skill-editor-panel-files"
              aria-labelledby="skill-editor-tab-files"
              className="knowledge-editor-section"
            >
              <SkillFilesEditor
                run={perform}
                skill={draft}
                disabled={busy}
                onChange={edit}
              />
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => setReading(draft)}>
                  阅读技能包
                </Button>
                {draft.id && (
                  <Button
                    variant="ghost"
                    onClick={() =>
                      void perform(() => exportSkill(base, draft.id!))
                    }
                  >
                    下载已保存技能包
                  </Button>
                )}
                {draft.id && draft.source_metadata?.kind === "github" && (
                  <Button
                    variant="ghost"
                    disabled={busy || dirty}
                    onClick={() =>
                      void perform(async () => {
                        const incoming = await api.post<Skill>(
                          `${base}/skills/${draft.id}/sync-preview`,
                          { revision: draft.revision },
                        );
                        setDraft(incoming);
                        setMessage(
                          "已读取来源最新内容，请核对正文和文件后保存为新版本。",
                        );
                      })
                    }
                  >
                    检查来源更新
                  </Button>
                )}
              </div>
            </div>
            <div
              hidden={editorTab !== "tools"}
              role="tabpanel"
              id="skill-editor-panel-tools"
              aria-labelledby="skill-editor-tab-tools"
              className="knowledge-editor-section"
            >
              <fieldset className="space-y-2">
                <legend className="mb-2 text-xs">依赖工具</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {tools.data
                    ?.filter(
                      (t) =>
                        !["finish", "skill.load", "skill.read_file"].includes(
                          t.name,
                        ),
                    )
                    .map((t) => (
                      <label
                        className="flex items-start gap-2 text-xs leading-5"
                        key={t.name}
                      >
                        <input
                          type="checkbox"
                          checked={draft.required_tools.includes(t.name)}
                          onChange={(e) =>
                            edit({
                              required_tools: e.target.checked
                                ? [...draft.required_tools, t.name]
                                : draft.required_tools.filter(
                                    (n) => n !== t.name,
                                  ),
                            })
                          }
                        />
                        <span title={t.description}>
                          {t.name}
                          {t.requires_review ? " · 需审阅" : ""}
                        </span>
                      </label>
                    ))}
                </div>
              </fieldset>
              <p className="text-xs leading-5 text-muted-foreground">
                技能提供方法，不扩大修改范围。任务启动时固定所选版本，后续更新或归档不会改变已启动任务。
              </p>
            </div>
            <div
              hidden={editorTab !== "history"}
              role="tabpanel"
              id="skill-editor-panel-history"
              aria-labelledby="skill-editor-tab-history"
              className="knowledge-editor-section"
            >
              {draft.id && (
                <details open className="knowledge-history">
                  <summary className="cursor-pointer text-sm">
                    版本与来源历史
                  </summary>
                  {history.error && (
                    <p className="text-danger text-xs">历史读取失败</p>
                  )}
                  {history.data?.map((h) => (
                    <div
                      key={h.revision}
                      className="flex items-center justify-between gap-2 border-b border-border py-3"
                    >
                      <div className="text-xs">
                        <div>
                          版本 {h.revision} · {h.document.name}
                        </div>
                        <div className="mt-1 text-muted-foreground">
                          {h.document.source}
                        </div>
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setReading({
                            ...h.document,
                            id: draft.id,
                            revision: h.revision,
                          })
                        }
                      >
                        查看内容
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy || h.revision === draft.revision}
                        onClick={() =>
                          void perform(async () => {
                            if (
                              !(await confirm({
                                message: `恢复版本 ${h.revision}？这会创建新版本，替换编辑区草稿。`,
                              }))
                            )
                              return;
                            acceptDraft(
                              await api.post<Skill>(
                                `${base}/skills/${draft.id}/history/${h.revision}/restore`,
                                { revision: draft.revision },
                              ),
                            );
                          })
                        }
                      >
                        恢复
                      </Button>
                    </div>
                  ))}
                  <div className="mt-2 flex gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!historyOffset}
                      onClick={() =>
                        setHistoryOffset((x) => Math.max(0, x - 20))
                      }
                    >
                      较新版本
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={history.data?.length !== 20}
                      onClick={() => setHistoryOffset((x) => x + 20)}
                    >
                      较早版本
                    </Button>
                  </div>
                </details>
              )}
              {!draft.id && (
                <p className="knowledge-empty-note">
                  保存技能后，版本记录会显示在这里。
                </p>
              )}
            </div>
            <div className="knowledge-editor-footer">
              <span>
                {dirty
                  ? "有未保存修改"
                  : `已保存 · 版本 ${draft.revision ?? 1}`}
              </span>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={
                    busy ||
                    !dirty ||
                    !draft.name.trim() ||
                    !draft.description.trim() ||
                    !draft.instructions.trim()
                  }
                  onClick={() => void perform(save)}
                >
                  <Save size={15} />
                  保存技能
                </Button>
                {draft.id && (
                  <Button
                    variant="outline"
                    disabled={busy || dirty}
                    onClick={() =>
                      void perform(async () => {
                        const result = await api.post<Skill>(
                          `${base}/skills/${draft.id}/archive`,
                          {
                            revision: draft.revision,
                            archived: !draft.archived,
                          },
                        );
                        acceptDraft(result);
                        setArchived(!!result.archived);
                      })
                    }
                  >
                    {draft.archived ? "重新启用" : "归档技能"}
                  </Button>
                )}
                {draft.id && (
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() =>
                      void perform(async () => {
                        if (
                          dirty &&
                          !(await confirm({
                            message: "载入服务器版本会替换当前草稿，确定继续？",
                          }))
                        )
                          return;
                        acceptDraft(
                          await api.get<Skill>(`${base}/skills/${draft.id}`),
                        );
                      })
                    }
                  >
                    载入服务器版本
                  </Button>
                )}
              </div>
            </div>
          </fieldset>
        )}
      </SkillOverlay>
      <SkillReader
        key={`${reading?.id ?? reading?.name ?? "closed"}:${reading?.revision}`}
        skill={reading}
        onClose={() => setReading(null)}
        actions={
          reading && !draft ? (
            <>
              {reading.id && (
                <button
                  className={`knowledge-reader-action ${reading.favorite ? "is-liked" : ""}`}
                  disabled={busy}
                  onClick={() =>
                    void favorite({
                      ...reading,
                      project_id: reading.project_id ?? projectId,
                      file_count: (reading.files?.length ?? 0) + 1,
                    })
                  }
                >
                  <Heart size={15} />
                  {reading.favorite ? "已收藏" : "收藏"}
                </button>
              )}
              {canEdit &&
              (reading.project_id === projectId || !reading.project_id) ? (
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    const { project_id: _, favorite: __, ...doc } = reading;
                    void choose(doc);
                  }}
                >
                  <Pencil size={14} />
                  {reading.id ? "编辑技能" : "使用模板"}
                </Button>
              ) : (
                canEdit && (
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void copy({
                        ...reading,
                        project_id: reading.project_id!,
                        file_count: (reading.files?.length ?? 0) + 1,
                      })
                    }
                  >
                    <Plus size={14} />
                    加入项目
                  </Button>
                )
              )}
            </>
          ) : undefined
        }
        feedback={notices}
      />
    </div>
  );
}
