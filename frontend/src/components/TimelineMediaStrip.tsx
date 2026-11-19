import { memo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, blobUrl, type Generation } from "@/lib/api";

type Preview = {
  status: "pending" | "ready" | "unavailable";
  duration_ms?: number;
  peaks?: number[];
  frames?: number;
  strip?: string;
};
type Props = {
  project: string;
  id: string;
  audio?: boolean;
  from: number;
  length: number;
};

export const TimelineMediaStrip = memo(function TimelineMediaStrip({
  project,
  id,
  audio = false,
  from,
  length,
}: Props) {
  const base = `/projects/${project}/generations/${id}`;
  const media = useQuery({
    queryKey: ["generation", project, id],
    queryFn: () => api.get<Generation>(base),
    enabled: !!id,
    staleTime: 60000,
  });
  const preview = useQuery({
    queryKey: ["timeline-media-preview", project, id],
    queryFn: () => api.get<Preview>(base + "/timeline-preview"),
    enabled:
      !!id &&
      (media.data?.output_type === "audio" ||
        media.data?.output_type === "video"),
    staleTime: Infinity,
    refetchInterval: (q) =>
      q.state.data?.status === "pending"
        ? Math.min(15000, 2000 * q.state.dataUpdateCount)
        : false,
    retry: 1,
  }).data;
  const duration = preview?.duration_ms || 1;
  if (audio) {
    if (!preview?.peaks?.length) return null;
    const samples = preview.peaks;
    const start = Math.min(
      samples.length - 1,
      Math.floor((from / duration) * samples.length),
    );
    const end = Math.min(
      samples.length,
      Math.max(
        start + 1,
        Math.ceil(((from + length) / duration) * samples.length),
      ),
    );
    const points = samples.slice(start, end);
    const path = points
      .map((v, i) => {
        const half = Math.sqrt(v / 255) * 9;
        return `M${i + 0.5} ${10 - half}v${2 * half}`;
      })
      .join(" ");
    return (
      <svg
        className="cut-waveform"
        aria-hidden="true"
        viewBox={`0 0 ${points.length} 20`}
        preserveAspectRatio="none"
      >
        <path d={path} stroke="currentColor" strokeWidth=".8" />
      </svg>
    );
  }
  if (preview?.strip && preview.frames) {
    const count = preview.frames;
    const cells = Array.from({ length: count }, (_, i) => {
      const sample = Math.min(
        count - 1,
        Math.floor(((from + ((i + 0.5) * length) / count) / duration) * count),
      );
      return (
        <i
          key={i}
          style={{
            backgroundImage: `url("${preview.strip}")`,
            backgroundSize: `${count * 100}% 100%`,
            backgroundPosition: `${count > 1 ? (sample / (count - 1)) * 100 : 0}% center`,
          }}
        />
      );
    });
    return (
      <span className="cut-filmstrip" aria-hidden="true">
        {cells}
      </span>
    );
  }
  const g = media.data;
  if (!g?.output_blob_hash) return null;
  const poster = g.thumbnail_hash
    ? blobUrl(project, g.thumbnail_hash)
    : blobUrl(project, g.output_blob_hash, "thumbnail");
  return (
    <span
      className="cut-filmstrip cut-poster"
      aria-hidden="true"
      style={{ backgroundImage: `url("${poster}")` }}
    />
  );
});
