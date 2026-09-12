import { MediaVideo } from "@/components/MediaVideo";
import { MediaImage } from "@/components/MediaImage";
import { useQuery } from "@tanstack/react-query";
import { ImageOff } from "lucide-react";
import { api, blobUrl, type Generation, type Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Show the selected output only. An unselected result is not an approved frame. */
export function ShotMedia({ projectId, shot }: { projectId: string; shot: Shot }) {
  const { lang } = useI18n();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["gens", "shot", shot.id],
    queryFn: () =>
      api.get<Generation[]>(`/projects/${projectId}/generations?target_type=shot&target_id=${shot.id}`),
    enabled: !!shot.selected_generation_id,
  });
  const result = data?.find((g) => g.id === shot.selected_generation_id);
  const hash = result?.output_blob_hash;
  if (hash && result?.output_type === "image") {
    return <MediaImage src={blobUrl(projectId, hash)} alt={shot.title ?? shot.code} />;
  }
  if (hash && result?.output_type === "video") {
    return <MediaVideo src={blobUrl(projectId, hash)} label={shot.title ?? shot.code} />;
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
    <div className="flex h-full min-h-24 w-full flex-col items-center justify-center gap-2 bg-elevated text-muted-foreground">
      <ImageOff className="h-5 w-5" strokeWidth={1.5} />
      <span className="text-xs">{message}</span>
    </div>
  );
}
