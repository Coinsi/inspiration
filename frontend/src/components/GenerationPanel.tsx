import { ImageRefiner } from "@/components/ImageRefiner";
import { ImageInpainter } from "@/components/ImageInpainter";
import { PromptOptimizer } from "@/components/PromptOptimizer";
import { JobStatus } from "@/components/JobStatus";
import { apiUpload } from "@/lib/api";
import { Link } from "react-router-dom";
import { MediaVideo } from "@/components/MediaVideo";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Star } from "lucide-react";
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
}: {
  projectId: string;
  targetType: "shot" | "asset";
  targetId: string;
}) {
  const base = `/projects/${projectId}`;
  const t = `${targetType}s`; // shots | assets
  const qc = useQueryClient();
  const toast = useToast();
  const { t: tr, lang } = useI18n();
  const zh = lang === "zh";
  const [type, setType] = useState("image");
  const [count, setCount] = useState(targetType === "asset" ? 4 : 3);
  const [useRefs, setUseRefs] = useState(true);
  const [est, setEst] = useState<number | null>(null);
  const [provider, setProvider] = usePersistentState<string>(
    `gen.provider.${projectId}`,
    "mock",
  );

  const { data: providerConfigs } = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${base}/providers`),
  });
  // 可选供应商:mock + 已配置且启用的生成供应商
  const providerOptions = [
    "mock",
    ...(providerConfigs ?? [])
      .filter(
        (p) =>
          p.kind === "generation" && p.enabled && p.provider_name !== "mock",
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
  const [controls, setControls] = useState<Record<string, string | number>>({});
  const [prompt, setPrompt] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [firstId, setFirstId] = useState("");
  const [lastId, setLastId] = useState("");
  const [editing, setEditing] = useState<Generation | null>(null);
  const [inpainting, setInpainting] = useState<Generation | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [lastJob, setLastJob] = usePersistentState<string>(
    `gen.job.${projectId}.${targetType}.${targetId}`,
    "",
  );
  useEffect(() => {
    if (caps && type === "video" && !supportsVideo) setType("image");
  }, [caps, type, supportsVideo]);
  useEffect(() => {
    setControls({});
    setSourceId("");
    setFirstId("");
    setLastId("");
  }, [provider, type]);
  useEffect(() => {
    setEst(null);
  }, [provider, type, controls, prompt, sourceId, firstId, lastId, count]);
  const payload = {
    provider,
    request_type: type,
    count,
    use_references: useRefs && !sourceId,
    prompt_override: prompt.trim() || undefined,
    provider_params: type === "video" ? controls : {},
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

  const { data: gens } = useQuery({
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
  };

  const estimate = useMutation({
    mutationFn: () =>
      api.post<{ points: number }>(
        `${base}/${t}/${targetId}/estimate`,
        payload,
      ),
    onSuccess: (d) => setEst(d.points),
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const generate = useMutation({
    mutationFn: () =>
      api.post<GenJob>(`${base}/${t}/${targetId}/generate`, payload),
    onSuccess: (j) => {
      setLastJob(j.id);
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

  return (
    <div className="space-y-3">
      {inpainting && (
        <ImageInpainter
          projectId={projectId}
          generation={inpainting}
          provider={provider}
          onClose={() => setInpainting(null)}
          onJob={setLastJob}
        />
      )}
      {optimizing && (
        <PromptOptimizer
          projectId={projectId}
          initial={prompt}
          mediaType={type}
          onClose={() => setOptimizing(false)}
          onApply={setPrompt}
        />
      )}
      {capsError && (
        <p role="alert" className="text-xs text-danger">
          {(capsError as Error).message}
        </p>
      )}
      <div className="flex items-center gap-2 flex-wrap rounded-lg border border-border bg-card p-2.5">
        <span className="text-xs text-muted-foreground">
          {targetType === "asset" ? tr("gen.concept") : tr("gen.gen")}
        </span>
        <select
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          title={tr("gen.provider")}
        >
          {providerOptions.map((p) => (
            <option key={p} value={p}>
              {PROVIDER_LABEL[p] ?? p}
            </option>
          ))}
        </select>
        <select
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="image">{tr("gen.image")}</option>
          {targetType === "shot" && supportsVideo && (
            <option value="video">{tr("gen.video")}</option>
          )}
        </select>
        <select
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
        >
          {[1, 2, 3, 4].map((n) => (
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
        <Button size="sm" variant="outline" onClick={() => estimate.mutate()}>
          {tr("gen.estimate")}
        </Button>
        {est !== null && (
          <span className="text-xs">
            ≈ {est} {tr("gen.points")}
          </span>
        )}
        <Button
          size="sm"
          onClick={() => generate.mutate()}
          disabled={
            generate.isPending || !caps || !providerOptions.includes(provider)
          }
        >
          {tr("gen.gen")}
        </Button>
        {quota && (
          <span className="ml-auto text-xs text-muted-foreground">
            {tr("gen.quota")} {quota.used_cost}/{quota.limit_cost}{" "}
            {tr("gen.points")}
          </span>
        )}
      </div>

      <div className="space-y-3 rounded-lg border border-border p-3">
        <div className="flex justify-end">
          <Button size="sm" variant="ghost" onClick={() => setOptimizing(true)}>
            {zh ? "优化提示词" : "Optimize prompt"}
          </Button>
        </div>
        <label className="block text-xs text-muted-foreground">
          {zh
            ? "本次生成指令（留空使用镜头 / 资产提示词）"
            : "Prompt override (leave empty to use source prompt)"}
          <textarea
            className="mt-1 w-full rounded-md border bg-bg p-2 text-sm text-foreground"
            rows={2}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </label>
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
                ?.filter((g) => g.output_type === "image" && g.output_blob_hash)
                .map((g, i) => (
                  <option key={g.id} value={g.id}>
                    {zh ? "图片" : "Image"} {i + 1} · {g.id.slice(0, 8)}
                  </option>
                ))}
            </select>
          </label>
        )}
        {type === "video" && (
          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {["duration", "aspect_ratio", "resolution"].map((name) => {
                const values = caps?.param_schema[name]?.enum || [];
                return (
                  <label key={name} className="text-xs">
                    {zh
                      ? {
                          duration: "时长（秒）",
                          aspect_ratio: "画面比例",
                          resolution: "清晰度",
                        }[name]
                      : name}
                    <select
                      aria-label={
                        zh
                          ? {
                              duration: "时长（秒）",
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
                    disabled={!caps?.features.includes(feature as string)}
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
                        (g) => g.output_type === "image" && g.output_blob_hash,
                      )
                      .map((g, i) => (
                        <option key={g.id} value={g.id}>
                          {zh ? "图片" : "Image"} {i + 1} · {g.id.slice(0, 8)}
                        </option>
                      ))}
                  </select>
                </label>
              ))}
            </div>
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
              ? "此适配器需要支持 /submit 与 /status 的兼容网关。参数与首尾帧需由网关配置声明支持。"
              : "Requires a compatible /submit and /status gateway with declared parameter and frame support."}
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
      {lastJob && (
        <JobStatus key={lastJob} projectId={projectId} jobId={lastJob} />
      )}
      {editing && (
        <ImageRefiner
          key={editing.id}
          projectId={projectId}
          generation={editing}
          onClose={() => setEditing(null)}
          onJob={setLastJob}
        />
      )}

      {gens && gens.length > 0 ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2.5">
          {gens.map((g) => (
            <div
              key={g.id}
              className={`group relative overflow-hidden rounded-xl border bg-card transition-all ${
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
              {g.output_type === "image" && g.output_blob_hash && (
                <div className="flex flex-wrap">
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
                </div>
              )}
              <div className="flex items-center justify-between px-2 py-1.5">
                <button
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
                >
                  {g.is_selected ? selectedLabel : selectLabel}
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="py-8 text-center text-sm text-muted-foreground">
          {tr("gen.emptyVariants")}
        </p>
      )}
    </div>
  );
}

export default function GenerationPanel(
  props: Parameters<typeof GenerationPanelContent>[0],
) {
  return (
    <GenerationPanelContent
      key={`${props.projectId}.${props.targetType}.${props.targetId}`}
      {...props}
    />
  );
}
