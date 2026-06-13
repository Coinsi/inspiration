import { useQuery } from "@tanstack/react-query";
import {
  ClipboardList,
  Clapperboard,
  Film,
  Images,
  LayoutGrid,
  Play,
  Plus,
  ScrollText,
  Settings2,
  Sparkles,
  Workflow,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { usePersistentState } from "@/lib/usePersistentState";
import {
  api,
  blobUrl,
  PRODUCTION_STATUS_LABEL,
  type Asset,
  type Board,
  type NovelDetail,
  type Novel,
  type Project,
  type Quota,
  type Shot,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const STATUS_VARIANT: Record<string, "default" | "primary" | "success" | "warning" | "info" | "danger"> = {
  to_design: "default",
  concept: "info",
  generating: "warning",
  pending_review: "warning",
  revising: "danger",
  approved: "success",
  in_cut: "primary",
};

export default function Workbench() {
  const { projectId } = useParams();
  const { t, lang } = useI18n();
  const base = `/projects/${projectId}`;

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const project = projects?.find((p) => p.id === projectId);

  const { data: novels } = useQuery({ queryKey: ["novels", projectId], queryFn: () => api.get<Novel[]>(`${base}/novels`) });
  // 小说 + 章节筛选(章节空 = 全部);选中章节后各面板只展示该章相关内容
  const [novelId, setNovelId] = usePersistentState<string | null>(`wb.novel.${projectId}`, null);
  const [chapterId, setChapterId] = usePersistentState<string>(`wb.chapter.${projectId}`, "");
  const effNovelId = novelId ?? novels?.[0]?.id ?? null;
  const { data: novelDetail } = useQuery({
    queryKey: ["novel", projectId, effNovelId],
    queryFn: () => api.get<NovelDetail>(`${base}/novels/${effNovelId}`),
    enabled: !!effNovelId,
  });
  const chParam = chapterId ? `&chapter_id=${chapterId}` : "";

  const { data: characters, isLoading: chLoading } = useQuery({
    queryKey: ["assets", projectId, "character", chapterId],
    queryFn: () => api.get<Asset[]>(`${base}/assets?type=character${chParam}`),
  });
  const { data: locations, isLoading: locLoading } = useQuery({
    queryKey: ["assets", projectId, "location", chapterId],
    queryFn: () => api.get<Asset[]>(`${base}/assets?type=location${chParam}`),
  });
  const { data: shots, isLoading: shotsLoading } = useQuery({
    queryKey: ["shots", projectId, chapterId],
    queryFn: () => api.get<Shot[]>(`${base}/shots${chapterId ? `?chapter_id=${chapterId}` : ""}`),
  });
  const { data: board } = useQuery({ queryKey: ["board", projectId], queryFn: () => api.get<Board>(`${base}/shots/board`) });
  const { data: quota } = useQuery({ queryKey: ["quota", projectId], queryFn: () => api.get<Quota | null>(`${base}/quota`) });

  const approved = (board?.by_status?.approved ?? 0) + (board?.by_status?.in_cut ?? 0);
  const usedPct = quota && quota.limit_cost > 0 ? Math.min(100, Math.round((quota.used_cost / quota.limit_cost) * 100)) : 0;
  // 脚本面板:选中章节则显示该章,否则第一章
  const chapters = novelDetail?.chapters ?? [];
  const firstChapter = (chapterId ? chapters.find((c) => c.id === chapterId) : chapters[0]) ?? null;

  return (
    <div className="flex h-full flex-col">
      {/* 顶部条:标题 + 主操作 */}
      <header className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-border bg-surface/40 px-5 py-3 backdrop-blur-md">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <Clapperboard className="h-5 w-5 text-primary" />
            {project?.name ?? t("wb.title")}
          </h1>
          <p className="truncate text-xs text-muted-foreground">{t("wb.subtitle")}</p>
        </div>
        <span className="flex-1" />
        {/* 小说 + 章节筛选 */}
        {novels && novels.length > 0 && (
          <div className="flex items-center gap-2">
            {novels.length > 1 && (
              <select
                className="h-8 rounded-md border border-border bg-card px-2 text-xs"
                value={effNovelId ?? ""}
                onChange={(e) => { setNovelId(e.target.value); setChapterId(""); }}
                title={t("wb.novel")}
              >
                {novels.map((n) => (
                  <option key={n.id} value={n.id}>{n.title}</option>
                ))}
              </select>
            )}
            <select
              className="h-8 rounded-md border border-border bg-card px-2 text-xs"
              value={chapterId}
              onChange={(e) => setChapterId(e.target.value)}
              title={t("wb.chapterFilter")}
            >
              <option value="">{t("wb.allChapters")}</option>
              {chapters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.ordinal}. {c.title ?? t("wb.chapter")}
                </option>
              ))}
            </select>
            {chapterId && (
              <button
                onClick={() => setChapterId("")}
                title={t("wb.clearFilter")}
                className="grid h-8 w-8 place-items-center rounded-md border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
              >
                ×
              </button>
            )}
          </div>
        )}
        <span className="hidden items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground sm:flex">
          <span className="h-1.5 w-1.5 rounded-full bg-success shadow-glow-sm" />
          {t("wb.saved")}
        </span>
        <Link
          to={`${base}/cuts`}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-elevated/60 px-3 text-xs font-medium text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground"
        >
          <Film className="h-3.5 w-3.5" /> {t("wb.export")}
        </Link>
        <Link
          to={`${base}/shots`}
          className="inline-flex h-8 items-center gap-1.5 rounded-md bg-gradient-primary px-3.5 text-xs font-semibold text-primary-foreground shadow-glow-sm transition hover:brightness-110"
        >
          <Sparkles className="h-3.5 w-3.5" /> {t("wb.generateFilm")}
        </Link>
      </header>

      {/* 工作台栅格 */}
      <div className="grid flex-1 grid-cols-1 gap-3.5 overflow-auto p-3.5 lg:grid-cols-6 lg:grid-rows-[minmax(260px,auto)_minmax(180px,auto)_minmax(240px,auto)]">
        {/* 脚本编辑 */}
        <Panel
          className="lg:col-span-2"
          title={t("wb.script")}
          icon={<ScrollText className="h-3.5 w-3.5" />}
          meta={firstChapter ? `${(firstChapter.content ?? "").length}` : undefined}
          action={
            <Link to={`${base}/narrative`} className="text-[11px] text-muted-foreground hover:text-primary">
              {t("wb.openNarrative")}
            </Link>
          }
        >
          {firstChapter ? (
            <div className="space-y-1.5 text-[12.5px] leading-relaxed">
              <p className="font-semibold text-primary">{firstChapter.title ?? t("wb.chapter")}</p>
              <p className="whitespace-pre-wrap text-muted-foreground">{(firstChapter.content ?? "").slice(0, 320)}…</p>
            </div>
          ) : (
            <EmptyHint icon={<ScrollText className="h-5 w-5" />} text={t("wb.empty.script")} to={`${base}/narrative`} cta={t("wb.openNarrative")} />
          )}
        </Panel>

        {/* 工作流 */}
        <Panel className="lg:col-span-2" title={t("wb.flow")} icon={<Workflow className="h-3.5 w-3.5" />}>
          <WorkflowGraph t={t} />
        </Panel>

        {/* 角色一览 */}
        <Panel
          className="lg:col-span-2"
          title={t("wb.roster")}
          icon={<Images className="h-3.5 w-3.5" />}
          action={
            <Link to={`${base}/assets`} className="text-[11px] text-muted-foreground hover:text-primary">
              {t("wb.manageRoster")}
            </Link>
          }
        >
          {chLoading ? (
            <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full rounded-lg" />)}</div>
          ) : characters && characters.length > 0 ? (
            <div className="space-y-2">
              {characters.slice(0, 5).map((c, i) => (
                <Link
                  key={c.id}
                  to={`${base}/assets/${c.id}`}
                  className="flex items-start gap-3 rounded-lg border border-border bg-card p-2 transition hover:-translate-y-px hover:border-primary/50"
                >
                  <Thumb projectId={projectId!} hash={c.representative_blob_hash} fallback={c.name} ratio="portrait" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-[13px] font-semibold">{c.name}</span>
                      <Badge variant={i === 0 ? "primary" : "info"} className="px-1.5 py-0 text-[9px]">
                        {i === 0 ? t("r.lead") : t("r.supp")}
                      </Badge>
                    </div>
                    <div className="line-clamp-2 break-words text-[11px] leading-snug text-muted-foreground">{c.summary ?? c.code}</div>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyHint icon={<Images className="h-5 w-5" />} text={t("wb.empty.roster")} to={`${base}/assets`} cta={t("common.new")} />
          )}
        </Panel>

        {/* 分镜时间轴 */}
        <Panel
          className="lg:col-span-4"
          title={t("wb.timeline")}
          icon={<LayoutGrid className="h-3.5 w-3.5" />}
          meta={board ? `${approved}/${board.total}` : undefined}
          action={
            <Link to={`${base}/shots`} className="text-[11px] text-muted-foreground hover:text-primary">
              {t("wb.openShots")}
            </Link>
          }
        >
          {shotsLoading ? (
            <div className="flex gap-3">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-24 w-28 rounded-lg" />)}</div>
          ) : shots && shots.length > 0 ? (
            <div className="flex gap-3 overflow-x-auto pb-1">
              {shots.slice(0, 12).map((s, i) => (
                <Link key={s.id} to={`${base}/shots`} className={cn("w-28 flex-shrink-0", i === 0 && "")}>
                  <div className="relative h-[76px] overflow-hidden rounded-lg border border-border bg-gradient-to-br from-elevated to-card">
                    <div className="absolute inset-0 bg-[radial-gradient(60px_60px_at_50%_40%,hsl(var(--accent)/0.22),transparent)]" />
                    <span className="absolute left-1.5 top-1.5 font-code text-[9px] text-faint">{s.code}</span>
                    <Badge variant={STATUS_VARIANT[s.production_status] ?? "default"} className="absolute bottom-1.5 left-1.5 px-1.5 py-0 text-[8.5px]">
                      {lang === "zh" ? PRODUCTION_STATUS_LABEL[s.production_status] : t(`status.${s.production_status}`)}
                    </Badge>
                  </div>
                  <div className="mt-1.5 truncate text-center text-[10.5px] text-muted-foreground">{s.title ?? s.code}</div>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyHint icon={<LayoutGrid className="h-5 w-5" />} text={t("wb.empty.shots")} to={`${base}/shots`} cta={t("wb.openShots")} />
          )}
        </Panel>

        {/* 场景管理 */}
        <Panel
          className="lg:col-span-2"
          title={t("wb.scenes")}
          icon={<ClipboardList className="h-3.5 w-3.5" />}
          action={
            <Link to={`${base}/assets`} className="text-[11px] text-muted-foreground hover:text-primary">
              <Plus className="h-3.5 w-3.5" />
            </Link>
          }
        >
          {locLoading ? (
            <div className="grid grid-cols-2 gap-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-lg" />)}</div>
          ) : locations && locations.length > 0 ? (
            <div className="grid grid-cols-2 gap-2">
              {locations.slice(0, 4).map((l, i) => (
                <Link key={l.id} to={`${base}/assets/${l.id}`} className="overflow-hidden rounded-lg border border-border bg-card transition hover:border-primary/50">
                  <div className="relative h-14">
                    <Thumb projectId={projectId!} hash={l.representative_blob_hash} fallback={l.name} ratio="wide" />
                    <span className="absolute right-1 top-1 grid h-4 w-4 place-items-center rounded bg-bg/70 font-code text-[9px] font-bold text-primary">
                      {String.fromCharCode(65 + i)}
                    </span>
                  </div>
                  <div className="px-2 py-1.5">
                    <b className="block truncate text-[11px]">{l.name}</b>
                    <small className="block truncate text-[9.5px] text-muted-foreground">{l.code}</small>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyHint icon={<ClipboardList className="h-5 w-5" />} text={t("wb.empty.scenes")} to={`${base}/assets`} cta={t("common.new")} />
          )}
        </Panel>

        {/* 成片预览 */}
        <Panel className="lg:col-span-4" title={t("wb.preview")} icon={<Film className="h-3.5 w-3.5" />} meta="3840×2160 · 4K" bodyClassName="flex flex-col">
          <div className="relative flex-1 overflow-hidden rounded-lg border border-border" style={{ minHeight: 180 }}>
            <div className="absolute inset-0 bg-[radial-gradient(420px_240px_at_50%_30%,hsl(210_60%_30%/0.5),transparent),linear-gradient(180deg,hsl(218_45%_12%),hsl(220_50%_6%))]" />
            <div className="absolute right-[22%] top-[14%] h-16 w-16 rounded-full bg-[radial-gradient(circle_at_40%_40%,hsl(40_40%_88%),hsl(35_30%_60%))] opacity-50 blur-[1px]" />
            <div className="absolute left-1/2 top-[40%] h-28 w-12 -translate-x-1/2 rounded-t-[40px] bg-gradient-to-b from-black/80 to-black/95" />
            <button className="absolute left-1/2 top-1/2 grid h-11 w-11 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-primary text-primary-foreground shadow-glow">
              <Play className="h-4 w-4 fill-current" />
            </button>
          </div>
          <div className="flex items-center gap-2.5 pt-2.5">
            <span className="font-code text-[11px] text-faint">00:00</span>
            <div className="relative h-1 flex-1 rounded bg-elevated">
              <span className="absolute inset-y-0 left-0 w-[18%] rounded bg-gradient-accent" />
            </div>
            <span className="font-code text-[11px] text-faint">01:20</span>
          </div>
        </Panel>

        {/* 成片信息 + 用量 */}
        <Panel className="lg:col-span-2" title={t("wb.info")} icon={<Settings2 className="h-3.5 w-3.5" />}>
          <div className="space-y-0.5">
            <InfoRow label={t("wb.info.project")} value={project?.name ?? "—"} />
            <InfoRow label={t("wb.info.shots")} value={String(board?.total ?? 0)} mono />
            <InfoRow label={t("wb.info.approved")} value={String(approved)} mono />
            <InfoRow label={t("wb.info.fps")} value="24 fps" mono />
            <InfoRow label={t("wb.info.ratio")} value="16 : 9" mono />
          </div>
          <div className="mt-3 rounded-lg border border-border bg-card p-2.5">
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-muted-foreground">{t("wb.usage.month")}</span>
              <b className="text-primary">{usedPct}%</b>
            </div>
            <div className="my-2 h-1.5 overflow-hidden rounded bg-elevated">
              <i className="block h-full rounded bg-gradient-accent" style={{ width: `${usedPct}%` }} />
            </div>
            <Link
              to={`${base}/settings`}
              className="block rounded-md bg-gradient-primary py-1.5 text-center text-[11px] font-semibold text-primary-foreground shadow-glow-sm transition hover:brightness-110"
            >
              {t("wb.usage.upgrade")}
            </Link>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-dashed border-border py-1.5 text-[12px] last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("font-semibold", mono && "font-code")}>{value}</span>
    </div>
  );
}

function Thumb({ projectId, hash, fallback, ratio }: { projectId: string; hash: string | null; fallback: string; ratio: "portrait" | "wide" }) {
  const size = ratio === "portrait" ? "h-[54px] w-11" : "h-full w-full";
  if (hash) {
    return <img src={blobUrl(projectId, hash)} alt={fallback} className={cn("flex-shrink-0 rounded-md object-cover", ratio === "portrait" ? size : "absolute inset-0")} />;
  }
  return (
    <div
      className={cn(
        "grid flex-shrink-0 place-items-center rounded-md bg-gradient-to-br from-accent/40 to-primary/40 text-sm font-semibold text-primary-foreground",
        ratio === "portrait" ? size : "absolute inset-0 rounded-none",
      )}
    >
      {fallback.slice(0, 1)}
    </div>
  );
}

function EmptyHint({ icon, text, to, cta }: { icon: React.ReactNode; text: string; to: string; cta: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 py-6 text-center text-muted-foreground">
      <span className="text-faint">{icon}</span>
      <p className="text-xs">{text}</p>
      <Link to={to} className="text-[11px] font-medium text-primary hover:underline">
        {cta} →
      </Link>
    </div>
  );
}

const FLOW_NODES = [
  { key: "fl.role", x: "50%", y: 4, gold: false, active: false },
  { key: "fl.shot", x: "12%", y: 88, gold: false, active: false },
  { key: "fl.scene", x: "60%", y: 88, gold: false, active: false },
  { key: "fl.comp", x: "50%", y: 158, gold: true, active: true },
  { key: "fl.out", x: "50%", y: 214, gold: true, active: false },
];

function WorkflowGraph({ t }: { t: (k: string) => string }) {
  return (
    <div className="relative h-full min-h-[210px]">
      <svg className="pointer-events-none absolute inset-0 h-full w-full">
        <path d="M 50% 26 C 50% 64, 26% 64, 26% 90" fill="none" stroke="hsl(var(--accent) / 0.5)" strokeWidth="1.5" />
        <path d="M 50% 26 C 50% 64, 70% 64, 70% 90" fill="none" stroke="hsl(var(--accent) / 0.5)" strokeWidth="1.5" />
        <path d="M 26% 116 C 26% 142, 50% 142, 50% 158" fill="none" stroke="hsl(var(--primary) / 0.6)" strokeWidth="1.5" />
        <path d="M 70% 116 C 70% 142, 50% 142, 50% 158" fill="none" stroke="hsl(var(--primary) / 0.6)" strokeWidth="1.5" />
        <path d="M 50% 186 L 50% 214" fill="none" stroke="hsl(var(--primary) / 0.6)" strokeWidth="1.5" />
      </svg>
      {FLOW_NODES.map((n) => (
        <div
          key={n.key}
          className={cn(
            "absolute whitespace-nowrap rounded-lg border px-3 py-1.5 text-[11.5px] font-semibold",
            n.gold ? "border-primary/60 bg-primary/12 text-foreground" : "border-accent/50 bg-accent/10 text-foreground",
            n.active && "shadow-glow ring-1 ring-primary",
          )}
          style={{ left: n.x, top: n.y, transform: n.x === "50%" ? "translateX(-50%)" : undefined }}
        >
          {t(n.key)}
        </div>
      ))}
      <div className="absolute bottom-0 left-0 text-[10.5px] text-faint">{t("fl.current")}</div>
    </div>
  );
}
