import { ShotNameDialog } from "@/components/ShotNameDialog";
import { useShotContext } from "@/lib/useShotContext";
import { SelectField } from "@/components/ui/select-field";
import { SearchField } from "@/components/ui/search-field";
import { useWorkspaceScroll } from "@/lib/useWorkspaceScroll";
import { ShotMedia } from "@/components/ShotMedia";
import { Modal } from "@/components/ui/modal";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  Clapperboard,
  ChevronDown,
  Film,
  LayoutGrid,
  SlidersHorizontal,
  GripVertical,
  Loader2,
  Plus,
  Pencil,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useConfirm } from "@/components/ui/confirm";
import { useToast } from "@/components/ui/toast";
import { useBackgroundTasks } from "@/components/BackgroundTasks";
import {
  api,
  type Novel,
  type NovelDetail,
  type SceneListItem,
  type Shot,
} from "@/lib/api";
import { useI18n, type Lang } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { useElapsedSeconds } from "@/lib/useElapsedSeconds";
import { cn } from "@/lib/utils";
import "./storyboard-workspace.css";

type Opt = { v: string; zh: string; en: string };
const O = (v: string, zh: string, en: string): Opt => ({ v, zh, en });

// 分镜规格下拉字段(值为稳定 key,展示走 zh/en;后端按 key 映射成提示词)
const SELECTS: { key: string; zh: string; en: string; options: Opt[] }[] = [
  {
    key: "shot_size",
    zh: "景别",
    en: "Shot size",
    options: [
      O("extreme_long", "大远景", "Extreme long"),
      O("long", "远景", "Long"),
      O("full", "全景", "Full"),
      O("medium", "中景", "Medium"),
      O("medium_close", "中近景", "Medium close"),
      O("close_up", "特写", "Close-up"),
      O("extreme_close_up", "大特写", "Extreme close-up"),
    ],
  },
  {
    key: "camera_angle",
    zh: "机位",
    en: "Angle",
    options: [
      O("eye_level", "平视", "Eye level"),
      O("high", "俯视", "High"),
      O("low", "仰视", "Low"),
      O("birds_eye", "鸟瞰", "Bird's-eye"),
      O("over_shoulder", "过肩", "Over-shoulder"),
    ],
  },
  {
    key: "camera_move",
    zh: "运镜",
    en: "Move",
    options: [
      O("static", "固定", "Static"),
      O("push_in", "推", "Push in"),
      O("pull_out", "拉", "Pull out"),
      O("pan", "横摇", "Pan"),
      O("tilt", "纵摇", "Tilt"),
      O("tracking", "跟移", "Tracking"),
      O("crane", "升降", "Crane"),
      O("handheld", "手持", "Handheld"),
    ],
  },
  {
    key: "transition",
    zh: "转场",
    en: "Transition",
    options: [
      O("cut", "直切", "Cut"),
      O("fade_in", "淡入", "Fade in"),
      O("fade_out", "淡出", "Fade out"),
      O("dissolve", "叠化", "Dissolve"),
      O("wipe", "划像", "Wipe"),
      O("push", "推拉", "Push"),
    ],
  },
  {
    key: "aspect_ratio",
    zh: "比例",
    en: "Aspect",
    options: [
      O("16:9", "16:9", "16:9"),
      O("2.35:1", "2.35:1", "2.35:1"),
      O("4:3", "4:3", "4:3"),
      O("1:1", "1:1", "1:1"),
      O("9:16", "9:16", "9:16"),
    ],
  },
  {
    key: "pacing",
    zh: "节奏",
    en: "Pacing",
    options: [
      O("slow", "慢", "Slow"),
      O("normal", "常速", "Normal"),
      O("fast", "快", "Fast"),
    ],
  },
];
const label = (o: { zh: string; en: string }, lang: Lang) =>
  lang === "zh" ? o.zh : o.en;

export default function Storyboard() {
  const { projectId } = useParams();
  return <StoryboardContent key={projectId} />;
}

function StoryboardContent() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const context = useShotContext(projectId!);
  const [scriptFilter, setScriptFilter] = usePersistentState<string>(
    `sb.script.${projectId}`,
    "",
  );
  const [renaming, setRenaming] = useState<Shot | null>(null);
  const { t: tr, lang } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();
  const [savedNovelId, setNovelId] = usePersistentState(
    `sb.novel.${projectId}`,
    "",
  );
  const [savedChapterId, setChapterId] = usePersistentState(
    `sb.chapter.${projectId}`,
    "",
  );
  const [savedSceneId, setSceneId] = usePersistentState<string | null>(
    `sb.scene.${projectId}`,
    null,
  );
  const [view, setView] = usePersistentState<"gallery" | "editor">(
    `sb.view.${projectId}`,
    "gallery",
  );
  const [sceneSearch, setSceneSearch] = usePersistentState(
    `sb.search.${projectId}`,
    "",
  );
  const [preview, setPreview] = useState<Shot | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [directoryOpen, setDirectoryOpen] = useState(false);
  const directoryId = useId();
  const directoryToggle = useRef<HTMLButtonElement>(null);

  const novelsQuery = useQuery({
    queryKey: ["novels", projectId],
    queryFn: () => api.get<Novel[]>(`${base}/novels`),
  });
  const novels = novelsQuery.data;
  const novelId = novels?.some((n) => n.id === savedNovelId)
    ? savedNovelId
    : "";
  const novelQuery = useQuery({
    queryKey: ["novel", projectId, novelId],
    queryFn: () => api.get<NovelDetail>(`${base}/novels/${novelId}`),
    enabled: !!novelId,
  });
  const chapters = novelQuery.data?.chapters ?? [];
  const chapterId = chapters.some((c) => c.id === savedChapterId)
    ? savedChapterId
    : "";
  const scopePending =
    (!!savedNovelId && novelsQuery.isPending) ||
    (!!novelId && !!savedChapterId && novelQuery.isPending);

  const {
    data: allScenes,
    isLoading: scenesLoading,
    isError: scenesError,
    refetch: retryScenes,
  } = useQuery({
    queryKey: ["scenes-all", projectId, novelId, chapterId],
    queryFn: () => {
      const p = new URLSearchParams();
      if (novelId) p.set("novel_id", novelId);
      if (chapterId) p.set("chapter_id", chapterId);
      const q = p.toString();
      return api.get<SceneListItem[]>(`${base}/scenes${q ? `?${q}` : ""}`);
    },
    enabled: !scopePending,
  });
  const scenes = allScenes?.filter(
    (s) =>
      !scriptFilter ||
      (scriptFilter === "other"
        ? !context.data?.scenes[s.id]
        : context.data?.scenes[s.id]?.script.id === scriptFilter),
  );
  useEffect(() => {
    const requested = params.get("scene");
    if (!requested || !allScenes || !context.data) return;
    if (allScenes.some((s) => s.id === requested)) {
      setScriptFilter(context.data.scenes[requested]?.script.id || "other");
      setSceneId(requested);
      const next = new URLSearchParams(params);
      next.delete("scene");
      setParams(next, { replace: true });
    } else if (novelId || chapterId) {
      setNovelId("");
      setChapterId("");
    }
  }, [params, allScenes, context.data, novelId, chapterId]);
  const sceneId =
    !scopePending && !scenesError && (!scriptFilter || !!context.data)
      ? (scenes?.find((s) => s.id === savedSceneId)?.id ??
        scenes?.[0]?.id ??
        null)
      : null;
  useEffect(() => {
    if (
      !params.get("scene") &&
      (!scriptFilter || context.data) &&
      scenes &&
      !scopePending &&
      !scenesError &&
      savedSceneId !== sceneId
    )
      setSceneId(sceneId);
  }, [
    scenes,
    sceneId,
    savedSceneId,
    scopePending,
    scenesError,
    setSceneId,
    params,
    scriptFilter,
    context.data,
  ]);
  const scene = scenes?.find((s) => s.id === sceneId) ?? null;

  const {
    data: shots,
    isLoading: shotsLoading,
    isError: shotsError,
    refetch: retryShots,
  } = useQuery({
    queryKey: ["scene-shots", projectId, sceneId],
    queryFn: () => api.get<Shot[]>(`${base}/scenes/${sceneId}/shots`),
    enabled: !!scene,
  });

  useWorkspaceScroll(
    `storyboard.${projectId}.${sceneId}.${view}`,
    !!shots && !shotsLoading && !scopePending,
  );
  const invalidate = () => {
    void qc.invalidateQueries({
      queryKey: ["scene-shots", projectId, sceneId],
    });
    void qc.invalidateQueries({ queryKey: ["scenes-all", projectId] });
    void qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
    void qc.invalidateQueries({ queryKey: ["board", projectId] });
  };
  const patchShot = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch(`${base}/shots/${id}`, body),
    onSuccess: invalidate,
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const createShot = useMutation({
    mutationFn: () => api.post<Shot>(`${base}/scenes/${sceneId}/shots`, {}),
    onSuccess: () => {
      invalidate();
      toast.push(tr("sb.shotAdded"), "success");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const deleteShot = useMutation({
    mutationFn: (id: string) => api.del(`${base}/shots/${id}`),
    onSuccess: () => {
      invalidate();
      toast.push(tr("sb.shotDeleted"), "success");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const reorder = useMutation({
    mutationFn: (ids: string[]) =>
      api.post(`${base}/scenes/${sceneId}/shots/reorder`, { shot_ids: ids }),
    onSuccess: invalidate,
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const { task, startBreakdown } = useBackgroundTasks();
  const breaking =
    task?.kind === "breakdown" &&
    task.status === "running" &&
    task.sceneId === sceneId; // 本场景正在拆分镜
  const runBreakdown = async () => {
    if (!sceneId || !scene) return;
    const ok = await confirm({
      title: tr("sb.aiBreakdown"),
      message: tr("sb.aiBreakdownConfirm").replace(
        "{n}",
        String(shots?.length ?? 0),
      ),
      confirmText: tr("sb.aiBreakdown"),
      danger: (shots?.length ?? 0) > 0,
    });
    if (ok)
      startBreakdown({
        projectId: projectId!,
        sceneId,
        sceneName:
          scene.title || (lang === "zh" ? "未命名场景" : "Untitled scene"),
      });
  };

  const breakingSec = useElapsedSeconds(breaking);
  const sbVal = (s: Shot, k: string) => String(s.storyboard?.[k] ?? "");
  const setSb = (s: Shot, k: string, v: string | number) => {
    if (sbVal(s, k) !== String(v))
      patchShot.mutate({ id: s.id, body: { storyboard: { [k]: v } } });
  };

  const moveShot = (index: number, direction: number) => {
    if (!shots || reorder.isPending) return;
    const ids = shots.map((s) => s.id);
    const target = index + direction;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    reorder.mutate(ids);
  };
  const onDrop = (targetId: string) => {
    if (!dragId || !shots || reorder.isPending || dragId === targetId) {
      setDragId(null);
      setOverId(null);
      return;
    }
    const ids = shots.map((s) => s.id);
    const from = ids.indexOf(dragId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    reorder.mutate(ids);
    setDragId(null);
    setOverId(null);
  };

  return (
    <div className="storyboard-workspace">
      <header className="storyboard-page-header">
        <div className="storyboard-page-title">
          <Clapperboard size={21} strokeWidth={1.6} />
          <h1>{lang === "zh" ? "分镜" : "Storyboard"}</h1>
        </div>
        <Link className="storyboard-text-link" to={`${base}/shots?view=status`}>
          {lang === "zh" ? "制作进度" : "Production progress"} <span aria-hidden>↗</span>
        </Link>
      </header>

      {(novelsQuery.isError || novelQuery.isError) && (
        <p role="alert" className="mb-3 text-sm text-danger">
          {lang === "zh" ? "章节筛选加载失败，已退回可用范围。" : "Chapter filters unavailable; showing the available scope."}{" "}
          <button className="underline" onClick={() => {
            void novelsQuery.refetch();
            if (novelId) void novelQuery.refetch();
          }}>{lang === "zh" ? "重试" : "Retry"}</button>
        </p>
      )}

      <div className="storyboard-layout">
        <aside className="storyboard-scenes" aria-label={tr("sb.scenes")}>
          <button
            ref={directoryToggle}
            className="storyboard-directory-toggle"
            aria-expanded={directoryOpen}
            aria-controls={directoryId}
            onClick={() => setDirectoryOpen((value) => !value)}
          >
            <Film size={17} strokeWidth={1.5} />
            <span>{lang === "zh" ? "场景目录" : "Scenes"}</span>
            <span className="storyboard-directory-current">
              {scene?.title || tr("sb.selectScene")}
            </span>
            <ChevronDown size={16} className="storyboard-disclosure-icon" />
          </button>
          <div id={directoryId} className="storyboard-directory-content" data-open={directoryOpen}>
          <div className="storyboard-directory-heading">
            <h2>{tr("sb.scenes")}</h2>
            <span className="storyboard-count">
              {scenesLoading || scenesError || scopePending ? "—" : (scenes?.length ?? 0)}
            </span>
          </div>
          <div className="storyboard-filters">
            <SelectField
              className="w-full"
              label={lang === "zh" ? "选择剧本" : "Choose script"}
              value={scriptFilter}
              disabled={!context.data}
              options={[
                { value: "", label: lang === "zh" ? "全部剧本与小说" : "All scripts and novels" },
                ...(context.data?.scripts || []).map((s) => ({ value: s.id, label: s.title })),
                { value: "other", label: lang === "zh" ? "其他来源的场景" : "Other scene sources" },
              ]}
              onChange={(value) => {
                setScriptFilter(value);
                setNovelId("");
                setChapterId("");
                setSceneSearch("");
              }}
            />
            <details className="storyboard-source-filters">
              <summary>
                <SlidersHorizontal size={14} />
                {lang === "zh" ? "来源筛选" : "Source filters"}
                {(novelId || chapterId) && <span className="storyboard-filter-active">{lang === "zh" ? "已筛选" : "Active"}</span>}
                <ChevronDown size={14} className="storyboard-disclosure-icon" />
              </summary>
              <div className="storyboard-source-fields">
                <SelectField
                  className="w-full"
                  label={tr("shots.allNovels")}
                  value={novelId}
                  onChange={(value) => { setNovelId(value); setChapterId(""); }}
                  options={[
                    { value: "", label: tr("shots.allNovels") },
                    ...(novels || []).map((n) => ({ value: n.id, label: n.title || (lang === "zh" ? "未命名小说" : "Untitled novel") })),
                  ]}
                />
                {novelId && (
                  <SelectField
                    className="w-full"
                    label={tr("shots.allChapters")}
                    value={chapterId}
                    onChange={setChapterId}
                    options={[
                      { value: "", label: tr("shots.allChapters") },
                      ...chapters.map((c) => ({ value: c.id, label: c.title || tr("nar.chapterN").replace("{n}", String(c.ordinal)) })),
                    ]}
                  />
                )}
                {(novelId || chapterId) && (
                  <button className="storyboard-text-link" onClick={() => { setNovelId(""); setChapterId(""); }}>
                    {tr("shots.clearFilter")}
                  </button>
                )}
              </div>
            </details>
            <SearchField
              className="w-full"
              label={lang === "zh" ? "查找场景" : "Find scenes"}
              placeholder={lang === "zh" ? "查找场景" : "Find scenes"}
              value={sceneSearch}
              onValueChange={setSceneSearch}
            />
          </div>
          <div className="storyboard-scene-list">
            {scenesLoading || scopePending ? (
              <p className="storyboard-directory-empty">{tr("common.loading")}</p>
            ) : scenesError ? (
              <p role="alert" className="storyboard-directory-empty text-danger">
                {tr("common.loadFailed")}
                <button className="ml-2 underline" onClick={() => void retryScenes()}>
                  {lang === "zh" ? "重试" : "Retry"}
                </button>
              </p>
            ) : scenes && scenes.length > 0 ? (
              scenes.filter((sc) => `${sc.title} ${sc.code}`.toLowerCase().includes(sceneSearch.trim().toLowerCase())).map((sc) => (
                <button
                  key={sc.id}
                  onClick={() => {
                    setSceneId(sc.id);
                    if (window.matchMedia("(max-width: 900px)").matches) {
                      setDirectoryOpen(false);
                      directoryToggle.current?.focus();
                    }
                  }}
                  aria-pressed={sceneId === sc.id}
                  className="storyboard-scene-item"
                  title={sc.title || undefined}
                >
                  <Film size={16} strokeWidth={1.5} className="storyboard-scene-icon" />
                  <span className="storyboard-scene-copy">
                    <span className="storyboard-scene-title">
                      {sc.title || (lang === "zh" ? "未命名场景" : "Untitled scene")}
                    </span>
                    <span className="storyboard-scene-origin">
                      {context.data?.scenes[sc.id]?.script.title || sc.novel_title || (lang === "zh" ? "其他来源" : "Other source")}
                      {!context.data?.scenes[sc.id] && sc.chapter_ordinal != null && (
                        <> · {tr("nar.chapterN").replace("{n}", String(sc.chapter_ordinal))}</>
                      )}
                    </span>
                  </span>
                  <span className="storyboard-scene-count">{sc.shot_count}</span>
                </button>
              ))
            ) : (
              <p className="storyboard-directory-empty">{tr("sb.noScenes")}</p>
            )}
            {!scenesLoading && !scenesError && scenes?.length && !scenes.some((sc) =>
              `${sc.title} ${sc.code}`.toLowerCase().includes(sceneSearch.trim().toLowerCase())
            ) ? (
              <div className="storyboard-directory-empty">
                <p>{lang === "zh" ? "没有匹配场景" : "No matching scenes"}</p>
                <button className="storyboard-text-link mt-2" onClick={() => setSceneSearch("")}>
                  {lang === "zh" ? "清除搜索" : "Clear search"}
                </button>
              </div>
            ) : null}
          </div>
          </div>
        </aside>

        <section className="storyboard-sequence" aria-label={lang === "zh" ? "分镜序列" : "Shot sequence"}>
          <div className="storyboard-sequence-header">
            <div className="storyboard-scene-heading">
              <h2>{scene ? scene.title || (lang === "zh" ? "未命名场景" : "Untitled scene") : tr("sb.selectScene")}</h2>
              {scene && shots && !shotsError && (
                <p>
                  {lang === "zh"
                    ? `${shots.length} 个镜头 · ${shots.filter((s) => s.selected_generation_id).length} 个已选定画面 · ${shots.filter((s) => ["approved", "in_cut"].includes(s.production_status)).length} 个已通过审阅`
                    : `${shots.length} shots · ${shots.filter((s) => s.selected_generation_id).length} selected outputs · ${shots.filter((s) => ["approved", "in_cut"].includes(s.production_status)).length} approved`}
                </p>
              )}
            </div>
            <span role="status" aria-live="polite" className="storyboard-save-status">
              {patchShot.isPending || reorder.isPending
                ? lang === "zh" ? "保存中…" : "Saving…"
                : patchShot.isSuccess || reorder.isSuccess
                  ? lang === "zh" ? "已保存" : "Saved"
                  : ""}
            </span>
          </div>

          <div className="storyboard-sequence-toolbar">
            <div className="storyboard-view-switch" role="group" aria-label={lang === "zh" ? "分镜视图" : "Storyboard view"}>
              {(["gallery", "editor"] as const).map((v) => (
                <button key={v} aria-pressed={view === v} onClick={() => setView(v)}>
                  {v === "gallery" ? <LayoutGrid size={15} /> : <Pencil size={15} />}
                  {v === "gallery" ? lang === "zh" ? "浏览" : "Browse" : lang === "zh" ? "编辑" : "Edit"}
                </button>
              ))}
            </div>
            {scene && (
              <div className="storyboard-scene-actions">
                <Button size="sm" variant="ghost" disabled={breaking || shotsLoading || shotsError} onClick={runBreakdown}>
                  {breaking ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
                  {breaking ? `${tr("sb.breakingDown")} ${breakingSec}s` : tr("sb.aiBreakdown")}
                </Button>
                <Button size="sm" variant="outline" disabled={createShot.isPending} onClick={() => createShot.mutate()}>
                  {createShot.isPending ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
                  {tr("sb.addShot")}
                </Button>
              </div>
            )}
          </div>

          {breaking && (
            <div className="mb-4 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-4 py-3 text-sm text-primary">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              <span>{tr("sb.breakingDownBanner").replace("{n}", String(breakingSec))}</span>
            </div>
          )}
          {shotsError ? (
            <div role="alert" className="studio-empty">
              {tr("common.loadFailed")}
              <Button variant="outline" size="sm" onClick={() => void retryShots()}>{lang === "zh" ? "重试" : "Retry"}</Button>
            </div>
          ) : shotsLoading || scopePending || scenesLoading ? (
            <p className="studio-empty">{tr("common.loading")}</p>
          ) : !scene ? (
            <div className="studio-empty">{tr("sb.selectScene")}</div>
          ) : !shots || shots.length === 0 ? (
            <div className="studio-empty">
              <Clapperboard size={30} strokeWidth={1.3} />
              {tr("sb.noShots")}
              <div className="flex flex-wrap justify-center gap-2">
                <Button size="sm" disabled={breaking} onClick={runBreakdown}>
                  {breaking ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
                  {breaking ? `${tr("sb.breakingDown")} ${breakingSec}s` : tr("sb.aiBreakdown")}
                </Button>
                <Button size="sm" variant="outline" disabled={createShot.isPending} onClick={() => createShot.mutate()}>
                  <Plus size={16} /> {tr("sb.addShot")}
                </Button>
              </div>
            </div>
          ) : view === "gallery" ? (
            <div className="storyboard-gallery">
              {shots.map((s, i) => (
                <article key={s.id} className="storyboard-gallery-shot">
                  <div className="storyboard-gallery-media">
                    <StoryboardFrame projectId={projectId!} shot={s} />
                    <span className="storyboard-frame-number">{String(i + 1).padStart(2, "0")}</span>
                  </div>
                  <div className="storyboard-gallery-title">
                    <h3>{s.title || (lang === "zh" ? "未命名镜头" : "Untitled shot")}</h3>
                    <button className="storyboard-icon-button" aria-label={`${lang === "zh" ? "重命名镜头" : "Rename shot"} ${s.title || ""}`} onClick={() => setRenaming(s)}>
                      <Pencil size={14} />
                    </button>
                  </div>
                  <p className="storyboard-gallery-description">
                    {s.description || (lang === "zh" ? "尚未填写画面描述" : "No visual description yet")}
                  </p>
                  <div className="storyboard-shot-specs">
                    <span>{sbVal(s, "duration_sec") ? `${sbVal(s, "duration_sec")}s` : lang === "zh" ? "时长未定" : "No duration"}</span>
                    {SELECTS.slice(0, 3).map((f) => {
                      const o = f.options.find((o) => o.v === sbVal(s, f.key));
                      return o ? <span key={f.key}>{label(o, lang)}</span> : null;
                    })}
                  </div>
                  <div className="storyboard-gallery-actions">
                    <button className="storyboard-text-link" onClick={() => setPreview(s)}>
                      {s.selected_generation_id
                        ? lang === "zh" ? "快速预览" : "Quick preview"
                        : lang === "zh" ? "镜头详情" : "Shot details"}
                    </button>
                    <Link className="storyboard-text-link" to={`${base}/shots?shot=${s.id}&from=storyboard&tab=gen`}>
                      {s.selected_generation_id
                        ? lang === "zh" ? "生成与引用" : "Generate & references"
                        : lang === "zh" ? "引用与审阅" : "References & review"} <span aria-hidden>↗</span>
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="storyboard-editor-list">
              {shots.map((s, i) => (
                <article
                  key={s.id}
                  onDragOver={(e) => { e.preventDefault(); if (overId !== s.id) setOverId(s.id); }}
                  onDrop={() => onDrop(s.id)}
                  className={cn(
                    "storyboard-shot-editor",
                    dragId === s.id && "is-dragging",
                    overId === s.id && dragId && dragId !== s.id && "is-drop-target",
                  )}
                >
                  <div className="storyboard-shot-header">
                    <span
                      draggable={!reorder.isPending}
                      onDragStart={() => setDragId(s.id)}
                      onDragEnd={() => { setDragId(null); setOverId(null); }}
                      className="storyboard-drag-handle"
                      title={lang === "zh" ? "拖动排序，或使用右侧按钮" : "Drag to reorder, or use the arrow buttons"}
                    ><GripVertical size={16} /></span>
                    <span className="storyboard-shot-number" aria-label={lang === "zh" ? `第 ${i + 1} 个镜头` : `Shot ${i + 1}`}>{String(i + 1).padStart(2, "0")}</span>
                    <Input
                      aria-label={tr("sb.shotTitle")}
                      maxLength={255}
                      defaultValue={s.title ?? ""}
                      onBlur={(e) => {
                        if (e.target.value.trim() && e.target.value.trim() !== (s.title ?? ""))
                          patchShot.mutate({ id: s.id, body: { title: e.target.value.trim() } });
                      }}
                      placeholder={tr("sb.shotTitle")}
                      className="storyboard-title-input"
                    />
                    <div className="storyboard-shot-order">
                      <button
                        disabled={i === 0 || reorder.isPending}
                        onClick={() => moveShot(i, -1)}
                        aria-label={lang === "zh" ? "上移镜头" : "Move shot up"}
                        title={lang === "zh" ? "上移镜头" : "Move shot up"}
                        className="storyboard-icon-button"
                      ><ArrowUp size={15} /></button>
                      <button
                        disabled={i === shots.length - 1 || reorder.isPending}
                        onClick={() => moveShot(i, 1)}
                        aria-label={lang === "zh" ? "下移镜头" : "Move shot down"}
                        title={lang === "zh" ? "下移镜头" : "Move shot down"}
                        className="storyboard-icon-button"
                      ><ArrowDown size={15} /></button>
                      <button
                        disabled={deleteShot.isPending}
                        onClick={async () => {
                          if (await confirm({
                            title: tr("sb.deleteShot"), message: tr("sb.deleteShotConfirm"),
                            confirmText: tr("common.delete"), danger: true,
                          })) deleteShot.mutate(s.id);
                        }}
                        aria-label={tr("sb.deleteShot")}
                        title={tr("sb.deleteShot")}
                        className="storyboard-icon-button storyboard-delete"
                      ><Trash2 size={15} /></button>
                    </div>
                  </div>
                  <div className="storyboard-shot-body">
                    <div className="storyboard-shot-visual">
                      <div className="storyboard-editor-media"><StoryboardFrame projectId={projectId!} shot={s} /></div>
                      <div className="storyboard-media-footer">
                        <label className="storyboard-duration">
                          <span>{lang === "zh" ? "时长" : "Duration"}</span>
                          <input
                            aria-label={tr("sb.duration")}
                            type="number" min={0} step={0.5}
                            defaultValue={sbVal(s, "duration_sec")}
                            onBlur={(e) => {
                              if (e.target.value !== sbVal(s, "duration_sec"))
                                setSb(s, "duration_sec", e.target.value ? Number(e.target.value) : 0);
                            }}
                            placeholder="—"
                          />
                          <span>{tr("sb.sec")}</span>
                        </label>
                        <button className="storyboard-text-link" onClick={() => setPreview(s)}>
                          {s.selected_generation_id
                            ? lang === "zh" ? "预览" : "Preview"
                            : lang === "zh" ? "详情" : "Details"}
                        </button>
                      </div>
                      <Link className="storyboard-text-link storyboard-generation-link" to={`${base}/shots?shot=${s.id}&from=storyboard&tab=gen`}>
                        {s.selected_generation_id
                          ? lang === "zh" ? "生成画面 · 引用与审阅" : "Generate · references & review"
                          : lang === "zh" ? "引用与审阅" : "References & review"} <span aria-hidden>↗</span>
                      </Link>
                    </div>
                    <label className="storyboard-description-field">
                      <span className="storyboard-field-label">{tr("sb.visual")}</span>
                      <Textarea
                        defaultValue={s.description}
                        onBlur={(e) => {
                          if (e.target.value !== s.description)
                            patchShot.mutate({ id: s.id, body: { description: e.target.value } });
                        }}
                        aria-label={tr("sb.visual")}
                        placeholder={lang === "zh" ? "描述这个镜头里发生的事…" : "Describe what happens in this shot…"}
                        rows={5}
                        className="storyboard-description-input"
                      />
                    </label>
                  </div>

                  <details className="storyboard-shot-details">
                    <summary>
                      <SlidersHorizontal size={15} />
                      <span>{lang === "zh" ? "镜头参数与声音" : "Camera & sound"}</span>
                      <span className="storyboard-parameter-summary">
                        {SELECTS.slice(0, 3).map((f) => {
                          const option = f.options.find((o) => o.v === sbVal(s, f.key));
                          return option ? label(option, lang) : null;
                        }).filter(Boolean).join(" · ")}
                      </span>
                      <ChevronDown size={15} className="storyboard-disclosure-icon" />
                    </summary>
                    <div className="storyboard-details-content">
                      <div className="storyboard-spec-fields">
                        {SELECTS.map((f) => (
                          <label key={f.key} className="storyboard-labelled-field">
                            <span className="storyboard-field-label">{label(f, lang)}</span>
                            <SelectField
                              className="w-full"
                              label={label(f, lang)}
                              value={String(sbVal(s, f.key))}
                              onChange={(value) => setSb(s, f.key, value)}
                              options={[
                                { value: "", label: lang === "zh" ? "未设置" : "Not set" },
                                ...f.options.map((o) => ({ value: o.v, label: label(o, lang) })),
                              ]}
                            />
                          </label>
                        ))}
                      </div>
                      <div className="storyboard-sound-fields">
                        <label className="storyboard-labelled-field storyboard-dialogue-field">
                          <span className="storyboard-field-label">{tr("sb.dialogue")}</span>
                          <Textarea
                            defaultValue={sbVal(s, "dialogue")}
                            onBlur={(e) => setSb(s, "dialogue", e.target.value)}
                            aria-label={tr("sb.dialogue")}
                            placeholder={lang === "zh" ? "人物对白或旁白…" : "Dialogue or voiceover…"}
                            rows={3}
                          />
                        </label>
                        <label className="storyboard-labelled-field">
                          <span className="storyboard-field-label">{tr("sb.audio")}</span>
                          <Input
                            defaultValue={sbVal(s, "audio")}
                            onBlur={(e) => setSb(s, "audio", e.target.value)}
                            aria-label={tr("sb.audio")}
                            placeholder={lang === "zh" ? "音乐、环境声或音效" : "Music, ambience or effects"}
                          />
                        </label>
                        <label className="storyboard-labelled-field">
                          <span className="storyboard-field-label">{tr("sb.notes")}</span>
                          <Input
                            defaultValue={sbVal(s, "notes")}
                            onBlur={(e) => setSb(s, "notes", e.target.value)}
                            aria-label={tr("sb.notes")}
                            placeholder={lang === "zh" ? "拍摄提示或补充说明" : "Direction or additional notes"}
                          />
                        </label>
                      </div>
                    </div>
                  </details>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
      {renaming && (
        <ShotNameDialog
          projectId={projectId!}
          shot={renaming}
          onClose={() => setRenaming(null)}
        />
      )}
      {preview && (
        <Modal
          open
          title={
            preview.title || (lang === "zh" ? "未命名镜头" : "Untitled shot")
          }
          onClose={() => setPreview(null)}
          width={960}
        >
          <div className={cn("grid gap-5", preview.selected_generation_id && "md:grid-cols-[1.3fr_1fr]")}>
            {preview.selected_generation_id && (
              <div className="aspect-video overflow-hidden rounded-lg border">
                <ShotMedia projectId={projectId!} shot={preview} />
              </div>
            )}
            <div className="min-w-0 space-y-3">
              <p className="text-xs text-muted-foreground">
                {scene?.title} · {tr(`status.${preview.production_status}`)}
              </p>
              <p className="whitespace-pre-wrap break-words text-sm leading-7">
                {preview.description}
              </p>
              {[
                ["dialogue", tr("sb.dialogue")],
                ["audio", tr("sb.audio")],
                ["notes", tr("sb.notes")],
              ].map(([k, title]) =>
                sbVal(preview, k) ? (
                  <p key={k} className="break-words text-xs leading-6">
                    <strong>{title}：</strong>
                    {sbVal(preview, k)}
                  </p>
                ) : null,
              )}
              <Link
                className="studio-link inline-block text-sm"
                to={`${base}/shots?shot=${preview.id}&from=storyboard&tab=gen`}
              >
                {lang === "zh"
                  ? "打开镜头 · 生成、引用与审阅"
                  : "Open shot · generate, reference & review"}{" "}
                →
              </Link>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function StoryboardFrame({ projectId, shot }: { projectId: string; shot: Shot }) {
  const { lang } = useI18n();
  if (shot.selected_generation_id) return <ShotMedia projectId={projectId} shot={shot} />;
  return (
    <Link
      to={`/projects/${projectId}/shots?shot=${shot.id}&from=storyboard&tab=gen`}
      className="storyboard-empty-frame"
      aria-label={`${lang === "zh" ? "生成或选择画面" : "Generate or select a frame"} · ${shot.title || shot.code}`}
    >
      <Film size={23} strokeWidth={1.4} aria-hidden="true" />
      <span>{lang === "zh" ? "尚未选定画面" : "No selected output"}</span>
      <strong>
        {lang === "zh" ? "生成 / 选择画面" : "Generate / select a frame"}
        <ArrowUpRight size={15} aria-hidden="true" />
      </strong>
    </Link>
  );
}
