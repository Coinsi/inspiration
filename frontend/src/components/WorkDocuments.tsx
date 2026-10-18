import { useState } from "react";
import { WorkPackage } from "./WorkPackage";
import { NativeDocuments } from "./NativeDocuments";
import { NewWorkDocument } from "./NewWorkDocument";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, type Timeline } from "@/lib/api";
import { Button } from "./ui/button";
import { SelectField } from "./ui/select-field";
import { TimelinePlayback } from "./TimelinePlayback";
import { editableContent, type TimelineDocument } from "@/lib/timeline-edit";

type Doc = {
  id: string;
  title: string;
  kind: string;
  source_timeline_id: string | null;
  working_revision: number | null;
  revisions: { revision: number; created_at: string; content_hash: string }[];
};
type Revision = {
  id: string;
  revision: number;
  title: string;
  kind: string;
  inputs: Record<string, { target: { id: string; revision: number } }>;
  payload: {
    editor?: Omit<TimelineDocument, "revision">;
    aspect_ratio?: string;
  };
};
export function WorkDocuments({
  projectId,
  workId,
}: {
  projectId: string;
  workId: string;
}) {
  const base = `/projects/${projectId}`;
  const path = `${base}/works/${workId}/engineering`;
  const navigate = useNavigate();
  const [chosen, setChosen] = useState("");
  const [revision, setRevision] = useState("");
  const [time, setTime] = useState(0);
  const docs = useQuery({
    queryKey: ["engineering-documents", projectId, workId],
    queryFn: () => api.get<{ documents: Doc[] }>(`${path}/documents`),
  });
  const sequences =
    docs.data?.documents.filter((d) => d.kind === "sequence.timeline") || [];
  const doc = sequences.find((d) => d.id === chosen) || sequences[0];
  const rev = Number(
    revision || doc?.working_revision || doc?.revisions[0]?.revision || 0,
  );
  const detail = useQuery({
    queryKey: ["engineering-document", projectId, workId, doc?.id, rev],
    queryFn: () =>
      api.get<Revision>(`${path}/documents/${doc!.id}/revisions/${rev}`),
    enabled: !!doc && !!rev,
  });
  const reopen = useMutation({
    mutationFn: async () => {
      const head = await api.get<{ revision: number }>(path);
      const timeline = await api.get<TimelineDocument>(
        `${base}/timelines/${doc!.source_timeline_id}/document`,
      );
      return api.post<{ id: string }>(`${path}/changes`, {
        request_key: crypto.randomUUID(),
        base_revision: head.revision,
        commands: [
          {
            operation: "document.edit",
            document_id: doc!.id,
            document_revision: rev,
            timeline_revision: timeline.revision,
          },
        ],
      });
    },
    onSuccess: (c) =>
      navigate(
        `${base}/cuts?timeline=${doc!.source_timeline_id}&work=${workId}&review=1&change=${c.id}`,
      ),
  });
  const initialize = useMutation({
    mutationFn: async () => {
      const timelines = await api.get<Timeline[]>(
        `${base}/works/${workId}/timelines`,
      );
      for (const t of timelines)
        await api.post(`${base}/works/${workId}/timelines`, {
          timeline_id: t.id,
        });
    },
    onSuccess: () => docs.refetch(),
  });
  const error = docs.error || detail.error || reopen.error || initialize.error;
  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">工程内容</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          剪辑文档和固定素材依赖保存在作品中。保存产生修订，候选和已采用版本各自引用固定内容。
        </p>
      </div>
      {docs.data?.documents.some((d) => d.kind === "work.manifest") && (
        <p className="text-xs text-muted-foreground">
          工程清单已建立 · {sequences.length} 份剪辑文档 ·{" "}
          {docs.data.documents.filter((d) => d.kind === "resource").length}{" "}
          份固定素材
        </p>
      )}
      {docs.isPending && <p role="status">正在读取工程文档…</p>}
      {error && (
        <p role="alert" className="text-danger">
          {error.message}
          <Button
            variant="ghost"
            onClick={() => {
              void docs.refetch();
              if (doc) void detail.refetch();
            }}
          >
            重新读取
          </Button>
        </p>
      )}
      {!docs.isPending && !sequences.length && (
        <div className="rounded-xl border p-6">
          <p className="text-sm text-muted-foreground">
            新建工作剪辑会建立文档。已有剪辑可在这里转为文档修订，不会替换已采用内容。
          </p>
          <Button
            className="mt-4"
            variant="outline"
            disabled={initialize.isPending}
            onClick={() => initialize.mutate()}
          >
            {initialize.isPending ? "整理工程内容…" : "接入已有剪辑"}
          </Button>
        </div>
      )}
      {doc && (
        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <div className="space-y-2">
            {sequences.map((d) => (
              <button
                key={d.id}
                onClick={() => {
                  setChosen(d.id);
                  setRevision("");
                  setTime(0);
                }}
                className={`w-full rounded-xl border p-4 text-left ${doc.id === d.id ? "border-primary bg-primary/5" : "bg-surface"}`}
              >
                <span className="block font-medium">{d.title}</span>
                <span className="mt-2 block text-xs text-muted-foreground">
                  剪辑文档 · {d.revisions.length} 个内容修订
                </span>
              </button>
            ))}
          </div>
          <div className="min-w-0 space-y-4 rounded-2xl border bg-surface p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="font-medium">{doc.title}</h3>
              <SelectField
                label="文档修订"
                value={String(rev)}
                onChange={(v) => {
                  setRevision(v);
                  setTime(0);
                }}
                options={doc.revisions.map((r) => ({
                  value: String(r.revision),
                  label: `修订 ${r.revision}${r.revision === doc.working_revision ? " · 工作剪辑" : ""} · ${new Date(r.created_at).toLocaleString()}`,
                }))}
              />
            </div>
            {detail.data?.payload.editor && (
              <>
                <div className="max-w-3xl">
                  <TimelinePlayback
                    key={`${doc.id}/${rev}`}
                    projectId={projectId}
                    shots={[]}
                    content={editableContent({
                      ...detail.data.payload.editor,
                    })}
                    aspect={detail.data.payload.aspect_ratio || "16:9"}
                    time={time}
                    onTime={setTime}
                    selected=""
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  固定修订的快速预览，最终效果以候选渲染为准。
                </p>
              </>
            )}
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              {Object.entries(detail.data?.inputs || {}).map(
                ([usage, input], i) => (
                  <span key={usage} className="rounded-lg bg-muted px-3 py-2">
                    素材 {i + 1} · 修订 {input.target.revision}
                  </span>
                ),
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-3">
              <Link
                className="rounded-lg border px-4 py-2 text-sm"
                to={`${base}/cuts?timeline=${doc.source_timeline_id}&work=${workId}`}
              >
                继续编辑工作剪辑
              </Link>
              <Button
                disabled={
                  reopen.isPending || !detail.data?.payload.editor?.items.length
                }
                onClick={() => reopen.mutate()}
              >
                {reopen.isPending ? "正在建立候选…" : "从此修订创建候选"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              打开历史修订不会覆盖工作剪辑；创建候选后，先预览再决定是否采用。
            </p>
          </div>
        </div>
      )}
      {!!docs.data?.documents.some((d) => d.kind === "resource") && (
        <div className="space-y-3">
          <h3 className="font-medium">固定素材依赖</h3>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {docs.data.documents
              .filter((d) => d.kind === "resource")
              .map((d) => (
                <div key={d.id} className="rounded-xl border p-3 text-sm">
                  <p className="truncate">{d.title}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {d.revisions.length} 个固定内容修订
                  </p>
                </div>
              ))}
          </div>
        </div>
      )}
      <NativeDocuments
        projectId={projectId}
        workId={workId}
        documents={docs.data?.documents || []}
      />
      <NewWorkDocument projectId={projectId} workId={workId} />
      <WorkPackage projectId={projectId} workId={workId} />
    </section>
  );
}
