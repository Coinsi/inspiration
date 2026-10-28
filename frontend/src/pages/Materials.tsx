import {
  MaterialProfile,
  MaterialProfileEditor,
} from "@/components/MaterialProfile";
import { HeaderMenu, headerMenuItem } from "@/components/ui/header-menu";
import { useAuth } from "@/lib/auth";
import { SearchField } from "@/components/ui/search-field";
import { SelectField } from "@/components/ui/select-field";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AudioLines,
  ArrowLeft,
  ArrowUpRight,
  ChevronDown,
  Download,
  Film,
  Image as ImageIcon,
  MoreHorizontal,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  Users,
} from "lucide-react";
import { api, blobUrl } from "@/lib/api";
import {
  mediaSize,
  mediaStatus,
  mediaTime,
  type LibraryMedia,
  type MediaVersion,
} from "@/lib/library";
import { MaterialTabs } from "@/components/MaterialTabs";
import { AdoptMaterial } from "@/components/AdoptMaterial";
import { MaterialVideoPreview } from "@/components/MaterialVideoPreview";
import { MediaImage } from "@/components/MediaImage";
import { MediaPreview } from "@/components/MediaPreview";
import { MediaVideo } from "@/components/MediaVideo";
import { LibraryUpload } from "@/components/LibraryUpload";
import { LibrarySearch, type SegmentResult } from "@/components/LibrarySearch";
import { LibraryProcessing } from "@/components/LibraryProcessing";
import { LibraryReuse } from "@/components/LibraryReuse";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import type { GeneratedMaterial } from "./GeneratedLibrary";
import "./materials.css";

const Detail = lazy(() =>
  import("./MediaLibrary").then((m) => ({ default: m.LibraryDetail })),
);
const Impact = lazy(() =>
  import("./MediaLibrary").then((m) => ({ default: m.ImpactDialog })),
);
type Material = {
  adopted: boolean;
  is_test: boolean;
  id: string;
  source: "upload" | "generated" | "asset" | "attachment";
  name: string;
  description?: string;
  tags?: string[] | null;
  metadata_revision?: number;
  kind: "image" | "video" | "audio" | "entity";
  created_at: string;
  deleted_at: string | null;
  status: string;
  poster_hash: string | null;
  blob_hash: string | null;
  preview_hash: string | null;
  size_bytes: number;
  duration_ms: number | null;
  version_id: string | null;
};
const sourceNames = {
  upload: "上传与复用",
  generated: "AI 生成",
  asset: "角色与场景",
  attachment: "对话附件",
};
const kindNames = {
  image: "图片",
  video: "视频",
  audio: "音频",
  entity: "角色与场景",
};

export default function Materials() {
  const { projectId } = useParams();
  return <Collection key={projectId} projectId={projectId!} />;
}
function Collection({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
  const root = `/projects/${projectId}`,
    base = `${root}/library`,
    qc = useQueryClient(),
    confirm = useConfirm();
  const tab = params.get("tab") || "browse",
    deleted = tab === "trash";
  const kind = params.get("kind") || "",
    source = params.get("source") || "",
    query = params.get("q") || "";
  const page = Math.max(0, Number(params.get("page")) || 0);
  const [search, setSearch] = useState(query);
  const media = params.get("media") || "",
    generation = params.get("generation") || "",
    attachment = params.get("attachment") || "";
  const [segment, setSegment] = useState<SegmentResult | null>(null);
  const [impact, setImpact] = useState<string | null>(null);
  const [upload, setUpload] = useState<{
    mediaId?: string;
    resume?: MediaVersion;
  } | null>(null);
  const [reuse, setReuse] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Material | null>(null);
  const { me } = useAuth();
  const canEdit = me?.memberships.some(
    (m) =>
      m.project_id === projectId &&
      ["admin", "director", "artist"].includes(m.role),
  );
  useEffect(
    () => setPreviewId(null),
    [media, generation, attachment, tab, page, kind, source, query, editing],
  );
  const returnTo = params.get("return")?.startsWith(`${root}/`)
    ? params.get("return")!
    : `${root}/workbench`;
  const fromConversation =
    returnTo.split("?")[0] === `${root}/workbench` &&
    new URLSearchParams(returnTo.split("?")[1]).has("conversation");
  const creativeLink = (key: string, value: string) => {
    const next = new URLSearchParams(
      fromConversation ? returnTo.split("?")[1] : "",
    );
    next.delete("message");
    next.delete("ordinal");
    next.delete("reference");
    next.delete("reuse");
    next.delete("attachment");
    next.set(key, value);
    return `${root}/workbench?${next}`;
  };
  const choose = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch))
      v ? next.set(k, v) : next.delete(k);
    if (!("page" in patch)) next.delete("page");
    setParams(next);
  };
  useEffect(() => setSearch(query), [query]);
  useEffect(() => {
    if (search === query) return;
    const timer = setTimeout(() => choose({ q: search }), 300);
    return () => clearTimeout(timer);
  }, [search, query]);
  const list = useQuery({
    queryKey: ["materials", projectId, deleted, kind, source, query, page],
    queryFn: () =>
      api.get<{ items: Material[]; total: number }>(
        `${base}/materials?${new URLSearchParams({ deleted: String(deleted), offset: String(page * 24), query, ...(kind ? { kind } : {}), ...(source ? { source } : {}) })}`,
      ),
    refetchInterval: 5000,
  });
  useEffect(() => {
    if (list.data && page > 0 && page * 24 >= list.data.total)
      choose({
        page: String(Math.max(0, Math.ceil(list.data.total / 24) - 1)),
      });
  }, [list.data, page]);
  const detail = useQuery({
    queryKey: ["library-item", projectId, media],
    queryFn: () => api.get<LibraryMedia>(`${base}/items/${media}`),
    enabled: !!media,
    refetchInterval: media ? 4000 : false,
  });
  const generated = useQuery({
    queryKey: ["material-generation", projectId, generation],
    queryFn: () =>
      api.get<GeneratedMaterial>(`${base}/generated/${generation}`),
    enabled: !!generation,
  });
  const attached = useQuery({
    queryKey: ["material-attachment", projectId, attachment],
    queryFn: () => api.get<{
      id: string; title: string; filename: string; kind: "image" | "video" | "audio";
      blob_hash: string; thumbnail_hash?: string; description: string; tags: string[];
      metadata_revision: number; conversation_id?: string; message_id?: string; message_ordinal?: number;
      duration_ms?: number; size_bytes: number;
    }>(`${root}/attachments/${attachment}`),
    enabled: !!attachment,
  });
  const refresh = () => {
    for (const key of [
      "materials",
      "material-attachment",
      "conversation-attachments",
      "saved-conversation-attachments",
      "workbench-references",
      "library",
      "library-item",
      "generated-library",
      "assets",
      "trash",
      "library-folders",
    ])
      void qc.invalidateQueries({ queryKey: [key, projectId] });
  };
  const mutation = useMutation({
    mutationFn: ({
      item,
      action,
    }: {
      item: Material;
      action: "restore" | "trash";
    }) => api.post(`${base}/materials/${item.source}/${item.id}/${action}`),
    onSuccess: refresh,
  });
  const closeDetail = () => {
    setSegment(null);
    choose({ media: "", generation: "", attachment: "", version: "", start: "", end: "" });
  };
  const open = (item: Material) => {
    mutation.reset();
    setSegment(null);
    choose(
      item.source === "attachment"
        ? { attachment: item.id, media: "", generation: "" }
        : item.source === "upload"
        ? { media: item.id, generation: "", attachment: "" }
        : { generation: item.id, media: "", attachment: "" },
    );
  };
  async function trash(item: Material) {
    if (item.source === "upload") {
      setImpact(item.id);
      return;
    }
    if (
      await confirm({
        title: `移入回收站：${item.name.slice(0, 36)}`,
        message:
          "从素材列表移除，原文件与已使用的创作结果保留。可以在回收站恢复。",
        confirmText: "移入回收站",
        danger: true,
      })
    )
      mutation.mutate({ item, action: "trash" });
  }
  return (
    <div className="studio-page material-workspace unified-materials">
      <header className="materials-heading">
        <div>
          <h1>{deleted ? "素材回收站" : "素材库"}</h1>
          {deleted && <p>上传素材、生成结果和角色设定，在这里统一找回。</p>}
        </div>
        <div className="materials-heading-actions">
          <Link to={returnTo}>
            {fromConversation
              ? "返回对话"
              : returnTo.split("?")[0].endsWith("/tasks")
                ? "返回任务"
                : "返回创作"}{" "}
            ↗
          </Link>
          {!deleted && (
            <Button onClick={() => setUpload({})}>
              <Plus size={16} />
              导入素材
            </Button>
          )}
        </div>
      </header>
      <div className="materials-navigation">
      <MaterialTabs projectId={projectId} compact />
      {!deleted && (
        <div className="materials-views" role="tablist" aria-label="素材视图">
          {[
            ["browse", "浏览素材"],
            ["search", "查找片段"],
            ["processing", "处理进度"],
          ].map(([v, label]) => (
            <button
              key={v}
              role="tab"
              aria-selected={tab === v}
              onClick={() => choose({ tab: v, media: "", generation: "", attachment: "" })}
            >
              {label}
            </button>
          ))}
          <details className="materials-more">
            <summary>更多操作</summary>
            <div>
              <button onClick={() => setReuse(true)}>从其他项目复用</button>
              <Link to={`${root}/library`}>管理文件与版本</Link>
            </div>
          </details>
        </div>
      )}
      </div>
      {tab === "search" ? (
        <LibrarySearch
          projectId={projectId}
          onSelect={(s) => {
            setSegment(s);
            choose({ media: s.media_id, generation: "", attachment: "" });
          }}
        />
      ) : tab === "processing" ? (
        <LibraryProcessing projectId={projectId} />
      ) : (
        <>
          <div className="materials-toolbar">
            <div role="group" aria-label="素材类型" className="materials-kinds">
              {[
                ["", "全部"],
                ["video", "视频"],
                ["image", "图片"],
                ["audio", "音频"],
                ...(deleted ? [["entity", "角色与场景"]] : []),
              ].map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={kind === value}
                  onClick={() => choose({ kind: value })}
                >
                  {label}
                </button>
              ))}
            </div>
            <SearchField
              label="搜索全部素材"
              placeholder="搜索名称、说明、标签或提示词"
              value={search}
              onValueChange={setSearch}
              className="ml-auto w-full sm:w-72"
            />
            <SelectField
              label="素材来源"
              value={source}
              onChange={(value) => choose({ source: value })}
              options={[
                { value: "", label: "全部来源" },
                { value: "upload", label: "上传与复用" },
                { value: "generated", label: "AI 生成" },
                { value: "attachment", label: "对话附件" },
                ...(deleted ? [{ value: "asset", label: "角色与场景" }] : []),
              ]}
            />
          </div>
          <p className="materials-total" role="status">
            {list.data
              ? `${query ? `“${query}” · ` : ""}${list.data.total} 项素材`
              : "正在读取素材…"}
          </p>
          {mutation.error && (
            <p role="alert" className="text-sm text-danger">
              {mutation.error.message}
            </p>
          )}
          {list.isError ? (
            <div className="materials-empty" role="alert">
              <p>{list.error.message}</p>
              <Button onClick={() => list.refetch()}>重新加载</Button>
            </div>
          ) : list.isPending ? (
            <p className="materials-empty" role="status">
              正在整理素材…
            </p>
          ) : !list.data.items.length ? (
            <div className="materials-empty">
              <ImageIcon size={28} />
              <h2>
                {deleted
                  ? "回收站为空"
                  : query || kind || source
                    ? "没有匹配的素材"
                    : "让素材成为创作的起点"}
              </h2>
              <p>
                {query || kind || source
                  ? "试试更短的关键词，或清除类型、来源筛选。当前搜索匹配素材名称与提示词，查找视频内容请使用「查找片段」。"
                  : deleted
                    ? "移除的素材会在这里保留，恢复后即可继续使用。"
                    : "导入文件，或在工作台生成图片和视频，它们会出现在同一个地方。"}
              </p>
              {(query || kind || source) && (
                <Button
                  variant="outline"
                  onClick={() => choose({ q: "", source: "", kind: "" })}
                >
                  清除筛选
                </Button>
              )}
            </div>
          ) : (
            <div className="materials-grid">
              {list.data.items.map((item) => {
                const previewKey = `${item.source}:${item.id}`;
                const videoSrc =
                  item.kind === "video" &&
                  !item.is_test &&
                  item.status === "ready" &&
                  !deleted &&
                  (item.preview_hash || item.blob_hash);
                return (
                  <article className="material-tile" key={previewKey}>
                    <div className="material-thumb">
                      {videoSrc ? (
                        <MaterialVideoPreview
                          src={blobUrl(projectId, videoSrc)}
                          poster={
                            item.poster_hash
                              ? blobUrl(projectId, item.poster_hash)
                              : undefined
                          }
                          label={item.name}
                          active={previewId === previewKey}
                          onActiveChange={(active) =>
                            setPreviewId((current) =>
                              active
                                ? previewKey
                                : current === previewKey
                                  ? null
                                  : current,
                            )
                          }
                          onOpen={() => open(item)}
                        />
                      ) : (
                        <button
                          className="material-preview-open"
                          aria-label={`预览 ${item.name}`}
                          disabled={deleted}
                          onClick={() => open(item)}
                        >
                          {item.is_test ? (
                            <span className="text-sm">离线测试占位</span>
                          ) : item.poster_hash ||
                            (item.kind === "image" && item.blob_hash) ? (
                            <MediaImage
                              src={blobUrl(
                                projectId,
                                item.poster_hash || item.blob_hash!,
                              )}
                              alt={item.name}
                            />
                          ) : item.kind === "video" ? (
                            <Film size={34} />
                          ) : item.kind === "audio" ? (
                            <AudioLines size={34} />
                          ) : (
                            <Users size={34} />
                          )}
                        </button>
                      )}
                      <span className="material-kind">
                        {kindNames[item.kind]}
                        {item.duration_ms
                          ? ` · ${mediaTime(item.duration_ms)}`
                          : ""}
                      </span>
                      {item.status !== "ready" && (
                        <span className="material-status">
                          {mediaStatus(item.status, true)}
                        </span>
                      )}
                    </div>
                    <div className="material-tile-body">
                      <h2 title={item.name}>
                        <button disabled={deleted} onClick={() => open(item)}>
                          {item.name}
                        </button>
                      </h2>
                      <div className="material-tile-meta">
                        <p>
                          {item.is_test ? "离线测试" : sourceNames[item.source]}
                          {item.source === "generated" &&
                            (item.adopted ? " · 已采用" : " · 候选")}
                        </p>
                        {deleted ? (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={mutation.isPending}
                            onClick={() =>
                              mutation.mutate({ item, action: "restore" })
                            }
                          >
                            恢复
                          </Button>
                        ) : (
                          <HeaderMenu
                            label={`更多操作：${item.name}`}
                            trigger={<MoreHorizontal size={19} />}
                            className="material-card-menu"
                          >
                            <button
                              role="menuitem"
                              className={headerMenuItem}
                              onClick={() => open(item)}
                            >
                              <ImageIcon size={16} />
                              查看详情
                            </button>
                            {canEdit && item.source !== "asset" && (
                              <button
                                role="menuitem"
                                className={headerMenuItem}
                                onClick={() => setEditing(item)}
                              >
                                <Pencil size={16} />
                                编辑资料
                              </button>
                            )}
                            {item.blob_hash && (
                              <a
                                role="menuitem"
                                className={headerMenuItem}
                                href={`${blobUrl(projectId, item.blob_hash)}&download=true`}
                                download
                              >
                                <Download size={16} />
                                下载原文件
                              </a>
                            )}
                            <button
                              role="menuitem"
                              className={`${headerMenuItem} text-danger`}
                              disabled={mutation.isPending}
                              onClick={() => void trash(item)}
                            >
                              <Trash2 size={16} />
                              移入回收站
                            </button>
                          </HeaderMenu>
                        )}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          {!!list.data && list.data.total > 24 && (
            <div className="materials-pagination">
              <Button
                variant="outline"
                disabled={page === 0}
                onClick={() => choose({ page: String(page - 1) })}
              >
                上一页
              </Button>
              <span>
                {page + 1} / {Math.ceil(list.data.total / 24)}
              </span>
              <Button
                variant="outline"
                disabled={(page + 1) * 24 >= list.data.total}
                onClick={() => choose({ page: String(page + 1) })}
              >
                下一页
              </Button>
            </div>
          )}
        </>
      )}
      {((detail.isError && media) || (generated.isError && generation) || (attached.isError && attachment)) && (
        <div role="alert" className="materials-error">
          {detail.error?.message || generated.error?.message || attached.error?.message}
          <Button
            onClick={() => {
              if (media) void detail.refetch();
              if (attachment) void attached.refetch();
              if (generation) void generated.refetch();
            }}
          >
            重试
          </Button>
          <Button variant="ghost" onClick={closeDetail}>
            关闭
          </Button>
        </div>
      )}
      {media && detail.data?.deleted_at && (
        <div className="materials-error" role="status">
          这份素材已在回收站。
          <Button onClick={() => choose({ tab: "trash", media: "" })}>
            前往回收站
          </Button>
          <Button variant="ghost" onClick={closeDetail}>
            关闭
          </Button>
        </div>
      )}
      <Suspense fallback={<p role="status">正在打开素材…</p>}>
        {media && detail.data && !detail.data.deleted_at && (
          <Detail
            key={`${media}:${segment?.id || ""}:${params.get("version") || ""}`}
            projectId={projectId}
            item={detail.data}
            segment={segment}
            onClose={closeDetail}
            onUpload={() => {
              closeDetail();
              setUpload({ mediaId: media });
            }}
            onChange={refresh}
          />
        )}
        {impact && (
          <Impact
            projectId={projectId}
            mediaId={impact}
            onClose={() => setImpact(null)}
            onChange={refresh}
          />
        )}
      </Suspense>
      <Modal
        open={!!generation && !!generated.data}
        title={
          <span className="flex items-center gap-4">
            {fromConversation && (
              <Link
                className="inline-flex items-center gap-1 text-sm text-primary"
                to={returnTo}
              >
                <ArrowLeft size={15} />
                返回对话
              </Link>
            )}
            <span>素材详情</span>
          </span>
        }
        width={1180}
        bodyClassName="material-result-body"
        onClose={closeDetail}
      >
        {generated.data && (
          <div className="material-result-detail">
            <div className="material-result-preview">
              {generated.data.provider === "mock" ? (
                <p className="materials-empty">
                  离线测试占位内容，不是真实模型生成结果。
                </p>
              ) : generated.data.output_type === "image" ? (
                <MediaPreview
                  src={blobUrl(projectId, generated.data.blob_hash)}
                  label={generated.data.title}
                  filename={`inspiration-${generation}`}
                />
              ) : (
                <MediaVideo
                  src={blobUrl(projectId, generated.data.blob_hash)}
                  label={generated.data.title}
                  presentation="studio"
                  poster={
                    generated.data.poster_hash
                      ? blobUrl(projectId, generated.data.poster_hash)
                      : undefined
                  }
                />
              )}
            </div>
            <aside className="material-result-info">
              <div className="material-result-kind">
                {generated.data.output_type === "image" ? (
                  <ImageIcon size={15} />
                ) : (
                  <Film size={15} />
                )}
                {generated.data.output_type === "image"
                  ? "生成图片"
                  : "生成视频"}
                <span>已保存到素材库</span>
              </div>
              {generated.data.title.length > 60 ? (
                <details className="material-result-title">
                  <summary>
                    <h2>{generated.data.title}</h2>
                    <ChevronDown size={15} aria-hidden="true" />
                    <span className="sr-only">展开或收起素材名称</span>
                  </summary>
                </details>
              ) : (
                <h2>{generated.data.title}</h2>
              )}
              <MaterialProfile
                projectId={projectId}
                source="generated"
                item={{ ...generated.data, name: generated.data.title }}
                video={
                  generated.data.output_type === "video" &&
                  generated.data.provider !== "mock"
                    ? { src: blobUrl(projectId, generated.data.blob_hash) }
                    : undefined
                }
              />
              <dl className="material-result-facts">
                <div>
                  <dt>生成模型</dt>
                  <dd>{generated.data.model_name}</dd>
                </div>
                <div>
                  <dt>文件信息</dt>
                  <dd>
                    {[
                      generated.data.width && generated.data.height
                        ? `${generated.data.width} × ${generated.data.height}`
                        : null,
                      generated.data.duration_ms
                        ? mediaTime(generated.data.duration_ms)
                        : null,
                      mediaSize(generated.data.size_bytes),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </dd>
                </div>
                <div>
                  <dt>生成时间</dt>
                  <dd>
                    {new Date(generated.data.created_at).toLocaleString(
                      "zh-CN",
                      {
                        year: "numeric",
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                        hour12: false,
                      },
                    )}
                  </dd>
                </div>
                {generated.data.work && (
                  <div>
                    <dt>来源作品</dt>
                    <dd>
                      <Link
                        to={`${root}/scripts/${generated.data.work.script_id}`}
                      >
                        {generated.data.work.title} ↗
                      </Link>
                    </dd>
                  </div>
                )}
              </dl>
              <div className="material-use-actions">
                {generated.data.reference_available && (
                  <Link
                    className="studio-primary"
                    to={creativeLink("reference", generation)}
                  >
                    <Sparkles size={15} />
                    用作生成参考
                  </Link>
                )}
                <Link
                  className="material-result-secondary"
                  to={creativeLink("reuse", generation)}
                >
                  复用提示词
                </Link>
              </div>
              <div className="material-result-utilities">
                <a
                  href={`${blobUrl(projectId, generated.data.blob_hash)}&download=true`}
                  download
                >
                  <Download size={15} />
                  下载原文件
                </a>
                <Link
                  to={`${root}/tasks?job=${generated.data.job_id}&return=${encodeURIComponent(fromConversation ? returnTo : `${root}/materials?${params}`)}`}
                >
                  <ArrowUpRight size={15} />
                  来源任务
                </Link>
              </div>
              <div className="material-result-usage">
                <h3>作品使用</h3>
                <AdoptMaterial
                  key={generation}
                  projectId={projectId}
                  generationId={generation}
                  kind={generated.data.output_type}
                />
              </div>
              <details className="material-result-prompt">
                <summary>
                  生成提示词
                  <ChevronDown size={15} aria-hidden="true" />
                </summary>
                <p>{generated.data.prompt || "没有提示词记录"}</p>
              </details>
            </aside>
          </div>
        )}
      </Modal>
      <Modal open={!!attachment && !!attached.data} title="对话附件" width={1100}
        bodyClassName="material-result-body" onClose={closeDetail}>
        {attached.data && <div className="material-result-detail">
          <div className="material-result-preview">
            {attached.data.kind === "image" ? <MediaPreview
              src={blobUrl(projectId, attached.data.blob_hash)} label={attached.data.title}
              filename={attached.data.filename} />
              : attached.data.kind === "video" ? <MediaVideo
                src={blobUrl(projectId, attached.data.blob_hash)} label={attached.data.title} />
              : <audio className="w-full" controls preload="metadata"
                src={blobUrl(projectId, attached.data.blob_hash)} aria-label={attached.data.title} />}
          </div>
          <aside className="material-result-info">
            <div className="material-result-kind">{kindNames[attached.data.kind]} · 对话附件</div>
            <h2 className="text-xl font-semibold break-words">{attached.data.title}</h2>
            <MaterialProfile projectId={projectId} source="attachment"
              item={{ ...attached.data, name: attached.data.title }} />
            <dl className="material-result-facts">
              <div><dt>原始文件</dt><dd>{attached.data.filename}</dd></div>
              <div><dt>文件信息</dt><dd>{mediaSize(attached.data.size_bytes)}{attached.data.duration_ms ? ` · ${mediaTime(attached.data.duration_ms)}` : ""}</dd></div>
            </dl>
            <div className="material-use-actions"><Link className="studio-primary" to={creativeLink("attachment", attached.data.id)}><Sparkles size={15} />用于创作</Link></div>
            <div className="material-result-utilities">
              <a download href={`${blobUrl(projectId, attached.data.blob_hash)}&download=true`}><Download size={15} />下载原文件</a>
              {attached.data.conversation_id && <Link
                to={`${root}/workbench?view=chat&conversation=${attached.data.conversation_id}${attached.data.message_id ? `&message=${attached.data.message_id}` : ""}${attached.data.message_ordinal ? `&ordinal=${attached.data.message_ordinal}` : ""}`}>
                返回来源对话 <ArrowUpRight size={14} />
              </Link>}
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed">发送时自动保存。修改名称或从素材列表移除，不会更改对话中的原文件。</p>
          </aside>
        </div>}
      </Modal>
      {editing && editing.source !== "asset" && (
        <MaterialProfileEditor
          projectId={projectId}
          source={editing.source}
          item={editing}
          video={
            editing.source !== "attachment" && editing.kind === "video" &&
            editing.status === "ready" &&
            !editing.is_test &&
            (editing.preview_hash || editing.blob_hash)
              ? {
                  src: blobUrl(
                    projectId,
                    (editing.preview_hash || editing.blob_hash)!,
                  ),
                  versionId: editing.version_id ?? undefined,
                }
              : undefined
          }
          onClose={() => setEditing(null)}
        />
      )}
      {upload && (
        <LibraryUpload
          projectId={projectId}
          {...upload}
          onClose={() => {
            setUpload(null);
            refresh();
          }}
          onChange={refresh}
        />
      )}
      {reuse && (
        <LibraryReuse
          projectId={projectId}
          onClose={() => setReuse(false)}
          onDone={(result) => {
            setReuse(false);
            refresh();
            choose({
              media: result.media_id,
              version: result.version_id,
              tab: "browse",
            });
          }}
        />
      )}
    </div>
  );
}
