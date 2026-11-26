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
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AssetActions,
  assetPath,
  invalidateAssetLists,
  useAssetPermissions,
  useAssetTrash,
} from "@/components/AssetActions";
import AssetGraph from "@/components/AssetGraph";
import { AssetPreview } from "@/components/AssetPreview";
import { MaterialTabs } from "@/components/MaterialTabs";
import { SceneMaterials, storyScope } from "@/components/SceneMaterials";
import { MediaImage } from "@/components/MediaImage";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { SearchField } from "@/components/ui/search-field";
import { SelectField } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { api, ASSET_TYPES, blobUrl, type Asset } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { useWorkspaceScroll } from "@/lib/useWorkspaceScroll";

const PAGE_SIZE = 48;
const commonTypes = ["character", "location", "prop"];
type SortKey = "name" | "type" | "ref_count" | "gen_count" | "shot_count";
type SortDir = "asc" | "desc";
type View = "cards" | "list" | "graph";

export default function Assets() {
  const { projectId } = useParams();
  return <AssetsContent key={projectId} projectId={projectId!} />;
}

function AssetsContent({ projectId }: { projectId: string }) {
  const qc = useQueryClient(),
    navigate = useNavigate(),
    location = useLocation();
  const [params, setParams] = useSearchParams();
  const scope = storyScope(params);
  const scopeKey = scope.toString();
  const toast = useToast(),
    { t, lang } = useI18n();
  const zh = lang === "zh";
  const { canEdit } = useAssetPermissions(projectId);
  const base = `/projects/${projectId}`;
  const here = location.pathname + location.search;
  const [filter, setFilter] = usePersistentState(`assets.type.${projectId}`, "");
  const [q, setQ] = usePersistentState(`assets.search.${projectId}`, "");
  const [tag, setTag] = usePersistentState(`assets.tag.${projectId}`, "");
  const [view, setView] = usePersistentState<View>(`assets.view.${projectId}`, "cards");
  const [page, setPage] = usePersistentState(`assets.page.${projectId}`, 0);
  const [sortKey, setSortKey] = usePersistentState<SortKey>(
    `assets.sort.${projectId}`,
    "shot_count",
  );
  const [sortDir, setSortDir] = usePersistentState<SortDir>(
    `assets.sortDirection.${projectId}`,
    "desc",
  );
  const [query, setQuery] = useState(q.trim());
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("character");

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(q.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [q]);

  const list = useQuery({
    queryKey: ["assets", projectId, filter, query, scopeKey],
    queryFn: () => {
      const search = new URLSearchParams(scopeKey);
      if (filter) search.set("type", filter);
      if (query) search.set("q", query);
      return api.get<Asset[]>(`${base}/assets?${search}`);
    },
  });
  const availableTags = useMemo(() => {
    const options = new Set<string>(tag ? [tag] : []);
    for (const asset of list.data ?? []) for (const value of asset.tags) options.add(value);
    return [...options].sort((a, b) => a.localeCompare(b, lang));
  }, [list.data, tag, lang]);
  const sorted = useMemo(() => {
    const rows = (list.data ?? []).filter((a) => !tag || a.tags.includes(tag));
    const direction = sortDir === "asc" ? 1 : -1;
    return rows.sort((a, b) => {
      if (sortKey === "name" || sortKey === "type")
        return a[sortKey].localeCompare(b[sortKey], lang) * direction;
      return ((a[sortKey] ?? 0) - (b[sortKey] ?? 0)) * direction;
    });
  }, [list.data, tag, sortKey, sortDir, lang]);
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageIndex = Math.min(Math.max(0, page), pages - 1);
  const visible = sorted.slice(pageIndex * PAGE_SIZE, (pageIndex + 1) * PAGE_SIZE);
  const pageHasArt = visible.some((a) => !!a.representative_blob_hash);
  const ready = list.isSuccess && query === q.trim();
  useEffect(() => {
    if (ready && page !== pageIndex) setPage(pageIndex);
  }, [ready, pageIndex, page, setPage]);
  useWorkspaceScroll(
    `assets.${projectId}.${view}.${pageIndex}.${filter}.${query}.${tag}.${sortKey}.${sortDir}.${scopeKey}`,
    ready,
  );

  function changeFilter(set: (value: string) => void, next: string) {
    set(next);
    setSelected(new Set());
    setPage(0);
  }
  function sortBy(key: SortKey, direction: SortDir) {
    setSortKey(key);
    setSortDir(direction);
    setPage(0);
  }
  function exitSelection() {
    setSelecting(false);
    setSelected(new Set());
  }
  const selectingItems = selecting && canEdit;
  const selectable = sorted.filter((a) => a.status !== "locked");
  const pageSelectable = visible.filter((a) => a.status !== "locked");
  const selectedItems = selectable.filter((a) => selected.has(a.id));
  const pageSelected = pageSelectable.length > 0 && pageSelectable.every((a) => selected.has(a.id));
  function togglePage() {
    setSelected((previous) => {
      const next = new Set(previous);
      for (const a of pageSelectable) {
        if (pageSelected) next.delete(a.id);
        else next.add(a.id);
      }
      return next;
    });
  }

  const previewId = params.get("preview");
  const preview = previewId ? list.data?.find((a) => a.id === previewId) : undefined;
  function setPreview(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("preview", id);
    else next.delete("preview");
    setParams(next, { replace: !id });
  }
  const trash = useAssetTrash(projectId, () => {
    exitSelection();
    if (previewId) setPreview(null);
  });
  const create = useMutation({
    mutationFn: () =>
      api.post<Asset>(base + "/assets", { name: name.trim(), type, tags: [], metadata: {} }),
    onSuccess: (asset) => {
      setCreateOpen(false);
      setName("");
      qc.setQueryData(["asset", asset.id], asset);
      navigate(assetPath(projectId, asset.id, here));
      invalidateAssetLists(qc, projectId);
      toast.push(zh ? `已创建「${asset.name}」` : `Created “${asset.name}”`, "success");
    },
    onError: (error) => toast.push(error.message, "error"),
  });
  const readyToSelect = ready && !list.isFetching && !trash.isPending;
  const tools: BrowseTools = {
    projectId,
    selecting: selectingItems,
    selected,
    blocked: !readyToSelect,
    activate: (asset) => {
      if (!selectingItems) return setPreview(asset.id);
      if (!readyToSelect || asset.status === "locked") return;
      setSelected((previous) => {
        const next = new Set(previous);
        if (!next.delete(asset.id)) next.add(asset.id);
        return next;
      });
    },
    menu: (asset) => (
      <AssetActions
        projectId={projectId}
        asset={asset}
        returnTo={here}
        canTrash={canEdit && !trash.isPending}
        onTrash={() => trash.mutate([asset])}
      />
    ),
    usagePath: (asset) => assetPath(projectId, asset.id, here, "usages"),
  };
  const otherTypes = ASSET_TYPES.filter((k) => !commonTypes.includes(k));
  const choices: [View, typeof List, string][] = [
    ["cards", LayoutGrid, t("assets.viewCards")],
    ["list", List, t("assets.viewList")],
    ["graph", Share2, t("assets.viewGraph")],
  ];
  const sortOptions = [
    ["shot_count.desc", zh ? "镜头使用最多" : "Most used in shots"],
    ["shot_count.asc", zh ? "镜头使用最少" : "Least used in shots"],
    ["gen_count.desc", zh ? "生成版本最多" : "Most generations"],
    ["gen_count.asc", zh ? "生成版本最少" : "Fewest generations"],
    ["ref_count.desc", zh ? "参考图片最多" : "Most references"],
    ["ref_count.asc", zh ? "参考图片最少" : "Fewest references"],
    ["name.asc", zh ? "名称 A → Z" : "Name A → Z"],
    ["name.desc", zh ? "名称 Z → A" : "Name Z → A"],
    ["type.asc", zh ? "类型 A → Z" : "Type A → Z"],
    ["type.desc", zh ? "类型 Z → A" : "Type Z → A"],
  ];
  return (
    <div className="studio-page material-workspace unified-materials space-y-3">
      <header className="materials-heading !mb-3 flex-wrap">
        <h1>{t("assets.title")}</h1>
        {canEdit && (
          <div className="materials-heading-actions">
            <Link
              to={base + "/assets/import"}
              className="inline-flex min-h-11 items-center gap-1.5"
            >
              <Upload size={15} />
              {zh ? "导入目录" : "Import catalog"}
            </Link>
            <Button
              onClick={() => {
                setType(filter || "character");
                setCreateOpen(true);
              }}
            >
              <Plus size={16} />
              {t("common.new")}
            </Button>
          </div>
        )}
      </header>
      <div className="materials-navigation !mb-3">
        <MaterialTabs projectId={projectId} />
      </div>
      <SceneMaterials projectId={projectId} scope={scope} canEdit={canEdit} onChange={nextScope => {
        const next = new URLSearchParams(params);
        for (const field of scope.keys()) next.delete(field);
        nextScope.forEach((value, key) => next.set(key, value));
        next.delete("preview");
        setParams(next);
        exitSelection();
        setPage(0);
      }} />

      <div className="flex flex-wrap items-center gap-2">
        <SearchField
          label={t("assets.search")}
          placeholder={zh ? "搜索角色、场景或道具" : "Search characters, scenes or props"}
          value={q}
          onValueChange={(value) => changeFilter(setQ, value)}
          className="w-full sm:mr-auto sm:w-72"
        />
        <div
          className="inline-flex rounded-lg border bg-card p-0.5"
          role="group"
          aria-label={zh ? "显示方式" : "View"}
        >
          {choices.map(([key, Icon, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={view === key}
              disabled={selectingItems && key === "graph"}
              onClick={() => setView(key)}
              className={`inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 text-sm disabled:opacity-40 sm:min-h-9 sm:px-3 ${view === key ? "bg-elevated text-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
        <Button
          variant="outline"
          disabled={!canEdit || (!selectingItems && (!selectable.length || !readyToSelect))}
          onClick={() => {
            if (selectingItems) return exitSelection();
            if (view === "graph") setView("list");
            setSelecting(true);
          }}
        >
          <CheckSquare size={15} />
          {selectingItems ? (zh ? "退出选择" : "Done") : zh ? "选择" : "Select"}
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-2 border-b pb-3 text-xs text-muted-foreground">
        <div className="hidden items-center gap-1.5 sm:flex">
          <div className="materials-kinds !w-auto" role="group" aria-label={t("assets.colType")}>
            {["", ...commonTypes].map((key) => (
              <button
                type="button"
                key={key}
                aria-pressed={filter === key}
                onClick={() => changeFilter(setFilter, key)}
              >
                {key ? t(`asset.${key}`) : t("assets.all")}
              </button>
            ))}
          </div>
          <SelectField
            label={zh ? "更多类型" : "More types"}
            value={commonTypes.includes(filter) ? "" : filter}
            onChange={(value) => changeFilter(setFilter, value)}
            options={[
              { value: "", label: zh ? "更多类型" : "More types" },
              ...otherTypes.map((value) => ({ value, label: t(`asset.${value}`) })),
            ]}
          />
        </div>
        <div className="min-w-0 max-w-[32%] sm:hidden">
          <SelectField
            label={t("assets.colType")}
            value={filter}
            onChange={(value) => changeFilter(setFilter, value)}
            className="!px-2"
            options={[
              { value: "", label: zh ? "全部类型" : "All types" },
              ...ASSET_TYPES.map((value) => ({ value, label: t(`asset.${value}`) })),
            ]}
          />
        </div>
        <div className="min-w-0 max-w-[30%] sm:max-w-48">
          <SelectField
            label={t("assets.filterTag")}
            value={tag}
            onChange={(value) => changeFilter(setTag, value)}
            className="!px-2 sm:!px-3"
            searchable
            options={[
              { value: "", label: zh ? "全部标签" : "All tags" },
              ...availableTags.map((value) => ({ value, label: value })),
            ]}
          />
        </div>
        {(q || tag || filter) && (
          <button
            type="button"
            className="min-h-9 px-1 underline underline-offset-2 hover:text-foreground"
            onClick={() => {
              setFilter("");
              setTag("");
              changeFilter(setQ, "");
            }}
          >
            {zh ? "清除筛选" : "Clear filters"}
          </button>
        )}
        <span role="status" className="ml-auto whitespace-nowrap">
          {list.isError ? (
            zh ? (
              "素材数量暂不可用"
            ) : (
              "Count unavailable"
            )
          ) : !ready ? (
            zh ? (
              "正在查找素材…"
            ) : (
              "Finding assets…"
            )
          ) : (
            <>
              {zh && <span className="hidden sm:inline">当前结果 </span>}
              {sorted.length}
              {zh ? " 项" : " results"}
            </>
          )}
        </span>
        {view !== "graph" && (
          <SelectField
            label={zh ? "素材排序" : "Sort assets"}
            className="!px-2 sm:!px-3"
            value={`${sortKey}.${sortDir}`}
            onChange={(value) => {
              const [key, dir] = value.split(".");
              sortBy(key as SortKey, dir as SortDir);
            }}
            options={sortOptions.map(([value, label]) => ({ value, label }))}
          />
        )}
      </div>

      {selectingItems && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border bg-surface p-2.5 text-sm">
          <strong role="status" className="px-1 font-medium">
            {t("assets.selected").replace("{n}", String(selectedItems.length))}
          </strong>
          <Button
            variant="outline"
            size="sm"
            disabled={!readyToSelect || !pageSelectable.length}
            onClick={togglePage}
          >
            {pageSelected
              ? zh
                ? "取消本页选择"
                : "Deselect page"
              : zh
                ? "选择本页"
                : "Select page"}
          </Button>
          {selectable.length > pageSelectable.length &&
            selectedItems.length < selectable.length && (
              <Button
                variant="ghost"
                size="sm"
                disabled={!readyToSelect}
                onClick={() => setSelected(new Set(selectable.map((a) => a.id)))}
              >
                {zh
                  ? `选择全部匹配（${selectable.length}）`
                  : `Select all matching (${selectable.length})`}
              </Button>
            )}
          {!!selectedItems.length && (
            <Button
              variant="ghost"
              size="sm"
              disabled={trash.isPending}
              onClick={() => setSelected(new Set())}
            >
              {zh ? "清除选择" : "Clear selection"}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            className="text-danger"
            disabled={!readyToSelect || !selectedItems.length}
            onClick={() => trash.mutate(selectedItems)}
          >
            <Trash2 size={15} />
            {zh ? "移入回收站" : "Move to trash"}
          </Button>
        </div>
      )}

      {list.isError ? (
        <div role="alert" className="studio-empty">
          <p>{t("common.loadFailed")}</p>
          <Button variant="outline" size="sm" onClick={() => void list.refetch()}>
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      ) : list.isPending ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-44 rounded-lg" />
          ))}
        </div>
      ) : !sorted.length ? (
        <div className="studio-empty">
          <ImageOff size={28} />
          <p>
            {q || tag || filter || scopeKey
              ? zh
                ? "没有符合筛选的素材"
                : "No matching assets"
              : zh
                ? "还没有角色与场景"
                : "No characters or scenes yet"}
          </p>
        </div>
      ) : view === "graph" ? (
        <AssetGraph
          assets={sorted}
          projectId={projectId}
          onOpen={(id) => navigate(assetPath(projectId, id, here))}
        />
      ) : view === "list" ? (
        <AssetList
          assets={visible}
          tools={tools}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={(key) =>
            sortBy(
              key,
              key === sortKey
                ? sortDir === "asc"
                  ? "desc"
                  : "asc"
                : key === "name" || key === "type"
                  ? "asc"
                  : "desc",
            )
          }
        />
      ) : (
        <div className="grid grid-cols-1 items-stretch gap-4 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {visible.map((asset) => (
            <AssetCard key={asset.id} asset={asset} tools={tools} pageHasArt={pageHasArt} />
          ))}
        </div>
      )}

      {view !== "graph" && pages > 1 && (
        <div className="flex items-center justify-center gap-4 py-5">
          <Button
            variant="outline"
            disabled={pageIndex === 0}
            onClick={() => setPage(pageIndex - 1)}
          >
            {zh ? "上一页" : "Previous"}
          </Button>
          <span className="text-sm">
            {pageIndex + 1} / {pages}
          </span>
          <Button
            variant="outline"
            disabled={pageIndex + 1 >= pages}
            onClick={() => setPage(pageIndex + 1)}
          >
            {zh ? "下一页" : "Next"}
          </Button>
        </div>
      )}
      {preview && (
        <AssetPreview
          projectId={projectId}
          asset={preview}
          canTrash={canEdit && !trash.isPending}
          onTrash={() => trash.mutate([preview])}
          onClose={() => setPreview(null)}
        />
      )}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t("common.new")}
        width={480}
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (canEdit && name.trim() && !create.isPending) create.mutate();
          }}
        >
          <label className="block space-y-2 text-sm">
            <span>{t("assets.colType")}</span>
            <SelectField
              label={t("assets.colType")}
              value={type}
              onChange={setType}
              options={ASSET_TYPES.map((value) => ({ value, label: t(`asset.${value}`) }))}
            />
          </label>
          <label className="block space-y-2 text-sm">
            <span>{t("assets.colName")}</span>
            <Input
              autoFocus
              maxLength={255}
              placeholder={t("assets.newName")}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={!canEdit || !name.trim() || create.isPending}>
              {t("common.create")}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

type BrowseTools = {
  projectId: string;
  selecting: boolean;
  selected: Set<string>;
  blocked: boolean;
  activate: (a: Asset) => void;
  menu: (a: Asset) => ReactNode;
  usagePath: (a: Asset) => string;
};

function ShotCount({
  asset,
  tools,
  compact,
}: {
  asset: Asset;
  tools: BrowseTools;
  compact?: boolean;
}) {
  const { t, lang } = useI18n();
  const count = asset.shot_count ?? 0;
  const text = compact ? count : `${t("assets.colShots")} ${count}`;
  if (!count || tools.selecting) return <span className="text-muted-foreground">{text}</span>;
  return (
    <Link
      className="studio-link inline-flex min-h-11 items-center sm:min-h-9"
      to={tools.usagePath(asset)}
      aria-label={
        lang === "zh"
          ? `查看${asset.name}的 ${count} 个引用镜头`
          : `View ${count} shots referencing ${asset.name}`
      }
    >
      {text}
    </Link>
  );
}

function AssetCard({
  asset: a,
  tools,
  pageHasArt,
}: {
  asset: Asset;
  tools: BrowseTools;
  pageHasArt: boolean;
}) {
  const { lang, t } = useI18n(),
    zh = lang === "zh";
  const checked = tools.selecting && tools.selected.has(a.id);
  const locked = a.status === "locked";
  return (
    <article
      className={`flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card transition-colors ${checked ? "border-primary ring-1 ring-primary" : "hover:border-border-strong"}`}
    >
      <button
        type="button"
        onClick={() => tools.activate(a)}
        aria-pressed={tools.selecting ? checked : undefined}
        aria-label={`${tools.selecting ? (zh ? "选择" : "Select") : zh ? "快速预览" : "Quick preview"} ${a.name}`}
        disabled={tools.selecting && (locked || tools.blocked)}
        className="block w-full min-w-0 text-left disabled:opacity-50"
      >
        <div
          className={`relative overflow-hidden bg-elevated ${a.representative_blob_hash ? "aspect-video" : pageHasArt ? "h-20 min-[480px]:aspect-video min-[480px]:h-auto" : "h-20"}`}
        >
          {a.representative_blob_hash ? (
            <MediaImage
              src={blobUrl(tools.projectId, a.representative_blob_hash, "thumbnail")}
              alt={a.name}
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center gap-3 px-4 text-muted-foreground">
              <span className="text-3xl font-medium" aria-hidden>
                {a.name.slice(0, 1)}
              </span>
              <span className="text-xs">{zh ? "待创作画面" : "Awaiting artwork"}</span>
            </div>
          )}
          {(locked || tools.selecting) && (
            <span className="absolute right-2 top-2 grid size-7 place-items-center rounded-md bg-card/90">
              {locked ? (
                <Lock size={15} aria-label={zh ? "已锁定" : "Locked"} />
              ) : checked ? (
                <CheckSquare size={17} className="text-primary" />
              ) : (
                <Square size={17} />
              )}
            </span>
          )}
        </div>
        <div className="px-4 pt-3">
          <h2 className="truncate text-sm font-semibold">{a.name}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {t(`asset.${a.type}`)} · {a.code}
          </p>
        </div>
      </button>
      {a.summary && (
        <p className="mx-4 mt-2 line-clamp-2 break-words text-xs leading-5 text-muted-foreground">
          {a.summary}
        </p>
      )}
      {!!a.tags.length && (
        <p className="mx-4 mt-2 truncate text-xs text-muted-foreground">
          {a.tags
            .slice(0, 3)
            .map((tg) => "#" + tg)
            .join(" · ")}
        </p>
      )}
      <div className="mt-auto flex min-h-11 flex-wrap items-center gap-x-3 px-4 py-1.5 text-xs text-muted-foreground">
        <span title={t("detail.ref")} className="inline-flex items-center gap-1">
          <Images size={14} />
          {a.ref_count ?? 0}
        </span>
        <span title={t("detail.gen")} className="inline-flex items-center gap-1">
          <Sparkles size={14} />
          {a.gen_count ?? 0}
        </span>
        <ShotCount asset={a} tools={tools} />
        {!tools.selecting && <span className="ml-auto">{tools.menu(a)}</span>}
      </div>
    </article>
  );
}

function AssetList({
  assets,
  tools,
  sortKey,
  sortDir,
  onSort,
}: {
  assets: Asset[];
  tools: BrowseTools;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const { t, lang } = useI18n(),
    zh = lang === "zh";
  const columns: { key?: SortKey; text: string; right?: boolean }[] = [
    { key: "name", text: t("assets.colName") },
    { key: "type", text: t("assets.colType") },
    { text: t("assets.colTags") },
    { key: "ref_count", text: t("detail.ref"), right: true },
    { key: "gen_count", text: t("detail.gen"), right: true },
    { key: "shot_count", text: t("assets.colShots"), right: true },
  ];
  const subject = (a: Asset) => {
    const checked = tools.selected.has(a.id),
      locked = a.status === "locked";
    return (
      <button
        type="button"
        className="flex min-h-11 min-w-0 flex-1 items-center gap-2.5 text-left disabled:opacity-50"
        disabled={tools.selecting && (locked || tools.blocked)}
        onClick={() => tools.activate(a)}
        aria-label={`${tools.selecting ? (zh ? "选择" : "Select") : zh ? "快速预览" : "Quick preview"} ${a.name}`}
        aria-pressed={tools.selecting ? checked : undefined}
      >
        {tools.selecting &&
          (checked ? (
            <CheckSquare size={16} className="shrink-0 text-primary" />
          ) : (
            <Square size={16} className="shrink-0 text-muted-foreground" />
          ))}
        <span className="grid size-10 shrink-0 place-items-center overflow-hidden rounded-md bg-elevated">
          {a.representative_blob_hash ? (
            <img
              loading="lazy"
              alt=""
              className="size-full object-cover"
              src={blobUrl(tools.projectId, a.representative_blob_hash, "thumbnail")}
            />
          ) : (
            <ImageOff size={16} className="text-muted-foreground" />
          )}
        </span>
        <span className="min-w-0">
          <span className="flex items-center gap-1 break-words text-sm font-medium">
            {a.name}
            {locked && (
              <Lock size={13} className="shrink-0" aria-label={zh ? "已锁定" : "Locked"} />
            )}
          </span>
          <span className="block break-all text-xs text-muted-foreground">
            <span className="sm:hidden">{t(`asset.${a.type}`)} · </span>
            {a.code}
          </span>
        </span>
      </button>
    );
  };
  const rowClass = (id: string) =>
    tools.selecting && tools.selected.has(id) ? "bg-primary/10" : "";
  return (
    <>
      <div className="space-y-2 sm:hidden">
        {assets.map((a) => (
          <article key={a.id} className={`rounded-lg border bg-card p-3 ${rowClass(a.id)}`}>
            <div className="flex items-center justify-between gap-2">
              {subject(a)}
              {!tools.selecting && tools.menu(a)}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
              <span>
                {t("detail.ref")} {a.ref_count ?? 0}
              </span>
              <span>
                {t("detail.gen")} {a.gen_count ?? 0}
              </span>
              <ShotCount asset={a} tools={tools} />
            </div>
            {!!a.tags.length && (
              <p className="mt-1 break-words text-xs text-muted-foreground">
                {a.tags
                  .slice(0, 3)
                  .map((tg) => "#" + tg)
                  .join(" · ")}
              </p>
            )}
          </article>
        ))}
      </div>
      <div className="hidden overflow-x-auto rounded-lg border bg-card sm:block">
        <table className="w-full min-w-[620px] text-left text-sm">
          <thead className="border-b bg-elevated/40">
            <tr>
              {columns.map((col) => (
                <th
                  scope="col"
                  key={col.text}
                  aria-sort={
                    col.key === sortKey
                      ? sortDir === "asc"
                        ? "ascending"
                        : "descending"
                      : undefined
                  }
                  className={`px-3 text-xs font-medium text-muted-foreground ${col.right ? "text-right" : ""}`}
                >
                  {col.key ? (
                    <button
                      type="button"
                      onClick={() => onSort(col.key!)}
                      className="inline-flex min-h-11 items-center gap-1"
                    >
                      {col.text}
                      {col.key === sortKey &&
                        (sortDir === "asc" ? <ChevronUp size={13} /> : <ChevronDown size={13} />)}
                    </button>
                  ) : (
                    col.text
                  )}
                </th>
              ))}
              {!tools.selecting && (
                <th scope="col" className="w-12">
                  <span className="sr-only">{zh ? "更多操作" : "Actions"}</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {assets.map((a) => (
              <tr
                key={a.id}
                className={`cursor-pointer hover:bg-muted/40 ${rowClass(a.id)}`}
                onClick={(event) => {
                  if (!(event.target as HTMLElement).closest("button, a")) tools.activate(a);
                }}
              >
                <td className="max-w-xs px-3 py-2">{subject(a)}</td>
                <td className="px-3 py-2 text-muted-foreground">{t(`asset.${a.type}`)}</td>
                <td className="max-w-44 break-words px-3 py-2 text-xs text-muted-foreground">
                  {a.tags
                    .slice(0, 3)
                    .map((tg) => "#" + tg)
                    .join(" · ")}
                </td>
                <td className="px-3 py-2 text-right text-muted-foreground">{a.ref_count ?? 0}</td>
                <td className="px-3 py-2 text-right text-muted-foreground">{a.gen_count ?? 0}</td>
                <td className="px-3 py-2 text-right">
                  <ShotCount asset={a} tools={tools} compact />
                </td>
                {!tools.selecting && <td className="px-2 text-right">{tools.menu(a)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
