import {
  useCreativeDefaults,
  type CreativeDefaults,
} from "@/components/CreativePreferences";
import SkillPicker from "@/components/SkillPicker";
import type { SkillUse } from "@/lib/skills";
import { ImageRefiner } from "@/components/ImageRefiner";
import { ImageInpainter } from "@/components/ImageInpainter";
import { PromptOptimizer } from "@/components/PromptOptimizer";
import { PromptLibrary } from "@/components/PromptLibrary";
import { JobStatus } from "@/components/JobStatus";
import { apiUpload } from "@/lib/api";
import { Link } from "react-router-dom";
import { MediaVideo } from "@/components/MediaVideo";
import { MediaAudio } from "@/components/MediaAudio";
import { VideoTools } from "@/components/VideoTools";
import { CharacterTools } from "@/components/CharacterTools";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  Star,
  Sparkles,
  ImagePlus,
  Loader2,
  SlidersHorizontal,
  Images,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/components/ui/confirm";
import { useGenerationDraft } from "@/lib/useGenerationDraft";
import { MediaImage } from "@/components/MediaImage";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { ZoomableImage } from "@/components/ImageViewer";
import {
  api,
  blobUrl,
  type GenJob,
  type Generation,
  type ProviderConfig,
  type Quota,
} from "@/lib/api";

// 各供应商支持的模态(gpt_image 仅图片)
const PROVIDER_LABEL: Record<string, string> = {
  mock: "Mock · 测试素材",
  jimeng: "即梦兼容网关",
  gpt_image: "GPT Image",
};

function GenerationPanelContent({
  projectId,
  targetType,
  targetId,
  defaults,
  onChooseReferences,
}: {
  projectId: string;
  targetType: "shot" | "asset";
  targetId: string;
  defaults?: CreativeDefaults;
  onChooseReferences?: () => void;
}) {
  const base = `/projects/${projectId}`;
  const t = `${targetType}s`; // shots | assets
  const qc = useQueryClient();
  const { me } = useAuth();
  const confirm = useConfirm();
  const composerRef = useRef<HTMLElement>(null);
  const toast = useToast();
  const { t: tr, lang } = useI18n();
  const zh = lang === "zh";
  const [type, setType] = useState("image");
  const [promptLibraryOpen, setPromptLibraryOpen] = useState(false);
  const [count, setCount] = useState(
    targetType === "asset"
      ? (defaults?.image_count ?? 4)
      : (defaults?.shot_count ?? 3),
  );
  const [skills, setSkills] = useState<SkillUse[]>([]);
  const [useRefs, setUseRefs] = useState(defaults?.use_references ?? true);
  const [est, setEst] = useState<number | null>(null);
  const defaultModelApplied = useRef(false);
  const [hasSavedProvider] = useState(() => {
    try {
      return !!localStorage.getItem(`gen.provider.${projectId}`);
    } catch {
      return false;
    }
  });
  const [provider, setProvider] = usePersistentState<string>(
    `gen.provider.${projectId}`,
    "mock",
  );

  const { data: providerConfigs, error: providersError } = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${base}/providers`),
  });
  useEffect(() => {
    if (!providerConfigs || defaultModelApplied.current) return;
    defaultModelApplied.current = true;
    if (hasSavedProvider) return;
    const model = providerConfigs.find(
      (p) =>
        p.enabled &&
        p.kind === "generation" &&
        p.config.is_default &&
        p.config.modality === "image",
    );
    if (model) setProvider(model.provider_name);
  }, [providerConfigs, hasSavedProvider, setProvider]);
  // 可选供应商:mock + 已配置且启用的生成供应商
  const providerOptions = [
    "mock",
    ...(providerConfigs ?? [])
      .filter(
        (p) =>
          p.kind === "generation" &&
          p.enabled &&
          p.provider_name !== "mock" &&
          (targetType !== "asset" || p.config.modality !== "video"),
      )
      .map((p) => p.provider_name),
  ];
  const { data: caps, error: capsError } = useQuery({
    queryKey: ["capabilities", projectId, provider],
    queryFn: () =>
      api.get<{
        modalities: string[];
        features: string[];
        param_schema: Record<string, { enum?: (string | number)[] }>;
      }>(`${base}/providers/${provider}/capabilities`),
  });
  const supportsVideo = caps?.modalities.includes("video") ?? false;
  const selectedModel = providerConfigs?.find(
    (p) => p.provider_name === provider,
  );
  const singleVideo = selectedModel?.config.protocol === "openai_video";
  useEffect(() => {
    if (singleVideo) setCount(1);
    if (targetType === "asset") setType("image");
    else if (caps?.modalities.length === 1) setType(caps.modalities[0]);
  }, [singleVideo, caps, targetType]);
  const [controls, setControls] = useState<Record<string, string | number>>({});
  const { prompt, setPrompt, storageError } = useGenerationDraft(
    `generation.draft.${me!.user.id}.${projectId}.${targetType}.${targetId}`,
  );
  const [resultFilter, setResultFilter] = useState("all");
  const [sourceId, setSourceId] = useState("");
  const [firstId, setFirstId] = useState("");
  const [lastId, setLastId] = useState("");
  const [editing, setEditing] = useState<Generation | null>(null);
  const [videoTools, setVideoTools] = useState<Generation | null>(null);
  const [characterTools, setCharacterTools] = useState<Generation | null>(null);
  const [inpainting, setInpainting] = useState<Generation | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [comparing, setComparing] = useState(false);
  const jobStorageKey = `gen.job.${projectId}.${targetType}.${targetId}`;
  const [lastJob, setLastJob] = usePersistentState<string>(jobStorageKey, "");
  const rememberJob = (id: string) => {
    // A user may switch shots while submission is in flight. Keep its task link
    // even if this object panel has already unmounted when the response arrives.
    try {
      localStorage.setItem(jobStorageKey, JSON.stringify(id));
    } catch {
      /* Task center remains available. */
    }
    setLastJob(id);
  };
  useEffect(() => {
    if (caps && type === "video" && !supportsVideo) setType("image");
  }, [caps, type, supportsVideo]);
  useEffect(() => {
    setControls({});
    setFirstId("");
    setLastId("");
  }, [provider, type]);
  useEffect(() => {
    setSourceId("");
  }, [provider]);
  useEffect(() => {
    setEst(null);
  }, [
    provider,
    type,
    controls,
    prompt,
    sourceId,
    firstId,
    lastId,
    count,
    useRefs,
    skills,
  ]);
  const payload = {
    skills: skills.map(({ id, revision }) => ({ id, revision })),
    provider,
    request_type: type,
    count,
    use_references:
      useRefs &&
      !(type === "image" && sourceId && caps?.features.includes("img2img")),
    prompt_override: prompt.trim() || undefined,
    provider_params: controls,
    source_generation_id:
      type === "image" && caps?.features.includes("img2img")
        ? sourceId || undefined
        : undefined,
    first_frame_id:
      type === "video" && caps?.features.includes("first_frame")
        ? firstId || undefined
        : undefined,
    last_frame_id:
      type === "video" && caps?.features.includes("last_frame")
        ? lastId || undefined
        : undefined,
  };

  const payloadSignature = useRef("");
  payloadSignature.current = JSON.stringify(payload);
  const {
    data: gens,
    isPending: gensLoading,
    error: gensError,
    refetch: reloadGens,
  } = useQuery({
    queryKey: ["gens", targetType, targetId],
    queryFn: () =>
      api.get<Generation[]>(
        `${base}/generations?target_type=${targetType}&target_id=${targetId}`,
      ),
  });
  const { data: quota } = useQuery({
    queryKey: ["quota", projectId],
    queryFn: () => api.get<Quota | null>(`${base}/quota`),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["gens", targetType, targetId] });
    void qc.invalidateQueries({ queryKey: ["quota", projectId] });
    void qc.invalidateQueries({ queryKey: ["asset", targetId] });
    void qc.invalidateQueries({ queryKey: ["assets", projectId] });
    void qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
    void qc.invalidateQueries({ queryKey: ["scene-shots", projectId] });
    void qc.invalidateQueries({
      queryKey: ["prompt-reference-options", projectId],
    });
  };

  const estimate = useMutation({
    mutationFn: (request: typeof payload) =>
      api.post<{ points: number }>(
        `${base}/${t}/${targetId}/estimate`,
        request,
      ),
    onSuccess: (d, request) => {
      if (JSON.stringify(request) === payloadSignature.current)
        setEst(d.points);
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const generate = useMutation({
    mutationFn: () =>
      api.post<GenJob>(`${base}/${t}/${targetId}/generate`, payload),
    onSuccess: (j) => {
      rememberJob(j.id);
      refresh();
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiUpload<Generation>(
        `${base}/media/${targetType}/${targetId}/upload`,
        form,
      );
    },
    onSuccess: () => {
      refresh();
      toast.push(zh ? "素材已上传" : "Media uploaded", "success");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const select = useMutation({
    mutationFn: (id: string) => api.post(`${base}/generations/${id}/select`),
    onSuccess: refresh,
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const favorite = useMutation({
    mutationFn: ({ id, fav }: { id: string; fav: boolean }) =>
      api.patch(`${base}/generations/${id}`, { is_favorite: fav }),
    onSuccess: refresh,
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  const selectLabel =
    targetType === "asset" ? tr("gen.setAvatar") : tr("gen.pick");
  const selectedLabel =
    targetType === "asset" ? tr("gen.curAvatar") : tr("gen.picked");
  const visibleGens = (gens ?? []).filter(
    (g) =>
      resultFilter === "all" ||
      (resultFilter === "favorite"
        ? g.is_favorite
        : g.output_type === resultFilter),
  );
  const reference = gens?.find((g) => g.id === sourceId);
  const useVersionPrompt = async (g: Generation) => {
    const original = g.input_refs?.original_prompt ?? g.prompt_snapshot;
    if (
      prompt.trim() &&
      prompt !== original &&
      !(await confirm({
        title: zh ? "替换当前创作指令？" : "Replace this draft?",
        message: zh
          ? "当前草稿会被这个版本的指令替换，已有生成结果不会改变。"
          : "This replaces your draft. Existing results stay unchanged.",
      }))
    )
      return;
    setPrompt(original.slice(0, 10000));
    setSkills(g.input_refs?.skills ?? []);
    composerRef.current?.scrollIntoView({ block: "nearest" });
  };

  return (
    <div className="generation-studio">
      {inpainting && (
        <ImageInpainter
          projectId={projectId}
          generation={inpainting}
          provider={provider}
          onClose={() => setInpainting(null)}
          onJob={rememberJob}
        />
      )}
      {optimizing && (
        <PromptOptimizer
          projectId={projectId}
          initial={prompt}
          mediaType={type}
          targetType={targetType}
          targetId={targetId}
          provider={provider}
          onClose={() => setOptimizing(false)}
          onApply={setPrompt}
        />
      )}
      <div className="generation-layout">
        <section
          ref={composerRef}
          className="generation-composer"
          aria-label={zh ? "创作输入" : "Creative input"}
        >
          <div className="generation-section-heading">
            <span className="generation-section-icon">
              <Sparkles size={18} />
            </span>
            <div>
              <h3>{zh ? "把想法变成画面" : "Bring your idea into frame"}</h3>
              <p>
                {zh
                  ? "写下指令，选择参考，再开始创作。"
                  : "Add a prompt, choose a reference, then create."}
              </p>
            </div>
          </div>
          {(capsError || providersError) && (
            <p role="alert" className="text-xs text-danger">
              {((capsError || providersError) as Error).message}
            </p>
          )}
          <fieldset disabled={generate.isPending} className="generation-fields">
            <div className="generation-parameters">
              <span className="generation-field-title">
                <SlidersHorizontal size={14} />
                {zh ? "生成设置" : "Generation settings"}
              </span>
              <select
                aria-label={tr("gen.provider")}
                className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
                value={provider}
                onChange={(e) => {
                  defaultModelApplied.current = true;
                  setProvider(e.target.value);
                }}
                title={tr("gen.provider")}
              >
                {!providerOptions.includes(provider) && (
                  <option value={provider}>
                    {provider} · {zh ? "不可用" : "Unavailable"}
                  </option>
                )}
                {providerOptions.map((p) => (
                  <option key={p} value={p}>
                    {providerConfigs?.find((c) => c.provider_name === p)
                      ?.channel_name
                      ? `${providerConfigs.find((c) => c.provider_name === p)?.channel_name} / ${providerConfigs.find((c) => c.provider_name === p)?.display_name}`
                      : (PROVIDER_LABEL[p] ?? p)}
                  </option>
                ))}
              </select>
              <select
                aria-label={zh ? "生成类型" : "Media type"}
                className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
                value={type}
                onChange={(e) => setType(e.target.value)}
              >
                {(!caps || caps.modalities.includes("image")) && (
                  <option value="image">{tr("gen.image")}</option>
                )}
                {targetType === "shot" && supportsVideo && (
                  <option value="video">{tr("gen.video")}</option>
                )}
              </select>
              <select
                aria-label={zh ? "生成数量" : "Output count"}
                className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
              >
                {(singleVideo ? [1] : [1, 2, 3, 4]).map((n) => (
                  <option key={n} value={n}>
                    {n} {tr("gen.count")}
                  </option>
                ))}
              </select>
              {targetType === "asset" && (
                <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={useRefs}
                    onChange={(e) => setUseRefs(e.target.checked)}
                    className="accent-[var(--primary)]"
                  />
                  {tr("gen.useRefs")}
                </label>
              )}
            </div>

            <div className="generation-prompt-fields">
              {onChooseReferences && (
                <Button variant="outline" onClick={onChooseReferences}>
                  <Images size={15} />
                  选择参考素材
                </Button>
              )}
              <SkillPicker
                projectId={projectId}
                value={skills}
                onChange={setSkills}
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="generation-field-title">
                  {zh ? "创作指令" : "Your prompt"}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setPromptLibraryOpen(true)}
                >
                  {zh ? "从提示词库添加" : "Prompt library"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setOptimizing(true)}
                >
                  <Sparkles size={13} />
                  {zh ? "优化提示词" : "Optimize prompt"}
                </Button>
              </div>
              <Modal
                open={promptLibraryOpen}
                onClose={() => setPromptLibraryOpen(false)}
                title={zh ? "从提示词库添加" : "Prompt library"}
                width={960}
              >
                <PromptLibrary
                  projectId={projectId}
                  onInsert={(text) => {
                    const next = [prompt.trimEnd(), text]
                      .filter(Boolean)
                      .join("\n\n");
                    if (next.length > 10000) {
                      toast.push(
                        zh
                          ? "追加后超过 10000 字，请先缩短当前指令。"
                          : "Prompt exceeds 10000 characters.",
                        "error",
                      );
                      return;
                    }
                    setPrompt(next);
                    setPromptLibraryOpen(false);
                    toast.push(
                      zh ? "已追加到创作指令" : "Added to prompt",
                      "success",
                    );
                  }}
                />
              </Modal>
              <label className="block text-xs text-muted-foreground">
                {zh
                  ? "本次生成指令（留空使用镜头 / 资产提示词）"
                  : "Prompt override (leave empty to use source prompt)"}
                <textarea
                  aria-label={zh ? "本次生成指令" : "Generation prompt"}
                  className="generation-prompt"
                  rows={5}
                  maxLength={10000}
                  placeholder={
                    zh
                      ? "描述主体、动作、环境与光线…"
                      : "Describe the subject, action, setting and light…"
                  }
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              </label>
              <div className="generation-draft-status">
                <span className={storageError ? "text-warning" : ""}>
                  {storageError
                    ? zh
                      ? "无法保存本机草稿，请勿关闭页面"
                      : "Cannot save locally. Keep this page open."
                    : prompt
                      ? zh
                        ? "本机草稿 · 自动保留"
                        : "Local draft · saved automatically"
                      : zh
                        ? "留空时沿用对象的提示词"
                        : "Leave empty to use the object's prompt"}
                </span>
                <span>{prompt.length}/10000</span>
              </div>
              {type === "image" && caps?.features.includes("img2img") && (
                <label className="block text-xs">
                  {zh ? "参考已有图片进行 AI 重绘" : "AI edit source"}
                  <select
                    className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
                    value={sourceId}
                    onChange={(e) => setSourceId(e.target.value)}
                  >
                    <option value="">{zh ? "不指定" : "None"}</option>
                    {gens
                      ?.filter(
                        (g) => g.output_type === "image" && g.output_blob_hash,
                      )
                      .map((g, i) => (
                        <option key={g.id} value={g.id}>
                          {zh ? "图片" : "Image"} {i + 1} · {g.id.slice(0, 8)}
                        </option>
                      ))}
                  </select>
                  {reference?.output_blob_hash && (
                    <div className="generation-reference-preview">
                      <div className="h-16 w-24 shrink-0 overflow-hidden rounded-lg">
                        <MediaImage
                          src={blobUrl(projectId, reference.output_blob_hash)}
                          alt={zh ? "重绘参考" : "Edit reference"}
                        />
                      </div>
                      <div>
                        <span className="block text-xs">
                          {zh
                            ? "将基于此图片继续创作"
                            : "Continue from this image"}
                        </span>
                        <span className="text-[10px] text-faint">
                          {reference.id.slice(0, 8)}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="ml-auto text-xs text-muted-foreground"
                        onClick={() => setSourceId("")}
                      >
                        {zh ? "移除" : "Remove"}
                      </button>
                    </div>
                  )}
                </label>
              )}
              {(type === "video" ||
                !!caps?.param_schema.size ||
                !!caps?.param_schema.quality) && (
                <div className="space-y-3">
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                    {(type === "image"
                      ? ["size", "quality"]
                      : singleVideo
                        ? ["duration", "size"]
                        : ["duration", "aspect_ratio", "resolution"]
                    ).map((name) => {
                      const values = caps?.param_schema[name]?.enum || [];
                      return (
                        <label key={name} className="text-xs">
                          {zh
                            ? {
                                duration: "时长（秒）",
                                size: "画面尺寸",
                                quality: "图片质量",
                                aspect_ratio: "画面比例",
                                resolution: "清晰度",
                              }[name]
                            : name}
                          <select
                            aria-label={
                              zh
                                ? {
                                    duration: "时长（秒）",
                                    size: "画面尺寸",
                                    quality: "图片质量",
                                    aspect_ratio: "画面比例",
                                    resolution: "清晰度",
                                  }[name]
                                : name
                            }
                            className="mt-1 h-9 w-full rounded-md border bg-bg px-2 disabled:opacity-50"
                            disabled={!values.length}
                            value={controls[name] ?? ""}
                            onChange={(e) =>
                              setControls((c) => {
                                const n = { ...c };
                                if (!e.target.value) delete n[name];
                                else
                                  n[name] =
                                    name === "duration"
                                      ? Number(e.target.value)
                                      : e.target.value;
                                return n;
                              })
                            }
                          >
                            <option value="">
                              {values.length
                                ? zh
                                  ? "供应商默认"
                                  : "Provider default"
                                : zh
                                  ? "未接入"
                                  : "Unavailable"}
                            </option>
                            {values.map((v) => (
                              <option key={v} value={v}>
                                {v}
                              </option>
                            ))}
                          </select>
                        </label>
                      );
                    })}
                  </div>
                  {type === "video" && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {[
                        ["first_frame", firstId, setFirstId],
                        ["last_frame", lastId, setLastId],
                      ].map(([feature, value, setter]) => (
                        <label key={feature as string} className="text-xs">
                          {zh
                            ? feature === "first_frame"
                              ? "首帧图片"
                              : "尾帧图片"
                            : (feature as string)}
                          <select
                            aria-label={
                              zh
                                ? feature === "first_frame"
                                  ? "首帧图片"
                                  : "尾帧图片"
                                : (feature as string)
                            }
                            className="mt-1 h-9 w-full rounded-md border bg-bg px-2 disabled:opacity-50"
                            disabled={
                              !caps?.features.includes(feature as string)
                            }
                            value={value as string}
                            onChange={(e) =>
                              (setter as (v: string) => void)(e.target.value)
                            }
                          >
                            <option value="">
                              {caps?.features.includes(feature as string)
                                ? zh
                                  ? "不指定"
                                  : "None"
                                : zh
                                  ? "供应商未接入"
                                  : "Unavailable"}
                            </option>
                            {gens
                              ?.filter(
                                (g) =>
                                  g.output_type === "image" &&
                                  g.output_blob_hash,
                              )
                              .map((g, i) => (
                                <option key={g.id} value={g.id}>
                                  {zh ? "图片" : "Image"} {i + 1} ·{" "}
                                  {g.id.slice(0, 8)}
                                </option>
                              ))}
                          </select>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {provider === "mock" && (
                <p className="text-xs text-muted-foreground">
                  {zh
                    ? "Mock 输出带标记的测试素材，用于检查流程，不会根据指令创作画面。"
                    : "Mock produces labelled test media for workflow checks; it does not create scenes from prompts."}
                </p>
              )}
              {provider === "jimeng" && (
                <p className="text-xs text-muted-foreground">
                  {zh
                    ? "可用的时长、比例和首尾帧取决于项目中已接通的视频服务。"
                    : "Available durations, formats and reference frames depend on this project's connected video service."}
                </p>
              )}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label
                  className={`studio-link cursor-pointer text-xs ${upload.isPending ? "pointer-events-none opacity-50" : ""}`}
                >
                  {upload.isPending
                    ? zh
                      ? "上传中…"
                      : "Uploading…"
                    : zh
                      ? "上传图片 / MP4（≤100 MB）"
                      : "Upload image / MP4 (≤100 MB)"}
                  <input
                    type="file"
                    accept="image/*,video/mp4"
                    className="sr-only"
                    disabled={upload.isPending}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        if (file.size > 100 * 1024 * 1024)
                          toast.push(
                            zh ? "文件不能超过 100 MB" : "File exceeds 100 MB",
                            "error",
                          );
                        else upload.mutate(file);
                      }
                      e.target.value = "";
                    }}
                  />
                </label>
                <Link className="studio-link text-xs" to={`${base}/tasks`}>
                  {zh ? "任务中心" : "Task center"} →
                </Link>
              </div>
            </div>
            <div className="generation-submit-row">
              <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={
                    estimate.isPending ||
                    !caps ||
                    !providerOptions.includes(provider)
                  }
                  onClick={() => estimate.mutate(payload)}
                >
                  {estimate.isPending ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : null}
                  {tr("gen.estimate")}
                </Button>
                <span>
                  {est !== null
                    ? `≈ ${est} ${tr("gen.points")}`
                    : quota
                      ? `${tr("gen.quota")} ${quota.used_cost}/${quota.limit_cost}`
                      : ""}
                </span>
              </div>
              <Button
                className="w-full !h-11"
                onClick={() => generate.mutate()}
                disabled={
                  generate.isPending ||
                  !caps ||
                  !providerOptions.includes(provider)
                }
              >
                {generate.isPending ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Sparkles size={16} />
                )}
                {generate.isPending
                  ? zh
                    ? "正在提交…"
                    : "Submitting…"
                  : tr("gen.gen")}
              </Button>
            </div>
          </fieldset>
          {lastJob && (
            <JobStatus key={lastJob} projectId={projectId} jobId={lastJob} />
          )}
        </section>
        <section
          className="generation-results"
          aria-label={zh ? "创作结果" : "Creative results"}
        >
          <div className="generation-section-heading">
            <span className="generation-section-icon">
              <Images size={18} />
            </span>
            <div>
              <h3>
                {zh ? "作品与版本" : "Results & versions"}{" "}
                <span className="text-xs font-normal text-faint">
                  {gens?.length ?? "—"}
                </span>
              </h3>
              <p>
                {zh
                  ? "预览、比较，选择最合适的那一版。"
                  : "Preview, compare and choose the right version."}
              </p>
            </div>
          </div>
          <div
            className="generation-result-filters"
            role="group"
            aria-label={zh ? "筛选生成结果" : "Filter results"}
          >
            {[
              ["all", zh ? "全部" : "All"],
              ["image", zh ? "图片" : "Images"],
              ["video", zh ? "视频" : "Videos"],
              ["audio", zh ? "音频" : "Audio"],
              ["favorite", zh ? "收藏" : "Favorites"],
            ].map(([value, label]) => (
              <button
                key={value}
                aria-pressed={resultFilter === value}
                onClick={() => setResultFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
          {editing && (
            <ImageRefiner
              key={editing.id}
              projectId={projectId}
              generation={editing}
              onClose={() => setEditing(null)}
              onJob={rememberJob}
            />
          )}

          {videoTools && (
            <VideoTools
              projectId={projectId}
              generation={videoTools}
              onClose={() => setVideoTools(null)}
              onJob={rememberJob}
            />
          )}
          {characterTools && (
            <CharacterTools
              projectId={projectId}
              generation={characterTools}
              provider={provider}
              onClose={() => setCharacterTools(null)}
              onJob={rememberJob}
            />
          )}
          {gensError ? (
            <div role="alert" className="studio-empty">
              <p>
                {zh
                  ? "作品暂时无法加载，已有版本不会丢失。"
                  : "Results could not be loaded. Your versions are safe."}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void reloadGens()}
              >
                {zh ? "重试" : "Retry"}
              </Button>
            </div>
          ) : gensLoading ? (
            <div role="status" className="studio-empty">
              <Loader2 size={20} className="animate-spin" />
              {zh ? "正在读取作品…" : "Loading results…"}
            </div>
          ) : gens && gens.length > 0 ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
                <p className="text-xs text-muted-foreground">
                  {zh
                    ? "选择两个图片版本，并排比较后再采用。"
                    : "Choose two image versions to compare before selecting."}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={compareIds.length !== 2}
                  onClick={() => setComparing(true)}
                >
                  {zh
                    ? `比较版本 (${compareIds.length}/2)`
                    : `Compare (${compareIds.length}/2)`}
                </Button>
              </div>
              {comparing && (
                <Modal
                  open
                  onClose={() => setComparing(false)}
                  title={zh ? "图片版本比较" : "Compare image versions"}
                  width={1100}
                >
                  <div className="grid gap-4 md:grid-cols-2">
                    {compareIds
                      .map((id) => gens.find((g) => g.id === id))
                      .filter((g): g is Generation => !!g)
                      .map((g) => (
                        <div
                          key={g.id}
                          className="min-w-0 space-y-3 rounded-lg border p-3"
                        >
                          <ZoomableImage
                            src={blobUrl(projectId, g.output_blob_hash!)}
                            filename={`compare-${g.id}`}
                            className="aspect-square w-full bg-elevated object-contain"
                          />
                          <p className="text-xs text-muted-foreground">
                            {g.provider} · {g.id.slice(0, 8)}
                            {g.input_refs?.source_generation_id
                              ? ` · ${zh ? "来源" : "Source"} ${g.input_refs.source_generation_id.slice(0, 8)}`
                              : ""}
                          </p>
                          <p className="max-h-32 overflow-auto whitespace-pre-wrap break-words text-xs leading-6">
                            {g.prompt_snapshot ||
                              (zh
                                ? "无生成提示词（上传素材）"
                                : "No generation prompt (uploaded media)")}
                          </p>
                          <Button
                            size="sm"
                            variant={g.is_selected ? "outline" : "default"}
                            disabled={g.is_selected || select.isPending}
                            onClick={() => select.mutate(g.id)}
                          >
                            {g.is_selected ? selectedLabel : selectLabel}
                          </Button>
                        </div>
                      ))}
                  </div>
                </Modal>
              )}
              <div className="generation-result-grid">
                {visibleGens.map((g) => (
                  <div
                    key={g.id}
                    data-generation-id={g.id}
                    className={`generation-result-card group relative overflow-hidden rounded-xl border bg-card transition-all ${
                      g.is_selected
                        ? "border-primary  ring-1 ring-primary"
                        : "border-border hover:border-primary/50"
                    }`}
                  >
                    {/* 钦定金角标 */}
                    {g.is_selected && (
                      <span className="absolute left-0 top-0 z-10 rounded-br-lg bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground ">
                        {selectedLabel}
                      </span>
                    )}
                    {g.output_blob_hash &&
                      (g.output_type === "video" ? (
                        <div className="aspect-video">
                          <MediaVideo
                            src={blobUrl(projectId, g.output_blob_hash)}
                            label={`${tr("gen.video")} ${g.id.slice(0, 8)}`}
                          />
                        </div>
                      ) : g.output_type === "audio" ? (
                        <MediaAudio
                          src={blobUrl(projectId, g.output_blob_hash)}
                        />
                      ) : (
                        <ZoomableImage
                          src={blobUrl(projectId, g.output_blob_hash)}
                          filename={`${targetType}-${g.id.slice(0, 8)}`}
                          className="aspect-video w-full bg-elevated object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                        />
                      ))}
                    <div className="flex items-center justify-between gap-2 px-2 pt-2 text-[10px] text-muted-foreground">
                      <span>
                        {g.provider === "mock"
                          ? zh
                            ? "测试素材"
                            : "Test media"
                          : g.provider}
                      </span>
                      <span>{g.id.slice(0, 8)}</span>
                    </div>
                    {g.input_refs?.library_origin && (
                      <Link
                        className="studio-link block truncate px-2 pt-2 text-xs"
                        title={g.input_refs.library_origin.name}
                        to={`${base}/library?media=${g.input_refs.library_origin.media_id}&version=${g.input_refs.library_origin.version_id}&start=${g.input_refs.library_origin.start_ms}&end=${g.input_refs.library_origin.end_ms}`}
                      >
                        {zh ? "查看原片" : "View source"} ·{" "}
                        {g.input_refs.library_origin.name} · v
                        {g.input_refs.library_origin.ordinal}
                      </Link>
                    )}
                    <div className="generation-version-actions">
                      {g.prompt_snapshot && (
                        <button
                          className="studio-link text-xs"
                          disabled={generate.isPending}
                          onClick={() => void useVersionPrompt(g)}
                        >
                          {zh ? "复用指令" : "Reuse prompt"}
                        </button>
                      )}
                      {g.output_type === "image" && g.output_blob_hash && (
                        <button
                          className="studio-link text-xs"
                          disabled={
                            generate.isPending ||
                            !caps?.features.includes("img2img")
                          }
                          title={
                            !caps?.features.includes("img2img")
                              ? zh
                                ? "当前供应商不支持参考图创作"
                                : "Provider does not support image references"
                              : undefined
                          }
                          onClick={() => {
                            setType("image");
                            setSourceId(g.id);
                            composerRef.current?.scrollIntoView({
                              block: "nearest",
                            });
                          }}
                        >
                          <ImagePlus size={13} />
                          {zh ? "作为参考" : "Use as reference"}
                        </button>
                      )}
                    </div>
                    {g.output_type === "image" && g.output_blob_hash && (
                      <div className="flex flex-wrap">
                        <label className="flex w-full items-center gap-2 px-2 pt-2 text-xs text-muted-foreground">
                          <input
                            type="checkbox"
                            checked={compareIds.includes(g.id)}
                            disabled={
                              !compareIds.includes(g.id) &&
                              compareIds.length >= 2
                            }
                            onChange={(e) =>
                              setCompareIds((ids) =>
                                e.target.checked
                                  ? [...ids, g.id]
                                  : ids.filter((id) => id !== g.id),
                              )
                            }
                          />
                          {zh ? "加入比较" : "Compare"} · {g.id.slice(0, 8)}
                        </label>
                        <details className="generation-edit-tools">
                          <summary>{zh ? "编辑工具" : "Editing tools"}</summary>
                          <div>
                            <button
                              className="studio-link px-2 pt-2 text-xs"
                              onClick={() => setEditing(g)}
                            >
                              {zh ? "精修图片" : "Refine image"}
                            </button>
                            <button
                              className="studio-link px-2 pt-2 text-xs disabled:opacity-40"
                              disabled={!caps?.features.includes("inpaint")}
                              title={
                                caps?.features.includes("inpaint")
                                  ? undefined
                                  : zh
                                    ? "请先选择支持蒙版编辑的供应商"
                                    : "Select a mask-capable provider"
                              }
                              onClick={() => setInpainting(g)}
                            >
                              {zh ? "局部重绘" : "Masked edit"}
                            </button>
                            <button
                              className="studio-link px-2 pt-2 text-xs disabled:opacity-40"
                              disabled={!caps?.features.includes("img2img")}
                              title={
                                zh
                                  ? "需要支持参考图编辑的图片供应商"
                                  : "Requires an image editing provider"
                              }
                              onClick={() => setCharacterTools(g)}
                            >
                              {zh ? "角色工具" : "Character tools"}
                            </button>
                          </div>
                        </details>
                      </div>
                    )}
                    {g.output_type === "video" && g.output_blob_hash && (
                      <button
                        className="studio-link px-2 pt-2 text-xs"
                        onClick={() => setVideoTools(g)}
                      >
                        {zh ? "视频工具" : "Video tools"}
                      </button>
                    )}
                    {g.output_blob_hash && (
                      <a
                        className="studio-link inline-block px-2 pt-2 text-xs"
                        href={`${blobUrl(projectId, g.output_blob_hash)}&download=true`}
                        download
                      >
                        {zh ? "下载素材" : "Download"}
                      </a>
                    )}
                    {g.input_refs?.actual_media?.source_time_ms != null && (
                      <p className="px-2 pt-1 text-[10px] text-muted-foreground">
                        {zh ? "源视频" : "Source"}{" "}
                        {(
                          g.input_refs.actual_media.source_time_ms / 1000
                        ).toFixed(2)}{" "}
                        s
                      </p>
                    )}
                    {g.input_refs?.character_preset && (
                      <p className="px-2 pt-1 text-[10px] text-muted-foreground">
                        {zh
                          ? g.input_refs.character_preset.label
                          : g.input_refs.character_preset.label_en}{" "}
                        · {zh ? "来源" : "Source"}{" "}
                        {g.input_refs.source_generation_id?.slice(0, 8)}
                      </p>
                    )}
                    <div className="flex items-center justify-between px-2 py-1.5">
                      <button
                        disabled={favorite.isPending}
                        className={`transition ${g.is_favorite ? "text-primary" : "text-faint hover:text-foreground"}`}
                        onClick={() =>
                          favorite.mutate({ id: g.id, fav: !g.is_favorite })
                        }
                        title={tr("gen.favorite")}
                      >
                        <Star
                          className="h-3.5 w-3.5"
                          fill={g.is_favorite ? "currentColor" : "none"}
                        />
                      </button>
                      <button
                        className={`text-xs font-medium transition ${g.is_selected ? "text-primary" : "text-muted-foreground hover:text-primary"}`}
                        onClick={() => select.mutate(g.id)}
                        disabled={select.isPending || g.is_selected}
                        hidden={g.output_type === "audio"}
                      >
                        {g.is_selected ? selectedLabel : selectLabel}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              {visibleGens.length === 0 && (
                <div className="studio-empty">
                  <Images size={25} />
                  <p>{zh ? "没有符合筛选的作品" : "No matching results"}</p>
                  <button
                    className="text-xs underline"
                    onClick={() => setResultFilter("all")}
                  >
                    {zh ? "查看全部作品" : "Show all results"}
                  </button>
                </div>
              )}
            </>
          ) : (
            <div className="studio-empty generation-empty">
              <Images size={32} strokeWidth={1.3} />
              <p>{tr("gen.emptyVariants")}</p>
              <span className="text-xs text-faint">
                {zh
                  ? "从一段指令或一份参考开始，作品会保留在这里。"
                  : "Start with a prompt or reference. Your results will appear here."}
              </span>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

export default function GenerationPanel(
  props: Parameters<typeof GenerationPanelContent>[0],
) {
  const { me, loading } = useAuth();
  const preferences = useCreativeDefaults(props.projectId);
  if (preferences.isPending)
    return (
      <p className="p-4 text-sm text-muted-foreground">正在读取创作偏好…</p>
    );
  if (preferences.isError)
    return (
      <div role="alert" className="p-4 text-sm">
        创作偏好读取失败{" "}
        <button onClick={() => void preferences.refetch()}>重试</button>
      </div>
    );
  if (loading || !me) return null;
  return (
    <GenerationPanelContent
      key={`${me.user.id}.${props.projectId}.${props.targetType}.${props.targetId}`}
      {...props}
      defaults={preferences.data}
    />
  );
}
