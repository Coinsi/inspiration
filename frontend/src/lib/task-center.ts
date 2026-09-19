import type { Generation, GenJob } from "./api";

export const ACTIVE_JOBS = new Set(["pending", "submitted", "running"]);
export const JOB_LABEL: Record<string, string> = {
  pending: "排队中",
  submitted: "准备中",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
  canceled: "已取消",
};
export const OPERATIONS: Record<string, [string, string]> = {
  generate: ["模型生成", "Generation"],
  render: ["成片导出", "Film export"],
  upload: ["素材上传", "Upload"],
  audio_upload: ["音频上传", "Audio upload"],
  grid_split: ["宫格拆分", "Grid split"],
  canvas_edit: ["画布图片编辑", "Canvas image edit"],
  director_reference: ["导演台回填", "Director reference"],
  refine: ["图片精修", "Image refinement"],
  inpaint: ["局部重绘", "Inpainting"],
  character: ["角色素材生成", "Character"],
  video_frames: ["视频抽帧", "Video frames"],
  video_audio: ["提取音轨", "Audio extraction"],
  video_trim: ["导出视频片段", "Video trim"],
  library_materialize: ["素材片段制作", "Library clip"],
};
export function taskLabel(operation: string, requestType: string, zh: boolean) {
  if (operation === "generate")
    return requestType === "video"
      ? zh
        ? "视频生成"
        : "Video generation"
      : requestType === "audio"
        ? zh
          ? "声音生成"
          : "Audio generation"
        : zh
          ? "图片生成"
          : "Image generation";
  return (
    OPERATIONS[operation]?.[zh ? 0 : 1] ||
    (zh ? "素材处理" : "Media processing")
  );
}
export function taskSource(project: string, targetType: string, id: string) {
  if (targetType === "timeline")
    return `/projects/${project}/cuts?timeline=${id}`;
  if (targetType === "asset") return `/projects/${project}/assets/${id}`;
  if (targetType === "shot") return `/projects/${project}/shots?shot=${id}`;
  return null;
}
export interface TaskSummary {
  id: string;
  target_type: string;
  target_id: string;
  target_name: string | null;
  target_available: boolean;
  status: string;
  provider: string;
  request_type: string;
  operation: string;
  created_at: string;
  updated_at: string;
  error: string | null;
  has_warnings: boolean;
  result_count: number;
  last_event: string | null;
}
export interface TaskCatalog {
  items: TaskSummary[];
  total: number;
  has_more: boolean;
  counts: Record<string, number>;
}
export interface TaskWorkspace {
  summary: TaskSummary;
  job: GenJob;
  outputs: (Generation & { output_mime: string })[];
  retry_parent: TaskSummary | null;
  retries: TaskSummary[];
  more_retries: boolean;
  can_cancel: boolean;
  can_retry: boolean;
}
export function mediaExtension(mime: string) {
  return (
    (
      {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
      } as Record<string, string>
    )[mime] || "bin"
  );
}
