import { useWorkspaceScroll } from "@/lib/useWorkspaceScroll";
import { ShotMedia } from "@/components/ShotMedia";
import { Modal } from "@/components/ui/modal";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Clapperboard,
  Filter,
  GripVertical,
  Loader2,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
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
    data: scenes,
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
  const sceneId =
    !scopePending && !scenesError
      ? (scenes?.find((s) => s.id === savedSceneId)?.id ??
        scenes?.[0]?.id ??
        null)
      : null;
  useEffect(() => {
    if (scenes && !scopePending && !scenesError && savedSceneId !== sceneId)
      setSceneId(sceneId);
  }, [scenes, sceneId, savedSceneId, scopePending, scenesError, setSceneId]);
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
        sceneName: scene.title || scene.code,
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
    <div className="flex min-h-full flex-col p-4 md:p-7 lg:p-8">
      <div className="page-heading mb-6">
        <div className="flex items-center gap-2">
          <Clapperboard className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-xl font-semibold leading-tight">
              {tr("sb.title")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {lang === "zh"
                ? "先看画面与节奏，再细化镜头、对白和声音。"
                : "Review the visual sequence, then refine shots, dialogue and sound."}
            </p>
          </div>
        </div>
      </div>

      {(novelsQuery.isError || novelQuery.isError) && (
        <p role="alert" className="mb-3 text-xs text-danger">
          {lang === "zh"
            ? "章节筛选加载失败，已退回可用范围。"
            : "Chapter filters unavailable; showing the available scope."}{" "}
          <button
            className="underline"
            onClick={() => {
              void novelsQuery.refetch();
              if (novelId) void novelQuery.refetch();
            }}
          >
            {lang === "zh" ? "重试" : "Retry"}
          </button>
        </p>
      )}

      {/* 筛选 */}
      <div className="mb-5 flex shrink-0 flex-wrap items-center gap-2 border-b pb-4">
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <Filter className="h-3.5 w-3.5" /> {tr("shots.filter")}
        </span>
        <select
          aria-label={tr("shots.allNovels")}
          value={novelId}
          onChange={(e) => {
            setNovelId(e.target.value);
            setChapterId("");
          }}
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
        >
          <option value="">{tr("shots.allNovels")}</option>
          {novels?.map((n) => (
            <option key={n.id} value={n.id}>
              {n.title || n.code}
            </option>
          ))}
        </select>
        <select
          aria-label={tr("shots.allChapters")}
          value={chapterId}
          onChange={(e) => setChapterId(e.target.value)}
          disabled={!novelId}
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm disabled:opacity-50"
        >
          <option value="">{tr("shots.allChapters")}</option>
          {chapters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title ?? tr("nar.chapterN").replace("{n}", String(c.ordinal))}
            </option>
          ))}
        </select>
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

      <div className="grid min-h-0 flex-1 grid-cols-1 items-start gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
        {/* 左:场景列表 */}
        <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-surface lg:sticky lg:top-4">
          <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border px-3 text-sm font-medium">
            <Clapperboard className="h-4 w-4 text-muted-foreground" />
            <span className="flex-1">{tr("sb.scenes")}</span>
            <span className="text-xs text-faint">
              {scenesLoading || scenesError || scopePending
                ? "—"
                : (scenes?.length ?? 0)}
            </span>
          </div>
          <div className="p-2">
            <Input
              aria-label={lang === "zh" ? "查找场景" : "Find scenes"}
              placeholder={lang === "zh" ? "查找场景…" : "Find scenes…"}
              value={sceneSearch}
              onChange={(e) => setSceneSearch(e.target.value)}
              className="h-8 text-xs"
            />
          </div>
          <div className="max-h-52 lg:max-h-[65vh] min-h-0 flex-1 space-y-1 overflow-auto p-2">
            {scenesLoading || scopePending ? (
              <p className="p-4 text-xs text-muted-foreground">
                {tr("common.loading")}
              </p>
            ) : scenesError ? (
              <p role="alert" className="p-4 text-xs text-danger">
                {tr("common.loadFailed")}
                <button
                  className="ml-2 underline"
                  onClick={() => void retryScenes()}
                >
                  {lang === "zh" ? "重试" : "Retry"}
                </button>
              </p>
            ) : scenes && scenes.length > 0 ? (
              scenes
                .filter((sc) =>
                  `${sc.title} ${sc.code}`
                    .toLowerCase()
                    .includes(sceneSearch.trim().toLowerCase()),
                )
                .map((sc) => (
                  <button
                    key={sc.id}
                    onClick={() => setSceneId(sc.id)}
                    aria-pressed={sceneId === sc.id}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors",
                      sceneId === sc.id
                        ? "bg-primary/15 text-primary"
                        : "hover:bg-elevated text-foreground/90",
                    )}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {sc.title || sc.code}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-xs text-faint">
                        <span className="font-code">{sc.code}</span>
                        {sc.chapter_ordinal != null && (
                          <span>
                            ·{" "}
                            {tr("nar.chapterN").replace(
                              "{n}",
                              String(sc.chapter_ordinal),
                            )}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 font-code text-xs text-muted-foreground">
                      {sc.shot_count}
                    </span>
                  </button>
                ))
            ) : (
              <div className="py-10 text-center text-xs text-muted-foreground">
                {tr("sb.noScenes")}
              </div>
            )}
          </div>
          {!scenesLoading &&
          !scenesError &&
          scenes?.length &&
          !scenes.some((sc) =>
            `${sc.title} ${sc.code}`
              .toLowerCase()
              .includes(sceneSearch.trim().toLowerCase()),
          ) ? (
            <p className="p-3 text-xs text-muted-foreground">
              {lang === "zh" ? "没有匹配场景" : "No matching scenes"}
            </p>
          ) : null}
        </div>

        {/* 右:分镜序列 */}
        <div className="flex min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-card">
          <div className="flex min-h-14 flex-wrap shrink-0 items-center gap-2 border-b border-border p-3 text-sm font-medium">
            <span className="flex-1 truncate">
              {scene ? scene.title || scene.code : tr("sb.selectScene")}
            </span>
            <span
              role="status"
              aria-live="polite"
              className="text-xs text-muted-foreground"
            >
              {patchShot.isPending || reorder.isPending
                ? lang === "zh"
                  ? "保存中…"
                  : "Saving…"
                : patchShot.isSuccess || reorder.isSuccess
                  ? lang === "zh"
                    ? "已保存"
                    : "Saved"
                  : ""}
            </span>
            <div className="flex rounded-md border p-0.5">
              {(["gallery", "editor"] as const).map((v) => (
                <button
                  key={v}
                  aria-pressed={view === v}
                  className={`rounded px-2 py-1 text-xs ${view === v ? "bg-primary/15 text-primary" : "text-muted-foreground"}`}
                  onClick={() => setView(v)}
                >
                  {v === "gallery"
                    ? lang === "zh"
                      ? "画面浏览"
                      : "Gallery"
                    : lang === "zh"
                      ? "分镜编辑"
                      : "Edit shots"}
                </button>
              ))}
            </div>
            {scene && (
              <>
                <Button
                  size="sm"
                  disabled={breaking || shotsLoading || shotsError}
                  onClick={runBreakdown}
                >
                  {breaking ? (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  ) : (
                    <Sparkles className="mr-1 h-4 w-4" />
                  )}
                  {breaking
                    ? `${tr("sb.breakingDown")} ${breakingSec}s`
                    : tr("sb.aiBreakdown")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={createShot.isPending}
                  onClick={() => createShot.mutate()}
                >
                  <Plus className="h-4 w-4 mr-1" /> {tr("sb.addShot")}
                </Button>
              </>
            )}
          </div>
          <div className="min-h-40 flex-1 p-3 md:p-4">
            {scene && shots && !shotsError && (
              <p className="mb-3 text-xs text-muted-foreground">
                {lang === "zh"
                  ? `本场景 ${shots.length} 个镜头 · ${shots.filter((s) => s.selected_generation_id).length} 个已选定画面 · ${shots.filter((s) => ["approved", "in_cut"].includes(s.production_status)).length} 个已审阅通过`
                  : `${shots.length} shots · ${shots.filter((s) => s.selected_generation_id).length} selected outputs · ${shots.filter((s) => ["approved", "in_cut"].includes(s.production_status)).length} approved`}
              </p>
            )}
            {breaking && (
              <div className="mb-3 flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-2.5 text-sm text-primary">
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                <span>
                  {tr("sb.breakingDownBanner").replace(
                    "{n}",
                    String(breakingSec),
                  )}
                </span>
              </div>
            )}
            {shotsError ? (
              <p role="alert" className="studio-empty">
                {tr("common.loadFailed")}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void retryShots()}
                >
                  {lang === "zh" ? "重试" : "Retry"}
                </Button>
              </p>
            ) : shotsLoading || scopePending || scenesLoading ? (
              <p className="studio-empty">{tr("common.loading")}</p>
            ) : !scene ? (
              <div className="studio-empty">{tr("sb.selectScene")}</div>
            ) : !shots || shots.length === 0 ? (
              <div className="studio-empty">
                {tr("sb.noShots")}
                <div className="flex gap-2">
                  <Button size="sm" disabled={breaking} onClick={runBreakdown}>
                    {breaking ? (
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    ) : (
                      <Sparkles className="mr-1 h-4 w-4" />
                    )}
                    {breaking
                      ? `${tr("sb.breakingDown")} ${breakingSec}s`
                      : tr("sb.aiBreakdown")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={createShot.isPending}
                    onClick={() => createShot.mutate()}
                  >
                    <Plus className="h-4 w-4 mr-1" /> {tr("sb.addShot")}
                  </Button>
                </div>
              </div>
            ) : view === "gallery" ? (
              <div className="grid grid-cols-1 gap-4 min-[600px]:grid-cols-2 2xl:grid-cols-3">
                {shots.map((s, i) => (
                  <article
                    key={s.id}
                    className="min-w-0 overflow-hidden rounded-lg border bg-surface"
                  >
                    <div className="relative aspect-video overflow-hidden">
                      <ShotMedia projectId={projectId!} shot={s} />
                    </div>
                    <div className="space-y-3 p-3">
                      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                        <span className="font-code">
                          {String(i + 1).padStart(2, "0")} · {s.code}
                        </span>
                        <span>
                          {sbVal(s, "duration_sec")
                            ? `${sbVal(s, "duration_sec")}s`
                            : lang === "zh"
                              ? "时长未定"
                              : "No duration"}
                        </span>
                      </div>
                      <h3 className="truncate text-sm font-medium">
                        {s.title || s.code}
                      </h3>
                      <p className="line-clamp-2 min-h-10 break-words text-xs leading-5 text-muted-foreground">
                        {s.description ||
                          (lang === "zh"
                            ? "尚未填写画面描述"
                            : "No visual description yet")}
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {SELECTS.slice(0, 3).map((f) => {
                          const o = f.options.find(
                            (o) => o.v === sbVal(s, f.key),
                          );
                          return o ? (
                            <span
                              key={f.key}
                              className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground"
                            >
                              {label(o, lang)}
                            </span>
                          ) : null;
                        })}
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs">
                        <button
                          className="studio-link"
                          onClick={() => setPreview(s)}
                        >
                          {lang === "zh" ? "快速预览" : "Quick preview"}
                        </button>
                        <Link
                          className="studio-link"
                          to={`${base}/shots?shot=${s.id}&from=storyboard&tab=gen`}
                        >
                          {lang === "zh"
                            ? "生成与引用"
                            : "Generate & references"}{" "}
                          →
                        </Link>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                {shots.map((s, i) => (
                  <div
                    key={s.id}
                    onDragOver={(e) => {
                      e.preventDefault();
                      if (overId !== s.id) setOverId(s.id);
                    }}
                    onDrop={() => onDrop(s.id)}
                    className={cn(
                      "rounded-lg border bg-surface p-3 md:p-4 transition-colors",
                      dragId === s.id ? "opacity-40" : "",
                      overId === s.id && dragId && dragId !== s.id
                        ? "border-primary ring-1 ring-primary"
                        : "border-border",
                    )}
                  >
                    <div className="flex flex-col items-stretch gap-4 xl:flex-row xl:items-start">
                      <div className="w-full shrink-0 space-y-2 xl:w-48">
                        <div className="aspect-video overflow-hidden rounded-md border">
                          <ShotMedia projectId={projectId!} shot={s} />
                        </div>
                        <div className="flex items-center gap-1 text-muted-foreground">
                          <span
                            draggable
                            onDragStart={() => setDragId(s.id)}
                            onDragEnd={() => {
                              setDragId(null);
                              setOverId(null);
                            }}
                            title={
                              lang === "zh"
                                ? "拖动排序，或使用右侧按钮"
                                : "Drag to reorder, or use the arrow buttons"
                            }
                          >
                            <GripVertical className="h-4 w-4 cursor-grab" />
                          </span>
                          <span className="flex-1 font-code text-xs">
                            {String(i + 1).padStart(2, "0")} · {s.code}
                          </span>
                          <button
                            disabled={i === 0 || reorder.isPending}
                            onClick={() => moveShot(i, -1)}
                            title={lang === "zh" ? "上移镜头" : "Move shot up"}
                            className="grid h-8 w-8 place-items-center rounded-md hover:bg-elevated disabled:opacity-30"
                          >
                            <ArrowUp className="h-3.5 w-3.5" />
                          </button>
                          <button
                            disabled={
                              i === shots.length - 1 || reorder.isPending
                            }
                            onClick={() => moveShot(i, 1)}
                            title={
                              lang === "zh" ? "下移镜头" : "Move shot down"
                            }
                            className="grid h-8 w-8 place-items-center rounded-md hover:bg-elevated disabled:opacity-30"
                          >
                            <ArrowDown className="h-3.5 w-3.5" />
                          </button>
                        </div>
                        <Link
                          to={`${base}/shots?shot=${s.id}&from=storyboard&tab=gen`}
                          className="block text-xs text-muted-foreground hover:text-primary"
                        >
                          {lang === "zh"
                            ? "生成、资产引用与审核"
                            : "Generation, references & review"}{" "}
                          →
                        </Link>
                      </div>

                      <div className="min-w-0 flex-1 space-y-2">
                        <div className="flex items-center gap-2">
                          <Input
                            aria-label={tr("sb.shotTitle")}
                            defaultValue={s.title ?? ""}
                            onBlur={(e) => {
                              if (e.target.value !== (s.title ?? ""))
                                patchShot.mutate({
                                  id: s.id,
                                  body: { title: e.target.value },
                                });
                            }}
                            placeholder={tr("sb.shotTitle")}
                            className="h-8 flex-1"
                          />
                          <input
                            aria-label={tr("sb.duration")}
                            type="number"
                            min={0}
                            step={0.5}
                            defaultValue={sbVal(s, "duration_sec")}
                            onBlur={(e) => {
                              if (e.target.value !== sbVal(s, "duration_sec"))
                                setSb(
                                  s,
                                  "duration_sec",
                                  e.target.value ? Number(e.target.value) : 0,
                                );
                            }}
                            placeholder={tr("sb.duration")}
                            className="h-8 w-20 rounded-md border border-border bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                          />
                          <span className="text-xs text-faint">
                            {tr("sb.sec")}
                          </span>
                          <button
                            onClick={async () => {
                              if (
                                await confirm({
                                  title: tr("sb.deleteShot"),
                                  message: tr("sb.deleteShotConfirm"),
                                  confirmText: tr("common.delete"),
                                  danger: true,
                                })
                              )
                                deleteShot.mutate(s.id);
                            }}
                            title={tr("sb.deleteShot")}
                            className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-faint transition hover:bg-danger/15 hover:text-danger"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>

                        {/* 分镜规格下拉 */}
                        <div className="flex flex-wrap gap-1.5">
                          {SELECTS.map((f) => (
                            <select
                              key={f.key}
                              value={sbVal(s, f.key)}
                              onChange={(e) => setSb(s, f.key, e.target.value)}
                              title={label(f, lang)}
                              className="h-7 rounded-md border border-border bg-bg px-1.5 text-xs"
                            >
                              <option value="">{label(f, lang)}</option>
                              {f.options.map((o) => (
                                <option key={o.v} value={o.v}>
                                  {label(o, lang)}
                                </option>
                              ))}
                            </select>
                          ))}
                        </div>

                        {/* 文本字段 */}
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                          <Textarea
                            defaultValue={s.description}
                            onBlur={(e) => {
                              if (e.target.value !== s.description)
                                patchShot.mutate({
                                  id: s.id,
                                  body: { description: e.target.value },
                                });
                            }}
                            aria-label={tr("sb.visual")}
                            placeholder={tr("sb.visual")}
                            rows={2}
                            className="text-xs"
                          />
                          <Textarea
                            defaultValue={sbVal(s, "dialogue")}
                            onBlur={(e) => setSb(s, "dialogue", e.target.value)}
                            aria-label={tr("sb.dialogue")}
                            placeholder={tr("sb.dialogue")}
                            rows={2}
                            className="text-xs"
                          />
                          <Input
                            defaultValue={sbVal(s, "audio")}
                            onBlur={(e) => setSb(s, "audio", e.target.value)}
                            aria-label={tr("sb.audio")}
                            placeholder={tr("sb.audio")}
                            className="h-8 text-xs"
                          />
                          <Input
                            defaultValue={sbVal(s, "notes")}
                            onBlur={(e) => setSb(s, "notes", e.target.value)}
                            aria-label={tr("sb.notes")}
                            placeholder={tr("sb.notes")}
                            className="h-8 text-xs"
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      {preview && (
        <Modal
          open
          title={preview.title || preview.code}
          onClose={() => setPreview(null)}
          width={960}
        >
          <div className="grid gap-5 md:grid-cols-[1.3fr_1fr]">
            <div className="aspect-video overflow-hidden rounded-lg border">
              <ShotMedia projectId={projectId!} shot={preview} />
            </div>
            <div className="min-w-0 space-y-3">
              <p className="text-xs text-muted-foreground">
                {preview.code} · {tr(`status.${preview.production_status}`)}
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
