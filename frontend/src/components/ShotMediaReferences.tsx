import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, blobUrl } from "@/lib/api";
import { mediaTime, type MediaUsage } from "@/lib/library";
import { MediaSegmentPlayer } from "@/components/MediaSegmentPlayer";
import { useI18n } from "@/lib/i18n";
import { useConfirm } from "@/components/ui/confirm";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { JobStatus } from "@/components/JobStatus";
import type { GenJob } from "@/lib/api";
import { ShotVideoPicker } from "@/components/ShotVideoPicker";

export function ShotMediaReferences({
  projectId,
  shotId,
  allowChoose = false,
}: {
  projectId: string;
  shotId: string;
  allowChoose?: boolean;
}) {
  const zh = useI18n().lang === "zh",
    base = `/projects/${projectId}/library`,
    qc = useQueryClient(),
    confirm = useConfirm();
  const [results, setResults] = useState<Record<string, string>>({});
  const materialize = useMutation({
    mutationFn: ({
      id,
      kind,
      key,
    }: {
      id: string;
      kind: string;
      key: string;
    }) =>
      api.post<GenJob>(`${base}/usages/${id}/materialize`, {
        request_key: key,
        output_type: kind,
      }),
    onSuccess: (job, args) => {
      setResults((current) => ({ ...current, [args.id]: job.id }));
      void qc.invalidateQueries({ queryKey: ["gens"] });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  const usages = useQuery({
    queryKey: ["media-usages", projectId, shotId],
    queryFn: () => api.get<MediaUsage[]>(`${base}/usages?shot_id=${shotId}`),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`${base}/usages/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["media-usages", projectId] });
      void qc.invalidateQueries({ queryKey: ["library", projectId] });
    },
  });
  return (
    <section className="space-y-3">
      {allowChoose && <ShotVideoPicker projectId={projectId} shotId={shotId} />}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">
          {zh ? "视频片段引用" : "Referenced video segments"}
        </h3>
        <Link
          to={`/projects/${projectId}/library?shot=${shotId}`}
          className="studio-link text-xs"
        >
          {zh ? "从视频素材库选择" : "Choose from video library"} →
        </Link>
      </div>
      {usages.isPending ? (
        <p className="text-xs text-muted-foreground">
          {zh ? "加载引用…" : "Loading…"}
        </p>
      ) : usages.isError ? (
        <button
          className="studio-link text-xs"
          onClick={() => void usages.refetch()}
        >
          {zh ? "引用加载失败，重试" : "Failed to load. Retry"}
        </button>
      ) : !usages.data.length ? (
        <p className="text-xs text-muted-foreground">
          {zh
            ? "尚未引用视频片段。引用保留原版本和时间范围，不会自动发送给生成模型。"
            : "No video segments yet. References preserve the source version and range; they are not automatically sent to models."}
        </p>
      ) : (
        usages.data.map((u) => (
          <div className="space-y-2 rounded-lg border p-3" key={u.id}>
            <p className="text-sm">
              {u.name} · v{u.ordinal}
            </p>
            <p className="text-xs text-muted-foreground">
              {mediaTime(u.start_ms)} — {mediaTime(u.end_ms)} ·{" "}
              {u.purpose === "visual_reference"
                ? zh
                  ? "画面参考"
                  : "Visual reference"
                : zh
                  ? "剪辑备选"
                  : "Editing source"}
            </p>
            <MediaSegmentPlayer
              key={`${u.id}.${u.proxy_hash}`}
              src={blobUrl(projectId, u.proxy_hash)}
              start={u.start_ms}
              end={u.end_ms}
              label={u.name}
            />
            {u.note && <p className="break-words text-xs">{u.note}</p>}
            <div className="flex flex-wrap gap-2">
              {(
                [
                  ["image", zh ? "提取中间参考帧" : "Extract middle frame"],
                  ["video", zh ? "制作剪辑片段" : "Create video clip"],
                  ["audio", zh ? "提取音轨" : "Extract audio"],
                ] as const
              ).map(([kind, label]) => (
                <Button
                  key={kind}
                  variant="outline"
                  size="sm"
                  disabled={
                    materialize.isPending ||
                    (kind !== "image" && u.end_ms - u.start_ms > 60000)
                  }
                  onClick={() =>
                    materialize.mutate({
                      id: u.id,
                      kind,
                      key: crypto.randomUUID(),
                    })
                  }
                >
                  {label}
                </Button>
              ))}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              {zh
                ? "从原片制作并保存到本镜头的生成结果，可继续用作参考图或剪辑。视频和音轨每段限 60 秒，画面最高 1920 × 1080，保留来源。"
                : "Creates independent shot media from the original. Use frames as generation references or clips in editing. Clips/audio up to 60 seconds; picture fits within 1920 × 1080. Source provenance is retained."}
            </p>
            {results[u.id] && (
              <JobStatus projectId={projectId} jobId={results[u.id]} />
            )}
            <button
              className="studio-link text-xs"
              disabled={remove.isPending || materialize.isPending}
              onClick={async () => {
                if (
                  await confirm({
                    message: zh
                      ? "解除此镜头的片段引用？原视频会保留。"
                      : "Remove this reference? The source video is retained.",
                  })
                )
                  remove.mutate(u.id);
              }}
            >
              {zh ? "解除此引用" : "Remove reference"}
            </button>
          </div>
        ))
      )}
      {remove.error && (
        <p role="alert" className="text-xs text-danger">
          {remove.error.message}
        </p>
      )}
      {materialize.error && (
        <p role="alert" className="text-xs text-danger">
          {materialize.error.message}
        </p>
      )}
    </section>
  );
}
