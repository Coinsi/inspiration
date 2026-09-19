import { useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight, Images, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { MediaImage } from "@/components/MediaImage";
import { ShotMedia } from "@/components/ShotMedia";
import { Badge } from "@/components/ui/badge";
import { LoadState } from "./LoadState";
import {
  api,
  blobUrl,
  type Asset,
  type Shot,
  type Novel,
  type NovelDetail,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { cn } from "@/lib/utils";
export function WorkbenchGallery({
  projectId,
  assets,
  shots,
  novels,
}: {
  projectId: string;
  assets: UseQueryResult<Asset[], Error>;
  shots: UseQueryResult<Shot[], Error>;
  novels: UseQueryResult<Novel[], Error>;
}) {
  const { lang, t } = useI18n();
  const zh = lang === "zh";
  const base = `/projects/${projectId}`;
  const [tab, setTab] = useState<"shots" | "assets">("shots");
  const [query, setQuery] = useState("");
  const [novelId, setNovelId] = usePersistentState<string | null>(
    `wb.novel.${projectId}`,
    null,
  );
  const [chapterId, setChapterId] = usePersistentState(
    `wb.chapter.${projectId}`,
    "",
  );
  const currentNovel =
    novels.data?.find((n) => n.id === novelId) ?? novels.data?.[0];
  const novel = useQuery({
    queryKey: ["novel", projectId, currentNovel?.id],
    queryFn: () => api.get<NovelDetail>(`${base}/novels/${currentNovel!.id}`),
    enabled: !!currentNovel,
  });
  const chapters = novel.data?.chapters ?? [];
  const chapter = novel.isError
    ? undefined
    : chapters.find((c) => c.id === chapterId);
  // Validate saved chapter against the selected novel before requesting scoped data.
  const scopedAssets = useQuery({
    queryKey: ["assets", projectId, "workbench", chapter?.id],
    queryFn: () => api.get<Asset[]>(`${base}/assets?chapter_id=${chapter!.id}`),
    enabled: !!chapter,
  });
  const scopedShots = useQuery({
    queryKey: ["all-shots", projectId, "workbench", chapter?.id],
    queryFn: () => api.get<Shot[]>(`${base}/shots?chapter_id=${chapter!.id}`),
    enabled: !!chapter,
  });
  const galleryAssets = chapter ? scopedAssets : assets;
  const galleryShots = chapter ? scopedShots : shots;
  const gallery = tab === "shots" ? galleryShots : galleryAssets;
  const search = query.trim().toLocaleLowerCase();
  const visibleShots = (galleryShots.data ?? []).filter((s) =>
    `${s.title ?? ""} ${s.code} ${s.description}`
      .toLocaleLowerCase()
      .includes(search),
  );
  const visibleAssets = (galleryAssets.data ?? []).filter((a) =>
    `${a.name} ${a.code} ${a.summary ?? ""} ${a.tags.join(" ")}`
      .toLocaleLowerCase()
      .includes(search),
  );
  const viewAll = (path: string) => (
    <Link
      to={`${base}/${path}`}
      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"
    >
      {zh ? "查看全部" : "View all"}
      <ArrowUpRight className="h-3.5 w-3.5" />
    </Link>
  );
  return (
    <section
      className="min-w-0"
      aria-label={zh ? "项目内容" : "Project content"}
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b pb-3">
        <div
          className="flex gap-4"
          role="group"
          aria-label={zh ? "内容类型" : "Content type"}
        >
          {(["shots", "assets"] as const).map((value) => (
            <button
              key={value}
              aria-pressed={tab === value}
              onClick={() => {
                setTab(value);
                setQuery("");
              }}
              className={cn(
                "border-b-2 py-2 text-sm transition-colors",
                tab === value
                  ? "border-primary font-semibold text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {value === "shots"
                ? zh
                  ? "镜头画面"
                  : "Shot gallery"
                : zh
                  ? "角色与资产"
                  : "Assets"}
            </button>
          ))}
        </div>
        {viewAll(tab === "shots" ? "storyboard" : "assets")}
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        <label className="flex min-w-[160px] flex-1 items-center gap-2 rounded-lg border bg-card px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-faint" />
          <input
            aria-label={zh ? "筛选当前内容" : "Filter current content"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              zh ? "按名称或描述筛选…" : "Filter by name or description…"
            }
            className="w-full min-w-0 bg-transparent text-xs outline-none"
          />
        </label>
        {!!novels.data?.length && (
          <select
            aria-label={zh ? "选择作品" : "Choose story"}
            value={currentNovel?.id ?? ""}
            onChange={(e) => {
              setNovelId(e.target.value);
              setChapterId("");
            }}
            className="max-w-[160px] rounded-lg border bg-card px-2 text-xs"
          >
            {novels.data.map((n) => (
              <option key={n.id} value={n.id}>
                {n.title}
              </option>
            ))}
          </select>
        )}
        {!!currentNovel && (
          <select
            aria-label={zh ? "章节范围" : "Chapter scope"}
            disabled={!novel.isSuccess || novel.isError}
            value={chapter?.id ?? ""}
            onChange={(e) => setChapterId(e.target.value)}
            className="max-w-[170px] rounded-lg border bg-card px-2 py-2 text-xs"
          >
            <option value="">
              {novel.isError
                ? zh
                  ? "章节加载失败"
                  : "Chapters unavailable"
                : novel.isPending
                  ? zh
                    ? "加载章节…"
                    : "Loading chapters…"
                  : zh
                    ? "整个项目"
                    : "Entire project"}
            </option>
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.ordinal}.{" "}
                {c.title || (zh ? "未命名章节" : "Untitled chapter")}
              </option>
            ))}
          </select>
        )}
      </div>
      {novel.isError && (
        <p role="alert" className="mb-3 text-xs text-danger">
          {zh
            ? "章节筛选暂不可用，当前展示整个项目。"
            : "Chapter filtering is unavailable. Showing the entire project."}{" "}
          <button className="underline" onClick={() => void novel.refetch()}>
            {zh ? "重试" : "Retry"}
          </button>
        </p>
      )}
      <LoadState
        loading={gallery.isPending}
        error={gallery.isError}
        retry={() => void gallery.refetch()}
      >
        {(tab === "shots" ? visibleShots.length : visibleAssets.length) > 0 ? (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 2xl:grid-cols-3">
              {tab === "shots"
                ? visibleShots.slice(0, 6).map((shot) => (
                    <article
                      key={shot.id}
                      className="overflow-hidden rounded-xl border bg-card"
                    >
                      <div className="aspect-video overflow-hidden">
                        <ShotMedia projectId={projectId} shot={shot} />
                      </div>
                      <div className="p-3.5">
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="font-code text-[10px] text-faint">
                            {shot.code}
                          </span>
                          <Badge
                            variant={
                              ["approved", "in_cut"].includes(
                                shot.production_status,
                              )
                                ? "success"
                                : "outline"
                            }
                          >
                            {t(`status.${shot.production_status}`)}
                          </Badge>
                        </div>
                        <Link
                          className="line-clamp-1 text-sm font-medium hover:text-primary"
                          to={`${base}/shots?shot=${shot.id}`}
                        >
                          {shot.title || shot.code}
                        </Link>
                        <p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">
                          {shot.description ||
                            (zh
                              ? "继续补充镜头描述"
                              : "Add a shot description")}
                        </p>
                      </div>
                    </article>
                  ))
                : visibleAssets.slice(0, 6).map((asset) => (
                    <Link
                      key={asset.id}
                      to={`${base}/assets/${asset.id}`}
                      className="group overflow-hidden rounded-xl border bg-card hover:border-primary/50"
                    >
                      <div className="aspect-video overflow-hidden">
                        <MediaImage
                          src={
                            asset.representative_blob_hash
                              ? blobUrl(
                                  projectId,
                                  asset.representative_blob_hash,
                                )
                              : null
                          }
                          alt={asset.name}
                        />
                      </div>
                      <div className="p-3.5">
                        <div className="mb-2 text-[11px] text-faint">
                          {t(`asset.${asset.type}`)}
                        </div>
                        <h3 className="truncate text-sm font-medium group-hover:text-primary">
                          {asset.name}
                        </h3>
                        <p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">
                          {asset.summary || asset.code}
                        </p>
                      </div>
                    </Link>
                  ))}
            </div>
            <p className="mt-3 text-xs text-faint">
              {zh
                ? `当前范围共 ${tab === "shots" ? visibleShots.length : visibleAssets.length} 项，工作台最多预览 6 项。`
                : `${tab === "shots" ? visibleShots.length : visibleAssets.length} matches in this scope. Up to 6 previews shown.`}
            </p>
          </>
        ) : (
          <div className="studio-empty min-h-64 bg-card">
            <Images className="h-7 w-7" strokeWidth={1.5} />
            <p>
              {search
                ? zh
                  ? "没有匹配的内容"
                  : "No matching content"
                : chapter
                  ? zh
                    ? "这一章还没有关联内容"
                    : "No content linked to this chapter"
                  : tab === "shots"
                    ? zh
                      ? "从第一个分镜开始，把故事变成画面。"
                      : "Create your first shot and bring the story to life."
                    : zh
                      ? "把角色、场景和参考整理到项目中。"
                      : "Bring characters, locations and references into your project."}
            </p>
            {search ? (
              <button className="studio-link" onClick={() => setQuery("")}>
                {zh ? "清除筛选" : "Clear filter"}
              </button>
            ) : (
              <Link
                className="studio-link"
                to={`${base}/${tab === "shots" ? "storyboard" : "assets"}`}
              >
                {zh ? "前往创作" : "Start creating"}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            )}
          </div>
        )}
      </LoadState>
    </section>
  );
}
