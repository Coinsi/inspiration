export type MediaType = "video" | "image" | "audio";
export type SearchBBox = { x: number; y: number; w: number; h: number };
export type RegionMatch = {
  id: string;
  vector_id: string;
  bbox: SearchBBox;
  start_ms: number | null;
  end_ms: number | null;
  matched_at_ms: number | null;
  score: number;
};
export function librarySourceUrl(
  projectId: string,
  source: {
    media_id: string;
    version_id: string;
    media_type?: MediaType;
    start_ms: number | null;
    end_ms: number | null;
  },
) {
  const params = new URLSearchParams({
    media: source.media_id,
    version: source.version_id,
  });
  if (
    source.media_type !== "image" &&
    source.start_ms !== null &&
    source.end_ms !== null &&
    Number.isFinite(source.start_ms) &&
    Number.isFinite(source.end_ms) &&
    source.start_ms >= 0 &&
    source.end_ms > source.start_ms
  ) {
    params.set("start", String(source.start_ms));
    params.set("end", String(source.end_ms));
  }
  return `/projects/${projectId}/library?${params.toString()}`;
}
export const mediaTypeName = (kind: MediaType, zh: boolean) =>
  ({
    video: zh ? "视频" : "Video",
    image: zh ? "图片" : "Image",
    audio: zh ? "音频" : "Audio",
  })[kind];
export const mediaAccept: Record<MediaType, string> = {
  video: ".mp4,.mov,.mkv,.webm",
  image: ".png,.jpg,.jpeg,.webp",
  audio: ".wav,.mp3,.flac,.m4a,.ogg",
};
export function fileMediaType(name: string): MediaType | null {
  const extension = `.${name.split(".").pop()?.toLowerCase()}`;
  return (
    (Object.keys(mediaAccept) as MediaType[]).find((kind) =>
      mediaAccept[kind].split(",").includes(extension),
    ) ?? null
  );
}
export interface MediaVersion {
  media_type: MediaType;
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
  has_audio?: boolean | null;
  width: number | null;
  height: number | null;
}
export interface LibraryMedia {
  description?: string;
  tags?: string[] | null;
  metadata_revision?: number;
  folder_id?: string | null;
  id: string;
  name: string;
  deleted_at: string | null;
  versions: MediaVersion[];
  usage_count: number;
}
export interface MediaUsage {
  media_type: MediaType;
  id: string;
  version_id: string;
  media_id: string;
  name: string;
  ordinal: number;
  shot_id: string;
  shot_title: string;
  shot_deleted: boolean;
  start_ms: number | null;
  end_ms: number | null;
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
export const mediaTime = (ms: number | null) =>
  ms === null
    ? "—"
    : `${Math.floor(ms / 60000)
        .toString()
        .padStart(
          2,
          "0",
        )}:${((ms % 60000) / 1000).toFixed(1).padStart(4, "0")}`;
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
