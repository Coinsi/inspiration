import { useEffect, useRef, useState } from "react";
import { api, apiUpload } from "@/lib/api";
import {
  digest,
  fileFingerprint,
  mediaSize,
  type MediaVersion,
} from "@/lib/library";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function LibraryUpload({
  projectId,
  mediaId,
  resume,
  folderId,
  onClose,
  onChange,
}: {
  projectId: string;
  mediaId?: string;
  resume?: MediaVersion;
  folderId?: string | null;
  onClose: () => void;
  onChange: () => void;
}) {
  const zh = useI18n().lang === "zh",
    base = `/projects/${projectId}/library`;
  const [file, setFile] = useState<File | null>(null),
    [version, setVersion] = useState(resume);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  const pause = useRef(false),
    running = useRef(false);
  useEffect(
    () => () => {
      pause.current = true;
    },
    [],
  );
  const send = async () => {
    if (!file || running.current) return;
    pause.current = false;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      setMessage(zh ? "检查文件…" : "Checking file…");
      const fingerprint = await fileFingerprint(file);
      let current = version
        ? await api.get<MediaVersion>(`${base}/versions/${version.id}`)
        : undefined;
      if (pause.current) return;
      if (
        current &&
        (current.size_bytes !== file.size ||
          current.fingerprint !== fingerprint)
      )
        throw new Error(
          zh
            ? "文件与原上传不一致，请重新选择原文件。"
            : "This is a different file. Select the original file.",
        );
      if (current && current.status !== "uploading") {
        setVersion(current);
        onChange();
        throw new Error(
          zh
            ? "此上传已结束，请关闭窗口查看状态。"
            : "This upload has ended. Close to check its status.",
        );
      }
      if (!current) {
        current = await api.post<MediaVersion>(`${base}/uploads`, {
          filename: file.name,
          size_bytes: file.size,
          fingerprint,
          media_id: mediaId,
          folder_id: folderId,
        });
        setVersion(current);
        onChange();
      }
      // Verify every committed prefix chunk, not just filename/size, before resuming.
      const count = Math.ceil(current.uploaded_bytes / current.chunk_size);
      if ((current.chunk_hashes?.length || 0) !== count)
        throw new Error(
          zh
            ? "续传校验信息不完整，请重新上传。"
            : "Resume manifest is incomplete. Start a new upload.",
        );
      for (let i = 0; i < count; i++) {
        if (pause.current) return;
        setMessage(
          zh
            ? `校验已上传部分 ${i + 1}/${count}`
            : `Verifying uploaded chunks ${i + 1}/${count}`,
        );
        if (
          (await digest(
            file.slice(
              i * current.chunk_size,
              Math.min((i + 1) * current.chunk_size, current.uploaded_bytes),
            ),
          )) !== current.chunk_hashes![i]
        )
          throw new Error(
            zh
              ? "已上传内容与所选文件不同，已停止续传。"
              : "Uploaded content differs from this file. Resume stopped.",
          );
      }
      while (current.uploaded_bytes < file.size) {
        if (pause.current) return;
        setMessage(
          zh ? "正在上传，可暂停后继续" : "Uploading; you can pause and resume",
        );
        const form = new FormData();
        form.append(
          "file",
          file.slice(
            current.uploaded_bytes,
            current.uploaded_bytes + current.chunk_size,
          ),
          "chunk",
        );
        current = await apiUpload<MediaVersion>(
          `${base}/versions/${current.id}/chunk?offset=${current.uploaded_bytes}`,
          form,
        );
        setVersion(current);
      }
      if (pause.current) return;
      setVersion(
        await api.post<MediaVersion>(`${base}/versions/${current.id}/complete`),
      );
      setMessage(
        zh
          ? "上传完成，后台正在制作预览，可以关闭此窗口。"
          : "Uploaded. The preview is being prepared in the background.",
      );
      onChange();
    } catch (e) {
      setMessage(
        zh
          ? "上传已暂停，已完成的分块会保留。"
          : "Upload paused. Completed chunks are retained.",
      );
      setError(
        e instanceof TypeError
          ? zh
            ? "网络连接中断，请检查连接后点击继续上传。"
            : "Connection interrupted. Check your connection and resume the upload."
          : (e as Error).message,
      );
      onChange();
    } finally {
      running.current = false;
      setBusy(false);
      if (pause.current)
        setMessage(
          zh
            ? "已暂停，已完成的分块会保留。"
            : "Paused. Completed chunks are retained.",
        );
    }
  };
  return (
    <Modal
      open
      title={
        zh
          ? resume
            ? "继续上传视频"
            : mediaId
              ? "上传新版本"
              : "导入视频"
          : "Upload video"
      }
      onClose={() => {
        pause.current = true;
        onClose();
      }}
      width={600}
    >
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {zh
            ? "支持 MP4 / MOV / MKV / WebM。默认最多10 GB、6小时。原片保留，预览单独生成；离开页面会暂停上传，重新选择原文件即可续传。"
            : "MP4 / MOV / MKV / WebM. Up to 10 GB and 6 hours by default. Originals are retained separately from previews. Reselect the original file to resume after leaving."}
        </p>
        {version && (
          <p className="break-words text-sm">
            {version.filename} · v{version.ordinal}
          </p>
        )}
        <input
          aria-label={zh ? "选择视频文件" : "Choose video file"}
          type="file"
          accept=".mp4,.mov,.mkv,.webm"
          disabled={busy || (!!version && version.status !== "uploading")}
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          className="max-w-full text-sm"
        />
        {version && (
          <div>
            <progress
              aria-label={zh ? "视频上传进度" : "Video upload progress"}
              className="h-2 w-full accent-primary"
              value={version.uploaded_bytes}
              max={version.size_bytes}
            />
            <p className="mt-2 text-xs text-muted-foreground">
              {mediaSize(version.uploaded_bytes)} /{" "}
              {mediaSize(version.size_bytes)} ·{" "}
              {Math.floor((version.uploaded_bytes / version.size_bytes) * 100)}%
            </p>
          </div>
        )}
        <p role="status" className="text-xs text-muted-foreground">
          {message}
        </p>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          {busy ? (
            <Button
              variant="outline"
              onClick={() => {
                pause.current = true;
                setMessage(
                  zh ? "当前分块完成后暂停…" : "Pausing after this chunk…",
                );
              }}
            >
              {zh ? "暂停上传" : "Pause"}
            </Button>
          ) : (
            <Button
              disabled={!file || (!!version && version.status !== "uploading")}
              onClick={() => void send()}
            >
              {zh ? "开始 / 继续上传" : "Start / resume upload"}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
