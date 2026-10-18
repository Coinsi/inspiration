import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, blobUrl } from "@/lib/api";
import { MediaVideo } from "./MediaVideo";
import { MediaAudio } from "./MediaAudio";
import { Button } from "./ui/button";
import { DocumentChangeReview } from "./WorkPackage";

export type SavedArtifact = {
  document_ref: { id: string; revision: number };
  generation_id: string;
  blob_hash: string;
  output_type: string;
  executor: string;
};

const documentNames: Record<string, string> = {
  "image.document": "图像",
  "audio.document": "音频",
  "composition.document": "动态合成",
  "sequence.timeline": "剪辑",
  "sync.document": "跨文档同步规则",
};

function FixedOutput({
  path,
  projectId,
  identity,
  revision,
  savedArtifact,
}: {
  path: string;
  projectId: string;
  identity: string;
  revision: number;
  savedArtifact?: SavedArtifact;
}) {
  const detail = useQuery({
    queryKey: ["fixed-work-output", path, identity, revision],
    queryFn: () =>
      api.get<{
        title: string;
        kind: string;
        adapter_available?: boolean;
        artifacts?: {
          id: string;
          blob_hash: string;
          output_type: string;
          executor: string;
        }[];
      }>(`${path}/documents/${identity}/revisions/${revision}`),
  });
  const artifact =
    savedArtifact?.document_ref.id === identity &&
    savedArtifact.document_ref.revision === revision
      ? savedArtifact
      : undefined;
  return (
    <div className="rounded-xl border bg-surface p-4">
      {artifact && (
        <div className="mb-3 overflow-hidden rounded-lg bg-black/10">
          {artifact.output_type === "image" ? (
            <img
              src={blobUrl(projectId, artifact.blob_hash)}
              alt={`${detail.data?.title} · 已采用修订预览`}
              className="max-h-64 w-full object-contain"
            />
          ) : artifact.output_type === "video" ? (
            <MediaVideo
              src={blobUrl(projectId, artifact.blob_hash)}
              label={`${detail.data?.title} · 已采用修订预览`}
            />
          ) : (
            <MediaAudio src={blobUrl(projectId, artifact.blob_hash)} />
          )}
        </div>
      )}
      <p className="font-medium">{detail.data?.title || "读取输出…"}</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {detail.data
          ? documentNames[detail.data.kind] || "扩展文档"
          : "工程文档"}{" "}
        · 固定修订 {revision}
      </p>
      {detail.data?.adapter_available === false && (
        <p className="mt-2 text-xs text-muted-foreground">
          扩展内容已保留，当前不可执行
        </p>
      )}
      {detail.error && (
        <p role="alert" className="text-sm text-danger">
          {detail.error.message}
        </p>
      )}
    </div>
  );
}

export function WorkSavedOutputs({
  projectId,
  workId,
  roots,
  artifacts,
}: {
  projectId: string;
  workId: string;
  roots: Record<string, { id: string; revision: number }>;
  artifacts?: Record<string, SavedArtifact>;
}) {
  const path = `/projects/${projectId}/works/${workId}/engineering`;
  return (
    <section className="space-y-3">
      <h2 className="font-medium">固定工程输出</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {Object.entries(roots).map(([key, ref]) => (
          <FixedOutput
            key={`${key}/${ref.revision}`}
            path={path}
            projectId={projectId}
            identity={ref.id}
            revision={ref.revision}
            savedArtifact={artifacts?.[key]}
          />
        ))}
      </div>
    </section>
  );
}

export function RestoreWorkVersion({
  projectId,
  workId,
  revision,
}: {
  projectId: string;
  workId: string;
  revision: number;
}) {
  const [candidate, setCandidate] = useState("");
  const path = `/projects/${projectId}/works/${workId}/engineering`;
  const restore = useMutation({
    mutationFn: async () => {
      const head = await api.get<{ revision: number }>(path);
      return api.post<{ id: string }>(`${path}/changes`, {
        request_key: crypto.randomUUID(),
        base_revision: head.revision,
        commands: [{ operation: "restore", revision }],
      });
    },
    onSuccess: (result) => setCandidate(result.id),
  });
  return (
    <section className="space-y-3 rounded-xl border p-4">
      <p className="text-sm text-muted-foreground">
        恢复作品采用的固定版本，保留后续历史。工作剪辑中的未采用修改不会被覆盖；如需继续编辑旧剪辑，请使用对应的审阅与恢复入口。
      </p>
      <Button
        variant="outline"
        disabled={restore.isPending || !!candidate}
        onClick={() => restore.mutate()}
      >
        {restore.isPending ? "正在准备…" : "从此版本创建恢复候选"}
      </Button>
      {restore.error && (
        <p role="alert" className="text-danger">
          {restore.error.message}
        </p>
      )}
      {candidate && (
        <DocumentChangeReview
          projectId={projectId}
          workId={workId}
          changeId={candidate}
        />
      )}
    </section>
  );
}
