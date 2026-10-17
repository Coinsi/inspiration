import { useEffect, useRef, useState } from "react";
import {
  UploadCloud,
  Film,
  Image as ImageIcon,
  AudioLines,
  FileCheck2,
  Check,
  ArrowRight,
} from "lucide-react";
import "./library-upload.css";
import { api, apiUpload } from "@/lib/api";
import {
  digest,
  fileMediaType,
  mediaAccept,
  mediaTypeName,
  mediaStatus,
  type MediaType,
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
  mediaType,
  onClose,
  onChange,
}: {
  projectId: string;
  mediaId?: string;
  resume?: MediaVersion;
  folderId?: string | null;
  mediaType?: MediaType;
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
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const ended = !!version && version.status !== "uploading";
  const finished =
    !!version && ["queued", "processing", "ready"].includes(version.status);
  const fileLocked = busy || ended;
  const selectedKind = file ? fileMediaType(file.name) : version?.media_type;
  const FileIcon =
    selectedKind === "image"
      ? ImageIcon
      : selectedKind === "audio"
        ? AudioLines
        : Film;
  const chooseFile = (files: FileList | null) => {
    setDragging(false);
    if (fileLocked || !files?.length) return;
    setError("");
    if (files.length !== 1) {
      setError(
        zh
          ? "请一次选择一个文件，完成后可以继续导入。"
          : "Choose one file at a time.",
      );
      return;
    }
    const next = files[0];
    const kind = fileMediaType(next.name);
    if (!kind || next.size === 0) {
      setError(
        zh
          ? "请选择有效的视频、静态图片或音频文件。"
          : "Choose a valid video, still image or audio file.",
      );
      return;
    }
    if (kind === "image" && next.size > 64 * 1024 * 1024) {
      setError(
        zh ? "图片不能超过 64 MB，请重新选择。" : "Images must be under 64 MB.",
      );
      return;
    }
    if (expectedType && kind !== expectedType) {
      setError(
        zh
          ? `请选择${mediaTypeName(expectedType, true)}文件，与原素材保持同一类型。`
          : "Choose the same media type as the original.",
      );
      return;
    }
    setFile(next);
    setMessage("");
  };
  const expectedType = resume?.media_type || mediaType;
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
    let hasSession = !!version;
    try {
      const kind = fileMediaType(file.name);
      if (!kind)
        throw new Error(
          zh
            ? "暂不支持此文件格式，请选择视频、图片或音频。"
            : "Choose a supported video, image or audio file.",
        );
      if (kind === "image" && file.size > 64 * 1024 * 1024)
        throw new Error(
          zh
            ? "请选择64 MB以内的静态图片。"
            : "Choose a still image under 64 MB.",
        );
      if (expectedType && kind !== expectedType)
        throw new Error(
          zh
            ? `新版本必须仍为${mediaTypeName(expectedType, true)}。`
            : `The new version must remain ${mediaTypeName(expectedType, false).toLowerCase()}.`,
        );
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
        setMessage(mediaStatus(current.status, zh));
        return;
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
        hasSession = true;
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
        hasSession
          ? zh
            ? "上传已暂停，已完成的分块会保留。"
            : "Upload paused. Completed chunks are retained."
          : "",
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
            ? "继续上传素材"
            : mediaId
              ? "上传新版本"
              : "导入素材"
          : "Import media"
      }
      onClose={() => {
        pause.current = true;
        onClose();
      }}
      width={640}
    >
      <div className="library-upload">
        <p className="upload-intro">
          {zh
            ? resume
              ? "重新选择原文件，从上次的位置继续。"
              : mediaId
                ? "为这份素材添加新版本，已有引用保持不变。"
                : "把你的画面与声音，带入创作。"
            : "Bring your footage, images and sound into your project."}
        </p>
        <input
          ref={input}
          aria-label={zh ? "选择素材文件" : "Choose media file"}
          type="file"
          accept={
            expectedType
              ? mediaAccept[expectedType]
              : Object.values(mediaAccept).join(",")
          }
          disabled={fileLocked}
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            chooseFile(e.target.files);
            e.target.value = "";
          }}
        />
        <div
          className={`upload-dropzone ${dragging ? "is-dragging" : ""} ${file || version ? "has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            if (!fileLocked) setDragging(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null))
              setDragging(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            chooseFile(e.dataTransfer.files);
          }}
        >
          {file || version ? (
            <div className="upload-selected">
              <span
                className={`upload-file-icon ${finished ? "is-complete" : ""}`}
              >
                {finished ? <FileCheck2 size={28} /> : <FileIcon size={28} />}
              </span>
              <div className="upload-file-copy">
                <strong title={file?.name || version?.filename}>
                  {file?.name || version?.filename}
                </strong>
                <span>
                  {selectedKind ? mediaTypeName(selectedKind, zh) : ""} ·{" "}
                  {mediaSize(file?.size || version?.size_bytes || 0)}
                  {version ? ` · v${version.ordinal}` : ""}
                </span>
                <small>
                  {ended
                    ? mediaStatus(version!.status, zh)
                    : file
                      ? version
                        ? zh
                          ? "已选择原文件，继续时将核对已上传内容"
                          : "Ready to verify and resume"
                        : zh
                          ? "已准备好上传"
                          : "Ready to upload"
                      : zh
                        ? "请选择原文件以继续上传"
                        : "Select the original file to resume"}
                </small>
              </div>
              {!fileLocked && (
                <button
                  type="button"
                  className="upload-reselect"
                  onClick={() => input.current?.click()}
                >
                  {zh ? (file ? "重新选择" : "选择原文件") : "Choose file"}
                </button>
              )}
            </div>
          ) : (
            <button
              type="button"
              autoFocus
              data-autofocus
              className="upload-drop-trigger"
              disabled={fileLocked}
              onClick={() => input.current?.click()}
            >
              <span className="upload-drop-icon">
                <UploadCloud size={30} strokeWidth={1.5} />
              </span>
              <strong>
                {zh ? "拖入文件，或点击选择" : "Drop a file here, or browse"}
              </strong>
              <span>
                {expectedType
                  ? mediaTypeName(expectedType, zh)
                  : zh
                    ? "视频、图片、音频"
                    : "Video, images and audio"}
              </span>
              <span className="upload-browse">
                {zh ? "选择文件" : "Choose file"}
                <ArrowRight size={14} />
              </span>
            </button>
          )}
        </div>
        {version && (
          <div className="upload-progress">
            <div>
              <strong>
                {ended
                  ? mediaStatus(version!.status, zh)
                  : busy
                    ? zh
                      ? "正在上传"
                      : "Uploading"
                    : zh
                      ? "等待继续"
                      : "Ready to resume"}
              </strong>
              <span>
                {Math.min(
                  100,
                  Math.floor(
                    (version.uploaded_bytes / Math.max(1, version.size_bytes)) *
                      100,
                  ),
                )}
                %
              </span>
            </div>
            <progress
              aria-label={zh ? "素材上传进度" : "Media upload progress"}
              value={version.uploaded_bytes}
              max={Math.max(1, version.size_bytes)}
            />
            <span>
              {mediaSize(version.uploaded_bytes)} /{" "}
              {mediaSize(version.size_bytes)}
            </span>
          </div>
        )}
        {message && (
          <p role="status" className="upload-message">
            {message}
          </p>
        )}
        {(error || (ended && version?.error)) && (
          <p role="alert" className="upload-error">
            {error || version?.error}
          </p>
        )}
        <div className="upload-reassurance">
          <span>
            <Check size={13} />
            {zh ? "保留原文件" : "Originals retained"}
          </span>
          <span>
            <Check size={13} />
            {zh ? "支持断点续传" : "Resumable uploads"}
          </span>
        </div>
        <details className="upload-requirements">
          <summary>
            {zh ? "支持格式与大小限制" : "Formats and file limits"}
          </summary>
          <p>
            {zh
              ? "图片支持 JPEG、PNG、WebP，最大 64 MB。视频与音频默认最大 10 GB、6 小时，实际以服务端配置为准。一次导入一个文件，上传完成后自动制作预览。"
              : "JPEG, PNG and WebP images up to 64 MB. Video and audio default to 10 GB and 6 hours, subject to server settings. Import one file at a time."}
          </p>
          {expectedType && (
            <p>
              {zh
                ? `此素材为${mediaTypeName(expectedType, true)}，新版本需保持同一类型。`
                : "New versions must use the same media type."}
            </p>
          )}
        </details>
        <footer className="upload-footer">
          <span>
            {zh
              ? "上传后可在素材库查看处理进度"
              : "Track processing in your media library"}
          </span>
          <div>
            {ended ? (
              <Button onClick={onClose}>{zh ? "完成" : "Done"}</Button>
            ) : busy ? (
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
              <Button disabled={!file} onClick={() => void send()}>
                <UploadCloud size={15} />
                {version
                  ? zh
                    ? "继续上传"
                    : "Resume upload"
                  : zh
                    ? "开始上传"
                    : "Start upload"}
              </Button>
            )}
          </div>
        </footer>
      </div>
    </Modal>
  );
}
