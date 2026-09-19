import { useWorkspaceScroll } from "@/lib/useWorkspaceScroll";
import { MaterialTabs } from "@/components/MaterialTabs";
import { MediaImage } from "@/components/MediaImage";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckSquare,
  ChevronDown,
  ChevronUp,
  ImageOff,
  Images,
  LayoutGrid,
  List,
  Lock,
  Plus,
  Share2,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AssetPreview } from "@/components/AssetPreview";
import { Link, useNavigate, useParams } from "react-router-dom";
import AssetGraph from "@/components/AssetGraph";
import { Button } from "@/components/ui/button";
import { usePersistentState } from "@/lib/usePersistentState";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";
import { api, ApiError, ASSET_TYPES, blobUrl, type Asset } from "@/lib/api";

export default function Assets() {
  const { projectId } = useParams();
  return <AssetsContent key={projectId} />;
}

function AssetsContent() {
  const { projectId } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr, lang } = useI18n();
  const zh = lang === "zh";
  const [preview, setPreview] = useState<Asset | null>(null);
  const base = `/projects/${projectId}`;
  const [filter, setFilter] = usePersistentState(
    `assets.type.${projectId}`,
    "",
  );
  const [q, setQ] = usePersistentState(`assets.search.${projectId}`, "");
  const [tag, setTag] = usePersistentState(`assets.tag.${projectId}`, "");
  const [search, setSearch] = useState({ q: q.trim(), tag: tag.trim() });
  useEffect(() => {
    const timer = setTimeout(
      () => setSearch({ q: q.trim(), tag: tag.trim() }),
      250,
    );
    return () => clearTimeout(timer);
  }, [q, tag]);
  const searchPending = q.trim() !== search.q || tag.trim() !== search.tag;
  // 批量选择模式
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // 视图:卡片 / 图谱 / 列表
  const [view, setView] = usePersistentState<"cards" | "graph" | "list">(
    `assets.view.${projectId}`,
    "cards",
  );
  // 列表排序
  const [sortKey, setSortKey] = usePersistentState<
    "name" | "type" | "ref_count" | "gen_count" | "shot_count"
  >(`assets.sort.${projectId}`, "shot_count");
  const [sortDir, setSortDir] = usePersistentState<"asc" | "desc">(
    `assets.sortDirection.${projectId}`,
    "desc",
  );

  const {
    data: assets,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["assets", projectId, filter, search.q, search.tag],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filter) params.set("type", filter);
      if (search.q) params.set("q", search.q);
      if (search.tag) params.set("tag", search.tag);
      return api.get<Asset[]>(`${base}/assets?${params.toString()}`);
    },
  });

  const [page, setPage] = usePersistentState(`assets.page.${projectId}`, 0);
  const filterScope = `${filter}\n${q}\n${tag}\n${sortKey}\n${sortDir}`;
  const previousScope = useRef(filterScope);
  useEffect(() => {
    if (previousScope.current !== filterScope) {
      previousScope.current = filterScope;
      setPage(0);
    }
  }, [filterScope, setPage]);
  useWorkspaceScroll(
    `assets.${projectId}.${view}.${page}.${filter}.${search.q}.${search.tag}`,
    !!assets && !isLoading,
  );
  useEffect(() => {
    if (assets && page * 48 >= assets.length && page > 0)
      setPage(Math.max(0, Math.ceil(assets.length / 48) - 1));
  }, [assets, page, setPage]);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("character");
  const create = useMutation({
    mutationFn: () =>
      api.post<Asset>(`${base}/assets`, {
        type,
        name: name.trim(),
        metadata: {},
        tags: [],
      }),
    onSuccess: () => {
      setName("");
      setCreateOpen(false);
      toast.push(tr("toast.assetCreated"), "success");
      void qc.invalidateQueries({ queryKey: ["assets", projectId] });
    },
    onError: (e) =>
      toast.push(
        e instanceof ApiError ? e.message : tr("toast.createFailed"),
        "error",
      ),
  });

  const selectable = (assets ?? []).filter((a) => a.status !== "locked");
  const visibleSelected = new Set(
    selectable.filter((a) => selected.has(a.id)).map((a) => a.id),
  );
  const allSelected =
    selectable.length > 0 && selectable.every((a) => visibleSelected.has(a.id));
  useEffect(() => {
    setSelected(new Set());
  }, [filter, q, tag]);
  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const exitSelecting = () => {
    setSelecting(false);
    setSelected(new Set());
  };
  const batchDelete = useMutation({
    mutationFn: (ids: string[]) =>
      api.post<{ deleted: number; skipped: number }>(
        `${base}/assets/batch-delete`,
        { ids },
      ),
    onSuccess: (r) => {
      toast.push(
        tr("assets.batchDeleted").replace("{n}", String(r.deleted)),
        "success",
      );
      if (r.skipped)
        toast.push(
          zh
            ? `${r.skipped} 项未删除，请检查锁定状态或引用关系`
            : `${r.skipped} items skipped; check locks or references`,
          "info",
        );
      exitSelecting();
      void qc.invalidateQueries({ queryKey: ["assets", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  // 列表视图排序
  const sortedAssets = useMemo(() => {
    const list = [...(assets ?? [])];
    const dir = sortDir === "asc" ? 1 : -1;
    list.sort((a, b) => {
      if (sortKey === "name" || sortKey === "type") {
        return a[sortKey].localeCompare(b[sortKey], "zh") * dir;
      }
      return ((a[sortKey] ?? 0) - (b[sortKey] ?? 0)) * dir;
    });
    return list;
  }, [assets, sortKey, sortDir]);
  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir(key === "name" || key === "type" ? "asc" : "desc");
    }
  };

  return (
    <div className="studio-page material-workspace">
      <MaterialTabs projectId={projectId!} />
      {/* 头部 */}
      <div className="page-heading mb-5">
        <div>
          <h1 className="text-xl font-semibold">{tr("assets.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {zh
              ? "管理角色、场景与道具，让参考画面衔接到创作。"
              : "Organize characters, scenes and props for your next creation."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            {tr("common.new")}
          </Button>
        </div>
      </div>
      <div className="mb-5 flex flex-wrap items-center gap-3 border-b border-border pb-4">
        <div className="flex flex-wrap items-center gap-2">
          {selecting ? (
            <>
              <span className="text-sm text-muted-foreground">
                {tr("assets.selected").replace(
                  "{n}",
                  String(visibleSelected.size),
                )}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setSelected(
                    allSelected
                      ? new Set()
                      : new Set(selectable.map((a) => a.id)),
                  )
                }
              >
                {allSelected
                  ? tr("assets.unselectAll")
                  : tr("assets.selectAll")}
              </Button>
              <Button
                size="sm"
                className="bg-danger text-white hover:bg-danger/90"
                disabled={
                  visibleSelected.size === 0 ||
                  batchDelete.isPending ||
                  isLoading ||
                  isError ||
                  searchPending
                }
                onClick={async () => {
                  const ids = [...visibleSelected];
                  if (
                    await confirm({
                      title: tr("assets.deleteSelected"),
                      message: tr("assets.batchDeleteConfirm").replace(
                        "{n}",
                        String(ids.length),
                      ),
                      confirmText: tr("common.delete"),
                      danger: true,
                    })
                  ) {
                    batchDelete.mutate(ids);
                  }
                }}
              >
                <Trash2 className="h-4 w-4 mr-1" />{" "}
                {tr("assets.deleteSelected")}
                {visibleSelected.size > 0 && ` (${visibleSelected.size})`}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                title={tr("common.cancel")}
                onClick={exitSelecting}
              >
                <X className="h-4 w-4" />
              </Button>
            </>
          ) : (
            <>
              <Input
                aria-label={tr("assets.search")}
                placeholder={zh ? "搜索角色、场景或道具…" : "Search materials…"}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                className="w-64 max-w-full"
              />
              <div className="flex rounded-lg border bg-card p-0.5">
                {(
                  [
                    {
                      key: "cards",
                      icon: LayoutGrid,
                      label: tr("assets.viewCards"),
                    },
                    { key: "list", icon: List, label: tr("assets.viewList") },
                    {
                      key: "graph",
                      icon: Share2,
                      label: tr("assets.viewGraph"),
                    },
                  ] as const
                ).map((v) => (
                  <button
                    key={v.key}
                    aria-label={v.label}
                    title={v.label}
                    aria-pressed={view === v.key}
                    onClick={() => setView(v.key)}
                    className={`p-2 rounded-md ${view === v.key ? "bg-primary/10 text-primary" : "text-muted-foreground"}`}
                  >
                    <v.icon size={16} />
                  </button>
                ))}
              </div>
              <details className="relative">
                <summary className="cursor-pointer rounded-lg border px-3 py-2 text-sm">
                  {zh ? "筛选与管理" : "Filter & manage"}
                  {tag ? " · 1" : ""}
                </summary>
                <div className="absolute top-full right-0 mt-2 z-20 w-60 rounded-xl border bg-card shadow-xl p-4 space-y-3">
                  <Input
                    aria-label={tr("assets.filterTag")}
                    placeholder={tr("assets.filterTag")}
                    value={tag}
                    onChange={(e) => setTag(e.target.value)}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setSelecting(true)}
                    disabled={
                      !selectable.length ||
                      isError ||
                      isLoading ||
                      searchPending
                    }
                  >
                    <CheckSquare size={15} />
                    {tr("assets.batch")}
                  </Button>
                  <Link
                    className="studio-link w-full"
                    to={`${base}/assets/import`}
                  >
                    {zh ? "导入外部目录" : "Import catalog"}
                  </Link>
                  <Link
                    className="studio-link w-full"
                    to={`${base}/assets/trash`}
                  >
                    {tr("assets.trash")}
                  </Link>
                </div>
              </details>
            </>
          )}
        </div>
      </div>

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title={tr("common.new")}
        width={480}
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim() && !create.isPending) create.mutate();
          }}
        >
          <label className="block space-y-2 text-sm">
            <span>{tr("assets.colType")}</span>
            <select
              className="h-9 w-full rounded-md border bg-surface px-3"
              value={type}
              onChange={(e) => setType(e.target.value)}
            >
              {ASSET_TYPES.map((t) => (
                <option key={t} value={t}>
                  {tr(`asset.${t}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-2 text-sm">
            <span>{tr("assets.colName")}</span>
            <Input
              autoFocus
              placeholder={tr("assets.newName")}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setCreateOpen(false)}
            >
              {tr("common.cancel")}
            </Button>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              {tr("common.create")}
            </Button>
          </div>
        </form>
      </Modal>

      {/* 分面 */}
      <div className="flex gap-2 flex-wrap mb-5">
        <Chip
          active={!filter}
          onClick={() => {
            setFilter("");
            exitSelecting();
          }}
        >
          {tr("assets.all")}
        </Chip>
        {ASSET_TYPES.map((t) => (
          <Chip
            key={t}
            active={filter === t}
            onClick={() => {
              setFilter(t);
              exitSelecting();
            }}
          >
            {tr(`asset.${t}`)}
          </Chip>
        ))}
      </div>

      {/* 海报墙 */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span role="status">
          {isLoading || searchPending
            ? zh
              ? "正在查找素材…"
              : "Finding assets…"
            : isError
              ? zh
                ? "素材数量暂不可用"
                : "Count unavailable"
              : zh
                ? `当前结果 ${assets?.length ?? 0} 项`
                : `${assets?.length ?? 0} results`}
        </span>
        <div className="flex items-center gap-2">
          {(q || tag || filter) && (
            <button
              className="studio-link"
              onClick={() => {
                setQ("");
                setTag("");
                setFilter("");
                exitSelecting();
              }}
            >
              {zh ? "清除筛选" : "Clear filters"}
            </button>
          )}
          {view !== "graph" && (
            <select
              aria-label={zh ? "素材排序" : "Sort assets"}
              className="h-8 max-w-full rounded-md border bg-bg px-2"
              value={`${sortKey}.${sortDir}`}
              onChange={(e) => {
                const [k, d] = e.target.value.split(".");
                setSortKey(k as typeof sortKey);
                setSortDir(d as typeof sortDir);
              }}
            >
              <option value="shot_count.desc">
                {zh ? "镜头使用最多" : "Most used in shots"}
              </option>
              <option value="gen_count.desc">
                {zh ? "生成版本最多" : "Most generations"}
              </option>
              <option value="ref_count.desc">
                {zh ? "参考图片最多" : "Most references"}
              </option>
              <option value="name.asc">
                {zh ? "名称 A → Z" : "Name A → Z"}
              </option>
              <option value="name.desc">
                {zh ? "名称 Z → A" : "Name Z → A"}
              </option>
              <option value="shot_count.asc">
                {zh ? "镜头使用最少" : "Least used in shots"}
              </option>
              <option value="gen_count.asc">
                {zh ? "生成版本最少" : "Fewest generations"}
              </option>
              <option value="ref_count.asc">
                {zh ? "参考图片最少" : "Fewest references"}
              </option>
              <option value="type.asc">
                {zh ? "类型 A → Z" : "Type A → Z"}
              </option>
              <option value="type.desc">
                {zh ? "类型 Z → A" : "Type Z → A"}
              </option>
            </select>
          )}
        </div>
      </div>
      {preview && (
        <AssetPreview
          projectId={projectId!}
          asset={preview}
          onClose={() => setPreview(null)}
        />
      )}
      {isError ? (
        <div role="alert" className="studio-empty">
          {tr("common.loadFailed")}
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      ) : isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-44 rounded-lg" />
          ))}
        </div>
      ) : !assets?.length ? (
        <div className="studio-empty">
          <ImageOff className="h-8 w-8" />
          <p>
            {q || tag || filter
              ? zh
                ? "没有符合筛选的素材"
                : "No matching assets"
              : tr("assets.empty")}
          </p>
        </div>
      ) : view === "graph" ? (
        <AssetGraph
          assets={assets ?? []}
          projectId={projectId!}
          onOpen={(id) => navigate(`${base}/assets/${id}`)}
        />
      ) : view === "list" ? (
        <ListView
          assets={sortedAssets.slice(page * 48, (page + 1) * 48)}
          projectId={projectId!}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={toggleSort}
          selecting={selecting}
          selected={visibleSelected}
          onToggle={toggle}
          onOpen={(id) => setPreview(assets.find((a) => a.id === id) ?? null)}
        />
      ) : assets && assets.length > 0 ? (
        <div className="grid grid-cols-1 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 items-start">
          {sortedAssets.slice(page * 48, (page + 1) * 48).map((a) => (
            <article
              key={a.id}
              className={`group flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card transition-colors ${selecting && selected.has(a.id) ? "border-primary ring-1 ring-primary" : "border-border hover:border-border-strong"} ${selecting && a.status === "locked" ? "opacity-50" : ""}`}
            >
              <Link
                to={`${base}/assets/${a.id}`}
                aria-label={`${a.name} · ${tr(`asset.${a.type}`)}`}
                aria-disabled={selecting && a.status === "locked"}
                onClick={(e) => {
                  e.preventDefault();
                  if (selecting) {
                    if (a.status !== "locked") toggle(a.id);
                  } else setPreview(a);
                }}
              >
                {/* 资产画面与真实的加载状态 */}
                <div
                  className={`relative overflow-hidden ${a.representative_blob_hash ? "aspect-[4/3] bg-elevated" : "h-20 bg-primary/5"}`}
                >
                  {a.representative_blob_hash ? (
                    <MediaImage
                      src={
                        a.representative_blob_hash
                          ? blobUrl(projectId!, a.representative_blob_hash)
                          : null
                      }
                      alt={a.name}
                    />
                  ) : (
                    <div className="h-full px-4 flex items-center gap-3 text-primary/70">
                      <span className="text-3xl font-medium">
                        {a.name.slice(0, 1)}
                      </span>
                      <span className="text-xs">
                        {tr(`asset.${a.type}`)} ·{" "}
                        {zh ? "待创作画面" : "Awaiting artwork"}
                      </span>
                    </div>
                  )}
                  {selecting && a.status !== "locked" && (
                    <span
                      className={`absolute top-2.5 right-2.5 grid h-7 w-7 place-items-center rounded-lg  transition-colors ${
                        selected.has(a.id)
                          ? "bg-primary text-primary-foreground"
                          : "bg-bg/70 text-muted-foreground"
                      }`}
                    >
                      {selected.has(a.id) ? (
                        <CheckSquare className="h-4 w-4" />
                      ) : (
                        <Square className="h-4 w-4" />
                      )}
                    </span>
                  )}
                  {a.status === "locked" && (
                    <span className="absolute top-2.5 right-2.5 grid h-7 w-7 place-items-center rounded-lg bg-bg/70 text-primary ">
                      <Lock className="h-4 w-4" />
                    </span>
                  )}
                </div>

                <div className="px-4 pt-3">
                  <div className="truncate text-sm font-semibold">{a.name}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {tr(`asset.${a.type}`)} ·{" "}
                    <span className="font-code">{a.code}</span>
                  </div>
                </div>
              </Link>
              {/* 统计角标 */}
              <div className="mt-3 flex flex-wrap items-center gap-2 px-4">
                <span
                  title={tr("detail.ref")}
                  className="inline-flex items-center gap-1.5 px-0 py-0 text-xs text-muted-foreground"
                >
                  <Images className="h-3.5 w-3.5" /> {a.ref_count ?? 0}
                </span>
                <span
                  title={tr("detail.gen")}
                  className="inline-flex items-center gap-1.5 px-0 py-0 text-xs text-muted-foreground"
                >
                  <Sparkles className="h-3.5 w-3.5" /> {a.gen_count ?? 0}
                </span>
                {a.tags?.slice(0, 2).map((tg) => (
                  <span
                    key={tg}
                    className="rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground"
                  >
                    #{tg}
                  </span>
                ))}
              </div>

              {/* 摘要 */}
              {a.summary && (
                <p className="mx-4 mt-2 line-clamp-2 break-words text-xs leading-6 text-muted-foreground">
                  {a.summary}
                </p>
              )}

              {!selecting && (
                <button
                  aria-label={`${zh ? "快速预览" : "Quick preview"} ${a.name}`}
                  onClick={() => setPreview(a)}
                  className="text-left px-4 py-4 text-sm text-primary hover:bg-primary/5"
                >
                  {zh ? "查看素材" : "View material"} →
                </button>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="text-center py-20 text-muted-foreground">
          <ImageOff className="h-10 w-10 mx-auto mb-3 opacity-50" />
          <p className="text-sm">{tr("assets.empty")}</p>
        </div>
      )}
      {view !== "graph" && sortedAssets.length > 48 && (
        <div className="flex justify-center items-center gap-4 py-4">
          <Button
            variant="outline"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            {zh ? "上一页" : "Previous"}
          </Button>
          <span className="text-sm">
            {page + 1} / {Math.ceil(sortedAssets.length / 48)}
          </span>
          <Button
            variant="outline"
            disabled={(page + 1) * 48 >= sortedAssets.length}
            onClick={() => setPage(page + 1)}
          >
            {zh ? "下一页" : "Next"}
          </Button>
        </div>
      )}
    </div>
  );
}

// 列表视图:Laper「选角」式表格,可排序
function ListView({
  assets,
  projectId,
  sortKey,
  sortDir,
  onSort,
  selecting,
  selected,
  onToggle,
  onOpen,
}: {
  assets: Asset[];
  projectId: string;
  sortKey: string;
  sortDir: "asc" | "desc";
  onSort: (
    k: "name" | "type" | "ref_count" | "gen_count" | "shot_count",
  ) => void;
  selecting: boolean;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onOpen: (id: string) => void;
}) {
  const { t: tr } = useI18n();
  const Th = ({
    k,
    children,
    right,
  }: {
    k?: "name" | "type" | "ref_count" | "gen_count" | "shot_count";
    children: React.ReactNode;
    right?: boolean;
  }) => (
    <th
      tabIndex={k ? 0 : undefined}
      aria-sort={
        k && sortKey === k
          ? sortDir === "asc"
            ? "ascending"
            : "descending"
          : undefined
      }
      onKeyDown={(e) => {
        if (k && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onSort(k);
        }
      }}
      onClick={k ? () => onSort(k) : undefined}
      className={`px-3 py-2.5 text-xs font-medium text-muted-foreground ${right ? "text-right" : "text-left"} ${
        k ? "cursor-pointer select-none hover:text-foreground" : ""
      }`}
    >
      <span
        className={`inline-flex items-center gap-0.5 ${right ? "justify-end" : ""}`}
      >
        {children}
        {k &&
          sortKey === k &&
          (sortDir === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          ))}
      </span>
    </th>
  );

  if (assets.length === 0) {
    return (
      <p className="py-16 text-center text-sm text-muted-foreground">
        {tr("assets.empty")}
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="min-w-[620px] w-full text-sm">
        <thead className="border-b border-border bg-elevated/40">
          <tr>
            {selecting && <th className="w-10 px-3" />}
            <Th k="name">{tr("assets.colName")}</Th>
            <Th k="type">{tr("assets.colType")}</Th>
            <Th>{tr("assets.colTags")}</Th>
            <Th k="ref_count" right>
              {tr("detail.ref")}
            </Th>
            <Th k="gen_count" right>
              {tr("detail.gen")}
            </Th>
            <Th k="shot_count" right>
              {tr("assets.colShots")}
            </Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {assets.map((a) => {
            const locked = a.status === "locked";
            return (
              <tr
                key={a.id}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    selecting ? !locked && onToggle(a.id) : onOpen(a.id);
                  }
                }}
                onClick={() =>
                  selecting ? !locked && onToggle(a.id) : onOpen(a.id)
                }
                className={`cursor-pointer transition-colors hover:bg-muted/60 ${
                  selecting && selected.has(a.id) ? "bg-primary/10" : ""
                } ${selecting && locked ? "opacity-50" : ""}`}
              >
                {selecting && (
                  <td className="px-3">
                    {!locked &&
                      (selected.has(a.id) ? (
                        <CheckSquare className="h-4 w-4 text-primary" />
                      ) : (
                        <Square className="h-4 w-4 text-faint" />
                      ))}
                  </td>
                )}
                <td className="px-3 py-2">
                  <span className="flex items-center gap-2.5">
                    <span className="relative h-9 w-9 shrink-0 overflow-hidden rounded-lg">
                      {a.representative_blob_hash ? (
                        <img
                          src={blobUrl(projectId, a.representative_blob_hash)}
                          alt=""
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <span className="flex h-full w-full items-center justify-center bg-elevated">
                          <ImageOff className="h-4 w-4 text-muted-foreground" />
                        </span>
                      )}
                    </span>
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 font-medium">
                        <span className="truncate">{a.name}</span>
                        {locked && (
                          <Lock className="h-3 w-3 shrink-0 text-muted-foreground" />
                        )}
                      </span>
                      <span className="font-code text-[11px] text-faint">
                        {a.code}
                      </span>
                    </span>
                  </span>
                </td>
                <td className="px-3 py-2 text-muted-foreground">
                  {tr(`asset.${a.type}`)}
                </td>
                <td className="px-3 py-2">
                  <span className="flex flex-wrap gap-1">
                    {a.tags?.slice(0, 3).map((tg) => (
                      <span
                        key={tg}
                        className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                      >
                        #{tg}
                      </span>
                    ))}
                  </span>
                </td>
                <td className="px-3 py-2 text-right font-code text-muted-foreground">
                  {a.ref_count ?? 0}
                </td>
                <td className="px-3 py-2 text-right font-code text-muted-foreground">
                  {a.gen_count ?? 0}
                </td>
                <td className="px-3 py-2 text-right font-code">
                  {a.shot_count ?? 0}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`text-sm px-3 py-1.5 rounded-md border transition-colors ${
        active
          ? "bg-primary/15 border-primary/40 text-primary"
          : "border-border text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}
