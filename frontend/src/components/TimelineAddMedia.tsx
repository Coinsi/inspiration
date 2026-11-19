import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LibraryUpload } from "./LibraryUpload";
import { useLibraryVersion } from "./LibraryVersionPicker";
import { TimelineSourcePicker } from "./TimelineSourcePicker";
import { timelineSources, type TimelineSource } from "@/lib/timeline-sources";
import { Modal } from "./ui/modal";
import { Button } from "./ui/button";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { mediaTypeName, type MediaType } from "@/lib/library";
import { Upload } from "lucide-react";

export type EditingSource = {
  id: string;
  name: string;
  media_type: "image" | "video" | "audio";
  duration_ms: number | null;
};
export function TimelineAddMedia({
  projectId,
  timelineId,
  onClose,
  onAdd,
  remainingMs,
  pictureFull = false,
  mediaType = "all",
  audioFull = false,
}: {
  projectId: string;
  timelineId: string;
  onClose: () => void;
  onAdd: (source: EditingSource) => void;
  remainingMs: number;
  pictureFull?: boolean;
  mediaType?: "all" | "audio";
  audioFull?: boolean;
}) {
  const zh = useI18n().lang === "zh";
  const qc = useQueryClient();
  const [selected, setSelected] = useState<TimelineSource | null>(null);
  const [filter, setFilter] = useState<MediaType | "all">(mediaType);
  const [uploading, setUploading] = useState(false);
  const [justUploaded, setJustUploaded] = useState("");
  const uploaded = useLibraryVersion(projectId, justUploaded, true);
  useEffect(() => {
    const file = uploaded.data;
    if (
      !justUploaded ||
      file?.id !== justUploaded ||
      file.status !== "ready" ||
      !file.original_hash
    )
      return;
    setSelected({
      id: file.id,
      source_type: "media_version",
      kind: file.media_type,
      title: file.name,
      blob_hash: file.original_hash,
      thumbnail_hash: file.poster_hash,
      duration_ms: file.duration_ms,
      ordinal: file.ordinal,
    });
    setJustUploaded("");
    void qc.invalidateQueries({ queryKey: ["materials", projectId] });
  }, [justUploaded, uploaded.data, qc, projectId]);
  const refreshUploads = () => {
    for (const key of ["materials", "library-version-catalog"]) {
      void qc.invalidateQueries({ queryKey: [key, projectId] });
    }
  };
  const isAudio = selected?.kind === "audio";
  const add = useMutation({
    mutationFn: (source: TimelineSource) =>
      api.post<EditingSource>(
        `/projects/${projectId}/timelines/${timelineId}/sources/${source.source_type}/${source.id}`,
        {},
      ),
    onSuccess: (source) => {
      onAdd(source);
      onClose();
    },
  });
  if (uploading)
    return (
      <LibraryUpload
        projectId={projectId}
        mediaType={filter === "all" ? undefined : filter}
        onImported={(file) => {
          setFilter(mediaType === "audio" ? "audio" : file.media_type);
          setSelected(null);
          setJustUploaded(file.id);
          setUploading(false);
          refreshUploads();
        }}
        onChange={refreshUploads}
        onClose={() => setUploading(false)}
      />
    );
  return (
    <Modal
      open
      title={zh ? "添加素材" : "Add media"}
      width={720}
      onClose={() => {
        if (!add.isPending) onClose();
      }}
    >
      <div className="space-y-4">
        <div className="cut-library-header">
          {mediaType === "all" ? (
            <div
              className="cut-library-types"
              role="group"
              aria-label={zh ? "素材类型" : "Media type"}
            >
              {(["all", "video", "image", "audio"] as const).map((type) => (
                <button
                  type="button"
                  key={type}
                  aria-pressed={filter === type}
                  disabled={add.isPending}
                  onPointerEnter={() =>
                    void qc.prefetchInfiniteQuery(
                      timelineSources(projectId, type),
                    )
                  }
                  onFocus={() =>
                    void qc.prefetchInfiniteQuery(
                      timelineSources(projectId, type),
                    )
                  }
                  onClick={() => {
                    setFilter(type);
                    setSelected(null);
                    setJustUploaded("");
                    add.reset();
                  }}
                >
                  {type === "all"
                    ? zh
                      ? "全部"
                      : "All"
                    : mediaTypeName(type, zh)}
                </button>
              ))}
            </div>
          ) : (
            <strong>{zh ? "声音素材" : "Audio"}</strong>
          )}
          <Button
            variant="outline"
            disabled={add.isPending}
            onClick={() => setUploading(true)}
          >
            <Upload size={15} />
            {zh ? "本地上传" : "Upload file"}
          </Button>
        </div>
        <TimelineSourcePicker
          project={projectId}
          filter={filter}
          selected={selected}
          onSelect={(source) => {
            setSelected(source);
            setJustUploaded("");
            add.reset();
          }}
          disabled={add.isPending}
        />
        {justUploaded &&
          !uploaded.isError &&
          uploaded.data?.status !== "failed" && (
            <p role="status" className="text-sm text-muted-foreground">
              {zh
                ? "正在处理刚上传的素材，就绪后可直接加入剪辑。"
                : "Preparing your upload. Add it as soon as it is ready."}
            </p>
          )}
        {justUploaded && uploaded.isError && (
          <p role="alert">
            {zh ? "未能读取上传状态。" : "Could not read upload status."}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void uploaded.refetch()}
            >
              {zh ? "重试" : "Retry"}
            </Button>
          </p>
        )}
        {uploaded.data?.status === "failed" && (
          <p role="alert" className="text-sm text-danger">
            {uploaded.data.error ||
              (zh
                ? "素材处理失败，请检查文件后重新上传。"
                : "Media processing failed. Check the file and try again.")}
          </p>
        )}
        {isAudio && remainingMs <= 0 && (
          <p role="status">
            {zh
              ? "请先添加画面，再加入配乐或对白。"
              : "Add picture clips before adding sound."}
          </p>
        )}
        {isAudio && audioFull && (
          <p role="status">最多可添加 24 段声音，请先移除不需要的音轨。</p>
        )}
        {!isAudio && pictureFull && (
          <p role="status">
            {zh
              ? "画面已达到 100 段或 30 分钟上限，请先缩短剪辑；仍可添加音频。"
              : "Picture limit reached (100 clips or 30 minutes). Audio can still be added."}
          </p>
        )}
        {add.error && (
          <p role="alert" className="text-danger">
            {add.error.message}
          </p>
        )}
        <div className="flex items-center justify-end gap-2">
          {selected && (
            <span
              className="mr-auto truncate text-sm text-muted-foreground"
              title={selected.title}
            >
              {zh ? "已选 · " : "Selected · "}
              {selected.title}
            </span>
          )}
          <Button variant="ghost" disabled={add.isPending} onClick={onClose}>
            {zh ? "取消" : "Cancel"}
          </Button>
          <Button
            disabled={
              !selected ||
              add.isPending ||
              (isAudio && (remainingMs <= 0 || audioFull)) ||
              (!isAudio && pictureFull)
            }
            onClick={() => selected && add.mutate(selected)}
          >
            {add.isPending
              ? zh
                ? "添加中…"
                : "Adding…"
              : zh
                ? "加入剪辑"
                : "Add to edit"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
