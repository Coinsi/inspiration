import {
  absolutePosition,
  cleanGraph,
  type Graph,
  type CanvasNode,
} from "./canvas";

export function arrangeSelection(
  graph: Graph,
  mode:
    | "left"
    | "right"
    | "top"
    | "bottom"
    | "center"
    | "middle"
    | "horizontal"
    | "vertical",
): Graph {
  const selected = graph.nodes.filter(
    (n) => n.selected && n.data.kind !== "group",
  );
  if (selected.length < 2) return graph;
  const nodes = selected.map((n) => ({
    n,
    p: absolutePosition(n, graph.nodes),
    w: n.width ?? 320,
    h: n.height ?? 260,
  }));
  const left = Math.min(...nodes.map((n) => n.p.x)),
    right = Math.max(...nodes.map((n) => n.p.x + n.w));
  const top = Math.min(...nodes.map((n) => n.p.y)),
    bottom = Math.max(...nodes.map((n) => n.p.y + n.h));
  const positions = new Map<string, { x: number; y: number }>();
  if (mode === "horizontal" || mode === "vertical") {
    const horizontal = mode === "horizontal",
      sorted = nodes.sort((a, b) =>
        horizontal ? a.p.x - b.p.x : a.p.y - b.p.y,
      );
    const gap =
      ((horizontal ? right - left : bottom - top) -
        sorted.reduce((sum, n) => sum + (horizontal ? n.w : n.h), 0)) /
      (sorted.length - 1);
    let cursor = horizontal ? left : top;
    for (const n of sorted) {
      positions.set(n.n.id, {
        x: horizontal ? cursor : n.p.x,
        y: horizontal ? n.p.y : cursor,
      });
      cursor += (horizontal ? n.w : n.h) + gap;
    }
  } else
    for (const n of nodes)
      positions.set(n.n.id, {
        x:
          mode === "left"
            ? left
            : mode === "right"
              ? right - n.w
              : mode === "center"
                ? (left + right - n.w) / 2
                : n.p.x,
        y:
          mode === "top"
            ? top
            : mode === "bottom"
              ? bottom - n.h
              : mode === "middle"
                ? (top + bottom - n.h) / 2
                : n.p.y,
      });
  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const p = positions.get(n.id);
      if (!p) return n;
      const parent = graph.nodes.find((x) => x.id === n.parentId);
      return {
        ...n,
        position: {
          x: p.x - (parent?.position.x ?? 0),
          y: p.y - (parent?.position.y ?? 0),
        },
      };
    }),
  };
}

export function copySelection(graph: Graph, projectId: string) {
  const ids = new Set(graph.nodes.filter((n) => n.selected).map((n) => n.id));
  for (const n of graph.nodes)
    if (n.parentId && ids.has(n.parentId)) ids.add(n.id);
  if (!ids.size) throw new Error("请先选择要复制的节点");
  const clean = cleanGraph(graph);
  return {
    format: "inspiration-canvas",
    projectId,
    nodes: clean.nodes
      .filter((n) => ids.has(n.id))
      .map((n) => ({
        ...n,
        position:
          n.parentId && !ids.has(n.parentId)
            ? absolutePosition(n, clean.nodes)
            : n.position,
        parentId: n.parentId && ids.has(n.parentId) ? n.parentId : undefined,
      })),
    edges: clean.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
  };
}

export function pasteSelection(
  graph: Graph,
  raw: string,
  projectId: string,
  position: { x: number; y: number },
): Graph {
  if (raw.length > 4_000_000) throw new Error("复制内容过大");
  const data = JSON.parse(raw);
  if (
    data.format !== "inspiration-canvas" ||
    !Array.isArray(data.nodes) ||
    !Array.isArray(data.edges)
  )
    throw new Error("不是可识别的画布内容");
  if (data.projectId !== projectId)
    throw new Error("目前支持同一项目内跨画布复制；跨项目请通过素材库复用");
  if (
    !data.nodes.length ||
    graph.nodes.length + data.nodes.length > 500 ||
    graph.edges.length + data.edges.length > 1500
  )
    throw new Error("粘贴内容超出画布容量");
  const original = cleanGraph({
    nodes: data.nodes,
    edges: data.edges,
    viewport: graph.viewport,
  });
  const ids = new Map(original.nodes.map((n) => [n.id, crypto.randomUUID()]));
  if (ids.size !== original.nodes.length)
    throw new Error("复制内容的节点编号重复");
  const roots = original.nodes.filter((n) => !n.parentId);
  const x = Math.min(...roots.map((n) => n.position.x)),
    y = Math.min(...roots.map((n) => n.position.y));
  const nodes: CanvasNode[] = original.nodes.map((n) => ({
    ...n,
    id: ids.get(n.id)!,
    selected: true,
    parentId: n.parentId ? ids.get(n.parentId) : undefined,
    position: n.parentId
      ? n.position
      : { x: n.position.x - x + position.x, y: n.position.y - y + position.y },
    data: {
      ...n.data,
      text: (n.data.mentions ?? [])
        .filter((m) => !ids.has(m.node_id))
        .reduce(
          (text, m) => text.split(`@【${m.alias}】`).join(m.alias),
          n.data.text,
        ),
      mentions: (n.data.mentions ?? [])
        .filter((m) => ids.has(m.node_id))
        .map((m) => ({ ...m, node_id: ids.get(m.node_id)! })),
    },
  }));
  if (
    nodes.some(
      (n) =>
        !Number.isFinite(n.position.x) ||
        !Number.isFinite(n.position.y) ||
        Math.abs(n.position.x) > 100000 ||
        Math.abs(n.position.y) > 100000,
    )
  )
    throw new Error("粘贴位置无效");
  return {
    ...graph,
    nodes: [...graph.nodes.map((n) => ({ ...n, selected: false })), ...nodes],
    edges: [
      ...graph.edges,
      ...original.edges
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({
          ...e,
          id: crypto.randomUUID(),
          source: ids.get(e.source)!,
          target: ids.get(e.target)!,
        })),
    ],
  };
}
