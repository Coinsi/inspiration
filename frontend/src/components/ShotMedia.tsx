import { MediaVideo } from "@/components/MediaVideo";
import { MediaImage } from "@/components/MediaImage";
import { useQuery } from "@tanstack/react-query";
import { ImageOff } from "lucide-react";
import { api, blobUrl, type Generation, type Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Show the selected output only. An unselected result is not an approved frame. */
export function ShotMedia({
  projectId,
  shot,
  thumbnail = false,
}: {
  projectId: string;
  shot: Shot;
  thumbnail?: boolean;
}) {
  const { lang } = useI18n();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["gens", "shot", shot.id],
    queryFn: () =>
      api.get<Generation[]>(
        `/projects/${projectId}/generations?target_type=shot&target_id=${shot.id}`,
      ),
    enabled: !!shot.selected_generation_id,
  });
  const result = data?.find((g) => g.id === shot.selected_generation_id);
  const hash = result?.output_blob_hash;
  if (hash && result?.output_type === "image") {
    return (
      <MediaImage
        src={blobUrl(projectId, hash)}
        alt={shot.title ?? shot.code}
        className={
          thumbnail ? "[&_span]:sr-only [&_svg]:h-4 [&_svg]:w-4" : undefined
        }
      />
    );
  }
  if (hash && result?.output_type === "video") {
    if (thumbnail)
      return (
        <video
          src={blobUrl(projectId, hash)}
          preload="metadata"
          muted
          playsInline
          aria-label={shot.title ?? shot.code}
          className="h-full w-full object-cover pointer-events-none"
        />
      );
    return (
      <MediaVideo
        src={blobUrl(projectId, hash)}
        label={shot.title ?? shot.code}
      />
    );
  }
  const message = isLoading
    ? lang === "zh"
      ? "加载画面…"
      : "Loading…"
    : isError || !!shot.selected_generation_id
      ? lang === "zh"
        ? "画面暂不可用"
        : "Preview unavailable"
      : lang === "zh"
        ? "尚未选定画面"
        : "No selected output";
  return (
    <div
      title={message}
      className={`flex h-full ${thumbnail ? "" : "min-h-24"} w-full flex-col items-center justify-center gap-2 bg-elevated text-muted-foreground`}
    >
      <ImageOff className="h-5 w-5" strokeWidth={1.5} />
      <span className={thumbnail ? "sr-only" : "text-xs"}>{message}</span>
    </div>
  );
}
