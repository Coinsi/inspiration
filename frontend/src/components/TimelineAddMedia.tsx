import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LibraryUpload } from "./LibraryUpload";
import {
  LibraryVersionPicker,
  useLibraryVersion,
} from "./LibraryVersionPicker";
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
  const [version, setVersion] = useState("");
  const [filter, setFilter] = useState<MediaType | "all">(mediaType);
  const [uploading, setUploading] = useState(false);
  const [justUploaded, setJustUploaded] = useState("");
  const selected = useLibraryVersion(projectId, version, true);
  useEffect(() => {
    if (justUploaded !== version || !version || selected.data?.status !== "ready") return;
    setJustUploaded("");
    void qc.invalidateQueries({ queryKey: ["library-version-catalog", projectId] });
  }, [justUploaded, version, selected.data?.status, qc, projectId]);
  const isAudio = selected.data?.media_type === "audio";
  const add = useMutation({
    mutationFn: () =>
      api.post<EditingSource>(
        `/projects/${projectId}/timelines/${timelineId}/library-sources/${version}`,
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
        onImported={file => {
          setFilter(mediaType === "audio" ? "audio" : file.media_type);
          setVersion(file.id);
          setJustUploaded(file.id);
          setUploading(false);
          void qc.invalidateQueries({ queryKey: ["library-version-catalog", projectId] });
        }}
        onChange={() => {
          void qc.invalidateQueries({
            queryKey: ["library-version-catalog", projectId],
          });
        }}
        onClose={() => {
          setUploading(false);
          void qc.invalidateQueries({
            queryKey: ["library-version-catalog", projectId],
          });
        }}
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
                  onClick={() => {
                    setFilter(type);
                    setVersion("");
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
        <LibraryVersionPicker
          projectId={projectId}
          value={version}
          onChange={(id) => {
            setVersion(id);
            add.reset();
          }}
          label={zh ? "素材库" : "Media library"}
          mediaType={filter}
          gallery
          disabled={add.isPending}
        />
        {selected.data &&
          ["queued", "processing"].includes(selected.data.status) && (
            <p role="status" className="text-sm text-muted-foreground">
              {zh
                ? "正在处理刚上传的素材，就绪后可直接加入剪辑。"
                : "Preparing your upload. Add it as soon as it is ready."}
            </p>
          )}
        {selected.data?.status === "failed" && (
          <p role="alert" className="text-sm text-danger">
            {selected.data.error ||
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
        {isAudio && !selected.data?.duration_ms && (
          <p role="status">音频缺少时长信息，请等待素材处理完成或重新上传。</p>
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
        <div className="flex justify-end gap-2">
          <Button variant="ghost" disabled={add.isPending} onClick={onClose}>
            {zh ? "取消" : "Cancel"}
          </Button>
          <Button
            disabled={
              !selected.data ||
              selected.data.status !== "ready" ||
              add.isPending ||
              (isAudio &&
                (remainingMs <= 0 ||
                  audioFull ||
                  !selected.data.duration_ms)) ||
              (!isAudio && pictureFull)
            }
            onClick={() => add.mutate()}
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
