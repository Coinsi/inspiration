import type { Node, Edge, Viewport } from "@xyflow/react";
export type Kind = "text" | "asset" | "shot" | "generate" | "group";
export type NodeData = Record<string, unknown> & {
  kind: Kind;
  label: string;
  text: string;
  target_type: "asset" | "shot";
  target_id: string | null;
  generation_id: string | null;
  mentions?: { node_id: string; alias: string }[];
  generate: {
    provider: string;
    request_type: "image" | "video" | "audio";
    count: number;
    params: Record<string, unknown>;
    provider_params: Record<string, unknown>;
    use_references: boolean;
    prompt_override?: string | null;
    skills?: import("@/lib/skills").SkillUse[];
  };
  preview?: string;
  status?: string;
};
export type CanvasNode = Node<NodeData>;
export type Graph = { nodes: CanvasNode[]; edges: Edge[]; viewport: Viewport };
export type CanvasDoc = {
  id: string;
  name: string;
  revision: number;
  document: Graph;
};
export type Run = {
  target_node_id?: string | null;
  id: string;
  revision: number;
  status: string;
  error?: string;
  steps: Record<
    string,
    {
      status: string;
      reused?: boolean;
      error?: string;
      job_id?: string;
      attempt: number;
      generation_ids?: string[];
      output?: { blob_hash?: string; generation_id?: string };
    }
  >;
};
export const labels: Record<Kind, string> = {
  text: "创意文字",
  asset: "角色与资产",
  shot: "镜头参考",
  generate: "生成节点",
  group: "分组",
};
export const statuses: Record<string, string> = {
  waiting: "等待输入",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  paused: "已暂停",
  canceled: "已取消",
};
export function cleanGraph(graph: Graph): Graph {
  return {
    viewport: graph.viewport,
    nodes: graph.nodes.map((n) => ({
      id: n.id,
      position: n.position,
      parentId: n.parentId ?? undefined,
      width: n.width ?? 260,
      height: n.height ?? 210,
      data: {
        kind: n.data.kind,
        label: n.data.label,
        text: n.data.text,
        target_type: n.data.target_type,
        target_id: n.data.target_id,
        generation_id: n.data.generation_id,
        generate: n.data.generate,
        mentions: n.data.mentions ?? [],
      },
    })),
    edges: graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? "text",
      targetHandle: e.targetHandle ?? "prompt",
    })),
  };
}
export function validConnection(
  graph: Graph,
  source: string,
  target: string,
  from: string,
  to: string,
): boolean {
  const a = graph.nodes.find((n) => n.id === source),
    b = graph.nodes.find((n) => n.id === target);
  if (
    !a ||
    !b ||
    source === target ||
    b.data.kind !== "generate" ||
    a.data.kind === "group"
  )
    return false;
  if ((from === "text" ? "prompt" : "reference") !== to) return false;
  if (
    from === "image" &&
    (a.data.kind === "text" ||
      (a.data.kind === "generate" && a.data.generate.request_type !== "image"))
  )
    return false;
  if (
    graph.edges.some(
      (e) =>
        e.source === source && e.target === target && e.targetHandle === to,
    )
  )
    return false;
  const visited = new Set<string>(),
    pending = [target];
  while (pending.length) {
    const id = pending.pop()!;
    if (id === source) return false;
    if (visited.has(id)) continue;
    visited.add(id);
    graph.edges
      .filter((e) => e.source === id)
      .forEach((e) => pending.push(e.target));
  }
  return true;
}
export function newNode(kind: Kind, x: number, y: number): CanvasNode {
  return {
    id: crypto.randomUUID(),
    position: { x, y },
    width: kind === "group" ? 800 : 320,
    height: kind === "group" ? 420 : 260,
    data: {
      kind,
      label: labels[kind],
      text: "",
      target_type: kind === "asset" ? "asset" : "shot",
      target_id: null,
      generation_id: null,
      generate: {
        provider: "mock",
        request_type: "image",
        count: 1,
        params: {},
        provider_params: {},
        use_references: false,
      },
    },
  };
}
export function absolutePosition(node: CanvasNode, nodes: CanvasNode[]) {
  const p = nodes.find((n) => n.id === node.parentId);
  return {
    x: node.position.x + (p?.position.x ?? 0),
    y: node.position.y + (p?.position.y ?? 0),
  };
}

/** Duplicate an editable branch; references stay fixed, outputs/runs are not copied. */
export function duplicateSelection(graph: Graph): Graph {
  const ids = new Set(graph.nodes.filter((n) => n.selected).map((n) => n.id));
  if (!ids.size) return graph;
  for (let changed = true; changed;) {
    changed = false;
    for (const node of graph.nodes) {
      if (node.parentId && ids.has(node.parentId) && !ids.has(node.id)) {
        ids.add(node.id);
        changed = true;
      }
    }
  }
  const incoming = graph.edges.filter((edge) => ids.has(edge.target));
  if (
    graph.nodes.length + ids.size > 500 ||
    graph.edges.length + incoming.length > 1500
  )
    throw new Error(
      "复制后将超过画布容量（500节点 / 1500连线），请减少选中项。",
    );
  const keys = new Map([...ids].map((id) => [id, crypto.randomUUID()]));
  const copies = cleanGraph(graph)
    .nodes.filter((n) => ids.has(n.id))
    .map((node) => {
      const withParent = !!node.parentId && ids.has(node.parentId);
      const position = withParent
        ? node.position
        : absolutePosition(node, graph.nodes);
      return {
        ...node,
        id: keys.get(node.id)!,
        parentId: withParent ? keys.get(node.parentId!) : undefined,
        position: withParent
          ? position
          : {
              x: Math.min(100000, position.x + 48),
              y: Math.min(100000, position.y + 48),
            },
        selected: true,
        data: {
          ...structuredClone(node.data),
          label: `${node.data.label.slice(0, 115)} · 副本`,
        },
      };
    });
  return {
    ...graph,
    nodes: [...graph.nodes.map((n) => ({ ...n, selected: false })), ...copies],
    edges: [
      ...graph.edges.map((e) => ({ ...e, selected: false })),
      ...incoming.map((e) => ({
        ...e,
        id: crypto.randomUUID(),
        selected: false,
        source: keys.get(e.source) ?? e.source,
        target: keys.get(e.target)!,
      })),
    ],
  };
}

/** Find space beside existing content, reserving room for a node composer. */
export function vacantPosition(
  nodes: CanvasNode[],
  preferred: { x: number; y: number },
  width = 320,
  height = 260,
) {
  const position = { x: Math.min(100000, preferred.x), y: preferred.y };
  for (let pass = 0; pass <= nodes.length; pass++) {
    const overlaps = nodes
      .filter((n) => n.data.kind !== "group")
      .filter((n) => {
        const p = absolutePosition(n, nodes);
        return (
          position.x < p.x + (n.width ?? 320) + 48 &&
          position.x + width + 48 > p.x &&
          position.y < p.y + (n.height ?? 260) + 48 &&
          position.y + height + 280 > p.y
        );
      });
    if (!overlaps.length && position.y <= 100000) return position;
    const right = Math.max(
      ...overlaps.map(
        (n) => absolutePosition(n, nodes).x + (n.width ?? 320) + 90,
      ),
    );
    if (right <= 100000) position.x = right;
    else
      position.y = Math.max(
        position.y,
        ...overlaps.map(
          (n) => absolutePosition(n, nodes).y + (n.height ?? 260) + 90,
        ),
      );
  }
  throw new Error("附近没有足够空间，请先整理布局。");
}
