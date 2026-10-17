import { StudioSelect } from "@/components/ui/studio-select";
import { MaterialProfile } from "@/components/MaterialProfile";
import { SearchField } from "@/components/ui/search-field";
import { MaterialShotPicker } from "@/components/MaterialShotPicker";
import "./library-detail.css";
import { SelectField } from "@/components/ui/select-field";
import { Lightbox } from "@/components/ImageViewer";
import { MaterialTabs } from "@/components/MaterialTabs";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Film,
  Download,
  Tags,
  Captions,
  UserRound,
  Upload,
  ChevronRight,
  Maximize2,
  Plus,
  LayoutGrid,
  List,
  Play,
  AudioLines,
  Image as ImageIcon,
} from "lucide-react";
import { usePersistentState } from "@/lib/usePersistentState";
import { api, blobUrl, type Shot } from "@/lib/api";
import {
  mediaSize,
  mediaTypeName,
  type MediaType,
  mediaStatus,
  mediaTime,
  type LibraryMedia,
  type MediaVersion,
  type DeletionImpact,
} from "@/lib/library";
import { useI18n } from "@/lib/i18n";
import { MediaImage } from "@/components/MediaImage";
import { LibraryUpload } from "@/components/LibraryUpload";
import { LibraryReuse } from "@/components/LibraryReuse";
import { LibraryOrganization } from "@/components/LibraryOrganization";
import {
  LibraryProcessing,
  LibraryProcessingControl,
} from "@/components/LibraryProcessing";
import {
  LibrarySearch,
  LibraryIndexControl,
  LibraryAnnotation,
  type SegmentResult,
} from "@/components/LibrarySearch";
import { LibraryMediaPreview } from "@/components/LibraryMediaPreview";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";

export default function MediaLibrary() {
  const { projectId } = useParams();
  return <LibraryContent key={projectId} projectId={projectId!} />;
}
function LibraryContent({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const folder = params.get("folder") || "all";
  const folderId = ["all", "root"].includes(folder) ? null : folder;
  const [selection, setSelection] = useState<Record<string, string | null>>({});
  const chooseFolder = (id: string) => {
    setSelection({});
    const next = new URLSearchParams(params);
    if (id === "all") next.delete("folder");
    else next.set("folder", id);
    setParams(next);
    setPage(0);
  };
  const zh = useI18n().lang === "zh",
    base = `/projects/${projectId}/library`,
    qc = useQueryClient();
  const [deleted] = useState(false),
    [search, setSearch] = useState("");
  const [query, setQuery] = useState(""),
    [page, setPage] = useState(0),
    [sort, setSort] = useState("recent");
  const [mediaType, setMediaType] = useState<MediaType | "all">("all");
  const [layout, setLayout] = usePersistentState<"grid" | "list">(
    "video-library.layout",
    "grid",
  );
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search);
      setPage(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  const [view, setView] = useState<"videos" | "search" | "processing">(
    "videos",
  );
  const [organizing, setOrganizing] = usePersistentState(
    `library.organizing.${projectId}`,
    false,
  );
  const [reuseOpen, setReuseOpen] = useState(false);
  const [segment, setSegment] = useState<SegmentResult | null>(null);
  const [openId, setOpenId] = useState(params.get("media") || ""),
    [impactId, setImpactId] = useState("");
  const [upload, setUpload] = useState<{
    mediaId?: string;
    resume?: MediaVersion;
    mediaType?: MediaType;
  } | null>(params.get("import") === "1" ? {} : null);
  const list = useQuery({
    queryKey: [
      "library",
      projectId,
      "catalog",
      mediaType,
      deleted,
      query,
      page,
      sort,
      folder,
    ],
    queryFn: () =>
      api.get<{ items: LibraryMedia[]; total: number }>(
        `${base}/catalog?${new URLSearchParams({ deleted: String(deleted), query, offset: String(page * 24), sort, ...(mediaType === "all" ? {} : { media_type: mediaType }), ...(folderId ? { folder_id: folderId } : folder === "root" ? { root_only: "true" } : {}) })}`,
      ),
    refetchInterval: 4000,
  });
  const detail = useQuery({
    queryKey: ["library-item", projectId, openId],
    queryFn: () => api.get<LibraryMedia>(`${base}/items/${openId}`),
    enabled: !!openId,
    refetchInterval: openId ? 4000 : false,
  });
  useEffect(() => {
    if (list.data && page * 24 >= list.data.total && page > 0)
      setPage(Math.max(0, Math.ceil(list.data.total / 24) - 1));
  }, [list.data, page]);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["library-folders", projectId] });
    void qc.invalidateQueries({ queryKey: ["library", projectId] });
    void qc.invalidateQueries({ queryKey: ["library-item", projectId] });
    void qc.invalidateQueries({ queryKey: ["media-usages", projectId] });
  };
  const action = useMutation({
    mutationFn: (path: string) => api.post(`${base}/${path}`),
    onSuccess: refresh,
  });
  const confirm = useConfirm();
  useEffect(() => {
    if (params.get("media")) {
      setOpenId(params.get("media")!);
      setSegment(null);
    }
  }, [params]);
  const open = detail.data;
  const filtered = list.data?.items || [];
  return (
    <div className="studio-page material-workspace">
      <MaterialTabs projectId={projectId} />
      <div className="page-heading mb-6">
        <div>
          <h1 className="text-xl font-semibold">
            {zh ? "文件与版本管理" : "Files & versions"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {zh
              ? "让视频、图片和声音成为创作的起点。保留原文件，随时预览、检索与引用。"
              : "Keep videos, images and audio together. Preview, find and reference them in your next creation."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setReuseOpen(true)}>
            {zh ? "从其他项目复用" : "Reuse from another project"}
          </Button>
          <Button onClick={() => setUpload({})}>
            <Plus className="h-4 w-4" />
            {zh ? "导入素材" : "Import media"}
          </Button>
        </div>
      </div>
      {params.get("media") && (detail.isError || detail.data?.deleted_at) && (
        <p
          role="status"
          className="mb-4 rounded-lg border p-3 text-sm text-muted-foreground"
        >
          {zh
            ? "原片当前不可用或已在回收站。独立制作的参考帧和剪辑片段仍保留。"
            : "The original is unavailable or in the trash. Independent frames and clips are retained."}
        </p>
      )}
      <div className="mb-5 flex gap-2 border-b pb-3">
        <Button
          variant={view === "videos" ? "default" : "ghost"}
          size="sm"
          onClick={() => setView("videos")}
        >
          {zh ? "我的素材" : "My media"}
        </Button>
        <Button
          variant={view === "search" ? "default" : "ghost"}
          size="sm"
          onClick={() => setView("search")}
        >
          {zh ? "搜索素材" : "Search media"}
        </Button>
        <Button
          variant={view === "processing" ? "default" : "ghost"}
          size="sm"
          onClick={() => setView("processing")}
        >
          {zh ? "素材处理" : "Processing"}
        </Button>
      </div>
      {view === "processing" ? (
        <LibraryProcessing projectId={projectId} />
      ) : view === "search" ? (
        <LibrarySearch
          projectId={projectId}
          onSelect={(r) => {
            setSegment(r);
            setOpenId(r.media_id);
          }}
        />
      ) : (
        <>
          <div
            className="mb-4 flex flex-wrap gap-2"
            role="group"
            aria-label={zh ? "素材类型" : "Media type"}
          >
            {(["all", "video", "image", "audio"] as const).map((kind) => (
              <Button
                key={kind}
                size="sm"
                variant={mediaType === kind ? "default" : "ghost"}
                aria-pressed={mediaType === kind}
                onClick={() => {
                  setMediaType(kind);
                  setPage(0);
                  setSelection({});
                }}
              >
                {kind === "all"
                  ? zh
                    ? "全部"
                    : "All"
                  : mediaTypeName(kind, zh)}
              </Button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={organizing}
              onClick={() => setOrganizing(!organizing)}
            >
              {zh ? "目录与批量管理" : "Folders & bulk actions"}
              {Object.keys(selection).length
                ? ` · ${Object.keys(selection).length}`
                : ""}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                navigate(`/projects/${projectId}/materials?tab=trash`);
                setSelection({});
                setPage(0);
              }}
            >
              {deleted
                ? zh
                  ? "返回素材"
                  : "Back to media"
                : zh
                  ? "回收站"
                  : "Trash"}
            </Button>
            {folder !== "all" && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => chooseFolder("all")}
              >
                {zh ? "查看全部目录" : "All folders"}
              </Button>
            )}
          </div>
          {organizing && (
            <LibraryOrganization
              projectId={projectId}
              value={folder}
              onSelect={chooseFolder}
              selection={selection}
              onClear={() => setSelection({})}
              onChange={refresh}
              onSelectPage={() =>
                setSelection((current) => {
                  const next = { ...current };
                  for (const item of filtered) {
                    if (Object.keys(next).length >= 100) break;
                    next[item.id] = item.folder_id ?? null;
                  }
                  return next;
                })
              }
            />
          )}
          {(!!list.data?.total || !!search || deleted || folder !== "all") && (
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-surface p-3">
              <div className="flex flex-wrap items-center gap-2">
                <SearchField
                  label={zh ? "搜索素材" : "Search media"}
                  value={search}
                  onValueChange={setSearch}
                  placeholder={
                    zh ? "搜索名称、说明或标签" : "Find media by name"
                  }
                  className="w-64 max-w-full"
                />
                <StudioSelect
                  aria-label={zh ? "素材排序" : "Sort media"}
                  className="h-9 rounded-md border bg-bg px-2 text-xs"
                  value={sort}
                  onValueChange={(selectedValue) => {
                    setSort(selectedValue);
                    setPage(0);
                  }}
                >
                  <option value="recent">{zh ? "最近导入" : "Newest"}</option>
                  <option value="oldest">{zh ? "最早导入" : "Oldest"}</option>
                  <option value="name">{zh ? "按名称" : "Name"}</option>
                </StudioSelect>
                <div
                  className="flex rounded-lg border p-0.5"
                  role="group"
                  aria-label={zh ? "素材显示方式" : "Media layout"}
                >
                  {(["grid", "list"] as const).map((mode) => (
                    <Button
                      key={mode}
                      size="sm"
                      variant={layout === mode ? "outline" : "ghost"}
                      aria-pressed={layout === mode}
                      aria-label={
                        mode === "grid"
                          ? zh
                            ? "网格视图"
                            : "Grid view"
                          : zh
                            ? "列表视图"
                            : "List view"
                      }
                      onClick={() => setLayout(mode)}
                    >
                      {mode === "grid" ? (
                        <LayoutGrid className="h-4 w-4" />
                      ) : (
                        <List className="h-4 w-4" />
                      )}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
          )}
          {action.error && (
            <p role="alert" className="mb-3 text-sm text-danger">
              {action.error.message}
            </p>
          )}
          {list.isPending ? (
            <div role="status" className="studio-empty">
              {zh ? "加载素材…" : "Loading media…"}
            </div>
          ) : list.isError ? (
            <div role="alert" className="studio-empty">
              <p>{zh ? "素材加载失败" : "Unable to load media"}</p>
              <Button variant="outline" onClick={() => void list.refetch()}>
                {zh ? "重试" : "Retry"}
              </Button>
            </div>
          ) : !filtered.length ? (
            <div className="studio-empty min-h-80 rounded-xl border border-dashed">
              <Film className="h-9 w-9" />
              <h2 className="text-base font-medium">
                {search
                  ? zh
                    ? "没有匹配的素材"
                    : "No matches"
                  : deleted
                    ? zh
                      ? "回收站为空"
                      : "Trash is empty"
                    : folder !== "all"
                      ? zh
                        ? "此目录还没有素材"
                        : "No media in this folder"
                      : zh
                        ? "导入第一份创作素材"
                        : "Bring in your first media"}
              </h2>
              <p className="max-w-md text-center text-sm">
                {zh
                  ? "上传后自动制作轻量预览；选择起止时间，即可引用到镜头。原有角色与场景仍在资产库中。"
                  : "Previews are prepared after upload. Select a time range and reference it from a shot. Characters and scenes remain in Assets."}
              </p>
              {!deleted && !search && (
                <Button onClick={() => setUpload({})}>
                  {zh ? "选择素材" : "Choose media"}
                </Button>
              )}
            </div>
          ) : (
            <>
              <p className="mb-3 text-xs text-muted-foreground">
                {zh
                  ? `共 ${list.data?.total || 0} 项 · 当前 ${page * 24 + 1}–${page * 24 + filtered.length} 项 · 引用固定到所选版本`
                  : `${list.data?.total || 0} results · ${page * 24 + 1}–${page * 24 + filtered.length} shown · version-pinned references`}
              </p>
              <div
                className={
                  layout === "grid"
                    ? "grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
                    : "space-y-3"
                }
              >
                {filtered.map((item) => {
                  const v = item.versions[0],
                    ready = item.versions.find((v) => v.status === "ready");
                  const kind = (ready || v)?.media_type || "video";
                  const cover =
                    ready?.poster_hash ||
                    (kind === "image" ? ready?.proxy_hash : null);
                  const KindIcon =
                    kind === "audio"
                      ? AudioLines
                      : kind === "image"
                        ? ImageIcon
                        : Film;
                  return (
                    <article
                      className={`group overflow-hidden rounded-xl border bg-card transition-colors hover:border-primary/40 ${layout === "list" ? "sm:grid sm:grid-cols-[220px_minmax(0,1fr)]" : ""}`}
                      key={item.id}
                    >
                      <div className="relative aspect-video bg-elevated">
                        {organizing && (
                          <label className="absolute left-2 top-2 z-10 flex items-center rounded bg-background/90 p-2 shadow-sm">
                            <input
                              type="checkbox"
                              aria-label={`${zh ? "选择" : "Select"} ${item.name}`}
                              checked={item.id in selection}
                              disabled={
                                !(item.id in selection) &&
                                Object.keys(selection).length >= 100
                              }
                              onChange={(e) =>
                                setSelection((current) => {
                                  const next = { ...current };
                                  if (e.target.checked)
                                    next[item.id] = item.folder_id ?? null;
                                  else delete next[item.id];
                                  return next;
                                })
                              }
                            />
                          </label>
                        )}
                        {!deleted && cover ? (
                          <MediaImage
                            src={blobUrl(projectId, cover)}
                            alt={item.name}
                            className={
                              kind === "image"
                                ? "[&_img]:object-contain"
                                : undefined
                            }
                          />
                        ) : (
                          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                            <KindIcon
                              className={
                                kind === "audio" ? "h-12 w-12" : "h-5 w-5"
                              }
                            />
                            {deleted
                              ? zh
                                ? "已移入回收站"
                                : "In trash"
                              : mediaStatus(v?.status || "uploading", zh)}
                          </div>
                        )}
                        {!deleted && ready && (
                          <button
                            className="absolute inset-0 grid place-items-center bg-black/0 transition-colors hover:bg-black/15 focus-visible:bg-black/20"
                            aria-label={`${zh ? "预览" : "Preview"} ${item.name}`}
                            onClick={() => {
                              setSegment(null);
                              setOpenId(item.id);
                            }}
                          >
                            <span className="grid h-11 w-11 place-items-center rounded-full border border-white/30 bg-black/45 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                              {kind === "image" ? (
                                <ImageIcon className="h-5 w-5" />
                              ) : (
                                <Play className="h-5 w-5" />
                              )}
                            </span>
                          </button>
                        )}
                        {ready?.duration_ms != null && kind !== "image" && (
                          <span className="pointer-events-none absolute bottom-2 right-2 rounded bg-black/70 px-2 py-0.5 text-[11px] tabular-nums text-white">
                            {mediaTime(ready.duration_ms)}
                          </span>
                        )}
                      </div>
                      <div className="min-w-0 space-y-3 p-4">
                        <h2
                          className="truncate text-sm font-semibold"
                          title={item.name}
                        >
                          {item.name}
                        </h2>
                        <p className="text-xs text-muted-foreground">
                          {v
                            ? `${mediaTypeName(kind, zh)} · ${mediaSize(v.size_bytes)} · v${v.ordinal} · ${mediaStatus(v.status, zh)}`
                            : ""}
                          {ready?.duration_ms
                            ? ` · ${mediaTime(ready.duration_ms)}`
                            : ""}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {zh
                            ? `${item.usage_count} 处镜头引用 · ${item.versions.length} 个版本`
                            : `${item.usage_count} references · ${item.versions.length} versions`}
                        </p>
                        {v?.error && (
                          <p role="alert" className="text-xs text-danger">
                            {v.error}
                          </p>
                        )}
                        <div className="flex flex-wrap gap-2">
                          {deleted ? (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={action.isPending}
                              onClick={() =>
                                action.mutate(`${item.id}/restore`)
                              }
                            >
                              {zh ? "恢复素材" : "Restore"}
                            </Button>
                          ) : (
                            <>
                              {ready && (
                                <Button
                                  size="sm"
                                  onClick={() => {
                                    setSegment(null);
                                    setOpenId(item.id);
                                  }}
                                >
                                  {zh ? "预览与引用" : "Preview & reference"}
                                </Button>
                              )}
                              {v?.status === "uploading" && (
                                <Button
                                  size="sm"
                                  onClick={() => setUpload({ resume: v })}
                                >
                                  {zh ? "继续上传" : "Resume upload"}
                                </Button>
                              )}
                              {v?.status === "failed" && (
                                <Button
                                  size="sm"
                                  disabled={action.isPending}
                                  onClick={() =>
                                    action.mutate(`versions/${v.id}/retry`)
                                  }
                                >
                                  {zh ? "重试处理" : "Retry processing"}
                                </Button>
                              )}
                              {(v?.status === "uploading" ||
                                v?.status === "failed") && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={action.isPending}
                                  onClick={async () => {
                                    if (
                                      await confirm({
                                        message: zh
                                          ? "取消此上传版本并清理暂存文件？已入库原片不会被删除。"
                                          : "Cancel this version and remove its staging file? Stored originals remain.",
                                      })
                                    )
                                      action.mutate(`versions/${v.id}/cancel`);
                                  }}
                                >
                                  {zh ? "取消此版本" : "Cancel version"}
                                </Button>
                              )}
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setImpactId(item.id)}
                              >
                                {zh ? "引用与删除" : "Usage & deletion"}
                              </Button>
                            </>
                          )}
                        </div>
                        {!deleted && ready && (
                          <>
                            <LibraryProcessingControl
                              projectId={projectId}
                              versionId={ready.id}
                              mediaType={kind}
                              hasAudio={ready.has_audio}
                            />
                            {kind === "video" && (
                              <details className="mt-3 text-xs text-muted-foreground">
                                <summary className="cursor-pointer">
                                  {zh ? "基础画面索引" : "Basic visual index"}
                                </summary>
                                <LibraryIndexControl
                                  projectId={projectId}
                                  versionId={ready.id}
                                />
                              </details>
                            )}
                          </>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
              {(list.data?.total || 0) > 24 && (
                <div className="mt-5 flex items-center justify-between gap-3 border-t pt-4">
                  <Button
                    variant="outline"
                    disabled={page === 0}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    {zh ? "上一页" : "Previous"}
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    {page + 1} / {Math.ceil((list.data?.total || 0) / 24)}
                  </span>
                  <Button
                    variant="outline"
                    disabled={(page + 1) * 24 >= (list.data?.total || 0)}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    {zh ? "下一页" : "Next"}
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      )}
      {upload && (
        <LibraryUpload
          key={upload.resume?.id || upload.mediaId || "new"}
          projectId={projectId}
          folderId={folderId}
          {...upload}
          onClose={() => {
            setUpload(null);
            refresh();
          }}
          onChange={refresh}
        />
      )}
      {detail.isLoading && openId && (
        <p role="status" className="text-sm text-muted-foreground">
          {zh ? "正在读取素材版本…" : "Loading media versions…"}
        </p>
      )}
      {detail.isError && openId && (
        <p role="alert" className="text-sm text-danger">
          {detail.error.message}
          <Button variant="ghost" onClick={() => void detail.refetch()}>
            {zh ? "重试" : "Retry"}
          </Button>
        </p>
      )}
      {open && !open.deleted_at && !deleted && (
        <LibraryDetail
          key={`${open.id}.${segment?.id || params.get("version") || "detail"}`}
          projectId={projectId}
          item={open}
          segment={segment}
          onClose={() => {
            setOpenId("");
            if (params.has("media")) {
              const next = new URLSearchParams(params);
              for (const key of ["media", "version", "start", "end"])
                next.delete(key);
              setParams(next, { replace: true });
            }
          }}
          onUpload={() =>
            setUpload({
              mediaId: open.id,
              mediaType: open.versions[0]?.media_type || "video",
            })
          }
          onChange={refresh}
        />
      )}
      {reuseOpen && (
        <LibraryReuse
          projectId={projectId}
          folderId={folderId}
          onClose={() => setReuseOpen(false)}
          onDone={(result) => {
            setReuseOpen(false);
            setView("videos");
            refresh();
            const next = new URLSearchParams(params);
            next.set("media", result.media_id);
            next.set("version", result.version_id);
            next.delete("start");
            next.delete("end");
            setParams(next);
            setOpenId(result.media_id);
          }}
        />
      )}
      {impactId && (
        <ImpactDialog
          projectId={projectId}
          mediaId={impactId}
          onClose={() => setImpactId("")}
          onChange={refresh}
        />
      )}
    </div>
  );
}

export function LibraryDetail({
  projectId,
  item,
  onClose,
  onUpload,
  onChange,
  segment,
}: {
  projectId: string;
  item: LibraryMedia;
  onClose: () => void;
  onUpload: () => void;
  onChange: () => void;
  segment?: SegmentResult | null;
}) {
  const zh = useI18n().lang === "zh",
    [params] = useSearchParams();
  const location = useLocation();
  const returnPath = location.pathname + location.search;
  const [versionId, setVersionId] = useState(
    segment?.version_id ||
      (params.get("media") === item.id ? params.get("version") : null) ||
      item.versions.find((v) => v.status === "ready")?.id ||
      item.versions[0]?.id ||
      "",
  );
  const v = item.versions.find((v) => v.id === versionId);
  const kind = v?.media_type || "video";
  const [matchId, setMatchId] = useState(segment?.matches?.[0]?.id || "");
  const selectedMatch =
    segment?.version_id === versionId
      ? segment?.matches?.find((match) => match.id === matchId)
      : undefined;
  const isImage = kind === "image";
  const linked = params.get("media") === item.id;
  const linkedTime = (key: string, fallback: number) => {
    const value = Number(params.get(key));
    return linked && params.has(key) && Number.isFinite(value) && value >= 0
      ? value
      : fallback;
  };
  const [start, setStart] = useState(
      String((segment?.start_ms ?? linkedTime("start", 0)) / 1000),
    ),
    [end, setEnd] = useState(
      String(
        (segment?.end_ms ?? linkedTime("end", v?.duration_ms || 0)) / 1000,
      ),
    );
  const [shotId, setShotId] = useState(params.get("shot") || ""),
    [purpose, setPurpose] = useState(
      v?.media_type === "audio" ? "editing_source" : "visual_reference",
    ),
    [note, setNote] = useState(segment?.description || "");
  const [enlarged, setEnlarged] = useState(false);
  const [annotating, setAnnotating] = useState(false);
  const [using, setUsing] = useState(Boolean(params.get("shot")));
  const shots = useQuery({
    enabled: using,
    queryKey: ["all-shots", projectId],
    queryFn: () => api.get<Shot[]>(`/projects/${projectId}/shots`),
  });
  const selectedShot = shots.data?.some((s) => s.id === shotId) ? shotId : "";
  const startMs = Math.round(Number(start) * 1000),
    endMs = Math.round(Number(end) * 1000);
  const valid =
    isImage ||
    (start !== "" &&
      end !== "" &&
      Number.isFinite(startMs) &&
      Number.isFinite(endMs) &&
      startMs >= 0 &&
      endMs - startMs >= 100 &&
      endMs <= (v?.duration_ms || 0));
  const reference = useMutation({
    mutationFn: () =>
      api.post(`/projects/${projectId}/library/usages`, {
        version_id: versionId,
        shot_id: selectedShot,
        start_ms: isImage ? null : startMs,
        end_ms: isImage ? null : endMs,
        purpose,
        note,
      }),
    onSuccess: onChange,
  });
  return (
    <>
      <Modal
        open
        title={item.name}
        onClose={onClose}
        width={1120}
        bodyClassName="library-viewer-body"
      >
        <div className="library-viewer">
          <section
            className="library-viewer-media"
            aria-label={zh ? "素材预览" : "Media preview"}
          >
            <div className="library-preview-stage">
              {v?.proxy_hash ? (
                <LibraryMediaPreview
                  showRangeControls={using || Boolean(segment)}
                  mediaType={kind}
                  key={v.id}
                  src={blobUrl(projectId, v.proxy_hash)}
                  start={startMs || 0}
                  end={endMs || 0}
                  label={item.name}
                  region={selectedMatch}
                  onMark={
                    using
                      ? (kind, ms) => {
                          if (kind === "start") setStart(String(ms / 1000));
                          else setEnd(String(ms / 1000));
                          reference.reset();
                        }
                      : undefined
                  }
                />
              ) : (
                <div className="library-preview-empty" role="status">
                  <ImageIcon size={32} strokeWidth={1.25} />
                  <p>
                    {v
                      ? mediaStatus(v.status, zh)
                      : zh
                        ? "此版本不可用"
                        : "Version unavailable"}
                  </p>
                  <span>
                    {v?.status === "failed"
                      ? zh
                        ? "请重新上传或稍后重试。"
                        : "Upload again or try later."
                      : zh
                        ? "预览准备好后将在这里显示。"
                        : "Your preview will appear here when ready."}
                  </span>
                </div>
              )}
            </div>
            <div className="library-preview-caption">
              <span>{zh ? "预览 · 保留完整画面" : "Preview · Full frame"}</span>
              {isImage && v?.proxy_hash && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEnlarged(true)}
                >
                  <Maximize2 size={14} />
                  {zh ? "放大查看" : "Enlarge"}
                </Button>
              )}
            </div>
          </section>
          <aside
            className="library-viewer-info"
            aria-label={zh ? "素材信息与操作" : "Media details and actions"}
          >
            <div className="library-viewer-heading">
              <span>{v ? mediaTypeName(kind, zh) : zh ? "素材" : "Media"}</span>
              <h3>{item.name}</h3>
            </div>
            <MaterialProfile
              projectId={projectId}
              source="upload"
              item={item}
              video={
                v?.status === "ready" &&
                v.media_type === "video" &&
                (v.proxy_hash || v.original_hash)
                  ? {
                      src: blobUrl(
                        projectId,
                        (v.proxy_hash || v.original_hash)!,
                      ),
                      versionId: v.id,
                    }
                  : undefined
              }
            />
            {!v && (
              <p role="alert" className="text-xs text-danger">
                {zh
                  ? "链接中的原片版本不可用，请检查版本记录。"
                  : "The linked source version is unavailable."}
              </p>
            )}
            <div className="library-viewer-version">
              {item.versions.length > 1 ? (
                <SelectField
                  label={zh ? "素材版本" : "Media version"}
                  value={versionId}
                  options={item.versions.map((version) => ({
                    value: version.id,
                    label: `v${version.ordinal} · ${mediaStatus(version.status, zh)}`,
                    disabled: version.status !== "ready",
                  }))}
                  onChange={(value) => {
                    const next = item.versions.find(
                      (version) => version.id === value,
                    );
                    setVersionId(value);
                    setStart("0");
                    setEnd(String((next?.duration_ms || 0) / 1000));
                    setPurpose(
                      next?.media_type === "audio"
                        ? "editing_source"
                        : "visual_reference",
                    );
                    reference.reset();
                  }}
                />
              ) : (
                v && (
                  <span className="library-version-label">
                    v{v.ordinal}
                    <span>·</span>
                    {mediaStatus(v.status, zh)}
                  </span>
                )
              )}
            </div>
            {v && (
              <dl className="library-viewer-facts">
                <div className="library-original-filename">
                  <dt>{zh ? "原文件名" : "Original filename"}</dt>
                  <dd title={v.filename}>{v.filename}</dd>
                </div>
                <div>
                  <dt>{zh ? "文件大小" : "File size"}</dt>
                  <dd>
                    {v.size_bytes < 1024 * 1024
                      ? `${Math.max(1, Math.round(v.size_bytes / 1024))} KB`
                      : mediaSize(v.size_bytes)}
                  </dd>
                </div>
                {!!v.width && !!v.height && (
                  <div>
                    <dt>{zh ? "尺寸" : "Dimensions"}</dt>
                    <dd>
                      {v.width} × {v.height}
                    </dd>
                  </div>
                )}
                {!isImage && v.duration_ms != null && (
                  <div>
                    <dt>{zh ? "时长" : "Duration"}</dt>
                    <dd>{mediaTime(v.duration_ms)}</dd>
                  </div>
                )}
                <div>
                  <dt>{zh ? "已被引用" : "References"}</dt>
                  <dd>
                    {item.usage_count}
                    {zh ? " 次" : ""}
                  </dd>
                </div>
              </dl>
            )}
            <div className="library-viewer-actions">
              <Button
                disabled={!v || v.status !== "ready"}
                variant={using ? "outline" : "default"}
                aria-expanded={using}
                aria-controls="library-use-panel"
                onClick={() => setUsing(!using)}
              >
                <Film size={16} />
                {using
                  ? zh
                    ? "收起引用设置"
                    : "Hide reference settings"
                  : zh
                    ? "用到镜头"
                    : "Use in a shot"}
              </Button>
              {v?.original_hash && (
                <a
                  className="library-download"
                  href={`${blobUrl(projectId, v.original_hash)}&download=true`}
                  download
                >
                  <Download size={15} />
                  {zh ? "下载原文件" : "Download original"}
                </a>
              )}
            </div>
            <div
              id="library-use-panel"
              hidden={!using}
              className="library-use-panel space-y-4"
            >
              <h3 className="text-sm font-medium">
                {isImage
                  ? zh
                    ? "将图片用到镜头中"
                    : "Reference this image in a shot"
                  : zh
                    ? "选一个片段，用到镜头中"
                    : "Reference a segment in a shot"}
              </h3>
              {isImage ? (
                <p className="rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">
                  {zh
                    ? "引用整张图片，无需设置起止时间。"
                    : "Use the whole image. No time range is needed."}
                </p>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  {[
                    [start, setStart, zh ? "入点（秒）" : "Start (seconds)"],
                    [end, setEnd, zh ? "出点（秒）" : "End (seconds)"],
                  ].map(([value, setter, label], i) => (
                    <label className="space-y-1 text-xs" key={i}>
                      <span>{label as string}</span>
                      <input
                        aria-label={label as string}
                        type="number"
                        min={0}
                        step={0.1}
                        value={value as string}
                        onChange={(e) => {
                          (setter as (v: string) => void)(e.target.value);
                          reference.reset();
                        }}
                        className="h-9 w-full rounded-md border bg-bg px-2 text-sm"
                      />
                    </label>
                  ))}
                </div>
              )}
              {!valid && (
                <p role="alert" className="text-xs text-danger">
                  {zh
                    ? "请选择素材范围内至少0.1秒的片段。"
                    : "Choose at least 0.1 seconds within the media."}
                </p>
              )}
              <div className="space-y-2">
                <MaterialShotPicker
                  projectId={projectId}
                  shots={shots.data || []}
                  selected={selectedShot}
                  loading={shots.isPending}
                  error={shots.isError}
                  onRetry={() => void shots.refetch()}
                  onSelect={(id) => {
                    setShotId(id);
                    reference.reset();
                  }}
                />
              </div>
              {shots.isError && (
                <button
                  className="studio-link text-xs"
                  onClick={() => void shots.refetch()}
                >
                  {zh ? "镜头加载失败，重试" : "Failed to load shots. Retry"}
                </button>
              )}
              <label className="block space-y-1 text-xs">
                <span>{zh ? "用途" : "Purpose"}</span>
                <SelectField
                  label={zh ? "片段用途" : "Segment purpose"}
                  value={purpose}
                  onChange={(value) => {
                    setPurpose(value);
                    reference.reset();
                  }}
                  options={[
                    ...(kind !== "audio"
                      ? [
                          {
                            value: "visual_reference",
                            label: zh ? "画面参考" : "Visual reference",
                          },
                        ]
                      : []),
                    {
                      value: "editing_source",
                      label: zh ? "剪辑备选" : "Editing source",
                    },
                  ]}
                />
              </label>
              <textarea
                aria-label={zh ? "片段备注" : "Segment note"}
                placeholder={
                  zh
                    ? "例如：说明这份素材在镜头中的用途"
                    : "What should this segment inform?"
                }
                maxLength={1000}
                rows={3}
                value={note}
                onChange={(e) => {
                  setNote(e.target.value);
                  reference.reset();
                }}
                className="w-full rounded-md border bg-bg p-2 text-sm"
              />
              <p className="text-xs text-muted-foreground">
                {zh
                  ? "保存后可在镜头详情中查看；不会自动加入时间线或发送给AI。"
                  : "View it from the shot details. This does not add it to the timeline or send it to AI."}
              </p>
              <Button
                disabled={
                  !valid ||
                  !selectedShot ||
                  v?.status !== "ready" ||
                  reference.isPending
                }
                onClick={() => reference.mutate()}
              >
                {reference.isPending
                  ? zh
                    ? "保存中…"
                    : "Saving…"
                  : zh
                    ? "引用到镜头"
                    : "Reference in shot"}
              </Button>
              {reference.error && (
                <p role="alert" className="text-xs text-danger">
                  {reference.error.message}
                </p>
              )}
              {reference.isSuccess && (
                <p role="status" className="text-sm text-primary">
                  {zh ? "引用已保存。" : "Reference saved."}{" "}
                  <Link
                    className="underline"
                    to={`/projects/${projectId}/shots?shot=${selectedShot}`}
                  >
                    {zh ? "打开镜头" : "Open shot"} →
                  </Link>
                </p>
              )}
            </div>
            {segment?.version_id === versionId && !!segment.matches?.length && (
              <div className="space-y-2 rounded-lg border bg-surface p-3">
                <p className="text-xs text-muted-foreground">
                  {zh
                    ? "选择一个命中区域核对。保留的区域仅来自本次有限候选集；区域框不会改变原图或引用范围。"
                    : "Inspect a region from this search's bounded candidate set. Boxes do not crop the source or change its reference."}
                </p>
                <div
                  role="group"
                  aria-label={zh ? "命中区域" : "Matched regions"}
                  className="flex max-h-36 flex-wrap gap-2 overflow-y-auto pr-1"
                >
                  {segment.matches.map((match, index) => (
                    <Button
                      key={match.id}
                      size="sm"
                      variant={match.id === matchId ? "default" : "outline"}
                      aria-pressed={match.id === matchId}
                      onClick={() => {
                        setMatchId(match.id);
                        if (!isImage) {
                          setStart(String((match.start_ms || 0) / 1000));
                          setEnd(String((match.end_ms || 0) / 1000));
                        }
                        reference.reset();
                      }}
                    >
                      {zh ? `区域 ${index + 1}` : `Region ${index + 1}`}
                      {match.matched_at_ms !== null
                        ? ` · ${mediaTime(match.matched_at_ms)}`
                        : ""}
                    </Button>
                  ))}
                </div>
              </div>
            )}
            {v?.reuse_origin && (
              <p className="rounded-lg border bg-muted/40 p-3 text-xs leading-6">
                {zh
                  ? `跨项目复用：${v.reuse_origin.name} · 来源 v${v.reuse_origin.ordinal}。本项目使用固定版本，不自动跟随原片更新。`
                  : `Reused source: ${v.reuse_origin.name} · v${v.reuse_origin.ordinal}. This project retains a fixed version.`}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {zh
                ? "正在查看轻量预览。引用始终指向原文件版本；新增版本不会替换已有引用。"
                : "Viewing a lightweight preview. References point to the original version and never follow newer uploads automatically."}
            </p>
            {segment?.match_type === "frame" &&
              segment.version_id === versionId && (
                <p className="rounded-lg border bg-surface px-3 py-2 text-xs leading-6 text-muted-foreground">
                  {zh
                    ? `定位到 ${mediaTime(selectedMatch?.matched_at_ms ?? segment.matched_at_ms ?? segment.start_ms)} 的采样画面。当前是定位预览，请播放前后内容，调整引用的起止时间。`
                    : `Matched a sampled frame at ${mediaTime(selectedMatch?.matched_at_ms ?? segment.matched_at_ms ?? segment.start_ms)}. Play the surrounding footage and adjust the range before referencing it.`}
                </p>
              )}
            <section
              className="library-tool-list"
              aria-label={zh ? "素材工具" : "Media tools"}
            >
              <h4>{zh ? "素材工具" : "Media tools"}</h4>
              {v?.status === "ready" && !isImage && (
                <button
                  className="library-tool-row"
                  onClick={() => setAnnotating(true)}
                >
                  <Tags size={16} />
                  <span>{zh ? "标注与字幕" : "Annotations & subtitles"}</span>
                  <ChevronRight size={14} />
                </button>
              )}
              {v && kind === "video" && valid && (
                <Link
                  className="library-tool-row"
                  to={`/projects/${projectId}/evidence?version=${v.id}&start=${startMs}&end=${endMs}&return=${encodeURIComponent(returnPath)}`}
                >
                  <UserRound size={16} />
                  <span>{zh ? "标注画面人物" : "Identify people"}</span>
                  <ChevronRight size={14} />
                </Link>
              )}
              {v?.status === "ready" &&
                kind === "video" &&
                v.has_audio !== false && (
                  <Link
                    className="library-tool-row"
                    to={`/projects/${projectId}/transcriptions?version=${v.id}`}
                  >
                    <Captions size={16} />
                    <span>
                      {zh ? "转写与编辑字幕" : "Transcribe & edit captions"}
                    </span>
                    <ChevronRight size={14} />
                  </Link>
                )}
              <button
                className="library-tool-row"
                disabled={item.versions.some((version) =>
                  ["uploading", "queued", "processing"].includes(
                    version.status,
                  ),
                )}
                onClick={onUpload}
              >
                <Upload size={16} />
                <span>{zh ? "上传新版本" : "Upload new version"}</span>
                <ChevronRight size={14} />
              </button>
            </section>
          </aside>
        </div>
      </Modal>
      {annotating && v && (
        <Modal
          open
          title={zh ? "标注与字幕" : "Annotations & subtitles"}
          width={880}
          onClose={() => setAnnotating(false)}
        >
          <p className="library-annotation-source">
            {item.name} · v{v.ordinal}
          </p>
          <LibraryAnnotation
            projectId={projectId}
            versionId={v.id}
            mediaType={kind}
            start={startMs}
            end={endMs}
            duration={v.duration_ms || 0}
          />
        </Modal>
      )}
      {enlarged && v?.proxy_hash && (
        <Lightbox
          src={blobUrl(projectId, v.proxy_hash)}
          alt={item.name}
          filename={v.filename}
          onClose={() => setEnlarged(false)}
        />
      )}
    </>
  );
}

export function ImpactDialog({
  projectId,
  mediaId,
  onClose,
  onChange,
}: {
  projectId: string;
  mediaId: string;
  onClose: () => void;
  onChange: () => void;
}) {
  const zh = useI18n().lang === "zh",
    base = `/projects/${projectId}/library`,
    confirm = useConfirm();
  const impact = useQuery({
    queryKey: ["library-impact", projectId, mediaId],
    queryFn: () =>
      api.get<DeletionImpact>(`${base}/${mediaId}/deletion-impact`),
  });
  const trash = useMutation({
    mutationFn: () => api.post(`${base}/${mediaId}/trash`),
    onSuccess: () => {
      onChange();
      onClose();
    },
    onError: () => void impact.refetch(),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`${base}/usages/${id}`),
    onSuccess: () => {
      onChange();
      void impact.refetch();
    },
  });
  return (
    <Modal
      open
      title={zh ? "引用与删除影响" : "Usage & deletion impact"}
      onClose={onClose}
      width={650}
    >
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {zh
            ? "移入回收站前，会重新检查所有版本的镜头引用和上传/处理任务。回收站可恢复，原片与预览文件暂不物理删除。"
            : "All version usages and active tasks are checked again before moving to trash. Trash is recoverable; files are retained."}
        </p>
        {impact.isPending ? (
          <p>{zh ? "检查中…" : "Checking…"}</p>
        ) : impact.isError ? (
          <button className="studio-link" onClick={() => void impact.refetch()}>
            {zh ? "检查失败，重试" : "Check failed. Retry"}
          </button>
        ) : (
          <>
            <p className="text-sm">
              {zh
                ? `${impact.data.references.length} 处引用 · ${impact.data.active_versions.length} 个上传/处理任务`
                : `${impact.data.references.length} references · ${impact.data.active_versions.length} active tasks`}
            </p>
            {impact.data.references.map((r) => (
              <div
                className="space-y-2 rounded-lg border p-3 text-xs"
                key={r.id}
              >
                <p>
                  {r.shot_title} · v{r.ordinal} ·{" "}
                  {r.media_type === "image"
                    ? zh
                      ? "整张图片"
                      : "Whole image"
                    : `${mediaTime(r.start_ms)} — ${mediaTime(r.end_ms)}`}{" "}
                  {r.shot_deleted
                    ? zh
                      ? "（镜头已删除，引用仍保留）"
                      : "(shot deleted, reference retained)"
                    : ""}
                </p>
                <button
                  disabled={remove.isPending}
                  className="studio-link"
                  onClick={async () => {
                    if (
                      await confirm({
                        message: zh
                          ? "解除这处片段引用？原素材仍保留。"
                          : "Remove this usage? The original media remains.",
                      })
                    )
                      remove.mutate(r.id);
                  }}
                >
                  {zh ? "解除这处引用" : "Remove this reference"}
                </button>
              </div>
            ))}
            {!!impact.data.evidence_count && (
              <p className="text-sm">
                {zh
                  ? `另有 ${impact.data.evidence_count} 条身份判断依赖此原片。`
                  : `${impact.data.evidence_count} identity evidence records depend on this source.`}{" "}
                <Link
                  className="studio-link"
                  to={`/projects/${projectId}/evidence`}
                >
                  {zh ? "查看证据" : "Review evidence"}
                </Link>
              </p>
            )}
            {!!impact.data.shared_count && (
              <div className="space-y-2 rounded-lg border p-3 text-sm">
                <p>
                  {zh
                    ? `另有 ${impact.data.shared_count} 份跨项目素材依赖原片，需先在对应项目移入回收站。`
                    : `${impact.data.shared_count} reused project items retain this source. Move them to trash in their projects first.`}
                </p>
                {impact.data.reuses?.map((r, i) =>
                  r.project_id && r.media_id ? (
                    <Link
                      key={i}
                      className="studio-link block text-xs"
                      to={`/projects/${r.project_id}/library?media=${r.media_id}&version=${r.version_id}`}
                    >
                      {r.project_name} · {zh ? "来源版本" : "Source version"}{" "}
                      {r.source_ordinal} →
                    </Link>
                  ) : (
                    <p key={i} className="text-xs text-muted-foreground">
                      {zh
                        ? "其他项目（暂无访问权限或项目已归档）"
                        : "Another project (unavailable or archived)"}
                    </p>
                  ),
                )}
                {(impact.data.reuses?.length ?? 0) <
                  impact.data.shared_count && (
                  <p className="text-xs text-muted-foreground">
                    {zh
                      ? "这里只显示前100份复用记录，删除检查会覆盖全部。"
                      : "Showing up to 100 reuse records; deletion checks cover all."}
                  </p>
                )}
              </div>
            )}
            <Button
              variant="outline"
              disabled={
                !impact.data.can_trash || trash.isPending || remove.isPending
              }
              onClick={async () => {
                if (
                  await confirm({
                    message: zh
                      ? "确认将此素材及其版本移入回收站？可稍后恢复。"
                      : "Move this media and its versions to trash? You can restore them later.",
                  })
                )
                  trash.mutate();
              }}
            >
              {zh ? "移入回收站" : "Move to trash"}
            </Button>
          </>
        )}
        {(trash.error || remove.error) && (
          <p role="alert" className="text-sm text-danger">
            {(trash.error || remove.error)?.message}
          </p>
        )}
      </div>
    </Modal>
  );
}
