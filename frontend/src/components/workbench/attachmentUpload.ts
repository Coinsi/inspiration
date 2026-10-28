import { api, apiUpload } from "@/lib/api";
import { digest, fileFingerprint, fileMediaType, type MediaType } from "@/lib/library";
import type { ReferenceRole } from "./referenceInput";

export type ConversationAttachment = {
  id: string;
  title: string;
  filename: string;
  kind: MediaType;
  status: "uploading" | "processing" | "ready" | "failed" | "cancelled";
  uploaded_bytes: number;
  size_bytes: number;
  chunk_size: number;
  fingerprint?: string;
  chunk_hashes?: string[];
  blob_hash: string | null;
  thumbnail_hash?: string | null;
  duration_ms?: number | null;
  width?: number | null;
  height?: number | null;
  error?: string | null;
  saved_at?: string | null;
  role?: ReferenceRole;
};

export type AttachmentDraft = {
  client_id: string;
  filename: string;
  kind: MediaType;
  size_bytes: number;
  role: ReferenceRole;
  attachment?: ConversationAttachment;
  state: "preparing" | "uploading" | "processing" | "ready" | "paused" | "failed";
  error?: string;
  preview_url?: string;
};

export const attachmentAccept = ".png,.jpg,.jpeg,.webp,.mp4,.mov,.mkv,.webm,.wav,.mp3,.flac,.m4a,.ogg";

export function validateAttachmentFile(file: File): MediaType {
  const kind = fileMediaType(file.name);
  if (!kind || !file.size) throw new Error("请选择有效的图片、视频或音频文件。");
  if (kind === "image" && file.size > 64 * 1024 ** 2) throw new Error("图片不能超过 64 MB。");
  if (file.size > 10 * 1024 ** 3) throw new Error("单个附件不能超过 10 GB。");
  return kind;
}

/** Never persist browser object URLs or pretend that a File survives a refresh. */
export function persistedAttachmentDrafts(items: AttachmentDraft[]) {
  return items.map(({ preview_url: _preview, ...item }) => item);
}

export function restoreAttachmentDrafts(raw: string | null): AttachmentDraft[] {
  try {
    const parsed = JSON.parse(raw || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(item => item && typeof item.client_id === "string" && typeof item.filename === "string" && ["image", "video", "audio"].includes(item.kind)).slice(0, 8)
      .map(({ preview_url: _preview, ...item }) => ({
        ...item,
        state: ["preparing", "uploading"].includes(item.state) ? "paused" : item.state,
      }));
  } catch { return []; }
}

export async function uploadConversationAttachment(
  base: string,
  file: File,
  draft: AttachmentDraft,
  update: (attachment: ConversationAttachment) => void,
  cancelled: () => boolean,
) {
  validateAttachmentFile(file);
  const fingerprint = await fileFingerprint(file);
  if (cancelled()) return;
  let current: ConversationAttachment;
  if (draft.attachment) {
    current = await api.get<ConversationAttachment>(`${base}/${draft.attachment.id}/manifest`);
    if (current.size_bytes !== file.size || current.fingerprint !== fingerprint)
      throw new Error("所选文件与原附件不同，请选择原文件继续上传。");
  } else {
    current = await api.post<ConversationAttachment>(`${base}/uploads`, {
      request_key: draft.client_id, filename: file.name, size_bytes: file.size, fingerprint,
    });
    if (current.uploaded_bytes > 0 && current.status === "uploading")
      current = await api.get<ConversationAttachment>(`${base}/${current.id}/manifest`);
  }
  update(current);
  if (cancelled()) return current;
  if (current.status === "ready" || current.status === "processing") return current;
  if (current.status === "failed" && current.uploaded_bytes === file.size) {
    current = await api.post<ConversationAttachment>(`${base}/${current.id}/complete`);
    update(current);
    return current;
  }
  if (current.status !== "uploading") throw new Error(current.error || "该附件无法续传，请移除后重新添加。");
  // Verify the complete committed prefix, not merely the first/last fingerprint.
  const count = Math.ceil(current.uploaded_bytes / current.chunk_size);
  if ((current.chunk_hashes?.length || 0) !== count)
    throw new Error("续传校验信息不完整，请移除后重新添加。");
  for (let i = 0; i < count; i++) {
    if (cancelled()) return current;
    if (await digest(file.slice(i * current.chunk_size, Math.min((i + 1) * current.chunk_size, current.uploaded_bytes))) !== current.chunk_hashes![i])
      throw new Error("已上传部分与所选文件不同，已停止续传。");
  }
  while (current.uploaded_bytes < file.size) {
    if (cancelled()) return current;
    const body = new FormData();
    body.append("file", file.slice(current.uploaded_bytes, current.uploaded_bytes + current.chunk_size), "chunk");
    current = await apiUpload<ConversationAttachment>(`${base}/${current.id}/chunk?offset=${current.uploaded_bytes}`, body);
    update(current);
  }
  if (cancelled()) return current;
  current = await api.post<ConversationAttachment>(`${base}/${current.id}/complete`);
  update(current);
  return current;
}
