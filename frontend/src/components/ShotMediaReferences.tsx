import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, blobUrl } from "@/lib/api";
import { mediaTime, type MediaUsage } from "@/lib/library";
import { LibraryMediaPreview } from "@/components/LibraryMediaPreview";
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
          {zh ? "素材引用" : "Referenced media"}
        </h3>
        <Link
          to={`/projects/${projectId}/library?shot=${shotId}`}
          className="studio-link text-xs"
        >
          {zh ? "从素材库选择" : "Choose from media library"} →
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
            ? "尚未引用素材。图片保留整张画面，音视频保留所选时间范围，均关联原版本。"
            : "No references yet. Images retain the whole picture; audio and video retain the selected range. All references link to their source version."}
        </p>
      ) : (
        usages.data.map((u) => (
          <div className="space-y-2 rounded-lg border p-3" key={u.id}>
            <p className="text-sm">
              {u.name} · v{u.ordinal}
            </p>
            <p className="text-xs text-muted-foreground">
              {u.media_type === "image"
                ? zh
                  ? "完整图片"
                  : "Whole image"
                : `${mediaTime(u.start_ms ?? 0)} — ${mediaTime(u.end_ms ?? 0)}`}{" "}
              ·{" "}
              {u.purpose === "visual_reference"
                ? zh
                  ? "创作参考"
                  : "Creative reference"
                : zh
                  ? "剪辑备选"
                  : "Editing source"}
            </p>
            <LibraryMediaPreview
              mediaType={u.media_type}
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
                  [
                    "image",
                    u.media_type === "image"
                      ? zh
                        ? "保存为镜头参考图"
                        : "Save as shot image"
                      : zh
                        ? "提取中间参考帧"
                        : "Extract middle frame",
                  ],
                  ["video", zh ? "制作剪辑片段" : "Create video clip"],
                  [
                    "audio",
                    u.media_type === "audio"
                      ? zh
                        ? "提取音频片段"
                        : "Extract audio clip"
                      : zh
                        ? "提取音轨"
                        : "Extract audio track",
                  ],
                ] as const
              )
                .filter(
                  ([kind]) => u.media_type === "video" || kind === u.media_type,
                )
                .map(([kind, label]) => (
                  <Button
                    key={kind}
                    variant="outline"
                    size="sm"
                    disabled={
                      materialize.isPending ||
                      (kind !== "image" &&
                        (u.end_ms ?? 0) - (u.start_ms ?? 0) > 60000)
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
              {u.media_type === "image"
                ? zh
                  ? "保存完整参考图到本镜头，保留原素材版本与来源。"
                  : "Save the whole image to this shot with source provenance."
                : u.media_type === "audio"
                  ? zh
                    ? "从原音频提取所选片段并保存到本镜头，可用于声音参考或剪辑。每段限 60 秒，保留原素材版本与来源。"
                    : "Save the selected audio range to this shot for sound references or editing. Clips are limited to 60 seconds and retain source provenance."
                  : zh
                    ? "从原视频提取参考帧、剪辑片段或音轨，并保存到本镜头。视频和音轨每段限 60 秒，画面最高 1920 × 1080，保留来源。"
                    : "Save frames, video clips or audio tracks from the source video to this shot. Clips and audio are limited to 60 seconds; picture fits within 1920 × 1080. Source provenance is retained."}
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
                      ? "解除此镜头的素材引用？原素材会保留。"
                      : "Remove this reference? The source media is retained.",
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
