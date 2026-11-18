import { useRef, useState } from "react";
import { Film, ImagePlus, RefreshCw, Replace, X } from "lucide-react";
import { Modal } from "./ui/modal";
import { blobUrl } from "@/lib/api";
import { validateAttachmentFile } from "./workbench/attachmentUpload";
import type { useConversationAttachments } from "./workbench/useConversationAttachments";

type Attachments = ReturnType<typeof useConversationAttachments>;
type Props = {
  files?: Attachments;
  projectId?: string;
  images: number;
  videos?: number;
  framePair?: boolean;
  disabled?: boolean;
};
const states = {
  preparing: "准备中",
  uploading: "上传中",
  processing: "处理中",
  ready: "已就绪",
  paused: "已暂停",
  failed: "上传失败",
};

export function ProviderReferenceField({
  files,
  projectId,
  images,
  videos = 0,
  framePair = false,
  disabled = false,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const retryId = useRef("");
  const replaceId = useRef("");
  const [previewId, setPreviewId] = useState("");
  const [dragging, setDragging] = useState(false);
  const items = files?.items ?? [];
  const preview = items.find((item) => item.client_id === previewId);
  const previewUrl =
    preview?.preview_url ||
    (projectId && preview?.attachment?.blob_hash
      ? blobUrl(projectId, preview.attachment.blob_hash)
      : "");
  const label = videos ? "参考素材（可选）" : "参考图（可选）";
  const disabledInput = disabled || !files || !projectId;
  function add(selected: File[]) {
    if (disabledInput || !files) return;
    const retry = items.find((item) => item.client_id === retryId.current);
    retryId.current = "";
    if (retry && selected[0]) {
      files.retry(retry, selected[0]);
      return;
    }
    const replacing = items.find(
      (item) => item.client_id === replaceId.current,
    );
    replaceId.current = "";
    if (replacing && selected.length) {
      try {
        if (validateAttachmentFile(selected[0]) !== replacing.kind)
          throw new Error("请选择相同类型的文件进行替换。");
      } catch (error) {
        files.setError((error as Error).message);
        return;
      }
      files.remove(replacing);
      files.addFiles([selected[0]]);
      return;
    }
    let remainingImages =
      images - items.filter((item) => item.kind === "image").length;
    let remainingVideos =
      videos - items.filter((item) => item.kind === "video").length;
    const valid: File[] = [];
    const errors: string[] = [];
    for (const file of selected) {
      try {
        const kind = validateAttachmentFile(file);
        if (kind === "image" && remainingImages > 0) remainingImages--;
        else if (kind === "video" && remainingVideos > 0) remainingVideos--;
        else throw new Error("文件类型或数量超出了当前模型的参考素材范围。");
        valid.push(file);
      } catch (error) {
        errors.push((error as Error).message);
      }
    }
    if (valid.length) files.addFiles(valid);
    if (errors.length) files.setError([...new Set(errors)].join(" "));
  }
  return (
    <div className="provider-references">
      <span className="provider-field-label">{label}</span>
      <input
        ref={input}
        type="file"
        className="sr-only"
        tabIndex={-1}
        aria-label={label}
        disabled={disabledInput}
        multiple={images + videos > 1}
        accept={[
          images && ".png,.jpg,.jpeg,.webp",
          videos && ".mp4,.mov,.webm,.mkv",
        ]
          .filter(Boolean)
          .join(",")}
        onChange={(e) => {
          add(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
      {items.length > 0 && (
        <div className="provider-reference-grid">
          {items.map((item, index) => {
            const hash =
              item.attachment?.thumbnail_hash ||
              (item.kind === "image" && item.attachment?.blob_hash);
            const src =
              item.preview_url ||
              (projectId && hash ? blobUrl(projectId, hash) : "");
            const progress = Math.min(
              100,
              Math.round(
                (100 * (item.attachment?.uploaded_bytes ?? 0)) /
                  item.size_bytes,
              ),
            );
            return (
              <div className="provider-reference" key={item.client_id}>
                <button
                  type="button"
                  className="provider-reference-preview"
                  aria-label={"预览 " + item.filename}
                  disabled={!src && !item.attachment?.blob_hash}
                  onClick={() => setPreviewId(item.client_id)}
                >
                  {item.kind === "video" && item.preview_url ? (
                    <video
                      src={item.preview_url}
                      muted
                      playsInline
                      preload="metadata"
                    />
                  ) : src ? (
                    <img src={src} alt={item.filename} />
                  ) : (
                    <Film size={18} />
                  )}
                </button>
                <span className="provider-file-copy">
                  <strong title={item.filename}>{item.filename}</strong>
                  <small>
                    {framePair && items.length === 2
                      ? index === 0
                        ? "首帧 · "
                        : "尾帧 · "
                      : ""}
                    {item.error || states[item.state]}
                    {item.state === "uploading" && " " + progress + "%"}
                  </small>
                </span>
                <div className="provider-reference-tools">
                  {items.length === 1 && (
                    <button
                      type="button"
                      disabled={disabled}
                      aria-label={"替换 " + item.filename}
                      title="替换"
                      onClick={() => {
                        retryId.current = "";
                        replaceId.current = item.client_id;
                        input.current?.click();
                      }}
                    >
                      <Replace size={14} />
                    </button>
                  )}
                  {(item.state === "paused" || item.state === "failed") && (
                    <button
                      type="button"
                      disabled={disabled}
                      aria-label={"重试 " + item.filename}
                      onClick={() => {
                        if (files && !files.retry(item)) {
                          replaceId.current = "";
                          retryId.current = item.client_id;
                          input.current?.click();
                        }
                      }}
                    >
                      <RefreshCw size={15} />
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={"移除 " + item.filename}
                    onClick={() => files?.remove(item)}
                  >
                    <X size={16} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {items.length < Math.min(8, images + videos) && (
        <button
          type="button"
          className="provider-drop"
          data-dragging={dragging}
          disabled={disabledInput}
          onDragOver={(e) => {
            e.preventDefault();
            if (!disabledInput) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            retryId.current = "";
            replaceId.current = "";
            add(Array.from(e.dataTransfer.files));
          }}
          onClick={() => {
            retryId.current = "";
            replaceId.current = "";
            input.current?.click();
          }}
        >
          <span className="provider-drop-icon">
            <ImagePlus size={20} strokeWidth={1.5} />
          </span>
          <span>
            <strong>{videos ? "添加参考素材" : "添加参考图"}</strong>
            <small>
              {videos ? "点击选择或拖入文件" : "拖入图片，或点击选择"}
            </small>
          </span>
        </button>
      )}
      <Modal
        open={!!preview && !!previewUrl}
        onClose={() => setPreviewId("")}
        title={preview?.filename}
        width={850}
        bodyClassName="provider-result-expanded"
      >
        {preview?.kind === "video" ? (
          <video src={previewUrl || undefined} controls playsInline />
        ) : (
          <img src={previewUrl || undefined} alt={preview?.filename ?? ""} />
        )}
      </Modal>
    </div>
  );
}
