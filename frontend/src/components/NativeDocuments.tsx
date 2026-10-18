import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "./ui/button";
import { SelectField } from "./ui/select-field";
import { DocumentChangeReview } from "./WorkPackage";

const names: Record<string, string> = {
  "image.document": "图像",
  "audio.document": "音频",
  "composition.document": "合成",
  "sync.document": "同步规则",
};
type Doc = {
  id: string;
  title: string;
  kind: string;
  revisions: { revision: number }[];
};
type Payload = {
  constraints?: {
    id: string;
    name: string;
    relation: string;
    policy: "hard" | "review";
    members: { document_input: string; instance_id: string }[];
  }[];
  canvas?: { width: number; height: number };
  layers?: { id: string; name: string; source: string }[];
  tracks?: { id: string; name: string; gain_db?: number; clips: unknown[] }[];
};
type Head = {
  revision: number;
  snapshot: { native_roots?: Record<string, { revision: number }> };
};
type Detail = {
  schema_version: number;
  adapter_available?: boolean;
  title: string;
  kind: string;
  inputs: Record<string, { target: { id: string; revision: number } }>;
  payload: Payload;
};

export function NativeDocuments({
  projectId,
  workId,
  documents,
}: {
  projectId: string;
  workId: string;
  documents: Doc[];
}) {
  const [selected, setSelected] = useState("");
  const [revision, setRevision] = useState("");
  const [candidate, setCandidate] = useState("");
  const [draft, setDraft] = useState<Payload | null>(null);
  const [basis, setBasis] = useState<Head | null>(null);
  const cache = useQueryClient();
  const path = `/projects/${projectId}/works/${workId}/engineering`;
  const native = documents.filter(
    (d) => !["resource", "work.manifest", "sequence.timeline"].includes(d.kind),
  );
  const doc = native.find((d) => d.id === selected) || native[0];
  const version = Number(revision || doc?.revisions[0]?.revision || 0);
  const detail = useQuery({
    queryKey: ["native-document", workId, doc?.id, version],
    queryFn: () =>
      api.get<Detail>(`${path}/documents/${doc!.id}/revisions/${version}`),
    enabled: !!doc && version > 0,
  });
  const head = useQuery({
    queryKey: ["native-edit-basis", workId],
    queryFn: () => api.get<Head>(path),
    enabled: !!doc,
  });
  function edit(payload: Payload) {
    if (!head.data) return;
    if (!basis) {
      setBasis(head.data);
      setRevision(String(version));
    }
    setDraft(payload);
  }
  const save = useMutation({
    mutationFn: async () => {
      if (!basis) throw new Error("请重新读取工程后修改");
      return api.post<{ id: string }>(`${path}/changes`, {
        request_key: crypto.randomUUID(),
        base_revision: basis.revision,
        commands: [
          {
            operation: "document.put",
            document_id: doc.id,
            expected_revision:
              basis.snapshot.native_roots?.[doc.id]?.revision || 0,
            kind: doc.kind,
            schema_version: detail.data!.schema_version,
            title: doc.title,
            inputs: detail.data!.inputs,
            payload: draft || detail.data!.payload,
          },
        ],
      });
    },
    onSuccess: (change) => {
      setCandidate(change.id);
      setDraft(null);
      setBasis(null);
      void cache.invalidateQueries();
    },
  });
  if (!doc) return null;
  const supported =
    !!names[doc.kind] &&
    detail.data?.adapter_available !== false &&
    ((detail.data?.schema_version ?? 1) === 1 ||
      (detail.data?.schema_version === 2 &&
        ["audio.document", "composition.document"].includes(doc.kind)));
  const content = supported ? draft || detail.data?.payload : undefined;
  return (
    <section className="space-y-4 rounded-2xl border p-5">
      <h3 className="font-medium">工程文档</h3>
      <p className="text-sm text-muted-foreground">
        这里保留可编辑结构。图像、音频与合成候选可按已支持的能力生成预览；保存不会自动生成媒体，不支持的效果会在执行前提示。
      </p>
      <div className="flex flex-wrap gap-3">
        <SelectField
          label="编辑文档"
          value={doc.id}
          options={native.map((d) => ({
            value: d.id,
            label: `${d.title} · ${names[d.kind] || d.kind}`,
          }))}
          onChange={(id) => {
            setSelected(id);
            setRevision("");
            setDraft(null);
            setBasis(null);
            setCandidate("");
          }}
        />
        <SelectField
          label="内容修订"
          value={String(version)}
          options={doc.revisions.map((r) => ({
            value: String(r.revision),
            label: `修订 ${r.revision}`,
          }))}
          onChange={(v) => {
            setRevision(v);
            setDraft(null);
            setBasis(null);
          }}
        />
      </div>
      {detail.isPending && <p role="status">正在读取文档…</p>}
      {(detail.error || head.error || save.error) && (
        <p role="alert" className="text-sm text-danger">
          {(detail.error || head.error || save.error)?.message}
        </p>
      )}
      {content?.constraints?.map((rule, index) => (
        <div key={rule.id} className="space-y-3 rounded-xl border p-4">
          <label className="block space-y-1 text-sm">
            <span>同步规则名称</span>
            <input
              className="w-full rounded-lg border bg-transparent p-2"
              value={rule.name}
              onChange={(event) =>
                edit({
                  ...content,
                  constraints: content.constraints!.map((item, position) =>
                    position === index
                      ? { ...item, name: event.target.value }
                      : item,
                  ),
                })
              }
            />
          </label>
          <p className="text-sm text-muted-foreground">
            {rule.members
              .map((member) => {
                const ref = detail.data?.inputs[member.document_input]?.target;
                const title =
                  documents.find((item) => item.id === ref?.id)?.title ||
                  member.document_input;
                return `${title} · 修订 ${ref?.revision ?? "—"}`;
              })
              .join(" / ")}
          </p>
          <SelectField
            label="同步要求"
            value={rule.policy}
            options={[
              { value: "hard", label: "必须保持同步" },
              { value: "review", label: "变化后需要人工复核" },
            ]}
            onChange={(value) =>
              edit({
                ...content,
                constraints: content.constraints!.map((item, position) =>
                  position === index
                    ? { ...item, policy: value as "hard" | "review" }
                    : item,
                ),
              })
            }
          />
          <p className="text-xs text-muted-foreground">
            {{
              offset: "对齐指定时间点",
              shared_map: "保持共同时间映射",
              semantic_alignment: "固定来源与对齐证据",
            }[rule.relation] || rule.relation}
            。修改规则会先生成候选，原规则在采用前继续生效。
          </p>
        </div>
      ))}
      {content?.canvas && (
        <div className="flex flex-wrap gap-4">
          {(["width", "height"] as const).map((key) => (
            <label key={key} className="text-sm">
              {key === "width" ? "画布宽度" : "画布高度"}
              <input
                type="number"
                min={1}
                max={32768}
                className="ml-2 w-24 rounded-lg border bg-transparent p-2"
                value={content.canvas![key]}
                onChange={(e) =>
                  edit({
                    ...content,
                    canvas: {
                      ...content.canvas!,
                      [key]: Number(e.target.value),
                    },
                  })
                }
              />
            </label>
          ))}
        </div>
      )}
      {content?.layers?.map((layer) => (
        <div key={layer.id} className="rounded-lg bg-muted p-3 text-sm">
          {layer.name} · 引用 {layer.source}
        </div>
      ))}
      {content?.tracks?.map((track, index) => (
        <label
          key={track.id}
          className="flex flex-wrap items-center gap-3 rounded-lg bg-muted p-3 text-sm"
        >
          <span>
            {track.name} · {track.clips.length} 个片段
          </span>
          <span>音量（dB）</span>
          <input
            type="number"
            min={-120}
            max={24}
            step={0.5}
            className="w-24 rounded-lg border bg-transparent p-2"
            value={track.gain_db || 0}
            onChange={(e) =>
              edit({
                ...content,
                tracks: content.tracks!.map((t, i) =>
                  i === index ? { ...t, gain_db: Number(e.target.value) } : t,
                ),
              })
            }
          />
        </label>
      ))}
      {detail.data && !supported && (
        <div className="space-y-2 rounded-lg border p-3 text-sm">
          <p>
            该文档已完整保留。当前页面尚不支持此格式的编辑，请安装对应适配器后继续；导出工程会保留原内容。
          </p>
          <details>
            <summary>查看保存的扩展内容</summary>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all">
              {JSON.stringify(detail.data.payload, null, 2)}
            </pre>
          </details>
        </div>
      )}
      {detail.data && supported && (
        <Button
          disabled={save.isPending || !draft}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "正在保存…" : "保存修改候选"}
        </Button>
      )}
      {candidate && (
        <DocumentChangeReview
          key={candidate}
          projectId={projectId}
          workId={workId}
          changeId={candidate}
        />
      )}
    </section>
  );
}
