import { MediaImage } from "@/components/MediaImage";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Clapperboard, Film, Images, ScrollText } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Panel } from "@/components/ui/panel";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ShotMedia } from "@/components/ShotMedia";
import { usePersistentState } from "@/lib/usePersistentState";
import {
  api,
  blobUrl,
  type Asset,
  type NovelDetail,
  type Novel,
  type Project,
  type Quota,
  type Shot,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function Workbench() {
  const { projectId } = useParams();
  const { t, lang } = useI18n();
  const zh = lang === "zh";
  const base = `/projects/${projectId}`;
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const project = projects?.find((p) => p.id === projectId);
  const { data: novels } = useQuery({
    queryKey: ["novels", projectId],
    queryFn: () => api.get<Novel[]>(`${base}/novels`),
  });
  const [novelId, setNovelId] = usePersistentState<string | null>(`wb.novel.${projectId}`, null);
  const [chapterId, setChapterId] = usePersistentState(`wb.chapter.${projectId}`, "");
  const effNovelId = novels?.find((n) => n.id === novelId)?.id ?? novels?.[0]?.id;
  const { data: detail } = useQuery({
    queryKey: ["novel", projectId, effNovelId],
    queryFn: () => api.get<NovelDetail>(`${base}/novels/${effNovelId}`),
    enabled: !!effNovelId,
  });
  const chapters = detail?.chapters ?? [];
  const chapter = chapters.find((c) => c.id === chapterId) ?? chapters[0];
  const {
    data: assets,
    isLoading: assetsLoading,
    isError: assetsError,
  } = useQuery({
    queryKey: ["assets", projectId, "workbench", chapterId],
    queryFn: () => api.get<Asset[]>(`${base}/assets${chapterId ? `?chapter_id=${chapterId}` : ""}`),
  });
  const {
    data: shots,
    isLoading: shotsLoading,
    isError: shotsError,
  } = useQuery({
    queryKey: ["all-shots", projectId, "workbench", chapterId],
    queryFn: () => api.get<Shot[]>(`${base}/shots${chapterId ? `?chapter_id=${chapterId}` : ""}`),
  });
  const { data: quota, isLoading: quotaLoading, isError: quotaError } = useQuery({
    queryKey: ["quota", projectId],
    queryFn: () => api.get<Quota | null>(`${base}/quota`),
  });
  const approved = shots?.filter((s) => ["approved", "in_cut"].includes(s.production_status)).length ?? 0;
  const progress = shots?.length ? Math.round((approved / shots.length) * 100) : 0;
  const usedPct =
    quota && quota.limit_cost > 0
      ? Math.min(100, Math.round((quota.used_cost / quota.limit_cost) * 100))
      : null;
  const characters = assets?.filter((a) => a.type === "character") ?? [];
  const locations = assets?.filter((a) => a.type === "location") ?? [];
  const next = !chapters.length ? "narrative" : !assets?.length ? "assets" : "storyboard";
  const nextLabel =
    next === "narrative"
      ? zh
        ? "从一个故事开始"
        : "Start with a story"
      : next === "assets"
        ? zh
          ? "让故事中的角色与场景成形"
          : "Develop your characters and locations"
        : zh
          ? "继续打磨下一个镜头"
          : "Shape your next shot";
  const viewAll = (to: string) => (
    <Link
      to={`${base}/${to}`}
      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
    >
      {zh ? "查看全部" : "View all"}
      <ArrowRight className="h-3.5 w-3.5" />
    </Link>
  );

  return (
    <div className="studio-page">
      <header className="page-heading">
        <div>
          <p className="!mt-0 !mb-1 text-xs text-faint">INSPIRATION / {zh ? "创作工作台" : "WORKSPACE"}</p>
          <h1>{project?.name ?? t("wb.title")}</h1>
          <p>
            {zh ? "从故事到画面，专注每一步创作。" : "From story to screen, one thoughtful step at a time."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {novels && novels.length > 1 && (
            <select
              aria-label={t("wb.novel")}
              className="h-9 rounded-md border bg-card px-3 text-sm"
              value={effNovelId ?? ""}
              onChange={(e) => {
                setNovelId(e.target.value);
                setChapterId("");
              }}
            >
              {novels.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.title}
                </option>
              ))}
            </select>
          )}
          {!!chapters.length && (
            <select
              aria-label={t("wb.chapterFilter")}
              className="h-9 rounded-md border bg-card px-3 text-sm"
              value={chapterId}
              onChange={(e) => setChapterId(e.target.value)}
            >
              <option value="">{t("wb.allChapters")}</option>
              {chapters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.ordinal}. {c.title ?? t("wb.chapter")}
                </option>
              ))}
            </select>
          )}
          <Link className="studio-link" to={`${base}/cuts`}>
            <Film className="h-4 w-4" />
            {t("nav.cuts")}
          </Link>
        </div>
      </header>

      <section className="grid overflow-hidden rounded-lg border bg-card lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col items-start p-6 lg:p-8">
          <span className="mb-3 text-xs text-muted-foreground">{zh ? "继续创作" : "CONTINUE CREATING"}</span>
          <h2 className="text-2xl font-semibold tracking-tight">{nextLabel}</h2>
          <p className="mt-3 max-w-xl text-sm leading-7 text-muted-foreground">
            {chapter
              ? `${chapter.title ?? t("wb.chapter")} · ${(chapter.content ?? "").slice(0, 90)}${(chapter.content?.length ?? 0) > 90 ? "…" : ""}`
              : zh
                ? "导入小说、整理设定，再把故事拆解成可制作的分镜。"
                : "Import a narrative, develop your story bible, then turn scenes into shots."}
          </p>
          <Link to={`${base}/${next}`} className="studio-primary mt-6">
            {zh ? "继续处理" : "Continue"}
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
        <div className="border-t bg-surface p-6 lg:border-l lg:border-t-0">
          <p className="mb-3 text-xs text-muted-foreground">
            {zh ? "镜头制作进度 · 当前范围" : "SHOT PROGRESS · CURRENT SCOPE"}
          </p>
          <div className="flex items-baseline justify-between">
            <span className="text-3xl font-medium tabular-nums">
              {shots ? approved : "—"}
              <span className="text-base text-muted-foreground"> / {shots?.length ?? "—"}</span>
            </span>
            <span className="text-xs text-muted-foreground">{shots ? `${progress}%` : "—"}</span>
          </div>
          <div className="my-4 h-1.5 overflow-hidden rounded-full bg-elevated">
            <div className="h-full rounded-full bg-primary" style={{ width: `${progress}%` }} />
          </div>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">{t("wb.roster")}</p>
              <p className="mt-1">{assets ? characters.length : "—"}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">{t("wb.scenes")}</p>
              <p className="mt-1">{assets ? locations.length : "—"}</p>
            </div>
          </div>
        </div>
      </section>

      <nav
        aria-label={zh ? "创作流程" : "Creative workflow"}
        className="grid grid-cols-2 gap-2 lg:grid-cols-4"
      >
        {[
          { to: "narrative", label: t("nav.narrative"), icon: ScrollText },
          { to: "assets", label: t("nav.assetLibrary"), icon: Images },
          { to: "storyboard", label: t("nav.storyboard"), icon: Clapperboard },
          { to: "shots", label: t("nav.shots"), icon: Film },
        ].map((step, i) => (
          <Link
            key={step.to}
            to={`${base}/${step.to}`}
            className="flex min-w-0 items-center gap-3 rounded-md border border-border bg-surface p-3 text-sm transition-colors hover:bg-elevated"
          >
            <span className="font-code text-xs text-faint">0{i + 1}</span>
            <step.icon className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{step.label}</span>
            <ArrowRight className="ml-auto h-3.5 w-3.5 shrink-0 text-faint" />
          </Link>
        ))}
      </nav>

      <Panel
        title={zh ? "分镜预览" : "Shot previews"}
        icon={<Clapperboard className="h-4 w-4" />}
        action={viewAll("storyboard")}
      >
        {shotsLoading ? (
          <Skeleton className="h-40" />
        ) : shotsError ? (
          <div role="alert" className="studio-empty">
            {zh ? "镜头暂时无法加载，请稍后重试。" : "Unable to load shots. Please try again."}
          </div>
        ) : shots?.length ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {shots.slice(0, 4).map((shot, i) => (
              <article key={shot.id} className="min-w-0">
                <div className="aspect-video overflow-hidden rounded-md border">
                  <ShotMedia projectId={projectId!} shot={shot} />
                </div>
                <div className="mt-3 flex items-center justify-between gap-2">
                  <Link
                    to={`${base}/shots?shot=${shot.id}`}
                    className="truncate text-sm font-medium hover:text-primary"
                  >
                    {String(i + 1).padStart(2, "0")} · {shot.title ?? shot.code}
                  </Link>
                  <Badge variant={shot.production_status === "approved" ? "success" : "outline"}>
                    {t(`status.${shot.production_status}`)}
                  </Badge>
                </div>
                <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                  {shot.description || shot.code}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <div className="studio-empty">
            <Clapperboard className="h-6 w-6" />
            <p>{t("wb.empty.shots")}</p>
            <Link to={`${base}/storyboard`} className="text-primary">
              {t("nav.storyboard")} →
            </Link>
          </div>
        )}
      </Panel>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        <Panel title={t("nav.assetLibrary")} icon={<Images className="h-4 w-4" />} action={viewAll("assets")}>
          {assetsLoading ? (
            <Skeleton className="h-44" />
          ) : assetsError ? (
            <div role="alert" className="studio-empty">
              {zh ? "资产暂时无法加载。" : "Unable to load assets."}
            </div>
          ) : assets?.length ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                ...characters,
                ...locations,
                ...assets.filter((a) => !["character", "location"].includes(a.type)),
              ]
                .slice(0, 4)
                .map((a) => (
                  <Link key={a.id} to={`${base}/assets/${a.id}`} className="group min-w-0">
                    <div className="aspect-[4/3] overflow-hidden rounded-md border bg-elevated">
                      <MediaImage
                        src={
                          a.representative_blob_hash ? blobUrl(projectId!, a.representative_blob_hash) : null
                        }
                        alt={a.name}
                      />
                    </div>
                    <p className="mt-2 truncate text-sm font-medium group-hover:text-primary">{a.name}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t(`asset.${a.type}`)}</p>
                  </Link>
                ))}
            </div>
          ) : (
            <div className="studio-empty">
              <Images className="h-6 w-6" />
              <p>{t("assets.empty")}</p>
              <Link className="text-primary" to={`${base}/assets`}>
                {t("common.new")} →
              </Link>
            </div>
          )}
        </Panel>
        <Panel title={zh ? "项目信息" : "Project information"}>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between gap-3">
              <span className="text-muted-foreground">{zh ? "章节" : "Chapters"}</span>
              <span>{detail ? chapters.length : "—"}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-muted-foreground">{zh ? "项目用量" : "Project usage"}</span>
              <span>{quotaLoading ? "…" : quotaError ? (zh ? "暂不可用" : "Unavailable") : usedPct === null ? (zh ? "未设置限额" : "No limit set") : `${usedPct}%`}</span>
            </div>
            <div className="flex flex-wrap gap-3 border-t pt-3">
              <Link className="text-xs text-muted-foreground hover:text-foreground" to={`${base}/settings`}>
                {t("nav.settings")} →
              </Link>
              <Link className="text-xs text-muted-foreground hover:text-foreground" to={`${base}/cuts`}>
                {zh ? "时间线与定剪基线" : "Timelines & baselines"} →
              </Link>
            </div>
            <p className="text-xs leading-6 text-faint">
              {zh
                ? "成片播放暂不可用；可继续管理时间线、冻结与比较定剪基线。"
                : "Film playback is not available yet. You can manage timelines, freeze cuts and compare baselines."}
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}
