import { useQueries, useQueryClient } from "@tanstack/react-query";
import { api, blobUrl, type Generation, type Shot } from "./api";
import type { TimelineContent } from "./timeline-edit";
import type { SourceBounds } from "./timeline-interaction";
export function useTimelineBounds(
  projectId: string,
  content: TimelineContent,
  shots: Shot[],
) {
  const qc = useQueryClient();
  const ids = [
    ...new Set(
      [
        ...content.items.map(
          (c) =>
            c.generation_id ||
            shots.find((s) => s.id === c.shot_id)?.selected_generation_id,
        ),
        ...content.audio.map((c) => c.generation_id),
        ...content.visuals.map((c) => c.generation_id),
      ].filter(Boolean),
    ),
  ] as string[];
  const queries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["timeline-source-bounds", "server-duration-v2", projectId, id],
      staleTime: Infinity,
      queryFn: async (): Promise<SourceBounds> => {
        const g = await api.get<Generation>(
          `/projects/${projectId}/generations/${id}`,
        );
        if (g.output_type === "image") return { image: true };
        // Use the same duration that ingestion, adding and server validation use.
        const versionId = g.input_refs?.library_origin?.version_id;
        if (versionId && g.input_refs?.operation === "timeline_library_source") {
          const version = await qc.fetchQuery({
            queryKey: ["library-version", projectId, versionId],
            queryFn: () =>
              api.get<{ duration_ms: number | null }>(
                `/projects/${projectId}/library/versions/${versionId}`,
              ),
          });
          if (version.duration_ms && version.duration_ms > 0)
            return { image: false, duration_ms: version.duration_ms };
        }
        if (g.output_type === "video") {
          const info = await qc.fetchQuery({
            queryKey: ["video-info", projectId, id],
            queryFn: () =>
              api.get<{ duration_ms: number }>(
                `/projects/${projectId}/generations/${id}/video-info`,
              ),
          });
          return { image: false, duration_ms: info.duration_ms };
        }
        if (g.output_type === "audio") {
          // Audio upload/export validation uses Blob.duration_ms, exposed by this catalog.
          for (let offset = 0; ; offset += 50) {
            const page = await qc.fetchQuery({
              queryKey: ["audio-sources", projectId, offset / 50],
              queryFn: () =>
                api.get<{
                  items: { id: string; duration_ms: number }[];
                  has_more: boolean;
                }>(`/projects/${projectId}/audio-sources?offset=${offset}`),
              staleTime: 30000,
            });
            const audio = page.items.find((a) => a.id === id);
            if (audio?.duration_ms)
              return { image: false, duration_ms: audio.duration_ms };
            if (!page.has_more) break;
          }
        }
        if (!g.output_blob_hash) throw new Error("素材尚未就绪");
        const duration = await new Promise<number>((resolve, reject) => {
          const media = document.createElement(
            g.output_type === "audio" ? "audio" : "video",
          );
          const timer = window.setTimeout(
            () => finish(new Error("素材时长读取超时")),
            15000,
          );
          const finish = (error?: Error) => {
            clearTimeout(timer);
            const value = Math.round(media.duration * 1000);
            media.onloadedmetadata = null;
            media.onerror = null;
            media.removeAttribute("src");
            media.load();
            if (error || !Number.isFinite(value) || value <= 0)
              reject(error || new Error("无法读取素材时长"));
            else resolve(value);
          };
          media.preload = "metadata";
          media.onloadedmetadata = () => finish();
          media.onerror = () => finish(new Error("无法读取素材"));
          media.src = blobUrl(projectId, g.output_blob_hash!);
        });
        return { image: false, duration_ms: duration };
      },
    })),
  });
  return Object.fromEntries(
    ids.map((id, i) => [id, queries[i].data]),
  ) as Record<string, SourceBounds | undefined>;
}
