import SkillPicker from "@/components/SkillPicker";
import { createContext, useContext, useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Handle,
  Position,
  NodeResizer,
  NodeToolbar,
  useUpdateNodeInternals,
  type NodeProps,
} from "@xyflow/react";
import {
  FileText,
  ImagePlus,
  SlidersHorizontal,
  Sparkles,
  Images,
  Film,
  Music,
  Loader2,
  Grid2X2,
} from "lucide-react";
import {
  api,
  blobUrl,
  type Generation,
  type Asset,
  type Shot,
} from "@/lib/api";
import { type CanvasNode, type NodeData, labels, statuses } from "@/lib/canvas";
import { MediaImage } from "./MediaImage";
import { MediaVideo } from "./MediaVideo";
import { MediaAudio } from "./MediaAudio";
import { Lightbox } from "./ImageViewer";
import { Button } from "./ui/button";
import { Modal } from "./ui/modal";
import { CanvasMentionInput, type MentionOption } from "./CanvasMentionInput";
import GenerationPanel from "./GenerationPanel";
import { CanvasMediaImport } from "./CanvasMediaImport";

export const CanvasActions = createContext<{
  projectId: string;
  editImage: (g: Generation) => void;
  reuse: boolean;
  mentionOptions: (id: string) => MentionOption[];
  mention: (id: string, option: MentionOption, text: string) => void;
  busy: boolean;
  single: boolean;
  running: boolean;
  run: (id: string) => void;
  references: (id: string) => { id: string; label: string; image: boolean }[];
  disconnect: (id: string) => void;
  update: (id: string, patch: Partial<NodeData>) => void;
  settings: (id: string) => void;
  versions: (id: string) => void;
  branch: (id: string, image: boolean, generation?: Generation) => void;
  split: (id: string, generation: Generation) => void;
}>({
  projectId: "",
  editImage: () => {},
  reuse: false,
  mentionOptions: () => [],
  mention: () => {},
  busy: false,
  single: false,
  running: false,
  run: () => {},
  references: () => [],
  disconnect: () => {},
  update: () => {},
  settings: () => {},
  versions: () => {},
  branch: () => {},
  split: () => {},
});

export function useCanvasVersions(
  projectId: string,
  type: string,
  id: string | null,
) {
  return useQuery({
    queryKey: ["gens", type, id],
    queryFn: () =>
      api.get<Generation[]>(
        `/projects/${projectId}/generations?target_type=${type}&target_id=${id}`,
      ),
    enabled: !!id,
    staleTime: 15000,
  });
}
export function CanvasMedia({
  src,
  kind,
  label,
}: {
  src: string;
  kind: string;
  label: string;
}) {
  if (kind === "video") return <MediaVideo src={src} label={label} />;
  if (kind === "audio") return <MediaAudio key={src} src={src} />;
  return <MediaImage src={src} alt={label} />;
}
export function CanvasCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const actions = useContext(CanvasActions),
    [large, setLarge] = useState(false),
    [originalOpen, setOriginalOpen] = useState(false);
  const versions = useCanvasVersions(
    actions.projectId,
    data.target_type,
    data.kind === "group" || data.kind === "text" ? null : data.target_id,
  );
  const versionId = data.generation_id || (data.outputId as string | undefined);
  useEffect(() => {
    if (data.outputId) void versions.refetch();
  }, [data.outputId]);
  const version = versionId
    ? versions.data?.find((g) => g.id === versionId)
    : data.kind !== "generate"
      ? versions.data?.find((g) => g.is_selected)
      : undefined;
  const original = versions.data?.find(
    (g) => g.id === version?.input_refs?.source_generation_id,
  );
  const src = version?.output_blob_hash
    ? blobUrl(actions.projectId, version.output_blob_hash)
    : !versionId
      ? data.preview
      : undefined;
  const kind =
    version?.output_type ||
    (data.kind === "generate" ? data.generate.request_type : "image");
  const references = actions.references(id);
  const capabilities = useQuery({
    queryKey: ["capabilities", actions.projectId, data.generate.provider],
    queryFn: () =>
      api.get<{ features: string[] }>(
        `/projects/${actions.projectId}/providers/${data.generate.provider}/capabilities`,
      ),
    enabled: data.kind === "generate",
    staleTime: 30000,
  });
  const unsupportedReferences =
    data.generate.request_type === "image" &&
    references.some((r) => r.image) &&
    capabilities.data &&
    !capabilities.data.features.includes("img2img");
  const updateInternals = useUpdateNodeInternals();
  useEffect(() => updateInternals(id), [id, kind, !!src, updateInternals]);
  const editing = selected && actions.single;
  if (data.kind === "group")
    return (
      <>
        <NodeResizer isVisible={selected} minWidth={300} minHeight={220} />
        <div className="canvas-group-label">{data.label}</div>
      </>
    );
  return (
    <div className={`canvas-node-wrap ${editing ? "is-editing" : ""}`}>
      <NodeToolbar
        isVisible={editing}
        position={Position.Top}
        offset={14}
        className="canvas-node-actions nodrag nopan"
      >
        <Button
          size="sm"
          variant="ghost"
          disabled={actions.busy}
          onClick={() => actions.settings(id)}
        >
          <SlidersHorizontal size={14} />
          设置
        </Button>
        {data.target_id && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => actions.versions(id)}
          >
            <Images size={14} />
            版本与编辑
          </Button>
        )}
        {(data.kind === "text" || (kind === "image" && src)) && (
          <Button
            size="sm"
            variant="ghost"
            disabled={actions.busy}
            onClick={() =>
              actions.branch(id, kind === "image" && !!src, version)
            }
          >
            <Sparkles size={14} />
            {src ? "参考创作" : "延伸创作"}
          </Button>
        )}
        {kind === "image" && version?.output_blob_hash && (
          <Button
            size="sm"
            variant="ghost"
            disabled={actions.busy}
            onClick={() => actions.split(id, version)}
          >
            <Grid2X2 size={14} />
            拆分图片
          </Button>
        )}
        {["grid_split", "canvas_edit"].includes(
          version?.input_refs?.operation ?? "",
        ) &&
          original?.output_blob_hash && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOriginalOpen(true)}
            >
              来源原图
            </Button>
          )}
        {kind === "image" && version?.output_blob_hash && (
          <Button
            size="sm"
            variant="ghost"
            disabled={actions.busy}
            onClick={() => actions.editImage(version)}
          >
            绘图与裁切
          </Button>
        )}
      </NodeToolbar>
      <div
        className={`canvas-card ${selected ? "is-selected" : ""} ${src ? "has-media" : ""} ${data.kind === "text" ? "is-note" : ""}`}
      >
        <div className="canvas-card-head">
          <span className="canvas-kind">
            {data.kind === "text" ? (
              <FileText size={13} />
            ) : kind === "video" ? (
              <Film size={13} />
            ) : kind === "audio" ? (
              <Music size={13} />
            ) : (
              <ImagePlus size={13} />
            )}{" "}
            {data.kind === "generate"
              ? "生成 · " +
                ({ image: "图片", video: "视频", audio: "声音" }[kind] || kind)
              : labels[data.kind]}
          </span>
          {data.status && (
            <span className={`canvas-status status-${data.status}`}>
              {data.reused ? "已复用" : (statuses[data.status] ?? data.status)}
            </span>
          )}
        </div>
        <div className="canvas-node-title" title={data.label}>
          {data.label}
        </div>
        {src ? (
          <div className="canvas-node-media nodrag nopan nowheel">
            {kind === "image" ? (
              <button
                className="canvas-image-open"
                aria-label={`放大 ${data.label}`}
                onClick={() => setLarge(true)}
              >
                <CanvasMedia src={src} kind={kind} label={data.label} />
              </button>
            ) : (
              <CanvasMedia src={src} kind={kind} label={data.label} />
            )}
          </div>
        ) : data.kind === "text" ? (
          <div className="canvas-note-body">
            {editing ? (
              <textarea
                className="nodrag nopan nowheel"
                aria-label="节点正文"
                placeholder="写下故事、镜头或灵感…"
                maxLength={10000}
                value={data.text}
                disabled={actions.busy}
                onChange={(e) => actions.update(id, { text: e.target.value })}
              />
            ) : (
              <p>{data.text || "选中这里，写下第一个想法。"}</p>
            )}
          </div>
        ) : (
          <button
            className="canvas-node-placeholder nodrag nopan"
            onClick={() =>
              data.target_id ? actions.versions(id) : actions.settings(id)
            }
          >
            {versions.isFetching ? (
              <Loader2 className="animate-spin" size={25} />
            ) : (
              <ImagePlus size={28} strokeWidth={1.2} />
            )}
            <span>
              {versions.error
                ? "素材读取失败，点击重试或选择版本"
                : versionId
                  ? "固定版本暂不可用，点击检查"
                  : data.kind === "generate"
                    ? "等待下一幅画面"
                    : "选择一份创作素材"}
            </span>
            <small>
              {data.kind === "generate"
                ? "选中后填写提示词，设置归属与模型"
                : "从项目中引用图片、视频或声音"}
            </small>
          </button>
        )}
        <div className="canvas-ports">
          <span>
            {version?.input_refs?.operation === "grid_split"
              ? `原图分格 ${(version.input_refs.cell_index ?? 0) + 1}`
              : versionId
                ? "固定版本"
                : src
                  ? "跟随已采用版本"
                  : data.kind === "text"
                    ? "创意文字"
                    : data.target_id
                      ? "项目引用"
                      : "尚未设置"}
          </span>
          <span>{kind === "image" && src ? "图片参考 ↗" : "文字 ↗"}</span>
        </div>
      </div>
      {editing && data.kind === "generate" && (
        <div className="canvas-node-composer nodrag nopan nowheel">
          {!!references.length && (
            <div className="canvas-reference-chips">
              {references.map((ref) => (
                <button
                  key={ref.id}
                  disabled={actions.busy}
                  title="移除这条输入连线"
                  aria-label={`移除参考 ${ref.label}`}
                  onClick={() => actions.disconnect(ref.id)}
                >
                  {ref.image ? <ImagePlus size={12} /> : <FileText size={12} />}
                  <span>{ref.label}</span> ×
                </button>
              ))}
            </div>
          )}
          <label>
            这一镜，想拍什么？
            <CanvasMentionInput
              value={data.text}
              disabled={actions.busy}
              options={actions.mentionOptions(id)}
              onChange={(text) =>
                actions.update(id, {
                  text,
                  generate: { ...data.generate, prompt_override: null },
                })
              }
              onMention={(option, text) => actions.mention(id, option, text)}
            />
          </label>
          <SkillPicker
            projectId={actions.projectId}
            value={data.generate.skills ?? []}
            disabled={actions.busy}
            onChange={(skills) =>
              actions.update(id, { generate: { ...data.generate, skills } })
            }
          />
          <div>
            <span>
              {data.generate.provider === "mock"
                ? "离线测试"
                : data.generate.provider}{" "}
              · {data.generate.count} 个候选
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => actions.settings(id)}
            >
              参数与归属
            </Button>
          </div>
          <Button
            className="canvas-node-generate"
            disabled={
              actions.busy ||
              actions.running ||
              !data.target_id ||
              !!unsupportedReferences ||
              !!capabilities.error ||
              capabilities.isPending
            }
            onClick={() => actions.run(id)}
          >
            <Sparkles size={14} />
            生成这一节点
          </Button>
          {unsupportedReferences && (
            <p className="text-xs text-amber-500 mt-2">
              当前服务不支持图片参考，请在参数中更换服务。
            </p>
          )}
          {capabilities.error && (
            <button
              className="text-xs text-danger"
              onClick={() => void capabilities.refetch()}
            >
              服务能力读取失败，点击重试
            </button>
          )}
          <small>
            {!data.target_id
              ? "先在参数中选择结果所属的镜头或资产。"
              : actions.reuse
                ? "未变化的上游会复用已有结果；本节点重新生成。"
                : "包含此节点的上游依赖；上游生成节点也会重新生成。"}
          </small>
        </div>
      )}
      {data.kind === "generate" && (
        <>
          <Handle
            type="target"
            position={Position.Left}
            id="prompt"
            style={{ top: "36%" }}
            title="文字输入"
          />
          <Handle
            type="target"
            position={Position.Left}
            id="reference"
            className="image-port"
            style={{ top: "68%" }}
            title="图片输入"
          />
        </>
      )}
      <Handle
        type="source"
        position={Position.Right}
        id="text"
        style={{ top: "36%" }}
        title="文字输出"
      />
      {data.kind !== "text" &&
        kind === "image" &&
        (src || data.kind === "generate") && (
          <Handle
            type="source"
            position={Position.Right}
            id="image"
            className="image-port"
            style={{ top: "68%" }}
            title="图片输出"
          />
        )}
      {originalOpen && original?.output_blob_hash && (
        <Lightbox
          src={blobUrl(actions.projectId, original.output_blob_hash)}
          alt="拆分来源原图"
          filename="source-image.png"
          onClose={() => setOriginalOpen(false)}
        />
      )}
      {large && src && (
        <Lightbox
          src={src}
          alt={data.label}
          filename={data.label}
          onClose={() => setLarge(false)}
        />
      )}
    </div>
  );
}

export function CanvasMaterialPicker({
  projectId,
  assets,
  shots,
  onClose,
  onPick,
  loading,
  loadError,
  onRetry,
}: {
  loading: boolean;
  loadError?: string;
  onRetry: () => void;
  projectId: string;
  assets: Asset[];
  shots: Shot[];
  onClose: () => void;
  onPick: (g: Generation, label: string) => boolean;
}) {
  const [importBusy, setImportBusy] = useState(false);
  const [type, setType] = useState<"asset" | "shot">("asset"),
    [query, setQuery] = useState(""),
    [target, setTarget] = useState(""),
    [filter, setFilter] = useState("all");
  const versions = useCanvasVersions(projectId, type, target || null);
  const objects =
    type === "asset"
      ? assets.map((a) => ({ id: a.id, label: a.name, code: a.code }))
      : shots.map((s) => ({
          id: s.id,
          label: s.title || s.code,
          code: s.code,
        }));
  const current = objects.find((o) => o.id === target);
  return (
    <Modal
      open
      title="把素材放进画布"
      onClose={() => {
        if (!importBusy) onClose();
      }}
      width={980}
    >
      <p className="text-sm text-muted-foreground mb-4">
        引用项目里已有的版本，原素材保持不变。视频素材库中的片段可先用于镜头，再从这里引用。
      </p>
      {loading && <p role="status">正在读取项目素材目录…</p>}
      {loadError && (
        <div role="alert" className="text-danger mb-4">
          {loadError}
          <Button variant="ghost" onClick={onRetry}>
            重试目录
          </Button>
        </div>
      )}
      <div className="canvas-picker">
        <section className="canvas-picker-objects">
          <div className="flex gap-2 mb-3">
            {(["asset", "shot"] as const).map((t) => (
              <Button
                key={t}
                size="sm"
                disabled={importBusy}
                variant={type === t ? "default" : "ghost"}
                onClick={() => {
                  setType(t);
                  setTarget("");
                }}
              >
                {t === "asset" ? "角色与资产" : "镜头作品"}
              </Button>
            ))}
          </div>
          <input
            className="canvas-picker-search"
            aria-label="搜索素材对象"
            placeholder="搜索名称或编号"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="canvas-object-list">
            {objects
              .filter((o) =>
                (o.label + o.code).toLowerCase().includes(query.toLowerCase()),
              )
              .map((o) => (
                <button
                  key={o.id}
                  disabled={importBusy}
                  aria-pressed={o.id === target}
                  onClick={() => setTarget(o.id)}
                >
                  <span>{o.label}</span>
                  <small>{o.code}</small>
                </button>
              ))}
            {!loading && !loadError && !objects.length && (
              <p className="text-sm text-muted-foreground py-6">
                还没有{type === "asset" ? "资产" : "镜头"}，可先到项目中创建。
              </p>
            )}
          </div>
        </section>
        <section className="canvas-picker-results">
          {current && (
            <CanvasMediaImport
              key={`${type}:${target}`}
              projectId={projectId}
              targetType={type}
              targetId={target}
              label={current.label}
              onBusy={setImportBusy}
              onPick={onPick}
            />
          )}

          <div className="flex gap-2 mb-4">
            {["all", "image", "video", "audio"].map((f) => (
              <Button
                key={f}
                size="sm"
                variant={filter === f ? "default" : "ghost"}
                onClick={() => setFilter(f)}
              >
                {
                  { all: "全部", image: "图片", video: "视频", audio: "声音" }[
                    f
                  ]
                }
              </Button>
            ))}
          </div>
          {!target ? (
            <div className="canvas-picker-empty">
              <Images size={32} />
              <p>选择一个对象，浏览它的创作版本</p>
            </div>
          ) : versions.isPending ? (
            <p>正在读取版本…</p>
          ) : versions.error ? (
            <div role="alert">
              <p>版本读取失败</p>
              <Button onClick={() => void versions.refetch()}>重试</Button>
            </div>
          ) : (
            <div className="canvas-picker-grid">
              {versions.data
                ?.filter(
                  (g) =>
                    g.output_blob_hash &&
                    (filter === "all" || g.output_type === filter),
                )
                .map((g) => (
                  <article key={g.id} data-generation-id={g.id}>
                    <div className="canvas-picker-preview">
                      <CanvasMedia
                        src={blobUrl(projectId, g.output_blob_hash!)}
                        kind={g.output_type}
                        label={current?.label || "作品"}
                      />
                    </div>
                    <div className="p-3">
                      <p className="text-xs text-muted-foreground mb-2">
                        {g.is_selected ? "已采用 · " : ""}
                        {new Date(g.created_at).toLocaleString()}
                      </p>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={importBusy}
                        onClick={() => onPick(g, current?.label || "素材")}
                      >
                        放入画布
                      </Button>
                    </div>
                  </article>
                ))}
              {!versions.data?.some(
                (g) =>
                  g.output_blob_hash &&
                  (filter === "all" || g.output_type === filter),
              ) && (
                <p className="text-sm text-muted-foreground">
                  没有此类可用版本，可切换筛选或选择其他对象。
                </p>
              )}
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}

export function CanvasVersionDialog({
  projectId,
  node,
  onClose,
  onPin,
  requiresImage,
}: {
  requiresImage: boolean;
  projectId: string;
  node: CanvasNode;
  onClose: () => void;
  onPin: (g: Generation) => void;
}) {
  const [tab, setTab] = useState<"versions" | "edit">("versions");
  const versions = useCanvasVersions(
    projectId,
    node.data.target_type,
    node.data.target_id,
  );
  return (
    <Modal
      open
      title={node.data.label + " · 版本与编辑"}
      onClose={onClose}
      width={1080}
    >
      <div className="flex gap-2 mb-4">
        <Button
          variant={tab === "versions" ? "default" : "ghost"}
          onClick={() => {
            setTab("versions");
            void versions.refetch();
          }}
        >
          选择画布版本
        </Button>
        <Button
          variant={tab === "edit" ? "default" : "ghost"}
          onClick={() => setTab("edit")}
        >
          生成与编辑工具
        </Button>
      </div>
      {tab === "edit" && node.data.target_id ? (
        <GenerationPanel
          projectId={projectId}
          targetType={node.data.target_type}
          targetId={node.data.target_id}
        />
      ) : (
        <>
          <p className="text-sm text-muted-foreground mb-4">
            固定到此节点，不改变项目中已采用的版本。编辑产生的新版本也会出现在这里。
          </p>
          {versions.isPending ? (
            <p>正在读取版本…</p>
          ) : versions.error ? (
            <div role="alert">
              读取失败
              <Button onClick={() => void versions.refetch()}>重试</Button>
            </div>
          ) : (
            <div className="canvas-picker-grid">
              {versions.data
                ?.filter((g) => g.output_blob_hash)
                .map((g) => (
                  <article key={g.id} data-generation-id={g.id}>
                    <div className="canvas-picker-preview">
                      <CanvasMedia
                        src={blobUrl(projectId, g.output_blob_hash!)}
                        kind={g.output_type}
                        label={node.data.label}
                      />
                    </div>
                    <div className="p-3 space-y-2">
                      <p className="text-xs text-muted-foreground">
                        {new Date(g.created_at).toLocaleString()} ·{" "}
                        {g.is_selected ? "项目已采用" : "候选版本"}
                        {g.input_refs?.operation === "grid_split" &&
                          ` · 原图分格 ${(g.input_refs.cell_index ?? 0) + 1} / ${(g.input_refs.rows ?? 1) * (g.input_refs.columns ?? 1)}`}
                      </p>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          node.data.kind === "generate" ||
                          (requiresImage && g.output_type !== "image") ||
                          node.data.generation_id === g.id
                        }
                        onClick={() => onPin(g)}
                      >
                        {node.data.generation_id === g.id
                          ? "当前固定版本"
                          : "固定到此节点"}
                      </Button>
                      {requiresImage && g.output_type !== "image" && (
                        <p className="text-xs text-muted-foreground">
                          此节点正在提供图片参考，请先移除图片连线再切换类型。
                        </p>
                      )}
                      {node.data.kind === "generate" && (
                        <p className="text-xs text-muted-foreground">
                          生成节点展示批次结果；固定引用请从「项目素材」添加。
                        </p>
                      )}
                    </div>
                  </article>
                ))}
              {!versions.data?.length && (
                <p>还没有作品，可切换到生成与编辑工具。</p>
              )}
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

export function CanvasRunMedia({
  projectId,
  node,
  generationId,
  hash,
}: {
  projectId: string;
  node?: CanvasNode;
  generationId?: string;
  hash: string;
}) {
  const versions = useCanvasVersions(
    projectId,
    node?.data.target_type || "shot",
    node?.data.target_id || null,
  );
  const g = versions.data?.find((item) => item.id === generationId);
  if (generationId && !g)
    return (
      <p className="text-xs text-muted-foreground">
        {versions.isFetching ? "读取结果…" : "在任务详情中查看原始结果"}
      </p>
    );
  return (
    <CanvasMedia
      src={blobUrl(projectId, hash)}
      kind={g?.output_type || "image"}
      label={node?.data.label || "画布生成结果"}
    />
  );
}
