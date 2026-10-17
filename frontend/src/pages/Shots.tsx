import { ShotNameDialog } from "@/components/ShotNameDialog";
import { useShotContext } from "@/lib/useShotContext";
import { SelectField } from "@/components/ui/select-field";
import { SearchField } from "@/components/ui/search-field";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  useParams,
  useSearchParams,
  useNavigate,
  Navigate,
  Link,
} from "react-router-dom";
import GenerationPanel from "@/components/GenerationPanel";
import ReviewPanel from "@/components/ReviewPanel";
import ShotAssetRefs from "@/components/ShotAssetRefs";
import {
  Pencil,
  Filter,
  X,
  ArrowLeft,
  Images,
  Clapperboard,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { ShotMedia } from "@/components/ShotMedia";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import {
  api,
  NEXT_STATUS,
  type ComposeResult,
  type Novel,
  type NovelDetail,
  type Shot,
} from "@/lib/api";
import "./shot-workspace.css";
import { Modal } from "@/components/ui/modal";
import { ShotMediaReferences } from "@/components/ShotMediaReferences";

const ORDER = [
  "to_design",
  "concept",
  "generating",
  "pending_review",
  "revising",
  "approved",
  "in_cut",
];
const DOT: Record<string, string> = {
  to_design: "bg-muted-foreground",
  concept: "bg-info",
  generating: "bg-primary",
  pending_review: "bg-warning",
  revising: "bg-danger",
  approved: "bg-success",
  in_cut: "bg-foreground",
};

export default function Shots() {
  const { projectId } = useParams();
  const [params] = useSearchParams();
  if (!params.get("shot") && params.get("view") !== "status")
    return <Navigate to={`/projects/${projectId}/storyboard`} replace />;
  return <ShotsWorkspace key={projectId} />;
}
function ShotsWorkspace() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const requestedShot = search.get("shot");
  const requestedTab = search.get("tab");
  const { t: tr, lang } = useI18n();
  const [open, setOpen] = useState<Shot | null>(null);
  const [renaming, setRenaming] = useState(false);
  const context = useShotContext(projectId!);
  const [mtab, setMtab] = useState("overview");
  const [compose, setCompose] = useState<ComposeResult | null>(null);
  const openIdRef = useRef("");
  const navigate = useNavigate();
  const [shotQuery, setShotQuery] = useState("");
  const [referencesOpen, setReferencesOpen] = useState(false);
  const [hideEmpty, setHideEmpty] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [scriptFilter, setScriptFilter] = usePersistentState<string>(
    `sb.script.${projectId}`,
    "",
  );
  const [boardLimit, setBoardLimit] = useState(40);
  const [railLimit, setRailLimit] = useState(60);
  const [referenceTab, setReferenceTab] = useState("assets");
  const boardScroll = useRef(0);
  const [novelId, setNovelId] = usePersistentState(
    `shots.novel.${projectId}`,
    "",
  );
  const [chapterId, setChapterId] = usePersistentState(
    `shots.chapter.${projectId}`,
    "",
  );
  const openShot = (s: Shot, keepTab = false) => {
    openIdRef.current = s.id;
    setOpen(s);
    if (!open)
      boardScroll.current = document.querySelector("main")?.scrollTop ?? 0;
    if (!keepTab) setMtab("gen");
    const next = new URLSearchParams(search);
    next.set("shot", s.id);
    if (!keepTab) next.set("tab", "gen");
    setSearch(next, { replace: keepTab });
    setCompose(null);
    doCompose.mutate(s.id);
  };

  // 筛选数据源:小说列表 + 选中小说的章节
  const { data: novels } = useQuery({
    queryKey: ["novels", projectId],
    queryFn: () => api.get<Novel[]>(`${base}/novels`),
  });
  const { data: novelDetail } = useQuery({
    queryKey: ["novel", projectId, novelId],
    queryFn: () => api.get<NovelDetail>(`${base}/novels/${novelId}`),
    enabled: !!novelId,
  });
  const chapters = novelDetail?.chapters ?? [];

  const {
    data: shots,
    isPending,
    error: loadError,
    refetch,
  } = useQuery({
    queryKey: [
      "all-shots",
      projectId,
      requestedShot ? "" : novelId,
      requestedShot ? "" : chapterId,
    ],
    queryFn: () => {
      const p = new URLSearchParams();
      if (novelId && !requestedShot) p.set("novel_id", novelId);
      if (chapterId && !requestedShot) p.set("chapter_id", chapterId);
      const q = p.toString();
      return api.get<Shot[]>(`${base}/shots${q ? `?${q}` : ""}`);
    },
  });

  const boardShots = (shots ?? []).filter(
    (s) =>
      !scriptFilter ||
      (scriptFilter === "other"
        ? !context.data?.scenes[s.scene_id]
        : context.data?.scenes[s.scene_id]?.script.id === scriptFilter),
  );
  // 看板计数 / 总数从(可能已筛选的)镜头列表派生,确保与筛选一致
  const counts = ORDER.reduce<Record<string, number>>((a, st) => {
    a[st] = boardShots.filter((s) => s.production_status === st).length;
    return a;
  }, {});
  const total = boardShots.length;
  const sceneShots = (shots ?? [])
    .filter((s) => s.scene_id === open?.scene_id)
    .sort((a, b) => a.ordinal - b.ordinal);
  const currentIndex = sceneShots.findIndex((s) => s.id === open?.id);
  const currentScript = open
    ? context.data?.scenes[open.scene_id]?.script
    : undefined;
  const relatedShots = (shots ?? []).filter((s) =>
    currentScript
      ? context.data?.scenes[s.scene_id]?.script.id === currentScript.id
      : s.novel_id === open?.novel_id,
  );
  const sceneOptions = Array.from(
    new Map(
      relatedShots.map((s) => [
        s.scene_id,
        {
          value: s.scene_id,
          label:
            s.scene_title || (lang === "zh" ? "未命名场景" : "Untitled scene"),
        },
      ]),
    ).values(),
  );

  useEffect(() => {
    if (currentIndex >= railLimit) setRailLimit(currentIndex + 20);
  }, [currentIndex, railLimit]);

  const fmtDate = (iso?: string | null) => {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
    qc.invalidateQueries({ queryKey: ["board", projectId] });
  };
  const transition = useMutation({
    mutationFn: ({ id, to }: { id: string; to: string }) =>
      api.post<Shot>(`${base}/shots/${id}/transition`, { to }),
    onSuccess: (s) => {
      if (openIdRef.current === s.id) setOpen(s);
      refresh();
    },
  });
  const doCompose = useMutation({
    mutationFn: (id: string) =>
      api.get<ComposeResult>(`${base}/shots/${id}/compose-prompt`),
    onSuccess: (result, id) => {
      if (openIdRef.current === id) setCompose(result);
    },
  });

  useEffect(() => {
    const found = shots?.find((s) => s.id === requestedShot);
    if (found && openIdRef.current !== found.id) {
      openIdRef.current = found.id;
      setOpen(found);
      setCompose(null);
      doCompose.mutate(found.id);
    } else if (!requestedShot) {
      openIdRef.current = "";
      setOpen(null);
    }
  }, [shots, requestedShot]);
  useEffect(() => {
    if (requestedShot)
      setMtab(
        requestedTab === "review"
          ? "review"
          : requestedTab === "overview"
            ? "overview"
            : "gen",
      );
  }, [requestedShot, requestedTab]);
  useEffect(() => {
    document
      .querySelector('.shot-rail-list [aria-current="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [open?.id]);
  function closeWorkspace() {
    setReferencesOpen(false);
    if (search.get("view") !== "status") {
      navigate(`${base}/storyboard?scene=${open?.scene_id || ""}`);
      return;
    }
    openIdRef.current = "";
    setOpen(null);
    setSearch({ view: "status" }, { replace: true });
    requestAnimationFrame(() => {
      const main = document.querySelector("main");
      if (main) main.scrollTop = boardScroll.current;
    });
  }
  const visibleShots = sceneShots.filter((s) =>
    `${s.title} ${s.code} ${s.description} ${s.scene_title ?? ""} ${s.chapter_title ?? ""} ${s.novel_title ?? ""}`
      .toLowerCase()
      .includes(shotQuery.trim().toLowerCase()),
  );
  const byStatus = (st: string) =>
    boardShots.filter((s) => s.production_status === st);

  useEffect(() => {
    setOpen((current) =>
      current ? shots?.find((s) => s.id === current.id) || current : null,
    );
  }, [shots]);
  if (
    requestedShot &&
    !open &&
    !loadError &&
    (isPending || shots?.some((s) => s.id === requestedShot))
  )
    return (
      <div className="p-6 text-sm text-muted-foreground" role="status">
        {tr("common.loading")}
      </div>
    );
  return (
    <div className={`shots-studio p-4 md:p-6 ${open ? "is-creating" : ""}`}>
      <h1 hidden={!!open} className="text-xl font-semibold mb-1">
        {lang === "zh" ? "制作进度" : "Production progress"}
      </h1>
      <p hidden={!!open} className="text-sm text-muted-foreground mb-4">
        {tr("shots.subtitle").replace("{n}", String(total))}
      </p>

      {!open && (
        <Link
          className="studio-link mb-4 inline-block"
          to={`${base}/storyboard`}
        >
          {lang === "zh" ? "← 返回分镜创作" : "← Back to storyboard"}
        </Link>
      )}
      {/* 筛选:按小说 / 章节 */}
      <div
        className={`flex flex-wrap items-center gap-2 mb-4 ${open ? "!hidden" : ""}`}
      >
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <Filter className="h-3.5 w-3.5" /> {tr("shots.filter")}
        </span>
        <SelectField
          label={lang === "zh" ? "选择剧本" : "Choose script"}
          value={scriptFilter}
          onChange={(value) => {
            setScriptFilter(value);
            setNovelId("");
            setChapterId("");
          }}
          options={[
            {
              value: "",
              label:
                lang === "zh" ? "全部剧本与故事" : "All scripts and stories",
            },
            ...(context.data?.scripts || []).map((s) => ({
              value: s.id,
              label: s.title,
            })),
            {
              value: "other",
              label: lang === "zh" ? "其他来源的场景" : "Other scene sources",
            },
          ]}
        />
        <SelectField
          label={tr("shots.allNovels")}
          value={novelId}
          onChange={(value) => {
            setNovelId(value);
            setChapterId("");
          }}
          options={[
            { value: "", label: tr("shots.allNovels") },
            ...(novels || []).map((n) => ({
              value: n.id,
              label: n.title || "未命名故事",
            })),
          ]}
        />
        {novelId && (
          <SelectField
            label={tr("shots.allChapters")}
            value={chapterId}
            onChange={setChapterId}
            options={[
              { value: "", label: tr("shots.allChapters") },
              ...chapters.map((c) => ({
                value: c.id,
                label:
                  c.title ||
                  tr("nar.chapterN").replace("{n}", String(c.ordinal)),
              })),
            ]}
          />
        )}
        <SelectField
          label={lang === "zh" ? "制作状态筛选" : "Production status"}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            {
              value: "",
              label: lang === "zh" ? "全部制作状态" : "All statuses",
            },
            ...ORDER.map((st) => ({ value: st, label: tr(`status.${st}`) })),
          ]}
        />

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={hideEmpty}
            onChange={(e) => setHideEmpty(e.target.checked)}
          />
          隐藏空列
        </label>
        {(novelId || chapterId) && (
          <button
            onClick={() => {
              setNovelId("");
              setChapterId("");
            }}
            className="text-xs text-muted-foreground hover:text-primary"
          >
            {tr("shots.clearFilter")}
          </button>
        )}
      </div>

      {loadError && (
        <div role="alert" className="studio-empty">
          <p>
            {lang === "zh"
              ? "镜头加载失败，统计暂不可用。"
              : "Unable to load shots."}
          </p>
          <Button onClick={() => void refetch()} variant="outline">
            {lang === "zh" ? "重试" : "Retry"}
          </Button>
        </div>
      )}
      {isPending && (
        <div role="status" className="studio-empty">
          {lang === "zh" ? "正在读取镜头与状态…" : "Loading shots…"}
        </div>
      )}
      {!isPending && !loadError && (
        <>
          {total === 0 && (
            <div className="studio-empty mb-5">
              <Clapperboard className="h-7 w-7" />
              <p>
                {lang === "zh"
                  ? "当前范围还没有镜头。可从剧本拆分场景与分镜，或清除筛选。"
                  : "No shots in this scope. Break down a script or clear the filters."}
              </p>
            </div>
          )}
          {/* KPI 指标条 */}
          <div
            className={`grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-7 gap-2 mb-6 ${open ? "!hidden" : ""}`}
          >
            {ORDER.map((st) => (
              <div
                key={st}
                className="glass rounded-xl border border-border p-3 shadow-panel  transition hover:border-primary/40"
              >
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className={`h-2 w-2 rounded-full ${DOT[st]}`} />{" "}
                  {tr(`status.${st}`)}
                </div>
                <div className="text-2xl font-semibold mt-1 font-code">
                  {counts[st] ?? 0}
                </div>
              </div>
            ))}
          </div>

          {/* 看板(左) + 详情(右) */}
          <div
            className={`flex flex-col xl:flex-row gap-4 items-start ${open ? "shot-creation-layout" : ""}`}
          >
            <div
              className={`w-full flex-1 min-w-0 overflow-x-auto pb-4 ${open ? "!hidden" : ""}`}
              aria-label={lang === "zh" ? "制作状态看板" : "Production board"}
            >
              <div className="flex gap-3">
                {ORDER.filter(
                  (st) =>
                    (!statusFilter || st === statusFilter) &&
                    (!hideEmpty || !!statusFilter || (counts[st] ?? 0) > 0),
                ).map((st) => {
                  const items = byStatus(st);
                  return (
                    <div key={st} className="w-56 shrink-0">
                      <div className="flex items-center gap-1.5 mb-2 text-sm font-medium">
                        <span className={`h-2 w-2 rounded-full ${DOT[st]}`} />
                        {tr(`status.${st}`)}
                        <span className="text-muted-foreground">
                          {items.length}
                        </span>
                      </div>
                      <div className="space-y-2">
                        {items.slice(0, boardLimit).map((s) => (
                          <div
                            key={s.id}
                            className={`block w-full overflow-hidden text-left rounded-lg border bg-card transition-all ${
                              open?.id === s.id
                                ? "border-primary ring-1 ring-primary "
                                : "border-border hover:border-primary/60"
                            }`}
                          >
                            {s.selected_generation_id && (
                              <div className="aspect-video overflow-hidden border-b">
                                <ShotMedia projectId={projectId!} shot={s} />
                              </div>
                            )}
                            <button
                              onClick={() => openShot(s)}
                              className="w-full p-3 text-left hover:bg-elevated"
                              aria-pressed={open?.id === s.id}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-[11px] font-code text-muted-foreground">
                                  {s.scene_title ||
                                    s.novel_title ||
                                    "未命名场景"}
                                </span>
                                {s.created_at && (
                                  <span className="font-code text-[10px] text-faint shrink-0">
                                    {fmtDate(s.created_at)}
                                  </span>
                                )}
                              </div>
                              <div className="text-sm mt-0.5 line-clamp-2">
                                {s.title || s.description || "—"}
                              </div>
                              {(s.novel_title || s.chapter_title) && (
                                <div className="mt-1.5 flex flex-wrap gap-1">
                                  {s.novel_title && (
                                    <span className="inline-flex max-w-full items-center gap-1 truncate rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                                      📖 {s.novel_title}
                                    </span>
                                  )}
                                  {(s.chapter_title ||
                                    s.chapter_ordinal != null) && (
                                    <span className="inline-flex max-w-full items-center gap-1 truncate rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                                      🔖{" "}
                                      {s.chapter_title ??
                                        tr("nar.chapterN").replace(
                                          "{n}",
                                          String(s.chapter_ordinal),
                                        )}
                                    </span>
                                  )}
                                </div>
                              )}
                            </button>
                          </div>
                        ))}
                        {items.length > boardLimit && (
                          <Button
                            variant="ghost"
                            onClick={() => setBoardLimit(boardLimit + 40)}
                          >
                            加载更多（{boardLimit}/{items.length}）
                          </Button>
                        )}
                        {items.length === 0 && (
                          <div className="text-xs text-muted-foreground/60 px-1">
                            —
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {open && (
              <nav className="shot-creation-rail" aria-label="镜头导航">
                <button className="shot-back" onClick={closeWorkspace}>
                  <ArrowLeft size={16} />
                  {search.get("from") === "storyboard"
                    ? "返回分镜"
                    : "返回看板"}
                </button>
                <div className="shot-scope-heading">
                  <span>
                    {currentScript?.title ||
                      open.novel_title ||
                      (lang === "zh" ? "当前创作" : "Current work")}
                  </span>
                  <SelectField
                    label={lang === "zh" ? "切换场景" : "Switch scene"}
                    value={open.scene_id}
                    options={sceneOptions}
                    onChange={(id) => {
                      const first = relatedShots
                        .filter((s) => s.scene_id === id)
                        .sort((a, b) => a.ordinal - b.ordinal)[0];
                      if (first) {
                        setShotQuery("");
                        openShot(first, true);
                      }
                    }}
                  />
                </div>
                <SearchField
                  className="shot-search"
                  label="搜索创作镜头"
                  placeholder="查找当前场景的镜头…"
                  value={shotQuery}
                  onValueChange={(value) => {
                    setShotQuery(value);
                    setRailLimit(60);
                  }}
                />
                <div className="shot-rail-list">
                  {visibleShots.slice(0, railLimit).map((s) => (
                    <button
                      key={s.id}
                      aria-label={`打开镜头 ${s.title || "未命名镜头"}`}
                      aria-current={s.id === open.id ? "true" : undefined}
                      onClick={() => openShot(s, true)}
                    >
                      <span className="shot-rail-thumb">
                        {s.selected_generation_id ? (
                          <ShotMedia
                            projectId={projectId!}
                            shot={s}
                            thumbnail
                          />
                        ) : (
                          <Clapperboard size={20} />
                        )}
                      </span>
                      <span>
                        <strong>{s.title || "未命名镜头"}</strong>
                        <small className="line-clamp-2">
                          {s.description || "暂无内容描述"}
                        </small>
                        <small>
                          {s.scene_title ||
                            s.chapter_title ||
                            s.novel_title ||
                            "项目镜头"}
                        </small>
                      </span>
                    </button>
                  ))}
                  {visibleShots.length > railLimit && (
                    <Button
                      variant="ghost"
                      onClick={() => setRailLimit(railLimit + 60)}
                    >
                      加载更多镜头
                    </Button>
                  )}
                  {!visibleShots.length && (
                    <p className="p-4 text-sm text-muted-foreground">
                      没有匹配的镜头
                    </p>
                  )}
                </div>
              </nav>
            )}
            {/* 右侧详情面板 */}
            {open && (
              <aside
                aria-label={lang === "zh" ? "镜头工作区" : "Shot workspace"}
                className="shot-detail-workspace shot-creation-main"
              >
                <div className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0">
                  <span className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground truncate max-w-64">
                      {open.scene_title ||
                        (lang === "zh" ? "当前场景" : "Current scene")}
                    </span>
                    <Badge variant="primary">
                      {tr(`status.${open.production_status}`)}
                    </Badge>
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      className="studio-icon-button disabled:opacity-30"
                      aria-label={
                        lang === "zh" ? "上一个镜头" : "Previous shot"
                      }
                      disabled={currentIndex <= 0}
                      onClick={() =>
                        sceneShots[currentIndex - 1] &&
                        openShot(sceneShots[currentIndex - 1], true)
                      }
                    >
                      <ChevronLeft size={16} />
                    </button>
                    <span className="text-[10px] text-faint">
                      {currentIndex >= 0 ? currentIndex + 1 : "—"}/
                      {sceneShots.length}
                    </span>
                    <button
                      className="studio-icon-button disabled:opacity-30"
                      aria-label={lang === "zh" ? "下一个镜头" : "Next shot"}
                      disabled={
                        currentIndex < 0 ||
                        currentIndex >= sceneShots.length - 1
                      }
                      onClick={() =>
                        sceneShots[currentIndex + 1] &&
                        openShot(sceneShots[currentIndex + 1], true)
                      }
                    >
                      <ChevronRight size={16} />
                    </button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setReferencesOpen(true)}
                    >
                      <Images size={15} />
                      参考素材
                    </Button>
                    <button
                      onClick={closeWorkspace}
                      className="h-8 min-w-8 px-2 rounded-md hover:bg-elevated flex items-center justify-center text-muted-foreground"
                      title={tr("shots.close")}
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                <div className="shot-title-row">
                  <h2>
                    {open.title ||
                      (lang === "zh" ? "未命名镜头" : "Untitled shot")}
                  </h2>
                  <button
                    className="studio-icon-button"
                    aria-label={lang === "zh" ? "重命名镜头" : "Rename shot"}
                    onClick={() => setRenaming(true)}
                  >
                    <Pencil size={15} />
                  </button>
                </div>
                <p className="shot-context-path">
                  {[currentScript?.title || open.novel_title, open.scene_title]
                    .filter(Boolean)
                    .join(" / ")}
                </p>

                <div className="px-4 pt-2 shrink-0">
                  <Tabs
                    value={mtab}
                    onChange={(tab) => {
                      setMtab(tab);
                      const next = new URLSearchParams(search);
                      next.set("tab", tab);
                      setSearch(next, { replace: true });
                    }}
                    tabs={[
                      { key: "overview", label: tr("shot.overview") },
                      { key: "gen", label: tr("shot.gen") },
                      { key: "review", label: tr("shot.review") },
                    ]}
                  />
                </div>

                <div className="shot-creation-body">
                  {mtab === "overview" && (
                    <div className="space-y-5">
                      {open.selected_generation_id && (
                        <div className="aspect-video overflow-hidden rounded-lg border">
                          <ShotMedia projectId={projectId!} shot={open} />
                        </div>
                      )}
                      {transition.error && (
                        <p role="alert" className="text-xs text-danger">
                          {transition.error.message}
                        </p>
                      )}
                      {doCompose.error && (
                        <p role="alert" className="text-xs text-danger">
                          {doCompose.error.message}
                        </p>
                      )}
                      <section>
                        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1.5">
                          {tr("shot.desc")}
                        </div>
                        <p className="text-sm leading-6 whitespace-pre-wrap break-words text-foreground/90">
                          {open.description || tr("shot.noDesc")}
                        </p>
                      </section>

                      <section>
                        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
                          {tr("shot.transition")}
                        </div>
                        <div className="flex gap-2 flex-wrap items-center">
                          {(NEXT_STATUS[open.production_status] ?? []).map(
                            (to) => (
                              <Button
                                key={to}
                                size="sm"
                                variant="outline"
                                onClick={() =>
                                  transition.mutate({ id: open.id, to })
                                }
                              >
                                → {tr(`status.${to}`)}
                              </Button>
                            ),
                          )}
                          {(NEXT_STATUS[open.production_status] ?? [])
                            .length === 0 && (
                            <span className="text-xs text-muted-foreground">
                              {tr("shots.terminal")}
                            </span>
                          )}
                        </div>
                      </section>

                      <section>
                        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
                          {tr("shot.refs")}
                        </div>
                        <ShotAssetRefs
                          projectId={projectId!}
                          shotId={open.id}
                          onChange={() => doCompose.mutate(open.id)}
                        />
                      </section>
                      <ShotMediaReferences
                        key={open.id}
                        projectId={projectId!}
                        shotId={open.id}
                      />

                      <section>
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-xs uppercase tracking-wider text-muted-foreground">
                            {tr("shot.finalPrompt")}
                          </span>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => doCompose.mutate(open.id)}
                          >
                            {tr("shot.recompute")}
                          </Button>
                        </div>
                        <div className="p-3 rounded-md bg-bg border border-border text-sm leading-6 min-h-[60px] whitespace-pre-wrap break-words">
                          {compose ? (
                            <>
                              {compose.overridden && (
                                <div className="text-xs text-warning mb-1">
                                  {tr("shot.overridden")}
                                </div>
                              )}
                              {compose.final_prompt || tr("shot.empty")}
                            </>
                          ) : (
                            <span className="text-muted-foreground">
                              {tr("shot.computing")}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground mt-1.5">
                          {tr("shot.refsHint")}
                        </p>
                      </section>
                    </div>
                  )}

                  <div hidden={mtab !== "gen"}>
                    <GenerationPanel
                      projectId={projectId!}
                      targetType="shot"
                      targetId={open.id}
                      onChooseReferences={() => setReferencesOpen(true)}
                    />
                  </div>
                  {mtab === "review" && (
                    <ReviewPanel projectId={projectId!} shotId={open.id} />
                  )}
                </div>
              </aside>
            )}
          </div>
        </>
      )}
      {open && renaming && (
        <ShotNameDialog
          projectId={projectId!}
          shot={open}
          onClose={() => setRenaming(false)}
          onSaved={(s) =>
            setOpen((current) =>
              current?.id === s.id ? { ...current, title: s.title } : current,
            )
          }
        />
      )}
      {open && (
        <Modal
          open={referencesOpen}
          onClose={() => setReferencesOpen(false)}
          title={`参考素材 · ${open.title || "未命名镜头"}`}
          width={1040}
        >
          <div className="shot-reference-dialog">
            <Tabs
              value={referenceTab}
              onChange={setReferenceTab}
              tabs={[
                { key: "assets", label: "角色与场景" },
                { key: "video", label: "素材库" },
              ]}
            />
            <p className="text-sm text-muted-foreground my-4">
              为当前镜头选择参考，完成后继续创作。
            </p>
            {referenceTab === "assets" ? (
              <ShotAssetRefs
                key={open.id}
                projectId={projectId!}
                shotId={open.id}
                onChange={() => doCompose.mutate(open.id)}
              />
            ) : (
              <ShotMediaReferences
                key={open.id}
                projectId={projectId!}
                shotId={open.id}
                allowChoose
              />
            )}
            <div className="mt-5 flex justify-end">
              <Button onClick={() => setReferencesOpen(false)}>
                完成，继续创作
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
