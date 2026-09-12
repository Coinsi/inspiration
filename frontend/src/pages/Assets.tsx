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
  Pencil,
  Plus,
  Share2,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
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
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr } = useI18n();
  const base = `/projects/${projectId}`;
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  // 批量选择模式
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // 视图:卡片 / 图谱 / 列表
  const [view, setView] = usePersistentState<"cards" | "graph" | "list">(`assets.view.${projectId}`, "cards");
  // 列表排序
  const [sortKey, setSortKey] = useState<"name" | "type" | "ref_count" | "gen_count" | "shot_count">(
    "shot_count",
  );
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const {
    data: assets,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["assets", projectId, filter, q, tag],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filter) params.set("type", filter);
      if (q) params.set("q", q);
      if (tag) params.set("tag", tag);
      return api.get<Asset[]>(`${base}/assets?${params.toString()}`);
    },
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("character");
  const create = useMutation({
    mutationFn: () => api.post<Asset>(`${base}/assets`, { type, name, metadata: {}, tags: [] }),
    onSuccess: () => {
      setName("");
      setCreateOpen(false);
      toast.push(tr("toast.assetCreated"), "success");
      void qc.invalidateQueries({ queryKey: ["assets", projectId] });
    },
    onError: (e) => toast.push(e instanceof ApiError ? e.message : tr("toast.createFailed"), "error"),
  });

  const selectable = (assets ?? []).filter((a) => a.status !== "locked");
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
    mutationFn: () =>
      api.post<{ deleted: number; skipped: number }>(`${base}/assets/batch-delete`, { ids: [...selected] }),
    onSuccess: (r) => {
      toast.push(tr("assets.batchDeleted").replace("{n}", String(r.deleted)), "success");
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
    <div className="studio-page">
      {/* 头部 */}
      <div className="page-heading mb-5">
        <div>
          <h1 className="text-xl font-semibold">{tr("assets.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {tr("assets.subtitle")}（{assets?.length ?? 0}）
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          {tr("common.new")}
        </Button>
      </div>
      <div className="mb-5 flex flex-wrap items-center gap-3 border-b border-border pb-4">
        <div className="flex flex-wrap items-center gap-2">
          {selecting ? (
            <>
              <span className="text-sm text-muted-foreground">
                {tr("assets.selected").replace("{n}", String(selected.size))}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setSelected(
                    selected.size === selectable.length ? new Set() : new Set(selectable.map((a) => a.id)),
                  )
                }
              >
                {selected.size === selectable.length ? tr("assets.unselectAll") : tr("assets.selectAll")}
              </Button>
              <Button
                size="sm"
                className="bg-danger text-white hover:bg-danger/90"
                disabled={selected.size === 0 || batchDelete.isPending}
                onClick={async () => {
                  if (
                    await confirm({
                      title: tr("assets.deleteSelected"),
                      message: tr("assets.batchDeleteConfirm").replace("{n}", String(selected.size)),
                      confirmText: tr("common.delete"),
                      danger: true,
                    })
                  ) {
                    batchDelete.mutate();
                  }
                }}
              >
                <Trash2 className="h-4 w-4 mr-1" /> {tr("assets.deleteSelected")}
                {selected.size > 0 && ` (${selected.size})`}
              </Button>
              <Button variant="ghost" size="icon" title={tr("common.cancel")} onClick={exitSelecting}>
                <X className="h-4 w-4" />
              </Button>
            </>
          ) : (
            <>
              {/* 视图切换:卡片 / 图谱 / 列表 */}
              <div className="flex items-center rounded-lg border border-border bg-card p-0.5">
                {(
                  [
                    { key: "cards", icon: LayoutGrid, label: tr("assets.viewCards") },
                    { key: "graph", icon: Share2, label: tr("assets.viewGraph") },
                    { key: "list", icon: List, label: tr("assets.viewList") },
                  ] as const
                ).map((v) => (
                  <button
                    key={v.key}
                    onClick={() => setView(v.key)}
                    title={v.label}
                    aria-pressed={view === v.key}
                    className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition-colors ${
                      view === v.key
                        ? "bg-primary/15 text-primary font-medium"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <v.icon className="h-3.5 w-3.5" /> {v.label}
                  </button>
                ))}
              </div>
              <Input
                aria-label={tr("assets.search")}
                placeholder={tr("assets.search")}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                className="w-40"
              />
              <Input
                aria-label={tr("assets.filterTag")}
                placeholder={tr("assets.filterTag")}
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                className="w-32"
              />
              {view !== "graph" && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelecting(true)}
                  disabled={!assets?.length}
                >
                  <CheckSquare className="h-4 w-4 mr-1" /> {tr("assets.batch")}
                </Button>
              )}
              <Link to={`${base}/assets/trash`}>
                <Button variant="outline" size="icon" title={tr("assets.trash")}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </Link>
            </>
          )}
        </div>
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title={tr("common.new")} width={480}>
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
            <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
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
        <Chip active={!filter} onClick={() => setFilter("")}>
          {tr("assets.all")}
        </Chip>
        {ASSET_TYPES.map((t) => (
          <Chip key={t} active={filter === t} onClick={() => setFilter(t)}>
            {tr(`asset.${t}`)}
          </Chip>
        ))}
      </div>

      {/* 海报墙 */}
      {isError ? (
        <div role="alert" className="studio-empty">
          {tr("common.loadFailed")}
        </div>
      ) : isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-44 rounded-lg" />
          ))}
        </div>
      ) : view === "graph" ? (
        <AssetGraph
          assets={assets ?? []}
          projectId={projectId!}
          onOpen={(id) => navigate(`${base}/assets/${id}`)}
        />
      ) : view === "list" ? (
        <ListView
          assets={sortedAssets}
          projectId={projectId!}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={toggleSort}
          selecting={selecting}
          selected={selected}
          onToggle={toggle}
          onOpen={(id) => navigate(`${base}/assets/${id}`)}
        />
      ) : assets && assets.length > 0 ? (
        <div className="grid grid-cols-1 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {assets.map((a) => (
            <article
              key={a.id}
              className={`group flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card transition-colors ${selecting && selected.has(a.id) ? "border-primary ring-1 ring-primary" : "border-border hover:border-border-strong"} ${selecting && a.status === "locked" ? "opacity-50" : ""}`}
            >
              <Link
                to={`${base}/assets/${a.id}`}
                aria-label={`${a.name} · ${tr(`asset.${a.type}`)}`}
                aria-disabled={selecting && a.status === "locked"}
                onClick={(e) => {
                  if (selecting) {
                    e.preventDefault();
                    if (a.status !== "locked") toggle(a.id);
                  }
                }}
              >
                {/* 资产画面与真实的加载状态 */}
                <div className="relative aspect-[4/3] overflow-hidden bg-elevated">
                  <MediaImage
                    src={a.representative_blob_hash ? blobUrl(projectId!, a.representative_blob_hash) : null}
                    alt={a.name}
                  />
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
                    {tr(`asset.${a.type}`)} · <span className="font-code">{a.code}</span>
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
                  <span key={tg} className="rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground">
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

              {/* 卡底操作:两颗独立按钮 */}
              {!selecting && (
                <div className="mt-auto flex gap-2 px-4 pb-4 pt-3">
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      navigate(`${base}/assets/${a.id}?tab=gen`);
                    }}
                    className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-border py-1.5 text-xs text-foreground/85 transition-colors hover:border-primary/50 hover:bg-primary/10 hover:text-primary"
                  >
                    <Sparkles className="h-4 w-4" /> {tr("assets.genImage")}
                  </button>
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      navigate(`${base}/assets/${a.id}`);
                    }}
                    className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-border py-1.5 text-xs text-foreground/85 transition-colors hover:border-primary/50 hover:bg-primary/10 hover:text-primary"
                  >
                    <Pencil className="h-4 w-4" /> {tr("assets.edit")}
                  </button>
                </div>
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
  onSort: (k: "name" | "type" | "ref_count" | "gen_count" | "shot_count") => void;
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
      aria-sort={k && sortKey === k ? (sortDir === "asc" ? "ascending" : "descending") : undefined}
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
      <span className={`inline-flex items-center gap-0.5 ${right ? "justify-end" : ""}`}>
        {children}
        {k &&
          sortKey === k &&
          (sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)}
      </span>
    </th>
  );

  if (assets.length === 0) {
    return <p className="py-16 text-center text-sm text-muted-foreground">{tr("assets.empty")}</p>;
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
                onClick={() => (selecting ? !locked && onToggle(a.id) : onOpen(a.id))}
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
                        {locked && <Lock className="h-3 w-3 shrink-0 text-muted-foreground" />}
                      </span>
                      <span className="font-code text-[11px] text-faint">{a.code}</span>
                    </span>
                  </span>
                </td>
                <td className="px-3 py-2 text-muted-foreground">{tr(`asset.${a.type}`)}</td>
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
                <td className="px-3 py-2 text-right font-code text-muted-foreground">{a.ref_count ?? 0}</td>
                <td className="px-3 py-2 text-right font-code text-muted-foreground">{a.gen_count ?? 0}</td>
                <td className="px-3 py-2 text-right font-code">{a.shot_count ?? 0}</td>
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
