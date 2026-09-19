import { MaterialTabs } from "@/components/MaterialTabs";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Film, Plus, LayoutGrid, List, Play } from "lucide-react";
import { usePersistentState } from "@/lib/usePersistentState";
import { api, blobUrl, type Shot } from "@/lib/api";
import {
  mediaSize,
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
  LibrarySearch,
  LibraryIndexControl,
  LibraryAnnotation,
  type SegmentResult,
} from "@/components/LibrarySearch";
import { MediaSegmentPlayer } from "@/components/MediaSegmentPlayer";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";

export default function MediaLibrary() {
  const { projectId } = useParams();
  return <LibraryContent key={projectId} projectId={projectId!} />;
}
function LibraryContent({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
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
  const [deleted, setDeleted] = useState(false),
    [search, setSearch] = useState("");
  const [query, setQuery] = useState(""),
    [page, setPage] = useState(0),
    [sort, setSort] = useState("recent");
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
  const [view, setView] = useState<"videos" | "search">("videos");
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
  } | null>(params.get("import") === "1" ? {} : null);
  const list = useQuery({
    queryKey: [
      "library",
      projectId,
      "catalog",
      deleted,
      query,
      page,
      sort,
      folder,
    ],
    queryFn: () =>
      api.get<{ items: LibraryMedia[]; total: number }>(
        `${base}/catalog?${new URLSearchParams({ deleted: String(deleted), query, offset: String(page * 24), sort, ...(folderId ? { folder_id: folderId } : folder === "root" ? { root_only: "true" } : {}) })}`,
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
            {zh ? "视频素材库" : "Video library"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {zh
              ? "保留原片，按片段引用。让拍摄素材成为下一次创作的起点。"
              : "Keep originals and reference precise segments in your next creation."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setReuseOpen(true)}>
            {zh ? "从其他项目复用" : "Reuse from another project"}
          </Button>
          <Button onClick={() => setUpload({})}>
            <Plus className="h-4 w-4" />
            {zh ? "导入视频" : "Import video"}
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
          {zh ? "我的视频" : "My videos"}
        </Button>
        <Button
          variant={view === "search" ? "default" : "ghost"}
          size="sm"
          onClick={() => setView("search")}
        >
          {zh ? "查找画面" : "Find scenes"}
        </Button>
      </div>
      {view === "search" ? (
        <LibrarySearch
          projectId={projectId}
          onSelect={(r) => {
            setSegment(r);
            setDeleted(false);
            setOpenId(r.media_id);
          }}
        />
      ) : (
        <>
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
                setDeleted(!deleted);
                setSelection({});
                setPage(0);
              }}
            >
              {deleted
                ? zh
                  ? "返回视频"
                  : "Back to videos"
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
                <input
                  aria-label={zh ? "搜索视频" : "Search videos"}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={zh ? "按名称查找视频…" : "Find videos by name…"}
                  className="h-9 max-w-full rounded-md border bg-bg px-3 text-sm"
                />
                <select
                  aria-label={zh ? "视频排序" : "Sort videos"}
                  className="h-9 rounded-md border bg-bg px-2 text-xs"
                  value={sort}
                  onChange={(e) => {
                    setSort(e.target.value);
                    setPage(0);
                  }}
                >
                  <option value="recent">{zh ? "最近导入" : "Newest"}</option>
                  <option value="oldest">{zh ? "最早导入" : "Oldest"}</option>
                  <option value="name">{zh ? "按名称" : "Name"}</option>
                </select>
                <div
                  className="flex rounded-lg border p-0.5"
                  role="group"
                  aria-label={zh ? "视频显示方式" : "Video layout"}
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
              {zh ? "加载视频素材…" : "Loading videos…"}
            </div>
          ) : list.isError ? (
            <div role="alert" className="studio-empty">
              <p>{zh ? "视频素材加载失败" : "Unable to load videos"}</p>
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
                    ? "没有匹配的视频"
                    : "No matches"
                  : deleted
                    ? zh
                      ? "回收站为空"
                      : "Trash is empty"
                    : folder !== "all"
                      ? zh
                        ? "此目录还没有视频"
                        : "No videos in this folder"
                      : zh
                        ? "把第一段视频放进来"
                        : "Bring in your first video"}
              </h2>
              <p className="max-w-md text-center text-sm">
                {zh
                  ? "上传后自动制作轻量预览；选择起止时间，即可引用到镜头。原有角色与场景仍在资产库中。"
                  : "Previews are prepared after upload. Select a time range and reference it from a shot. Characters and scenes remain in Assets."}
              </p>
              {!deleted && !search && (
                <Button onClick={() => setUpload({})}>
                  {zh ? "选择视频" : "Choose video"}
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
                        {!deleted && ready?.poster_hash ? (
                          <MediaImage
                            src={blobUrl(projectId, ready.poster_hash)}
                            alt={item.name}
                          />
                        ) : (
                          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                            <Film className="h-5 w-5" />
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
                              <Play className="h-5 w-5" />
                            </span>
                          </button>
                        )}
                        {ready?.duration_ms && (
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
                            ? `${mediaSize(v.size_bytes)} · v${v.ordinal} · ${mediaStatus(v.status, zh)}`
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
                              {zh ? "恢复视频" : "Restore"}
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
                          <LibraryIndexControl
                            projectId={projectId}
                            versionId={ready.id}
                          />
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
          {zh ? "正在读取视频版本…" : "Loading video versions…"}
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
          onUpload={() => setUpload({ mediaId: open.id })}
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
            setDeleted(false);
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

function LibraryDetail({
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
  const [versionId, setVersionId] = useState(
    segment?.version_id ||
      (params.get("media") === item.id ? params.get("version") : null) ||
      item.versions.find((v) => v.status === "ready")?.id ||
      "",
  );
  const v = item.versions.find((v) => v.id === versionId);
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
    [purpose, setPurpose] = useState("visual_reference"),
    [note, setNote] = useState(segment?.description || "");
  const shots = useQuery({
    queryKey: ["all-shots", projectId],
    queryFn: () => api.get<Shot[]>(`/projects/${projectId}/shots`),
  });
  const selectedShot = shots.data?.some((s) => s.id === shotId) ? shotId : "";
  const startMs = Math.round(Number(start) * 1000),
    endMs = Math.round(Number(end) * 1000);
  const valid =
    start !== "" &&
    end !== "" &&
    Number.isFinite(startMs) &&
    Number.isFinite(endMs) &&
    startMs >= 0 &&
    endMs - startMs >= 100 &&
    endMs <= (v?.duration_ms || 0);
  const reference = useMutation({
    mutationFn: () =>
      api.post(`/projects/${projectId}/library/usages`, {
        version_id: versionId,
        shot_id: selectedShot,
        start_ms: startMs,
        end_ms: endMs,
        purpose,
        note,
      }),
    onSuccess: onChange,
  });
  return (
    <Modal open title={item.name} onClose={onClose} width={1050}>
      <div className="grid gap-5 lg:grid-cols-[1.5fr_1fr]">
        <div className="min-w-0 space-y-3">
          {!v && (
            <p role="alert" className="text-sm text-danger">
              {zh
                ? "链接中的原片版本不可用，请检查版本记录。"
                : "The linked source version is unavailable."}
            </p>
          )}
          <label className="flex items-center gap-3 text-xs">
            {zh ? "视频版本" : "Version"}
            <select
              aria-label={zh ? "视频版本" : "Video version"}
              value={versionId}
              className="h-9 min-w-0 flex-1 rounded-md border bg-bg px-2"
              onChange={(e) => {
                const next = item.versions.find((v) => v.id === e.target.value);
                setVersionId(e.target.value);
                setStart("0");
                setEnd(String((next?.duration_ms || 0) / 1000));
                reference.reset();
              }}
            >
              {item.versions.map((v) => (
                <option key={v.id} value={v.id} disabled={v.status !== "ready"}>
                  v{v.ordinal} · {mediaStatus(v.status, zh)}
                </option>
              ))}
            </select>
          </label>
          {v?.proxy_hash && (
            <MediaSegmentPlayer
              key={v.id}
              src={blobUrl(projectId, v.proxy_hash)}
              start={startMs || 0}
              end={endMs || 0}
              label={item.name}
              onMark={(kind, ms) => {
                if (kind === "start") setStart(String(ms / 1000));
                else setEnd(String(ms / 1000));
                reference.reset();
              }}
            />
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
              ? "正在播放轻量预览。引用始终指向原片版本；新增版本不会替换已有引用。"
              : "Playing a lightweight preview. References point to the original version and never follow newer uploads automatically."}
          </p>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button
              size="sm"
              variant="outline"
              disabled={item.versions.some((v) =>
                ["uploading", "queued", "processing"].includes(v.status),
              )}
              onClick={onUpload}
            >
              {zh ? "上传新版本" : "Upload new version"}
            </Button>
            {v?.original_hash && (
              <a
                className="studio-link text-xs"
                href={`${blobUrl(projectId, v.original_hash)}&download=true`}
                download
              >
                {zh ? "下载原片" : "Download original"}
              </a>
            )}
          </div>
          {v && valid && (
            <Link
              className="studio-link block text-sm"
              to={`/projects/${projectId}/evidence?version=${v.id}&start=${startMs}&end=${endMs}`}
            >
              {zh
                ? "为这段画面记录身份与证据 →"
                : "Record identity evidence for this segment →"}
            </Link>
          )}
          {v?.status === "ready" && (
            <Link
              className="studio-link block text-sm"
              to={`/projects/${projectId}/transcriptions?version=${v.id}`}
            >
              {zh
                ? "转写音轨与编辑字幕 →"
                : "Transcribe audio and edit captions →"}
            </Link>
          )}
          {v && (
            <LibraryAnnotation
              key={`${v.id}.${startMs}.${endMs}`}
              projectId={projectId}
              versionId={v.id}
              start={startMs}
              end={endMs}
              valid={valid}
            />
          )}
        </div>
        <div className="space-y-4 rounded-lg border bg-surface p-4">
          <h3 className="text-sm font-medium">
            {zh ? "选一个片段，用到镜头中" : "Reference a segment in a shot"}
          </h3>
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
          {!valid && (
            <p role="alert" className="text-xs text-danger">
              {zh
                ? "请选择视频范围内至少0.1秒的片段。"
                : "Choose at least 0.1 seconds within the video."}
            </p>
          )}
          <label className="block space-y-1 text-xs">
            <span>{zh ? "目标镜头" : "Target shot"}</span>
            <select
              aria-label={zh ? "目标镜头" : "Target shot"}
              value={selectedShot}
              onChange={(e) => {
                setShotId(e.target.value);
                reference.reset();
              }}
              className="h-9 w-full rounded-md border bg-bg px-2 text-sm"
            >
              <option value="">
                {shots.isPending
                  ? zh
                    ? "加载镜头…"
                    : "Loading…"
                  : zh
                    ? "选择镜头"
                    : "Choose a shot"}
              </option>
              {shots.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.code} · {s.title || s.description.slice(0, 40)}
                </option>
              ))}
            </select>
          </label>
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
            <select
              aria-label={zh ? "片段用途" : "Segment purpose"}
              value={purpose}
              onChange={(e) => {
                setPurpose(e.target.value);
                reference.reset();
              }}
              className="h-9 w-full rounded-md border bg-bg px-2 text-sm"
            >
              <option value="visual_reference">
                {zh ? "画面参考" : "Visual reference"}
              </option>
              <option value="editing_source">
                {zh ? "剪辑备选" : "Editing source"}
              </option>
            </select>
          </label>
          <textarea
            aria-label={zh ? "片段备注" : "Segment note"}
            placeholder={
              zh
                ? "例如：参考这段运镜与人物动作"
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
              ? "保存后可在镜头详情中播放；不会自动加入时间线或发送给AI。"
              : "Play it from the shot details. This does not add it to the timeline or send it to AI."}
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
      </div>
    </Modal>
  );
}

function ImpactDialog({
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
                  {r.shot_title} · v{r.ordinal} · {mediaTime(r.start_ms)} —{" "}
                  {mediaTime(r.end_ms)}{" "}
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
                          ? "解除这处片段引用？原视频仍保留。"
                          : "Remove this usage? The original video remains.",
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
                      ? "确认将此视频及其版本移入回收站？可稍后恢复。"
                      : "Move this video and its versions to trash? You can restore them later.",
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
