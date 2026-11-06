// 轻量 API 客户端:统一加 JWT、解析后端统一错误格式。
const TOKEN_KEY = "inspiration_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options?: { signal?: AbortSignal },
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: options?.signal,
  });

  if (!res.ok) {
    let code = "ERROR";
    let message = res.statusText;
    try {
      const data = await res.json();
      code = data?.error?.code ?? code;
      message =
        data?.error?.message ??
        (Array.isArray(data?.detail)
          ? data.detail
              .map((d: { msg?: string }) => d.msg)
              .filter(Boolean)
              .join("；")
          : message);
    } catch {
      /* ignore */
    }
    // An older request must not erase a session that just replaced its token.
    if (res.status === 401 && getToken() === token) clearToken();
    throw new ApiError(code, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type UploadOptions = {
  signal?: AbortSignal;
  onProgress: (fraction: number) => void;
};

/** Track bytes in flight and detect stalled connections, including response waits. */
function progressUpload<T>(path: string, form: FormData, options: UploadOptions): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const token = getToken();
    let timer: ReturnType<typeof setTimeout>;
    let timedOut = false;
    const abort = () => xhr.abort();
    const cleanup = () => { clearTimeout(timer); options.signal?.removeEventListener("abort", abort); };
    const fail = (error: Error) => { cleanup(); reject(error); };
    const watch = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { timedOut = true; xhr.abort(); }, 60_000);
    };
    xhr.open("POST", `/api/v1${path}`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = e => { watch(); if (e.lengthComputable) options.onProgress(e.loaded / e.total); };
    xhr.upload.onload = watch;
    xhr.onload = () => {
      cleanup();
      let data;
      try { data = JSON.parse(xhr.responseText); } catch { reject(new ApiError("INVALID_RESPONSE", "上传响应不完整，请继续上传以同步进度。")); return; }
      if (xhr.status < 200 || xhr.status >= 300) {
        if (xhr.status === 401 && getToken() === token) clearToken();
        reject(new ApiError(data?.error?.code || "ERROR", data?.error?.message || `上传失败（HTTP ${xhr.status}），请重试。`));
      } else resolve(data as T);
    };
    xhr.onerror = () => fail(new TypeError("网络连接中断，请继续上传。"));
    xhr.onabort = () => fail(timedOut
      ? new ApiError("UPLOAD_TIMEOUT", "上传连接长时间没有响应，已保留进度，请继续上传。")
      : new DOMException("Upload paused", "AbortError"));
    if (options.signal?.aborted) { fail(new DOMException("Upload paused", "AbortError")); return; }
    options.signal?.addEventListener("abort", abort, { once: true });
    watch();
    try { xhr.send(form); } catch (error) { fail(error as Error); }
  });
}

export async function apiUpload<T>(path: string, form: FormData, options?: UploadOptions): Promise<T> {
  if (options) return progressUpload<T>(path, form, options);
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    let code = "ERROR";
    let message = res.statusText;
    try {
      const data = await res.json();
      code = data?.error?.code ?? code;
      message = data?.error?.message ?? message;
    } catch {
      /* ignore */
    }
    throw new ApiError(code, message);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string, options?: { signal?: AbortSignal }) => request<T>("GET", p, undefined, options),
  post: <T>(p: string, b?: unknown, options?: { signal?: AbortSignal }) => request<T>("POST", p, b, options),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export async function downloadBlob(
  projectId: string,
  hash: string,
  filename: string,
) {
  const response = await fetch(`/api/v1/projects/${projectId}/blobs/${hash}`, {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(
      error?.error?.message || `Download failed (${response.status})`,
    );
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

// ── 类型 ──
export interface Project {
  id: string;
  code: string;
  name: string;
  description: string | null;
  cover_blob_hash?: string | null;
  deleted_at?: string | null;
  owner_id: string;
  settings: Record<string, unknown>;
  created_at: string;
}
export interface Member {
  id: string;
  user_id: string;
  role: string;
  project_id?: string;
  username?: string;
  display_name?: string;
  avatar_data?: string | null;
  is_owner?: boolean;
}
export interface Me {
  user: {
    id: string;
    username: string;
    email: string;
    display_name: string;
    is_active: boolean;
    is_platform_admin: boolean;
    avatar_data?: string | null;
    bio: string;
  };
  memberships: Member[];
}

export const ASSET_TYPES = [
  "character",
  "prop",
  "location",
  "costume",
  "vehicle",
  "style",
  "model_lora",
] as const;
export const ASSET_TYPE_LABEL: Record<string, string> = {
  character: "角色",
  prop: "道具",
  location: "场景",
  costume: "服化道",
  vehicle: "载具",
  style: "风格",
  model_lora: "模型/LoRA",
};

export interface Asset {
  id: string;
  code: string;
  type: string;
  name: string;
  summary: string | null;
  status: string;
  current_version_id: string | null;
  representative_blob_hash: string | null;
  metadata: Record<string, unknown>;
  tags: string[];
  created_at: string;
  ref_count?: number;
  gen_count?: number;
  shot_count?: number;
}

export interface AssetVersion {
  id: string;
  version_no: number;
  label: string | null;
  status: string;
  is_locked: boolean;
  content: Record<string, unknown>;
  created_at: string;
}

export interface RefImage {
  id: string;
  blob_hash: string;
  role: string;
  note: string | null;
  ordinal: number;
}

export interface Novel {
  cover_blob_hash?: string | null;
  id: string;
  code: string;
  title: string;
  source_format: string | null;
  created_at: string;
}
export interface Chapter {
  id: string;
  ordinal: number;
  title: string | null;
  content: string;
}
export interface NovelDetail extends Novel {
  chapters: Chapter[];
}
export interface Script {
  cover_blob_hash?: string | null;
  id: string;
  code: string;
  title: string;
  created_at: string;
  block_count?: number;
  scene_count?: number;
}
export interface ScriptBlock {
  id?: string | null;
  block_type: string;
  text: string;
}
export interface ScriptDetail extends Script {
  content_revision: string;
  source_chapter_id: string | null;
  content_blocks: ScriptBlock[];
}
export const SCRIPT_BLOCK_TYPES = [
  "scene_heading",
  "action",
  "character",
  "dialogue",
  "parenthetical",
  "transition",
] as const;

// ── AI 对话助手 ──
export interface AssistProposal {
  summary: string;
  ops: Record<string, unknown>[];
  needs_clarification: boolean;
  preview: Record<string, unknown> | null;
  applied_at: string | null;
}
export interface AssistMessage {
  skills?: { id: string; name: string; revision: number }[];
  id: string;
  role: string;
  content: string;
  created_at: string | null;
  proposal: AssistProposal | null;
}
export interface AssistChat {
  id: string;
  target_type: string;
  target_id: string;
  title: string | null;
  created_at: string;
  messages: AssistMessage[];
}
export interface EntityDraft {
  type: string;
  name: string;
  summary: string;
}

// ── 设定(故事圣经)──
export const SETTING_CATEGORIES = [
  "world",
  "power_system",
  "faction",
  "character",
  "location",
  "item",
  "glossary",
  "timeline",
] as const;
export interface Setting {
  id: string;
  novel_id: string | null;
  category: string;
  name: string;
  content: string;
  source_chapters: number[];
  created_at: string;
  updated_at: string;
}
export interface SettingExtraction {
  id: string;
  novel_id: string;
  status: string;
  total_chapters: number;
  done_chapters: number;
  error: string | null;
  created_at: string;
}
export interface ShotBrief {
  title: string;
  description: string;
}
export interface SceneSuggestion {
  title: string;
  summary: string;
  body: string;
  source_chapter_ordinal: number | null;
  shots: ShotBrief[];
}
export interface DecomposeResult {
  strategy: string;
  scenes: SceneSuggestion[];
}

export interface Scene {
  id: string;
  code: string;
  ordinal: number;
  title: string | null;
  summary: string | null;
  adapted_from_chapter_id: string | null;
}
export interface Shot {
  id: string;
  code: string;
  scene_id: string;
  ordinal: number;
  title: string | null;
  description: string;
  production_status: string;
  prompt_override: string | null;
  selected_generation_id: string | null;
  created_at?: string | null;
  scene_code?: string | null;
  scene_title?: string | null;
  chapter_id?: string | null;
  chapter_ordinal?: number | null;
  chapter_title?: string | null;
  novel_id?: string | null;
  novel_title?: string | null;
  storyboard?: Record<string, string | number> | null;
}

export interface SceneListItem {
  id: string;
  code: string;
  ordinal: number;
  title: string | null;
  summary: string | null;
  shot_count: number;
  chapter_id: string | null;
  chapter_ordinal: number | null;
  chapter_title: string | null;
  novel_id: string | null;
  novel_title: string | null;
}
export interface ComposeResult {
  final_prompt: string;
  parts: string[];
  overridden: boolean;
}
export interface ShotAssetRef {
  id: string;
  asset_id: string;
  role: string;
  ref_mode: string;
  pinned_version_id: string | null;
  ordinal: number;
}
export interface Board {
  total: number;
  by_status: Record<string, number>;
}
export interface Fragment {
  cover_image?: string;
  updated_at: string;
  id: string;
  code: string;
  category: string;
  name: string;
  text: string;
}
export interface Prompt {
  cover_image?: string;
  id: string;
  code: string;
  name: string;
  positive: string;
  negative: string;
  tags: string[];
}

export interface GenJob {
  id: string;
  status: string;
  estimated_cost: number | null;
  actual_cost: number | null;
  error: string | null;
  target_type: string;
  target_id: string;
  provider: string;
  request_type: string;
  created_at: string;
  updated_at: string;
  routing_state?: {
    phase?: string;
    channel_name?: string;
    model_name?: string;
    cost_points?: number | null;
    attempts?: {
      phase: string;
      channel_name: string;
      message: string;
      at?: string;
    }[];
  };
  input_snapshot: {
    skills?: {
      id: string;
      name: string;
      revision: number;
      files: { path: string; sha256: string }[];
    }[];
    operation?: string;
    prompt?: string;
    retry_of?: string;
    [key: string]: unknown;
  };
  cost_raw: {
    events?: { at: string; status: string; message: string }[];
    warnings?: string[];
  } | null;
}
export interface Generation {
  input_refs?: {
    engineering_change_id?: string;
    engineering_work_id?: string;
    original_prompt?: string;
    skills?: { id: string; name: string; revision: number }[];
    library_origin?: {
      media_id: string;
      version_id: string;
      media_type?: "video" | "image" | "audio";
      usage_id: string;
      ordinal: number;
      name: string;
      start_ms: number | null;
      end_ms: number | null;
    };
    character_preset?: {
      mode: string;
      preset: string;
      label: string;
      label_en: string;
    };
    cell_index?: number;
    rows?: number;
    columns?: number;
    crop_box?: number[];
    source_generation_id?: string;
    operation?: string;
    actual_media?: { source_time_ms?: number; duration_ms?: number };
  };
  id: string;
  target_type: "shot" | "asset" | "timeline";
  target_id: string;
  job_id: string;
  provider: string;
  created_at: string;
  output_type: string;
  output_blob_hash: string | null;
  prompt_snapshot: string;
  rating: number | null;
  is_favorite: boolean;
  is_selected: boolean;
  cost_points: number;
}
export interface Quota {
  limit_cost: number;
  used_cost: number;
}
export interface ProviderConfig {
  display_name?: string;
  channel_name?: string;
  id: string;
  provider_name: string;
  kind: string;
  enabled: boolean;
  endpoint: string | null;
  config: Record<string, unknown>;
  capabilities: Record<string, unknown>;
}

export function blobUrl(projectId: string, hash: string): string {
  return `/api/v1/projects/${projectId}/blobs/${hash}?token=${getToken() ?? ""}`;
}

export interface Review {
  id: string;
  status: string;
  round_no: number;
  decision: string | null;
}
export interface Timeline {
  id: string;
  name: string;
  kind: string;
}
export interface Cut {
  id: string;
  code: string;
  name: string;
  kind: string;
  baseline_id: string | null;
  status: string;
}
export interface Baseline {
  id: string;
  name: string;
  kind: string;
  note: string | null;
  created_at: string;
}

export const PRODUCTION_STATUS_LABEL: Record<string, string> = {
  to_design: "待设计",
  concept: "概念图",
  generating: "生成中",
  pending_review: "待审",
  revising: "返修",
  approved: "定版",
  in_cut: "已入剪",
};
export const NEXT_STATUS: Record<string, string[]> = {
  to_design: ["concept"],
  concept: ["generating"],
  generating: ["pending_review"],
  pending_review: ["approved", "revising"],
  revising: ["generating"],
  approved: ["in_cut"],
  in_cut: [],
};
