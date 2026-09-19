import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  applyNodeChanges,
  applyEdgeChanges,
  useReactFlow,
  SelectionMode,
  getViewportForBounds,
  type Connection,
  type OnConnectEnd,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./canvas.css";
import {
  Boxes,
  Group,
  Hand,
  LayoutGrid,
  MousePointer2,
  Play,
  Plus,
  Redo2,
  Save,
  Trash2,
  Undo2,
  Workflow,
  Copy,
  PanelRightClose,
  PanelRightOpen,
  Images,
  FileText,
  ImagePlus,
  Film,
  Music,
  X,
  History,
  AlignLeft,
} from "lucide-react";
import {
  api,
  getToken,
  blobUrl,
  type Asset,
  type Shot,
  type ProviderConfig,
  type Generation,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import {
  CanvasCard,
  CanvasActions,
  CanvasMaterialPicker,
  CanvasVersionDialog,
} from "@/components/CanvasMedia";
import { CanvasLibraryPanel } from "@/components/CanvasLibraryPanel";
import { CanvasImageStudio } from "@/components/CanvasImageStudio";
import { CanvasFileDrop } from "@/components/CanvasFileDrop";
import {
  arrangeSelection,
  copySelection,
  pasteSelection,
} from "@/lib/canvas-tools";
import { CanvasGridSplit } from "@/components/CanvasGridSplit";
import { CanvasInspector } from "@/components/CanvasInspector";
import {
  absolutePosition,
  vacantPosition,
  duplicateSelection,
  cleanGraph,
  newNode,
  validConnection,
  type CanvasDoc,
  type CanvasNode,
  type Graph,
  type Kind,
  type NodeData,
  type Run,
} from "@/lib/canvas";

const nodeTypes = { studio: CanvasCard };
const empty: Graph = {
  nodes: [],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
};
const field =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary";

export default function Canvas() {
  const { projectId } = useParams();
  return <CanvasList key={projectId} projectId={projectId!} />;
}
function CanvasList({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams(),
    qc = useQueryClient();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0);
  const base = `/projects/${projectId}/canvases`,
    list = useQuery({
      queryKey: ["canvases", projectId, offset],
      queryFn: () => api.get<CanvasDoc[]>(`${base}?offset=${offset}`),
    });
  const selected = params.get("canvas") || list.data?.[0]?.id;
  async function create() {
    setBusy(true);
    setError("");
    try {
      const c = await api.post<CanvasDoc>(base, {
        name: `创作画布 ${new Date().toLocaleDateString()}`,
      });
      setOffset(0);
      setParams({ canvas: c.id });
      void qc.invalidateQueries({ queryKey: ["canvases", projectId] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="canvas-page">
      <header className="canvas-page-header">
        <h1 className="flex items-center gap-2 text-sm font-medium">
          <Workflow size={18} />
          自由画布
        </h1>
        <div className="flex flex-wrap gap-2">
          <select
            aria-label="选择画布"
            className={`${field} !w-auto max-w-52`}
            value={selected ?? ""}
            onChange={(e) => setParams({ canvas: e.target.value })}
          >
            <option value="" disabled>
              选择画布
            </option>
            {selected && !list.data?.some((c) => c.id === selected) && (
              <option value={selected}>当前画布</option>
            )}
            {list.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void create()}
          >
            <Plus size={16} />
            新建画布
          </Button>
        </div>
      </header>
      {(error || list.error) && (
        <p role="alert" className="text-danger">
          {error || list.error?.message}
        </p>
      )}
      {list.isLoading ? (
        <p>正在载入画布…</p>
      ) : selected ? (
        <ReactFlowProvider key={selected}>
          <Editor projectId={projectId} canvasId={selected} />
        </ReactFlowProvider>
      ) : (
        <div className="rounded-2xl border border-dashed border-border py-24 text-center">
          <Boxes className="mx-auto text-muted-foreground" size={40} />
          <h2 className="mt-5 text-lg">从一个想法开始</h2>
          <p className="my-3 text-muted-foreground">
            添加文字和项目参考，用连线组织生成流程。
          </p>
          <Button disabled={busy} onClick={() => void create()}>
            创建第一张画布
          </Button>
        </div>
      )}
      {(offset > 0 || list.data?.length === 50) && (
        <div className="flex gap-2">
          <Button
            variant="ghost"
            disabled={!offset}
            onClick={() => setOffset((x) => Math.max(0, x - 50))}
          >
            上一页画布
          </Button>
          <Button
            variant="ghost"
            disabled={list.data?.length !== 50}
            onClick={() => setOffset((x) => x + 50)}
          >
            下一页画布
          </Button>
        </div>
      )}
    </div>
  );
}
function Editor({
  projectId,
  canvasId,
}: {
  projectId: string;
  canvasId: string;
}) {
  const { theme } = useTheme();
  const [viewParams] = useSearchParams();
  const readOnly = viewParams.get("view") === "readonly";
  const [toolsOpen, setToolsOpen] = useState(false),
    [snap, setSnap] = useState(false);
  const [incomingFiles, setIncomingFiles] = useState<{
    files: File[];
    position: { x: number; y: number };
  } | null>(null);
  const [notice, setNotice] = useState("");
  const [reuse, setReuse] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [imageStudio, setImageStudio] = useState<{
    source?: Generation;
  } | null>(null);

  const base = `/projects/${projectId}`,
    path = `${base}/canvases/${canvasId}`,
    qc = useQueryClient(),
    flow = useReactFlow<CanvasNode>(),
    confirm = useConfirm(),
    { me } = useAuth();
  const [graph, setGraph] = useState<Graph>(empty),
    [name, setName] = useState(""),
    [revision, setRevision] = useState(0),
    [loaded, setLoaded] = useState(false),
    [saved, setSaved] = useState("");
  const [error, setError] = useState(""),
    [working, setBusy] = useState(false),
    [hand, setHand] = useState(false),
    [panel, setPanel] = useState<"edit" | "runs" | "history">("edit");
  const busy = working || readOnly;
  const [past, setPast] = useState<Graph[]>([]),
    [future, setFuture] = useState<Graph[]>([]),
    [localDraft, setLocalDraft] = useState<{
      name: string;
      revision: number;
      document: Graph;
    } | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [edgeDrop, setEdgeDrop] = useState<{
    nodeId: string;
    handle: string;
    direction: "source" | "target";
    position: { x: number; y: number };
    left: number;
    top: number;
  } | null>(null);
  const connectionCanceled = useRef(false);
  const edgeMenu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function dismiss(event: KeyboardEvent) {
      if (event.key === "Escape") {
        connectionCanceled.current = true;
        setEdgeDrop(null);
      }
    }
    function outside(event: PointerEvent) {
      if (edgeMenu.current && !edgeMenu.current.contains(event.target as Node))
        setEdgeDrop(null);
    }
    const resize = () => setEdgeDrop(null);
    document.addEventListener("keydown", dismiss);
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", resize);
    return () => {
      document.removeEventListener("keydown", dismiss);
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", resize);
    };
  }, []);
  useEffect(() => {
    if (edgeDrop)
      edgeMenu.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [edgeDrop]);
  const [splitSource, setSplitSource] = useState<{
    nodeId: string;
    generation: Generation;
    label: string;
  } | null>(null);
  const [materialOpen, setMaterialOpen] = useState(false),
    [versionNode, setVersionNode] = useState<string | null>(null),
    [addOpen, setAddOpen] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0),
    [runOffset, setRunOffset] = useState(0),
    [viewRun, setViewRun] = useState("");
  const beforeDrag = useRef<Graph | null>(null),
    runKey = useRef<{ key: string; revision: number; target?: string } | null>(
      null,
    );
  const draftKey = `canvas-draft:${me?.user.id}:${projectId}:${canvasId}`;
  const doc = useQuery({
    queryKey: ["canvas", canvasId],
    queryFn: () => api.get<CanvasDoc>(path),
  });
  const runs = useQuery({
    queryKey: ["canvas-runs", canvasId, runOffset],
    queryFn: () => api.get<Run[]>(`${path}/runs?offset=${runOffset}`),
    refetchInterval: 2000,
    enabled: loaded,
  });
  const history = useQuery({
    queryKey: ["canvas-history", canvasId, historyOffset],
    queryFn: () =>
      api.get<{ revision: number; created_at: string }[]>(
        `${path}/history?offset=${historyOffset}`,
      ),
    enabled: panel === "history",
  });
  const assets = useQuery({
    queryKey: ["canvas-assets", projectId],
    queryFn: () => api.get<Asset[]>(`${base}/assets`),
  });
  const shots = useQuery({
    queryKey: ["canvas-shots", projectId],
    queryFn: () => api.get<Shot[]>(`${base}/shots`),
  });
  const providers = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${base}/providers`),
  });
  const serial = JSON.stringify({ name, document: cleanGraph(graph) }),
    dirty = !readOnly && loaded && serial !== saved;
  function accept(c: CanvasDoc) {
    setGraph(c.document);
    setName(c.name);
    setRevision(c.revision);
    setSaved(
      JSON.stringify({ name: c.name, document: cleanGraph(c.document) }),
    );
    setPast([]);
    setFuture([]);
    setLoaded(true);
    void flow.setViewport(c.document.viewport);
  }
  useEffect(() => {
    if (doc.data && !loaded) {
      accept(doc.data);
      try {
        const draft = JSON.parse(localStorage.getItem(draftKey) || "null");
        if (
          draft &&
          JSON.stringify({
            name: draft.name,
            document: cleanGraph(draft.document),
          }) !==
            JSON.stringify({
              name: doc.data.name,
              document: cleanGraph(doc.data.document),
            })
        )
          setLocalDraft(draft);
      } catch {
        /* invalid local draft */
      }
    }
  }, [doc.data, loaded]);
  useEffect(() => {
    if (!loaded || localDraft || readOnly) return;
    try {
      if (dirty)
        localStorage.setItem(
          draftKey,
          JSON.stringify({ name, revision, document: cleanGraph(graph) }),
        );
      else localStorage.removeItem(draftKey);
    } catch {
      setError("浏览器草稿空间不足，请及时保存到项目");
    }
  }, [serial, revision, loaded, localDraft]);
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  function commit(next: Graph) {
    next = {
      ...next,
      nodes: next.nodes.map((n) => {
        const mentions = (n.data.mentions ?? []).filter((m) =>
          next.edges.some((e) => e.source === m.node_id && e.target === n.id),
        );
        let text = n.data.text;
        for (const old of n.data.mentions ?? [])
          if (!mentions.includes(old))
            text = text.split(`@【${old.alias}】`).join(old.alias);
        return { ...n, data: { ...n.data, text, mentions } };
      }),
    };
    setPast((p) => [...p, cleanGraph(graph)].slice(-100));
    setFuture([]);
    setGraph(next);
  }
  function undo() {
    if (!past.length || busy) return;
    setFuture((f) => [cleanGraph(graph), ...f]);
    setGraph(past[past.length - 1]);
    setPast((p) => p.slice(0, -1));
  }
  function redo() {
    if (!future.length || busy) return;
    setPast((p) => [...p, cleanGraph(graph)]);
    setGraph(future[0]);
    setFuture((f) => f.slice(1));
  }
  async function perform(action: () => Promise<void>, allowRead = false) {
    if (working || (readOnly && !allowRead)) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function persist() {
    const result = await api.put<CanvasDoc>(path, {
      name,
      revision,
      document: cleanGraph(graph),
    });
    setRevision(result.revision);
    setSaved(
      JSON.stringify({
        name: result.name,
        document: cleanGraph(result.document),
      }),
    );
    setGraph((g) => ({
      ...g,
      nodes: g.nodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          ...result.document.nodes.find((r) => r.id === n.id)?.data,
        },
      })),
    }));
    void qc.invalidateQueries({ queryKey: ["canvases", projectId] });
    void qc.invalidateQueries({ queryKey: ["canvas-history", canvasId] });
    return result;
  }
  async function startRun(target?: string) {
    const c = await persist();
    if (
      !runKey.current ||
      runKey.current.revision !== c.revision ||
      runKey.current.target !== target
    )
      runKey.current = {
        key: crypto.randomUUID(),
        revision: c.revision,
        target,
      };
    const result = await api.post<Run>(`${path}/runs`, {
      revision: c.revision,
      request_key: runKey.current.key,
      target_node_id: target,
      reuse_unchanged: reuse,
    });
    runKey.current = null;
    setViewRun(result.id);
    setRunOffset(0);
    if (!target) {
      setPanel("runs");
      setInspectorOpen(true);
    }
    await runs.refetch();
  }
  const selected =
    graph.nodes.find((n) => n.selected && n.data.kind !== "group") ||
    graph.nodes.find((n) => n.selected);
  const active = runs.data?.find((r) =>
    ["running", "paused"].includes(r.status),
  );
  const run = runs.data?.find((r) => r.id === viewRun) || runs.data?.[0];
  const decorated = useMemo(
    () =>
      graph.nodes.map((n) => {
        const asset =
          n.data.kind === "asset"
            ? assets.data?.find((a) => a.id === n.data.target_id)
            : null;
        const output =
          run?.revision === revision ? run.steps[n.id]?.output : undefined;
        const hash = output?.blob_hash || asset?.representative_blob_hash;
        return {
          ...n,
          type: "studio",
          style: { width: n.width ?? 320, height: n.height ?? 260 },
          data: {
            ...n.data,
            preview: hash ? blobUrl(projectId, hash) : undefined,
            outputId: output?.generation_id,
            reused:
              run?.revision === revision ? run.steps[n.id]?.reused : false,
            status:
              run?.revision === revision ? run.steps[n.id]?.status : undefined,
          },
        };
      }),
    [graph.nodes, assets.data, run, revision, projectId],
  );
  function updateNode(id: string, data: Partial<NodeData>) {
    if (busy) return;
    commit({
      ...graph,
      nodes: graph.nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, ...data } } : n,
      ),
    });
  }
  function settings(id: string) {
    setGraph((g) => ({
      ...g,
      nodes: g.nodes.map((n) => ({ ...n, selected: n.id === id })),
    }));
    setPanel("edit");
    setInspectorOpen(true);
  }
  function placeMedia(
    items: { generation: Generation; label: string }[],
    columns = 1,
    originId?: string,
    dropPosition?: { x: number; y: number },
  ) {
    if (busy) throw new Error("请等待当前操作完成");
    if (graph.nodes.length + items.length > 500)
      throw new Error("画布最多容纳500个节点，请先整理节点");
    if (
      items.some(
        ({ generation: g }) =>
          g.target_type !== "asset" && g.target_type !== "shot",
      )
    )
      throw new Error("请选择资产或镜头素材");
    const rect = document
      .querySelector(".canvas-surface")!
      .getBoundingClientRect();
    const origin = graph.nodes.find((n) => n.id === originId);
    const preferred = dropPosition
      ? { ...dropPosition }
      : origin
        ? absolutePosition(origin, graph.nodes)
        : flow.screenToFlowPosition({
            x: rect.left + rect.width * 0.4,
            y: rect.top + rect.height * 0.3,
          });
    if (origin) preferred.x += (origin.width ?? 320) + 100;
    const indices = items.map(({ generation: g }, i) =>
      originId ? (g.input_refs?.cell_index ?? i) : i,
    );
    const width = columns * 370 - 50,
      height = (Math.floor(Math.max(...indices) / columns) + 1) * 320 - 60;
    const pos = vacantPosition(graph.nodes, preferred, width, height);
    const nodes = items.map(({ generation: g, label }, i) => {
      const index = indices[i];
      const node = newNode(
        g.target_type as "asset" | "shot",
        pos.x + (index % columns) * 370,
        pos.y + Math.floor(index / columns) * 320,
      );
      node.data = {
        ...node.data,
        label: label.slice(0, 120),
        target_type: g.target_type as "asset" | "shot",
        target_id: g.target_id,
        generation_id: g.id,
      };
      return { ...node, selected: true };
    });
    const viewport = getViewportForBounds(
      { ...pos, y: pos.y - 50, width, height: height + 100 },
      rect.width,
      rect.height,
      0.2,
      1,
      0.15,
    );
    commit({
      ...graph,
      viewport,
      nodes: [...graph.nodes.map((n) => ({ ...n, selected: false })), ...nodes],
    });
    void flow.setViewport(viewport, { duration: 250 });
    setInspectorOpen(false);
  }
  function addMedia(g: Generation, label: string): boolean {
    try {
      placeMedia([{ generation: g, label }]);
      setMaterialOpen(false);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }
  function branch(id: string, image: boolean, g?: Generation) {
    const origin = graph.nodes.find((n) => n.id === id);
    if (!origin || busy) return;
    if (graph.nodes.length >= 500 || graph.edges.length >= 1500) {
      setError("画布容量不足，请先整理节点与连线");
      return;
    }
    const originPosition = absolutePosition(origin, graph.nodes);
    let pos;
    try {
      pos = vacantPosition(graph.nodes, {
        x: originPosition.x + (origin.width ?? 320) + 120,
        y: originPosition.y,
      });
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    const node = newNode("generate", pos.x, pos.y);
    node.data = {
      ...node.data,
      label: (origin.data.label + " · 新创作").slice(0, 120),
      target_type: origin.data.target_type,
      target_id: origin.data.target_id,
    };
    if (origin.data.kind === "generate")
      node.data.generate = { ...origin.data.generate, prompt_override: null };
    // Fixed media references never follow a later adopted version by accident.
    const nodes = graph.nodes.map((n) => ({
      ...n,
      selected: false,
      data:
        n.id === id && g && n.data.kind !== "generate"
          ? { ...n.data, generation_id: g.id }
          : n.data,
    }));
    commit({
      ...graph,
      nodes: [...nodes, { ...node, selected: true }],
      edges: [
        ...graph.edges,
        {
          id: crypto.randomUUID(),
          source: id,
          target: node.id,
          sourceHandle: image ? "image" : "text",
          targetHandle: image ? "reference" : "prompt",
        },
      ],
    });
    setInspectorOpen(false);
    setPanel("edit");
    const rect = document
      .querySelector(".canvas-surface")!
      .getBoundingClientRect();
    const x = Math.min(originPosition.x, node.position.x),
      y = Math.min(originPosition.y, node.position.y) - 65;
    void flow.setViewport(
      getViewportForBounds(
        {
          x,
          y,
          width:
            Math.max(
              originPosition.x + (origin.width ?? 320),
              node.position.x + 320,
            ) - x,
          height:
            Math.max(
              originPosition.y + (origin.height ?? 260),
              node.position.y + 560,
            ) - y,
        },
        rect.width,
        rect.height,
        0.2,
        1,
        0.12,
      ),
      { duration: 250 },
    );
  }
  function alignSelected() {
    const nodes = graph.nodes.filter((n) => n.selected && !n.parentId);
    if (nodes.length < 2) return;
    const left = Math.min(...nodes.map((n) => n.position.x));
    commit({
      ...graph,
      nodes: graph.nodes.map((n) =>
        nodes.some((x) => x.id === n.id)
          ? { ...n, position: { ...n.position, x: left } }
          : n,
      ),
    });
  }
  function update(data: Partial<NodeData>) {
    if (!selected) return;
    commit({
      ...graph,
      nodes: graph.nodes.map((n) =>
        n.id === selected.id ? { ...n, data: { ...n.data, ...data } } : n,
      ),
    });
  }
  function add(kind: Kind, requestType: "image" | "video" | "audio" = "image") {
    if (graph.nodes.length >= 500) {
      setError("画布最多容纳500个节点，请先整理已有内容。");
      return;
    }
    const rect = document
      .querySelector(".canvas-surface")!
      .getBoundingClientRect();
    const p = flow.screenToFlowPosition({
      x: rect.left + rect.width * 0.38,
      y: rect.top + rect.height * 0.28,
    });
    const node = newNode(kind, p.x, p.y);
    node.data.generate.request_type = requestType;
    if (kind === "generate")
      node.data.label = {
        image: "新的画面",
        video: "新的动态镜头",
        audio: "新的声音",
      }[requestType];
    commit({
      ...graph,
      nodes: [
        ...graph.nodes.map((n) => ({ ...n, selected: false })),
        { ...node, selected: true },
      ],
    });
    setPanel("edit");
    setInspectorOpen(kind !== "text");
    setAddOpen(false);
  }
  function duplicate() {
    if (busy) return;
    try {
      const next = duplicateSelection(graph);
      if (next !== graph) commit(next);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function removeSelected() {
    const ids = new Set(graph.nodes.filter((n) => n.selected).map((n) => n.id));
    commit({
      ...graph,
      nodes: graph.nodes
        .filter((n) => !ids.has(n.id))
        .map((n) =>
          n.parentId && ids.has(n.parentId)
            ? {
                ...n,
                position: absolutePosition(n, graph.nodes),
                parentId: undefined,
              }
            : n,
        ),
      edges: graph.edges.filter(
        (e) => !ids.has(e.source) && !ids.has(e.target) && !e.selected,
      ),
    });
  }
  function groupSelected() {
    if (graph.nodes.length >= 500) {
      setError("画布最多容纳500个节点");
      return;
    }
    const nodes = graph.nodes.filter(
      (n) => n.selected && !n.parentId && n.data.kind !== "group",
    );
    if (nodes.length < 2) return;
    const x = Math.min(...nodes.map((n) => n.position.x)) - 35,
      y = Math.min(...nodes.map((n) => n.position.y)) - 65;
    const group = newNode("group", x, y);
    group.width =
      Math.max(...nodes.map((n) => n.position.x + (n.width ?? 320))) - x + 35;
    group.height =
      Math.max(...nodes.map((n) => n.position.y + (n.height ?? 260))) - y + 35;
    commit({
      ...graph,
      nodes: [
        group,
        ...graph.nodes.map((n) =>
          nodes.some((a) => a.id === n.id)
            ? {
                ...n,
                parentId: group.id,
                position: { x: n.position.x - x, y: n.position.y - y },
                selected: false,
              }
            : n,
        ),
      ],
    });
  }
  function fitGraph(next: Graph) {
    const rect = document
      .querySelector(".canvas-surface")
      ?.getBoundingClientRect();
    if (!rect || !next.nodes.length) return next;
    const bounds = next.nodes.map((n) => ({
      ...absolutePosition(n, next.nodes),
      width: n.width ?? 260,
      height: n.height ?? 210,
    }));
    const x = Math.min(...bounds.map((n) => n.x)),
      y = Math.min(...bounds.map((n) => n.y));
    const viewport = getViewportForBounds(
      {
        x,
        y,
        width: Math.max(...bounds.map((n) => n.x + n.width)) - x,
        height: Math.max(...bounds.map((n) => n.y + n.height)) - y,
      },
      rect.width,
      rect.height,
      0.05,
      1.2,
      0.15,
    );
    void flow.setViewport(viewport);
    return { ...next, viewport };
  }
  function layout() {
    const columns = new Map<string, number>();
    let pending = [...graph.nodes.filter((n) => !n.parentId)];
    for (let pass = 0; pending.length && pass < 500; pass++) {
      pending = pending.filter((n) => {
        const parents = graph.edges
          .filter((e) => e.target === n.id)
          .map((e) => e.source)
          .filter((id) => pending.some((p) => p.id === id) || columns.has(id));
        if (parents.some((p) => !columns.has(p))) return true;
        columns.set(
          n.id,
          Math.max(0, ...parents.map((p) => (columns.get(p) ?? 0) + 1)),
        );
        return false;
      });
    }
    const widths: Record<number, number> = {},
      lefts: Record<number, number> = {},
      rows: Record<number, number> = {};
    graph.nodes
      .filter((n) => !n.parentId)
      .forEach((n) => {
        const col = columns.get(n.id) ?? 0;
        widths[col] = Math.max(widths[col] ?? 0, n.width ?? 320);
      });
    let left = 40;
    Object.keys(widths)
      .map(Number)
      .sort((a, b) => a - b)
      .forEach((col) => {
        lefts[col] = left;
        left += widths[col] + 120;
      });
    commit(
      fitGraph({
        ...graph,
        nodes: graph.nodes.map((n) => {
          if (n.parentId) return n;
          const col = columns.get(n.id) ?? 0,
            y = rows[col] ?? 90;
          rows[col] = y + (n.height ?? 260) + 110;
          return { ...n, position: { x: lefts[col], y } };
        }),
      }),
    );
  }

  function centerPosition() {
    const r = document
      .querySelector(".canvas-surface")!
      .getBoundingClientRect();
    return flow.screenToFlowPosition({
      x: r.left + r.width * 0.45,
      y: r.top + r.height * 0.35,
    });
  }
  function receiveFiles(files: File[], position = centerPosition()) {
    if (busy || !files.length) return;
    if (files.length > 20 || graph.nodes.length + files.length > 500) {
      setError("每次最多导入20个文件，且画布不能超过500个节点");
      return;
    }
    setIncomingFiles({ files, position });
    setToolsOpen(false);
  }
  async function copyNodes() {
    try {
      await navigator.clipboard.writeText(
        JSON.stringify(copySelection(graph, projectId)),
      );
      setNotice("已复制，可在同一项目的另一张画布粘贴");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function pasteNodes(raw?: string) {
    await perform(async () => {
      const next = pasteSelection(
        graph,
        raw ?? (await navigator.clipboard.readText()),
        projectId,
        centerPosition(),
      );
      await api.post(path + "/validate", cleanGraph(next));
      commit(next);
      setToolsOpen(false);
    });
  }
  async function exportBundle() {
    await perform(async () => {
      if (dirty && !readOnly) await persist();
      const response = await fetch(`/api/v1${path}/export`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      if (!response.ok)
        throw new Error((await response.json()).error?.message || "导出失败");
      const url = URL.createObjectURL(await response.blob()),
        link = document.createElement("a");
      link.href = url;
      link.download = (name || "画布") + ".zip";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      setNotice("已导出只读作品包，解压后打开 index.html 即可离线查看与分享");
    }, true);
  }
  async function shareLink() {
    try {
      if (dirty) {
        setError("请先保存，再复制分享链接");
        return;
      }
      await navigator.clipboard.writeText(
        `${location.origin}${location.pathname}?canvas=${canvasId}&view=readonly`,
      );
      setNotice(
        "已复制只读链接，仅有项目访问权限的成员可打开；对外分享请导出作品包",
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const endConnection: OnConnectEnd = (event, state) => {
    if (
      busy ||
      connectionCanceled.current ||
      state.isValid ||
      !state.fromNode ||
      !state.fromHandle
    )
      return;
    const point = "changedTouches" in event ? event.changedTouches[0] : event;
    if (!point) return;
    const surface = document.querySelector(".canvas-surface");
    const hit = document.elementFromPoint(point.clientX, point.clientY);
    // Releasing over a node, an incompatible port or any toolbar must not create content.
    if (
      !surface ||
      !hit?.classList.contains("react-flow__pane") ||
      !surface.contains(hit)
    )
      return;
    const handle = state.fromHandle.id;
    if (!handle || !["text", "image", "prompt", "reference"].includes(handle))
      return;
    if (graph.nodes.length >= 500 || graph.edges.length >= 1500) {
      setError("画布容量不足，请先整理节点与连线");
      return;
    }
    const rect = surface.getBoundingClientRect();
    setAddOpen(false);
    setEdgeDrop({
      nodeId: state.fromNode.id,
      handle,
      direction: state.fromHandle.type,
      position: flow.screenToFlowPosition({
        x: point.clientX,
        y: point.clientY,
      }),
      left: Math.max(8, Math.min(point.clientX - rect.left, rect.width - 260)),
      top: Math.max(8, Math.min(point.clientY - rect.top, rect.height - 260)),
    });
  };
  function createConnected(kind: "text" | "image" | "video" | "audio") {
    if (!edgeDrop || busy) return;
    const origin = graph.nodes.find((n) => n.id === edgeDrop.nodeId);
    if (!origin) {
      setEdgeDrop(null);
      return;
    }
    if (graph.nodes.length >= 500 || graph.edges.length >= 1500) {
      setError("画布容量不足，请先整理节点与连线");
      setEdgeDrop(null);
      return;
    }
    const reverse = edgeDrop.direction === "target";
    let position;
    try {
      position = vacantPosition(graph.nodes, {
        x: edgeDrop.position.x - (reverse ? 320 : 0),
        y: edgeDrop.position.y - 100,
      });
    } catch (e) {
      setError((e as Error).message);
      setEdgeDrop(null);
      return;
    }
    const node = newNode(
      kind === "text" ? "text" : "generate",
      position.x,
      position.y,
    );
    node.data = {
      ...node.data,
      target_type: origin.data.target_type,
      target_id: origin.data.target_id,
      label: {
        text: "创意文字",
        image: "新的画面",
        video: "新的动态镜头",
        audio: "新的声音",
      }[kind],
    };
    if (kind !== "text") {
      if (
        origin.data.kind === "generate" &&
        origin.data.generate.request_type === kind
      )
        node.data.generate = { ...origin.data.generate, prompt_override: null };
      node.data.generate.request_type = kind;
      node.data.generate.use_references =
        !reverse && edgeDrop.handle === "image";
    }
    const edge = {
      id: crypto.randomUUID(),
      source: reverse ? node.id : origin.id,
      target: reverse ? origin.id : node.id,
      sourceHandle: reverse
        ? edgeDrop.handle === "reference"
          ? "image"
          : "text"
        : edgeDrop.handle,
      targetHandle: reverse
        ? edgeDrop.handle
        : edgeDrop.handle === "image"
          ? "reference"
          : "prompt",
    };
    const chosen = qc
      .getQueryData<Generation[]>([
        "gens",
        origin.data.target_type,
        origin.data.target_id,
      ])
      ?.find((g) => g.is_selected);
    const next = {
      ...graph,
      nodes: [
        ...graph.nodes.map((n) => ({
          ...n,
          selected: false,
          data:
            n.id === origin.id &&
            !reverse &&
            chosen &&
            ["shot", "asset"].includes(n.data.kind) &&
            !n.data.generation_id
              ? { ...n.data, generation_id: chosen.id }
              : n.data,
        })),
        { ...node, selected: true },
      ],
      edges: [...graph.edges, edge],
    };
    if (
      !validConnection(
        { ...next, edges: graph.edges },
        edge.source,
        edge.target,
        edge.sourceHandle,
        edge.targetHandle,
      )
    ) {
      setError("这两个节点无法连接，请重新选择");
      setEdgeDrop(null);
      return;
    }
    commit(next);
    setEdgeDrop(null);
    setInspectorOpen(false);
    setPanel("edit");
    setError("");
  }
  function connect(c: Connection) {
    if (busy || connectionCanceled.current || !c.source || !c.target) return;
    setEdgeDrop(null);
    if (
      !validConnection(
        graph,
        c.source,
        c.target,
        c.sourceHandle ?? "text",
        c.targetHandle ?? "prompt",
      )
    ) {
      setError("连线不兼容或会形成循环：文字连接文字输入，图片连接图片输入");
      return;
    }
    commit({
      ...graph,
      edges: [...graph.edges, { id: crypto.randomUUID(), ...c }],
    });
    setError("");
  }
  if (doc.error)
    return (
      <div role="alert">
        {doc.error.message}
        <Button onClick={() => void doc.refetch()}>重新加载</Button>
      </div>
    );
  if (!loaded)
    return (
      <div className="py-20 text-center text-muted-foreground">
        正在载入画布…
      </div>
    );
  return (
    <>
      {localDraft && !readOnly && (
        <div className="rounded-lg bg-primary/10 p-3 text-sm flex flex-wrap items-center gap-3">
          <span>
            发现上次未保存的草稿
            {localDraft.revision !== revision
              ? "，服务器版本已变化；恢复后请核对内容再保存"
              : ""}
            。
          </span>
          <Button
            size="sm"
            onClick={() => {
              commit(localDraft.document);
              setName(localDraft.name);
              setLocalDraft(null);
            }}
          >
            恢复草稿
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setLocalDraft(null)}>
            使用服务器版本
          </Button>
        </div>
      )}
      <div className="canvas-document-bar">
        <input
          className={`${field} !w-52`}
          aria-label="画布名称"
          maxLength={120}
          value={name}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
        <span className="canvas-save-status text-xs text-muted-foreground mr-auto">
          {dirty ? "有未保存的修改 · 浏览器草稿" : "已保存"} · 版本 {revision}
          <span className="ml-3 text-faint">
            {graph.nodes.length} 节点 · {graph.edges.length} 连线
          </span>
        </span>
        {readOnly ? (
          <span className="canvas-readonly-badge">只读浏览</span>
        ) : (
          <>
            <label className="canvas-reuse-toggle">
              <input
                type="checkbox"
                checked={reuse}
                disabled={busy}
                onChange={(e) => {
                  setReuse(e.target.checked);
                  runKey.current = null;
                }}
              />
              复用未变化的结果
            </label>
          </>
        )}
        <Button
          variant="ghost"
          size="icon"
          aria-label="查看运行记录"
          title="运行记录"
          onClick={() => {
            setPanel("runs");
            setInspectorOpen(true);
          }}
        >
          <Play size={16} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="查看保存历史"
          title="保存历史"
          onClick={() => {
            setPanel("history");
            setInspectorOpen(true);
          }}
        >
          <History size={16} />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          aria-label={inspectorOpen ? "收起属性面板" : "展开属性面板"}
          aria-expanded={inspectorOpen}
          onClick={() => setInspectorOpen((v) => !v)}
        >
          {inspectorOpen ? (
            <PanelRightClose size={17} />
          ) : (
            <PanelRightOpen size={17} />
          )}
        </Button>
        {!readOnly && (
          <>
            <Button
              variant="ghost"
              size="icon"
              aria-label="撤销"
              disabled={!past.length || busy}
              onClick={undo}
            >
              <Undo2 size={17} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label="重做"
              disabled={!future.length || busy}
              onClick={redo}
            >
              <Redo2 size={17} />
            </Button>
            <Button
              variant="outline"
              disabled={!dirty || busy || !name.trim()}
              onClick={() =>
                void perform(async () => {
                  await persist();
                })
              }
            >
              <Save size={15} />
              保存
            </Button>
            <Button
              disabled={
                busy ||
                !!active ||
                !graph.nodes.some((n) => n.data.kind === "generate")
              }
              onClick={() => void perform(() => startRun())}
            >
              <Play size={15} />
              运行画布
            </Button>
          </>
        )}
      </div>
      {error && (
        <p
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger"
        >
          {error}
        </p>
      )}
      <CanvasActions.Provider
        value={{
          projectId,
          reuse,
          editImage: (source) => setImageStudio({ source }),
          mentionOptions: (id) => {
            const bindings =
              graph.nodes.find((n) => n.id === id)?.data.mentions ?? [];
            return decorated
              .filter((n) => n.id !== id && n.data.kind !== "group")
              .map((n) => {
                const fixed = qc
                  .getQueryData<Generation[]>([
                    "gens",
                    n.data.target_type,
                    n.data.target_id,
                  ])
                  ?.find(
                    (g) =>
                      g.id === (n.data.generation_id || n.data.outputId) ||
                      (!n.data.generation_id &&
                        !n.data.outputId &&
                        g.is_selected),
                  );
                let alias =
                  bindings.find((m) => m.node_id === n.id)?.alias ||
                  n.data.label.replace(/[【】@]/g, "").slice(0, 100) ||
                  "参考";
                let suffix = 2;
                const baseAlias = alias;
                while (
                  bindings.some((m) => m.alias === alias && m.node_id !== n.id)
                )
                  alias = baseAlias + suffix++;
                return {
                  id: n.id,
                  alias,
                  image:
                    n.data.kind === "generate"
                      ? n.data.generate.request_type === "image"
                      : fixed
                        ? fixed.output_type === "image"
                        : !!n.data.preview,
                };
              });
          },
          mention: (id, option, text) => {
            const target = graph.nodes.find((n) => n.id === id),
              source = graph.nodes.find((n) => n.id === option.id);
            if (!target || !source || busy) return;
            const from = option.image ? "image" : "text",
              to = option.image ? "reference" : "prompt";
            const exists = graph.edges.some(
              (e) =>
                e.source === source.id &&
                e.target === id &&
                e.targetHandle === to,
            );
            if (
              !exists &&
              (!validConnection(graph, source.id, id, from, to) ||
                graph.edges.length >= 1500)
            ) {
              setError("此引用会形成循环或超过连线容量");
              return;
            }
            const mentions = [
              ...(target.data.mentions ?? []).filter(
                (m) => m.node_id !== source.id,
              ),
              { node_id: source.id, alias: option.alias },
            ];
            if (mentions.length > 50) {
              setError("每个节点最多50个智能引用");
              return;
            }
            const adopted = qc
              .getQueryData<Generation[]>([
                "gens",
                source.data.target_type,
                source.data.target_id,
              ])
              ?.find((g) => g.is_selected);
            commit({
              ...graph,
              nodes: graph.nodes.map((n) =>
                n.id === id
                  ? {
                      ...n,
                      data: {
                        ...n.data,
                        text,
                        mentions,
                        generate: { ...n.data.generate, prompt_override: null },
                      },
                    }
                  : n.id === source.id &&
                      adopted &&
                      n.data.kind !== "generate" &&
                      !n.data.generation_id
                    ? { ...n, data: { ...n.data, generation_id: adopted.id } }
                    : n,
              ),
              edges: exists
                ? graph.edges
                : [
                    ...graph.edges,
                    {
                      id: crypto.randomUUID(),
                      source: source.id,
                      target: id,
                      sourceHandle: from,
                      targetHandle: to,
                    },
                  ],
            });
          },
          busy,
          running: !!active,
          run: (id) => void perform(() => startRun(id)),
          references: (id) =>
            graph.edges
              .filter((e) => e.target === id)
              .map((e) => ({
                id: e.id,
                label:
                  graph.nodes.find((n) => n.id === e.source)?.data.label ||
                  "参考",
                image: e.targetHandle === "reference",
              })),
          disconnect: (id) =>
            commit({ ...graph, edges: graph.edges.filter((e) => e.id !== id) }),
          single: graph.nodes.filter((n) => n.selected).length === 1,
          update: updateNode,
          settings,
          versions: (id) => {
            if (!readOnly) setVersionNode(id);
          },
          branch,
          split: (id, generation) =>
            setSplitSource({
              nodeId: id,
              generation,
              label: graph.nodes.find((n) => n.id === id)?.data.label || "图片",
            }),
        }}
      >
        <div
          className={`canvas-workspace ${inspectorOpen ? "" : "is-focused"}`}
        >
          <div
            className="canvas-surface"
            tabIndex={0}
            onDragOver={(e) => {
              if (!busy && e.dataTransfer.types.includes("Files")) {
                e.preventDefault();
                e.dataTransfer.dropEffect = "copy";
              }
            }}
            onDrop={(e) => {
              if (e.dataTransfer.files.length) {
                e.preventDefault();
                receiveFiles(
                  Array.from(e.dataTransfer.files),
                  flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
                );
              }
            }}
            onPaste={(e) => {
              if (
                (e.target as HTMLElement).closest(
                  "input,textarea,[contenteditable=true]",
                ) ||
                busy
              )
                return;
              const files = Array.from(e.clipboardData.files);
              if (files.length) {
                e.preventDefault();
                receiveFiles(files);
                return;
              }
              const text = e.clipboardData.getData("text/plain");
              if (text.includes('"format":"inspiration-canvas"')) {
                e.preventDefault();
                void pasteNodes(text);
              }
            }}
            onKeyDown={(e) => {
              if (
                (e.target as HTMLElement).closest(
                  "input,textarea,select,[contenteditable=true]",
                )
              )
                return;
              if (
                (e.ctrlKey || e.metaKey) &&
                e.key.toLowerCase() === "a" &&
                !busy
              ) {
                e.preventDefault();
                setGraph((g) => ({
                  ...g,
                  nodes: g.nodes.map((n) => ({ ...n, selected: true })),
                  edges: g.edges.map((edge) => ({ ...edge, selected: true })),
                }));
              }
              if (
                (e.ctrlKey || e.metaKey) &&
                e.key.toLowerCase() === "c" &&
                !busy
              ) {
                e.preventDefault();
                void copyNodes();
              }
              if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
                e.preventDefault();
                duplicate();
              }
              if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
                e.preventDefault();
                e.shiftKey ? redo() : undo();
              }
              if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
                e.preventDefault();
                void perform(async () => {
                  await persist();
                });
              }
            }}
          >
            <div className="canvas-addbar">
              <Button
                variant="ghost"
                aria-label="添加内容"
                aria-expanded={addOpen}
                onClick={() => setAddOpen((v) => !v)}
              >
                <Plus size={19} />
                <span>添加</span>
              </Button>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => setMaterialOpen(true)}
              >
                <Images size={17} />
                项目素材
              </Button>
              <Button variant="ghost" onClick={() => setToolsOpen(!toolsOpen)}>
                画布工具
              </Button>
              {toolsOpen && (
                <div className="canvas-tools-menu">
                  <strong>创作与整理</strong>
                  <button
                    disabled={busy}
                    onClick={() =>
                      document.getElementById("canvas-direct-file")?.click()
                    }
                  >
                    导入本地文件
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setLibraryOpen(true);
                      setToolsOpen(false);
                    }}
                  >
                    从视频素材库选取
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setImageStudio({});
                      setToolsOpen(false);
                    }}
                  >
                    自由绘图
                  </button>
                  <button disabled={busy} onClick={() => void copyNodes()}>
                    复制选中节点
                  </button>
                  <button disabled={busy} onClick={() => void pasteNodes()}>
                    粘贴节点
                  </button>
                  <button disabled={busy} onClick={() => setSnap(!snap)}>
                    {snap ? "关闭" : "开启"}网格吸附
                  </button>
                  <button
                    disabled={working}
                    onClick={() => void exportBundle()}
                  >
                    导出离线作品包
                  </button>
                  <button onClick={() => void shareLink()}>
                    复制项目只读链接
                  </button>
                </div>
              )}
              <input
                id="canvas-direct-file"
                type="file"
                multiple
                accept="image/png,image/jpeg,image/webp,video/mp4"
                className="sr-only"
                onChange={(e) => {
                  receiveFiles(Array.from(e.target.files ?? []));
                  e.target.value = "";
                }}
              />
              {addOpen && (
                <div className="canvas-add-menu">
                  <p>从这里开始创作</p>
                  <button disabled={busy} onClick={() => add("text")}>
                    <FileText size={18} />
                    <span>
                      创意文字<small>故事、提示词与镜头笔记</small>
                    </span>
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setMaterialOpen(true);
                      setAddOpen(false);
                    }}
                  >
                    <Images size={18} />
                    <span>
                      引用素材<small>图片、视频与声音版本</small>
                    </span>
                  </button>
                  <button disabled={busy} onClick={() => add("asset")}>
                    <Boxes size={18} />
                    <span>
                      角色与资产<small>引用设定与代表版本</small>
                    </span>
                  </button>
                  <button disabled={busy} onClick={() => add("shot")}>
                    <Film size={18} />
                    <span>
                      镜头引用<small>关联镜头说明与已有作品</small>
                    </span>
                  </button>
                  {(["image", "video", "audio"] as const).map((type) => (
                    <button
                      key={type}
                      disabled={busy}
                      onClick={() => add("generate", type)}
                    >
                      {type === "image" ? (
                        <ImagePlus size={18} />
                      ) : type === "video" ? (
                        <Film size={18} />
                      ) : (
                        <Music size={18} />
                      )}
                      <span>
                        {
                          {
                            image: "图片创作",
                            video: "视频创作",
                            audio: "声音创作",
                          }[type]
                        }
                        <small>连接参考，准备新一轮生成</small>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {graph.nodes.filter((n) => n.selected).length > 1 && (
              <div className="canvas-selection-actions">
                <span>
                  已选 {graph.nodes.filter((n) => n.selected).length} 项
                </span>
                <select
                  aria-label="对齐与分布"
                  value=""
                  disabled={busy}
                  onChange={(e) =>
                    commit(
                      arrangeSelection(
                        graph,
                        e.target.value as Parameters<
                          typeof arrangeSelection
                        >[1],
                      ),
                    )
                  }
                >
                  <option value="" disabled>
                    对齐与分布
                  </option>
                  {Object.entries({
                    left: "左对齐",
                    right: "右对齐",
                    top: "顶端对齐",
                    bottom: "底端对齐",
                    center: "水平居中",
                    middle: "垂直居中",
                    horizontal: "水平等距",
                    vertical: "垂直等距",
                  }).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={alignSelected}
                  disabled={busy}
                >
                  <AlignLeft size={14} />
                  左对齐
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={groupSelected}
                  disabled={busy}
                >
                  创建分组
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={duplicate}
                  disabled={busy}
                >
                  复制
                </Button>
              </div>
            )}
            {edgeDrop && (
              <div
                ref={edgeMenu}
                className="canvas-edge-menu nodrag nopan nowheel"
                role="dialog"
                aria-label="拖线新增节点"
                style={{ left: edgeDrop.left, top: edgeDrop.top }}
                onKeyDown={(event) => {
                  if (
                    !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)
                  )
                    return;
                  event.preventDefault();
                  const buttons = Array.from(
                    event.currentTarget.querySelectorAll<HTMLButtonElement>(
                      "button",
                    ),
                  );
                  const i = buttons.indexOf(
                    document.activeElement as HTMLButtonElement,
                  );
                  buttons[
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? buttons.length - 1
                        : (i +
                            (event.key === "ArrowDown" ? 1 : -1) +
                            buttons.length) %
                          buttons.length
                  ]?.focus();
                }}
              >
                <strong>
                  {edgeDrop.direction === "source"
                    ? "接着创作"
                    : "添加上游内容"}
                </strong>
                <p>
                  {["image", "reference"].includes(edgeDrop.handle)
                    ? "自动连接图片参考"
                    : "自动连接文字输入"}
                </p>
                {(edgeDrop.direction === "target"
                  ? edgeDrop.handle === "prompt"
                    ? (["text"] as const)
                    : (["image"] as const)
                  : edgeDrop.handle === "image"
                    ? (["image", "video"] as const)
                    : (["image", "video", "audio"] as const)
                ).map((kind) => (
                  <button
                    key={kind}
                    onClick={() => createConnected(kind)}
                    disabled={busy}
                  >
                    {kind === "text" ? (
                      <FileText size={17} />
                    ) : kind === "image" ? (
                      <ImagePlus size={17} />
                    ) : kind === "video" ? (
                      <Film size={17} />
                    ) : (
                      <Music size={17} />
                    )}
                    {
                      {
                        text: "创意文字",
                        image: "图片创作",
                        video: "视频创作",
                        audio: "声音创作",
                      }[kind]
                    }
                    <Plus size={14} />
                  </button>
                ))}
                <small>Esc 取消 · 新节点与连线可一起撤销</small>
              </div>
            )}
            <ReactFlow<CanvasNode>
              colorMode={theme}
              nodes={decorated}
              edges={graph.edges.map((e) => ({
                ...e,
                style: {
                  stroke:
                    e.targetHandle === "reference" ? "#a78bfa" : "#38bdf8",
                  strokeWidth: 2,
                },
              }))}
              nodeTypes={nodeTypes}
              onNodesChange={(changes) => {
                if (busy) return;
                setGraph((g) => ({
                  ...g,
                  nodes: applyNodeChanges(changes, g.nodes),
                }));
              }}
              onEdgesChange={(changes) => {
                if (!busy)
                  setGraph((g) => ({
                    ...g,
                    edges: applyEdgeChanges(changes, g.edges),
                  }));
              }}
              snapToGrid={snap}
              snapGrid={[24, 24]}
              edgesReconnectable={!busy}
              onReconnect={(old, c) => {
                if (busy) return;
                const without = {
                  ...graph,
                  edges: graph.edges.filter((e) => e.id !== old.id),
                };
                if (
                  !validConnection(
                    without,
                    c.source,
                    c.target,
                    c.sourceHandle ?? "text",
                    c.targetHandle ?? "prompt",
                  )
                ) {
                  setError("这条连线不兼容或会形成循环");
                  return;
                }
                commit({
                  ...graph,
                  edges: graph.edges.map((e) =>
                    e.id === old.id ? { ...e, ...c } : e,
                  ),
                });
                setError("");
              }}
              onPaneContextMenu={(e) => {
                if (!busy) {
                  e.preventDefault();
                  setToolsOpen(true);
                  setAddOpen(false);
                }
              }}
              onNodeContextMenu={(e, n) => {
                if (!busy) {
                  e.preventDefault();
                  setGraph((g) => ({
                    ...g,
                    nodes: g.nodes.map((x) => ({
                      ...x,
                      selected: x.id === n.id,
                    })),
                  }));
                  setToolsOpen(true);
                }
              }}
              onConnect={connect}
              onConnectStart={() => {
                connectionCanceled.current = false;
                setEdgeDrop(null);
              }}
              onConnectEnd={endConnection}
              onMoveStart={() => setEdgeDrop(null)}
              onPaneClick={() => {
                setToolsOpen(false);
                setAddOpen(false);
                setEdgeDrop(null);
              }}
              onNodeClick={() => setPanel("edit")}
              onNodeDoubleClick={(_, node) => settings(node.id)}
              onNodeDragStart={() => {
                beforeDrag.current = cleanGraph(graph);
              }}
              onNodeDragStop={() => {
                if (beforeDrag.current) {
                  const previous = beforeDrag.current;
                  setPast((p) => [...p, previous].slice(-100));
                  setFuture([]);
                  beforeDrag.current = null;
                }
              }}
              onMoveEnd={(_, viewport) => {
                if (!busy) setGraph((g) => ({ ...g, viewport }));
              }}
              defaultViewport={graph.viewport}
              minZoom={0.05}
              maxZoom={4}
              selectionOnDrag={!hand}
              panOnDrag={readOnly ? true : hand ? [0, 1, 2] : [1, 2]}
              panActivationKeyCode="Space"
              selectionMode={SelectionMode.Partial}
              multiSelectionKeyCode="Shift"
              nodesDraggable={!busy}
              nodesConnectable={!busy}
              elementsSelectable={!busy}
              deleteKeyCode={null}
              onlyRenderVisibleElements
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={24} size={1} />
              <Controls
                showInteractive={false}
                onFitView={() => setGraph(fitGraph(graph))}
              />
              <MiniMap
                pannable
                zoomable
                nodeColor={(n) =>
                  n.data.kind === "group" ? "#475569" : "#38bdf8"
                }
              />
            </ReactFlow>
            {!graph.nodes.length && (
              <div className="canvas-empty">
                <div className="canvas-empty-mark">
                  <Plus size={28} />
                </div>
                <h2>让想法在这里展开</h2>
                <p>一段文字、一张参考图，或一个新的镜头。</p>
                <div className="flex flex-wrap justify-center gap-2">
                  <Button variant="outline" onClick={() => add("text")}>
                    <FileText size={15} />
                    写下想法
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setMaterialOpen(true)}
                  >
                    <Images size={15} />
                    放入素材
                  </Button>
                </div>
              </div>
            )}
            <div className="canvas-bottom-tools">
              <Button
                size="icon"
                variant={hand ? "ghost" : "default"}
                aria-label="区域选择"
                onClick={() => setHand(false)}
              >
                <MousePointer2 size={16} />
              </Button>
              <Button
                size="icon"
                variant={hand ? "default" : "ghost"}
                aria-label="抓手平移"
                onClick={() => setHand(true)}
              >
                <Hand size={16} />
              </Button>
              <div className="w-px h-5 bg-border mx-1" />
              <Button
                size="icon"
                variant="ghost"
                aria-label="复制选中节点"
                title="复制选中节点（Ctrl / ⌘ D）"
                disabled={busy || !graph.nodes.some((n) => n.selected)}
                onClick={duplicate}
              >
                <Copy size={16} />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                disabled={busy}
                title="整理布局"
                aria-label="整理布局"
                onClick={layout}
              >
                <LayoutGrid size={16} />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                disabled={
                  busy ||
                  graph.nodes.filter(
                    (n) => n.selected && !n.parentId && n.data.kind !== "group",
                  ).length < 2
                }
                title="选中节点分组"
                aria-label="选中节点分组"
                onClick={groupSelected}
              >
                <Group size={16} />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                disabled={
                  busy ||
                  (!graph.nodes.some((n) => n.selected) &&
                    !graph.edges.some((e) => e.selected))
                }
                aria-label="删除选中项"
                onClick={removeSelected}
              >
                <Trash2 size={16} />
              </Button>
            </div>
          </div>
          {inspectorOpen && (
            <button
              className="canvas-inspector-close"
              aria-label="关闭画布面板"
              onClick={() => setInspectorOpen(false)}
            >
              <X size={16} />
            </button>
          )}
          <CanvasInspector
            {...{
              projectId,
              path,
              graph,
              selected,
              busy,
              panel,
              setPanel,
              update,
              connect,
              run,
              runs,
              history,
              revision,
              viewRun,
              setViewRun,
              runOffset,
              setRunOffset,
              historyOffset,
              setHistoryOffset,
              assets,
              shots,
              providers,
              perform,
              accept,
              dirty,
              confirm,
            }}
            ungroup={() => {
              if (selected)
                commit({
                  ...graph,
                  nodes: graph.nodes.map((n) =>
                    n.id === selected.id
                      ? {
                          ...n,
                          position: absolutePosition(n, graph.nodes),
                          parentId: undefined,
                        }
                      : n,
                  ),
                });
            }}
          />
        </div>
      </CanvasActions.Provider>
      {notice && (
        <div className="canvas-notice" role="status">
          {notice}
          <button onClick={() => setNotice("")} aria-label="关闭提示">
            ×
          </button>
        </div>
      )}
      {readOnly && (
        <div className="canvas-notice" role="status">
          只读浏览 · 修改请打开项目中的原画布
        </div>
      )}
      {libraryOpen && (
        <CanvasLibraryPanel
          projectId={projectId}
          shots={shots.data ?? []}
          onClose={() => setLibraryOpen(false)}
          onDone={(g, label) => placeMedia([{ generation: g, label }])}
        />
      )}
      {imageStudio && (
        <CanvasImageStudio
          path={path}
          projectId={projectId}
          source={imageStudio.source}
          onClose={() => setImageStudio(null)}
          onDone={(g, label) => {
            placeMedia([{ generation: g, label }]);
            void assets.refetch();
            void qc.invalidateQueries({
              queryKey: ["gens", g.target_type, g.target_id],
            });
          }}
        />
      )}
      {incomingFiles && (
        <CanvasFileDrop
          path={path}
          files={incomingFiles.files}
          onClose={() => setIncomingFiles(null)}
          onDone={(items) => {
            placeMedia(
              items,
              Math.min(items.length, 3),
              undefined,
              incomingFiles.position,
            );
            void assets.refetch();
          }}
        />
      )}
      {splitSource && (
        <CanvasGridSplit
          projectId={projectId}
          source={splitSource.generation}
          label={splitSource.label}
          available={500 - graph.nodes.length}
          onClose={() => setSplitSource(null)}
          onInsert={(items, columns) =>
            placeMedia(
              items.map((generation, i) => ({
                generation,
                label: `${splitSource.label.slice(0, 100)} · 分格 ${String((generation.input_refs?.cell_index ?? i) + 1).padStart(2, "0")}`,
              })),
              columns,
              splitSource.nodeId,
            )
          }
        />
      )}
      {materialOpen && (
        <CanvasMaterialPicker
          projectId={projectId}
          assets={assets.data ?? []}
          shots={shots.data ?? []}
          loading={assets.isPending || shots.isPending}
          loadError={assets.error?.message || shots.error?.message}
          onRetry={() => {
            void assets.refetch();
            void shots.refetch();
          }}
          onClose={() => setMaterialOpen(false)}
          onPick={addMedia}
        />
      )}
      {versionNode && graph.nodes.find((n) => n.id === versionNode) && (
        <CanvasVersionDialog
          projectId={projectId}
          node={graph.nodes.find((n) => n.id === versionNode)!}
          requiresImage={graph.edges.some(
            (e) => e.source === versionNode && e.sourceHandle === "image",
          )}
          onClose={() => setVersionNode(null)}
          onPin={(g) => {
            updateNode(versionNode, { generation_id: g.id });
            setVersionNode(null);
          }}
        />
      )}
      <p className="canvas-keyboard-hint">
        拖线到空白处新增节点 <span>·</span> Shift 多选 <span>·</span>{" "}
        空格拖动画布 <span>·</span> Ctrl / ⌘ D 复制 <span>·</span> Ctrl / ⌘ Z
        撤销
      </p>
    </>
  );
}
