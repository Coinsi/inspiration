import { LoadState } from "@/components/workbench/LoadState";
import { WorkbenchGallery } from "@/components/workbench/WorkbenchGallery";
import { WorkbenchActivity } from "@/components/workbench/WorkbenchActivity";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  Clapperboard,
  FileText,
  Film,
  Images,
  RefreshCw,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { MediaImage } from "@/components/MediaImage";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  blobUrl,
  type Asset,
  type Novel,
  type Project,
  type Script,
  type Shot,
  type Timeline,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { StudioScenes } from "@/components/StudioScenes";
import { CreativeLaunch } from "@/components/CreativeLaunch";

export default function Workbench() {
  const { projectId } = useParams();
  return <ProjectWorkbench key={projectId} projectId={projectId!} />;
}

function ProjectWorkbench({ projectId }: { projectId: string }) {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const base = `/projects/${projectId}`;
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.get<Project>(base),
  });
  const novels = useQuery({
    queryKey: ["novels", projectId],
    queryFn: () => api.get<Novel[]>(`${base}/novels`),
  });
  const scripts = useQuery({
    queryKey: ["scripts", projectId],
    queryFn: () => api.get<Script[]>(`${base}/scripts`),
  });
  const assets = useQuery({
    queryKey: ["assets", projectId, "workbench"],
    queryFn: () => api.get<Asset[]>(`${base}/assets`),
  });
  const shots = useQuery({
    queryKey: ["all-shots", projectId, "workbench"],
    queryFn: () => api.get<Shot[]>(`${base}/shots`),
  });
  const timelines = useQuery({
    queryKey: ["timelines", projectId],
    queryFn: () => api.get<Timeline[]>(`${base}/timelines`),
  });
  const selected =
    shots.data?.filter((s) => s.selected_generation_id).length ?? 0;
  const approved =
    shots.data?.filter((s) =>
      ["approved", "in_cut"].includes(s.production_status),
    ).length ?? 0;
  const withoutOutput = shots.data?.find((s) => !s.selected_generation_id);
  const cover =
    assets.data?.find(
      (a) => a.representative_blob_hash && a.type === "character",
    ) ?? assets.data?.find((a) => a.representative_blob_hash);
  const core = [novels, scripts, assets, shots, timelines];
  const ready = core.every((q) => q.isSuccess && !q.isError);
  const coreError = core.some((q) => q.isError);
  const stages = [
    {
      to: "narrative",
      label: zh ? "故事章节" : "Story",
      detail: zh ? "导入小说，整理创作起点" : "Import and develop your story",
      icon: BookOpen,
      query: novels,
      count: novels.data?.length,
      unit: zh ? "部作品" : "stories",
    },
    {
      to: "scripts",
      label: zh ? "剧本正文" : "Script",
      detail: zh ? "改编内容，打磨动作与对白" : "Shape action and dialogue",
      icon: FileText,
      query: scripts,
      count: scripts.data?.length,
      unit: zh ? "份剧本" : "scripts",
    },
    {
      to: "assets",
      label: zh ? "角色与资产" : "Assets",
      detail: zh
        ? "确定人物、场景与视觉参考"
        : "Develop characters and references",
      icon: Images,
      query: assets,
      count: assets.data?.length,
      unit: zh ? "项资产" : "assets",
    },
    {
      to: "storyboard",
      label: zh ? "分镜与画面" : "Storyboard",
      detail: zh
        ? "组织镜头，生成并选择画面"
        : "Compose shots and select media",
      icon: Clapperboard,
      query: shots,
      count: shots.data?.length,
      unit: zh ? "个镜头" : "shots",
    },
    {
      to: "cuts",
      label: zh ? "剪辑成片" : "Edit & export",
      detail: zh
        ? "编排片段，预览并导出成片"
        : "Arrange clips and export a film",
      icon: Film,
      query: timelines,
      count: timelines.data?.length,
      unit: zh ? "条时间线" : "timelines",
    },
  ];
  const next =
    !novels.data?.length && !scripts.data?.length && !shots.data?.length
      ? stages[0]
      : !scripts.data?.length && !shots.data?.length
        ? stages[1]
        : !assets.data?.length
          ? stages[2]
          : !shots.data?.length || withoutOutput
            ? stages[3]
            : stages[4];
  const nextHref =
    withoutOutput && next.to === "storyboard"
      ? `${base}/shots?shot=${withoutOutput.id}`
      : `${base}/${next.to}`;
  const retryAll = () => {
    for (const q of core) void q.refetch();
    void project.refetch();
  };
  const countText = (
    q: { isError: boolean; isPending: boolean },
    count: number | undefined,
    unit: string,
  ) =>
    q.isError
      ? zh
        ? "加载失败"
        : "Unavailable"
      : q.isPending
        ? "…"
        : `${count ?? 0} ${unit}`;
  const progress = shots.data?.length
    ? Math.round((selected / shots.data.length) * 100)
    : 0;

  return (
    <div className="studio-page workbench-page">
      <section className="home-hero workbench-hero">
        <StudioScenes />
        <p className="studio-eyebrow">
          INSPIRATION · {zh ? "你的创作现场" : "YOUR CREATIVE STUDIO"}
        </p>
        <h1>
          {zh ? (
            <>
              今天，想讲一个<span>怎样的故事？</span>
            </>
          ) : (
            <>
              What story<span>will you tell today?</span>
            </>
          )}
        </h1>
        <CreativeLaunch projectId={projectId} />
      </section>
      <header className="page-heading items-center">
        <div className="min-w-0">
          <p className="!mb-2 !mt-0 text-[11px] font-medium uppercase tracking-[.18em] text-faint">
            INSPIRATION STUDIO
          </p>
          <h2 className="text-xl font-semibold tracking-tight">
            {project.data?.name ?? (zh ? "创作工作台" : "Creative workspace")}
          </h2>
          <p>
            {project.data?.description ||
              (zh
                ? "让故事、参考与画面，在这里连起来。"
                : "Bring your story, references and shots together.")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link className="studio-primary" to={`${base}/storyboard`}>
            <Clapperboard className="h-4 w-4" />
            {zh ? "进入分镜" : "Open storyboard"}
          </Link>
        </div>
      </header>
      <section
        className="workbench-focus"
        aria-label={zh ? "继续创作" : "Continue creating"}
      >
        <div className="relative z-10 flex min-w-0 flex-col items-start justify-center p-6 md:p-8">
          <span className="mb-4 inline-flex items-center gap-2 text-xs font-medium text-primary">
            <span className="h-1.5 w-1.5 rounded-full bg-primary" />
            {zh ? "继续你的创作" : "CONTINUE YOUR STORY"}
          </span>
          <h2 className="max-w-lg text-xl font-semibold leading-relaxed tracking-tight md:text-2xl">
            {!ready
              ? coreError
                ? zh
                  ? "项目进度暂时无法获取"
                  : "Project progress is unavailable"
                : zh
                  ? "正在整理项目进度…"
                  : "Loading your project…"
              : `${zh ? "下一步 · " : "Next · "}${next.label}`}
          </h2>
          <p className="mt-3 max-w-lg text-sm leading-7 text-muted-foreground">
            {!ready
              ? zh
                ? "你仍可以通过导航进入已有的创作工具。"
                : "You can still open your tools from the navigation."
              : withoutOutput && next.to === "storyboard"
                ? zh
                  ? `「${withoutOutput.title || withoutOutput.code}」还没有选定画面，继续生成或上传参考。`
                  : `Select an output for “${withoutOutput.title || withoutOutput.code}”.`
                : next.detail}
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-4">
            {ready ? (
              <Link className="studio-primary" to={nextHref}>
                {zh ? "继续创作" : "Continue creating"}
                <ArrowRight className="h-4 w-4" />
              </Link>
            ) : coreError ? (
              <button className="studio-link" onClick={retryAll}>
                <RefreshCw className="h-4 w-4" />
                {zh ? "重新加载进度" : "Reload progress"}
              </button>
            ) : (
              <Skeleton className="h-9 w-32" />
            )}
            <Link
              to={`${base}/assets`}
              className="text-sm text-muted-foreground hover:text-primary"
            >
              {zh ? "浏览角色与参考" : "Browse references"}
              <ArrowUpRight className="ml-1 inline h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
        <div className="workbench-cover relative hidden min-h-[240px] overflow-hidden md:block">
          {cover ? (
            <>
              <MediaImage
                src={blobUrl(projectId, cover.representative_blob_hash!)}
                alt={cover.name}
              />
              <div className="absolute bottom-4 right-4 rounded-lg bg-black/70 px-3 py-1.5 text-xs text-white">
                {zh ? "项目资产 · " : "Project asset · "}
                {cover.name}
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center text-primary/40">
              <Clapperboard className="h-24 w-24" strokeWidth={0.7} />
            </div>
          )}
        </div>
      </section>
      <section aria-labelledby="workflow-heading">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h2 id="workflow-heading" className="text-sm font-semibold">
            {zh ? "制作流程" : "Production workflow"}
          </h2>
          <span className="text-xs text-faint">
            {zh
              ? "全项目概览 · 可从任一步开始"
              : "Project overview · start at any stage"}
          </span>
        </div>
        <div className="grid auto-cols-[176px] grid-flow-col gap-3 overflow-x-auto pb-2 sm:auto-cols-auto sm:grid-flow-row sm:grid-cols-2 sm:overflow-visible xl:grid-cols-5">
          {stages.map((stage, index) => (
            <Link
              key={stage.to}
              to={`${base}/${stage.to}`}
              className={cn(
                "workbench-stage group",
                ready &&
                  next.to === stage.to &&
                  "border-primary/50 bg-primary/[.04]",
              )}
            >
              <div className="mb-5 flex items-center justify-between">
                <stage.icon
                  className="h-5 w-5 text-muted-foreground group-hover:text-primary"
                  strokeWidth={1.6}
                />
                <span className="font-code text-[11px] text-faint">
                  0{index + 1}
                </span>
              </div>
              <h3 className="font-medium">{stage.label}</h3>
              <p className="mt-1 min-h-10 text-xs leading-5 text-muted-foreground">
                {stage.detail}
              </p>
              <div className="mt-4 flex items-center justify-between border-t pt-3 text-xs">
                <span
                  className={
                    stage.query.isError
                      ? "text-danger"
                      : "text-muted-foreground"
                  }
                >
                  {countText(stage.query, stage.count, stage.unit)}
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-faint group-hover:text-primary" />
              </div>
            </Link>
          ))}
        </div>
      </section>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
        <WorkbenchGallery
          projectId={projectId}
          assets={assets}
          shots={shots}
          novels={novels}
        />
        <aside className="min-w-0 space-y-5">
          <section
            className="rounded-xl border bg-card p-5"
            aria-labelledby="attention-heading"
          >
            <div className="mb-5 flex items-center justify-between">
              <h2 id="attention-heading" className="text-sm font-semibold">
                {zh ? "镜头准备情况" : "Shot readiness"}
              </h2>
              <span className="text-[11px] text-faint">
                {zh ? "全项目" : "Project"}
              </span>
            </div>
            <LoadState
              loading={shots.isPending}
              error={shots.isError}
              retry={() => void shots.refetch()}
            >
              <div className="flex items-baseline gap-2">
                <strong className="text-3xl font-medium tabular-nums">
                  {selected}
                  <span className="ml-1 text-base text-faint">
                    / {shots.data?.length ?? 0}
                  </span>
                </strong>
                <span className="text-xs text-muted-foreground">
                  {zh ? "已选画面" : "outputs selected"}
                </span>
              </div>
              <div
                role="progressbar"
                aria-label={zh ? "已选画面比例" : "Selected output progress"}
                aria-valuenow={progress}
                aria-valuemin={0}
                aria-valuemax={100}
                className="my-4 h-1.5 overflow-hidden rounded-full bg-elevated"
              >
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <p className="text-xs leading-6 text-muted-foreground">
                {zh
                  ? `${approved} 个镜头已通过审阅或进入成片。选定画面不代表已完成审阅。`
                  : `${approved} shots approved or in a cut. Selecting an output does not approve a shot.`}
              </p>
              {withoutOutput ? (
                <Link
                  to={`${base}/shots?shot=${withoutOutput.id}`}
                  className="mt-4 flex items-center justify-between gap-3 border-t pt-4 text-xs"
                >
                  <span className="min-w-0 truncate">
                    {zh ? "待选画面 · " : "Needs output · "}
                    {withoutOutput.title || withoutOutput.code}
                  </span>
                  <ArrowUpRight className="h-4 w-4 shrink-0 text-primary" />
                </Link>
              ) : (
                !!shots.data?.length && (
                  <Link
                    to={`${base}/cuts`}
                    className="mt-4 flex items-center gap-2 text-xs text-primary"
                  >
                    <Check className="h-4 w-4" />
                    {zh ? "前往编排成片" : "Arrange your film"}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                )
              )}
            </LoadState>
          </section>
          <WorkbenchActivity projectId={projectId} />
        </aside>
      </div>
    </div>
  );
}
