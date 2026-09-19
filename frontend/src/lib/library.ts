export interface MediaVersion {
  reuse_origin?: { version_id: string; name: string; ordinal: number } | null;
  id: string;
  media_id: string;
  ordinal: number;
  filename: string;
  size_bytes: number;
  uploaded_bytes: number;
  fingerprint: string;
  chunk_size: number;
  chunk_hashes?: string[];
  status: string;
  error: string | null;
  original_hash: string | null;
  proxy_hash: string | null;
  poster_hash: string | null;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
}
export interface LibraryMedia {
  folder_id?: string | null;
  id: string;
  name: string;
  deleted_at: string | null;
  versions: MediaVersion[];
  usage_count: number;
}
export interface MediaUsage {
  id: string;
  version_id: string;
  media_id: string;
  name: string;
  ordinal: number;
  shot_id: string;
  shot_title: string;
  shot_deleted: boolean;
  start_ms: number;
  end_ms: number;
  purpose: string;
  note: string;
  proxy_hash: string;
  original_hash: string;
}
export interface DeletionImpact {
  shared_count?: number;
  reuses?: {
    project_id: string | null;
    project_name: string | null;
    media_id: string | null;
    version_id: string | null;
    source_ordinal: number;
  }[];
  evidence_count?: number;
  references: MediaUsage[];
  active_versions: string[];
  can_trash: boolean;
  physical_delete: boolean;
}
export const mediaStatus = (status: string, zh: boolean) =>
  ({
    uploading: zh ? "待续传" : "Upload paused",
    queued: zh ? "等待处理" : "Queued",
    processing: zh ? "制作预览中" : "Preparing preview",
    ready: zh ? "可使用" : "Ready",
    failed: zh ? "处理失败" : "Failed",
    canceled: zh ? "已取消" : "Canceled",
  })[status] || status;
export const mediaSize = (bytes: number) =>
  bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(2)} GB`
    : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
export const mediaTime = (ms: number) =>
  `${Math.floor(ms / 60000)
    .toString()
    .padStart(2, "0")}:${((ms % 60000) / 1000).toFixed(1).padStart(4, "0")}`;
export async function digest(blob: Blob) {
  return [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()),
    ),
  ]
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
}
export async function fileFingerprint(file: File) {
  return digest(
    new Blob([
      String(file.size),
      file.slice(0, 65536),
      file.slice(Math.max(0, file.size - 65536)),
    ]),
  );
}
