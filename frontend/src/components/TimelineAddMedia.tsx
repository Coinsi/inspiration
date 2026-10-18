import { useState } from "react";
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
  const [uploading, setUploading] = useState(false);
  const selected = useLibraryVersion(projectId, version);
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
      width={680}
      onClose={() => {
        if (!add.isPending) onClose();
      }}
    >
      <div className="space-y-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {zh
              ? "图片、视频加入画面，音频加入声音轨。"
              : "Images and videos become clips; audio goes on the sound track."}
          </p>
          <Button
            variant="outline"
            disabled={add.isPending}
            onClick={() => setUploading(true)}
          >
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
          mediaType={mediaType}
          disabled={add.isPending}
        />
        <p className="text-xs text-muted-foreground">
          {zh
            ? "上传完成后，等待素材处理完成再选择。添加后可继续裁剪，原文件不会改变。"
            : "Wait for uploaded media to finish processing, then select it. Editing keeps the original intact."}
        </p>
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
