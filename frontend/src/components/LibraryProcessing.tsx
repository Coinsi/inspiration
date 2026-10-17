import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Film,
  LoaderCircle,
  RotateCcw,
  ScanSearch,
  Square,
} from "lucide-react";
import { api } from "@/lib/api";
import type { MediaType } from "@/lib/library";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import "./library-processing.css";

type ProviderId = "wise" | "mediacms";
type Capability = "visual" | "audio" | "preview" | "speech" | "face" | "object";
export type LibraryProviders = {
  enabled: boolean;
  automatic: boolean;
  providers: {
    id: ProviderId;
    name: string;
    configured: boolean;
    capabilities: string[];
    media_capabilities?: Partial<Record<MediaType, string[]>>;
    optional_models?: Partial<
      Record<
        "face" | "object",
        { configured: boolean; ready: boolean; reason: string | null }
      >
    >;
  }[];
};
type ProcessingRun = {
  id: string;
  version_id: string;
  media_id?: string;
  media_name?: string;
  version_ordinal?: number;
  provider: ProviderId;
  operation?: "process" | "delete";
  capabilities: string[];
  status: string;
  progress: { phase?: string };
  capability_status?: Record<string, { state: string; vector_count?: number }>;
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
};
const activeStates = ["queued", "processing", "waiting"];

export function useLibraryProviders(projectId: string) {
  return useQuery({
    queryKey: ["library-processing-providers", projectId],
    queryFn: () =>
      api.get<LibraryProviders>(
        `/projects/${projectId}/library/processing/providers`,
      ),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}

export function optionalModelReason(
  reason: string | null | undefined,
  zh: boolean,
) {
  const labels: Record<string, [string, string]> = {
    capability_disabled: ["尚未启用", "Not enabled"],
    service_unavailable: ["暂时无法核对模型状态", "Model status unavailable"],
    service_not_configured: [
      "尚未连接处理服务",
      "Processing service is not connected",
    ],
    model_manifest_missing: [
      "尚未准备本地模型清单",
      "Local model manifest is missing",
    ],
    model_manifest_invalid: [
      "模型清单或固定版本不符",
      "Model manifest or pinned version mismatch",
    ],
    model_file_missing: ["模型文件尚未准备完整", "Model files are incomplete"],
    model_hash_mismatch: ["模型文件校验不一致", "Model file checksum mismatch"],
    model_layout_mismatch: [
      "模型缓存布局需要核对",
      "Model cache layout needs review",
    ],
    model_revision_mismatch: [
      "模型缓存版本不符",
      "Model cache revision mismatch",
    ],
    dependency_manifest_invalid: [
      "模型依赖清单不完整",
      "Model dependency manifest is incomplete",
    ],
    dependency_missing: [
      "模型运行依赖尚未安装",
      "Model dependencies are missing",
    ],
    dependency_mismatch: [
      "模型运行依赖版本不符",
      "Model dependency version mismatch",
    ],
    offline_mode_required: [
      "需要配置离线运行",
      "Offline execution is required",
    ],
    license_use_unspecified: [
      "尚未记录使用用途与许可核对",
      "Usage and license review are not recorded",
    ],
    license_record_missing: [
      "缺少对应用途的许可依据",
      "A license reference for this use is missing",
    ],
    license_scope_mismatch: [
      "许可记录与使用用途不符",
      "License record does not match the intended use",
    ],
    model_validation_required: [
      "尚未完成真实模型验收",
      "Real inference validation is still required",
    ],
    model_device_unspecified: [
      "物体模型尚未指定运行设备",
      "Object model execution device is not specified",
    ],
    model_device_unavailable: [
      "物体模型的运行设备当前不可用",
      "Object model execution device is unavailable",
    ],
    model_fingerprint_mismatch: [
      "模型配置已变化，需要重新验收或重建索引",
      "Model configuration changed; validate again or rebuild the index",
    ],
  };
  return (labels[reason || ""] || [
    "尚未完成模型验收",
    "Model validation is incomplete",
  ])[zh ? 0 : 1];
}

export function OptionalModelsStatus({
  provider,
}: {
  provider: LibraryProviders["providers"][number] | undefined;
}) {
  const zh = useI18n().lang === "zh";
  if (!provider) return null;
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer py-2">
        {zh ? "人脸与物体检索（可选）" : "Face & object search (optional)"}
      </summary>
      <div className="space-y-2 rounded-lg border p-3">
        {(["face", "object"] as const).map((cap) => (
          <p key={cap}>
            {capabilityName(cap, zh)} ·{" "}
            {provider.optional_models?.[cap]?.ready === true
              ? zh
                ? "已就绪"
                : "Ready"
              : optionalModelReason(
                  provider.optional_models?.[cap]?.reason,
                  zh,
                )}
          </p>
        ))}
        <p className="leading-5">
          {zh
            ? "需要另行准备模型并完成真实验收。人脸模型的许可需按用途核对，连接服务或填写密钥不会自动启用。"
            : "Models need separate preparation and real inference validation. Review face-model licensing for your use; connecting a service does not enable them."}
        </p>
      </div>
    </details>
  );
}

function capabilityName(capability: string, zh: boolean) {
  const names: Record<string, [string, string]> = {
    visual: ["画面检索", "Visual search"],
    audio: ["声音检索", "Sound search"],
    preview: ["预览与转码", "Preview & transcode"],
    speech: ["字幕转写", "Transcription"],
    face: ["人脸检索", "Face search"],
    object: ["物体检索", "Object search"],
  };
  return names[capability]?.[zh ? 0 : 1] || capability;
}

function phaseName(run: ProcessingRun, zh: boolean) {
  if (run.operation === "delete" && run.status === "ready")
    return zh ? "清理完成" : "Cleanup complete";
  if (run.progress.phase === "canceling" && activeStates.includes(run.status))
    return run.provider === "mediacms"
      ? zh
        ? "等待当前处理步骤结束后清理"
        : "Waiting for the current step, then cleaning up"
      : zh
        ? "清理检索索引"
        : "Cleaning up search index";
  if (run.cancel_requested && activeStates.includes(run.status))
    return run.provider === "mediacms"
      ? zh
        ? "等待当前处理步骤结束后清理"
        : "Waiting for the current step, then cleaning up"
      : zh
        ? "正在停止"
        : "Stopping";
  const labels: Record<string, [string, string]> = {
    queued: ["等待处理", "Queued"],
    waiting: ["等待处理服务", "Waiting for service"],
    reconciling: ["核对远端任务", "Checking remote task"],
    processing: ["正在处理", "Processing"],
    preparing: ["准备素材", "Preparing media"],
    uploading: ["传送分析素材", "Transferring media"],
    extracting: ["分析素材内容", "Analyzing media"],
    extracting_features: ["分析素材内容", "Analyzing media"],
    indexing: ["建立检索索引", "Building search index"],
    building_index: ["建立检索索引", "Building search index"],
    publishing: ["核验并发布索引", "Verifying search index"],
    transcoding: ["制作播放预览", "Preparing previews"],
    transcribing: ["识别字幕", "Transcribing"],
    interrupted: ["等待恢复", "Waiting to resume"],
    recovering: ["恢复处理任务", "Recovering task"],
    ready: ["处理完成", "Completed"],
    failed: ["处理失败", "Failed"],
    canceled: ["已停止", "Stopped"],
  };
  const phase = activeStates.includes(run.status)
    ? run.progress?.phase || run.status
    : run.status;
  return labels[phase]?.[zh ? 0 : 1] || (zh ? "正在处理" : "Processing");
}

function useProcessingAction(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api.post<ProcessingRun>(`/projects/${projectId}/library/${path}`, body),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: ["library-processing-runs", projectId],
      });
      void client.invalidateQueries({
        queryKey: ["library-search", projectId],
      });
      void client.invalidateQueries({ queryKey: ["library-item", projectId] });
    },
  });
}

function ProcessingRunRow({
  projectId,
  run,
  compact = false,
}: {
  projectId: string;
  run: ProcessingRun;
  compact?: boolean;
}) {
  const zh = useI18n().lang === "zh";
  const action = useProcessingAction(projectId);
  const active = activeStates.includes(run.status);
  const Icon = active
    ? run.status === "queued"
      ? Clock3
      : LoaderCircle
    : run.status === "ready"
      ? CheckCircle2
      : run.status === "failed"
        ? AlertCircle
        : Square;
  return (
    <div className={`library-processing-run ${compact ? "is-compact" : ""}`}>
      <span className={`library-processing-state is-${run.status}`}>
        <Icon
          size={compact ? 15 : 18}
          className={active && run.status !== "queued" ? "animate-spin" : ""}
        />
      </span>
      <div className="library-processing-run-body">
        <div className="library-processing-run-title">
          {!compact &&
            (run.media_id && run.operation !== "delete" ? (
              <Link
                to={`/projects/${projectId}/library?media=${run.media_id}&version=${run.version_id}`}
              >
                {run.media_name || (zh ? "查看素材" : "View media")}
                {run.version_ordinal ? ` · v${run.version_ordinal}` : ""}
              </Link>
            ) : (
              <span>{zh ? "素材处理任务" : "Media processing task"}</span>
            ))}
          <span>{run.provider === "wise" ? "WISE" : "MediaCMS"}</span>
          {run.operation === "delete" && (
            <span>
              {zh
                ? "清理检索与临时文件"
                : "Clean search index & temporary files"}
            </span>
          )}
          <span className={`library-processing-status is-${run.status}`}>
            {phaseName(run, zh)}
          </span>
        </div>
        <div className="library-processing-capabilities">
          {run.capabilities.map((cap) => (
            <span key={cap}>
              {capabilityName(cap, zh)}
              {run.capability_status?.[cap]?.state === "unavailable"
                ? zh
                  ? " · 无可用内容"
                  : " · No indexable content"
                : ""}
            </span>
          ))}
        </div>
        {run.error && (
          <p role="alert" className="text-danger">
            {run.error}
          </p>
        )}
        {action.error && (
          <p role="alert" className="text-danger">
            {action.error.message}
          </p>
        )}
      </div>
      <div className="library-processing-run-actions">
        <Link
          to={`/projects/${projectId}/tasks?kind=processing&task=${run.id}`}
        >
          {zh ? "任务与结果 ↗" : "Task & results ↗"}
        </Link>
        {active && run.operation !== "delete" && (
          <Button
            variant="ghost"
            size="sm"
            disabled={action.isPending || run.cancel_requested}
            title={
              run.provider === "mediacms"
                ? zh
                  ? "当前编码或转写步骤结束后清理临时文件"
                  : "Clean up temporary files after the current encoding or transcription step"
                : undefined
            }
            onClick={() =>
              action.mutate({ path: `processing/runs/${run.id}/cancel` })
            }
          >
            <Square size={13} />
            {run.cancel_requested
              ? run.provider === "mediacms"
                ? zh
                  ? "等待清理"
                  : "Awaiting cleanup"
                : zh
                  ? "停止中"
                  : "Stopping"
              : zh
                ? "停止"
                : "Stop"}
          </Button>
        )}
        {run.status === "failed" && (
          <Button
            variant="outline"
            size="sm"
            disabled={action.isPending}
            onClick={() =>
              action.mutate({ path: `processing/runs/${run.id}/retry` })
            }
          >
            <RotateCcw size={13} />
            {zh ? "重试" : "Retry"}
          </Button>
        )}
      </div>
    </div>
  );
}

export function LibraryProcessing({ projectId }: { projectId: string }) {
  const zh = useI18n().lang === "zh";
  const providers = useLibraryProviders(projectId);
  const [offset, setOffset] = useState(0);
  const runs = useQuery({
    queryKey: ["library-processing-runs", projectId, "all", offset],
    queryFn: () =>
      api.get<{ items: ProcessingRun[]; has_more: boolean }>(
        `/projects/${projectId}/library/processing/runs?offset=${offset}&limit=20`,
      ),
    refetchInterval: 5000,
  });
  return (
    <section className="library-processing">
      <div className="library-processing-intro">
        <div>
          <h2>{zh ? "让素材准备好被找到" : "Make your media discoverable"}</h2>
          <p>
            {zh
              ? "查看预览、字幕与内容检索的处理进度，检查结果或重试失败的任务。"
              : "Follow previews, captions and search preparation, review results or retry failed tasks."}
          </p>
        </div>
        <span className="library-processing-auto">
          {providers.data?.enabled && providers.data.automatic
            ? zh
              ? "新素材自动处理"
              : "Automatic processing"
            : zh
              ? "按需处理"
              : "Process on demand"}
        </span>
      </div>
      {providers.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          {zh ? "正在读取服务配置…" : "Loading service configuration…"}
        </p>
      ) : providers.isError ? (
        <p role="alert" className="text-sm text-danger">
          {zh ? "处理服务配置暂不可用" : "Service configuration unavailable"}{" "}
          <button
            className="studio-link"
            onClick={() => void providers.refetch()}
          >
            {zh ? "重试" : "Retry"}
          </button>
        </p>
      ) : (
        <div className="library-processing-providers">
          {providers.data.providers.map((provider) => {
            const configured = providers.data.enabled && provider.configured;
            const Icon = provider.id === "wise" ? ScanSearch : Film;
            return (
              <article
                key={provider.id}
                className="library-processing-provider"
              >
                <span className="library-processing-provider-icon">
                  <Icon size={23} />
                </span>
                <div>
                  <h3>
                    {provider.id === "wise"
                      ? provider.capabilities.includes("audio")
                        ? zh
                          ? "画面与声音"
                          : "Visuals & sound"
                        : zh
                          ? "画面检索"
                          : "Visual search"
                      : provider.capabilities.includes("speech")
                        ? zh
                          ? "预览与字幕"
                          : "Previews & captions"
                        : zh
                          ? "播放预览"
                          : "Playback previews"}
                    <span>{provider.name}</span>
                  </h3>
                  <p>
                    {provider.id === "wise"
                      ? provider.capabilities.includes("audio")
                        ? zh
                          ? "用描述或参考图找到画面，用文字或参考音频寻找声音。"
                          : "Find visuals by description or image, and sounds by description or reference audio."
                        : zh
                          ? "用描述或参考图找到画面。声音索引尚未启用。"
                          : "Find visuals by description or image. Sound indexing is not enabled."
                      : provider.capabilities.includes("speech")
                        ? zh
                          ? "制作可播放的预览，并识别音轨中的台词。"
                          : "Prepare playable previews and transcribe spoken dialogue."
                        : zh
                          ? "制作可播放的轻量预览。字幕转写尚未启用。"
                          : "Prepare lightweight playable previews. Transcription is not enabled."}
                  </p>
                  <span
                    className={`library-processing-service-status ${configured ? "is-configured" : ""}`}
                  >
                    {configured
                      ? zh
                        ? "已配置 · 以实际任务结果为准"
                        : "Configured · Verified by task results"
                      : providers.data.enabled
                        ? zh
                          ? "等待连接服务"
                          : "Service not connected"
                        : zh
                          ? "尚未启用"
                          : "Not enabled"}
                  </span>
                </div>
              </article>
            );
          })}
        </div>
      )}
      <div className="library-processing-queue-heading">
        <h3>{zh ? "处理记录" : "Processing history"}</h3>
        <span>
          {zh
            ? "在素材卡片的“处理与检索”中提交任务"
            : "Start tasks from “Processing & search” on a media card"}
        </span>
      </div>
      {runs.isPending ? (
        <div role="status" className="studio-empty">
          {zh ? "加载处理记录…" : "Loading tasks…"}
        </div>
      ) : runs.isError ? (
        <div role="alert" className="studio-empty">
          <p>{runs.error.message}</p>
          <Button variant="outline" onClick={() => void runs.refetch()}>
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      ) : runs.data.items.length ? (
        <div className="library-processing-runs">
          {runs.data.items.map((run) => (
            <ProcessingRunRow key={run.id} run={run} projectId={projectId} />
          ))}
        </div>
      ) : (
        <div className="studio-empty min-h-52 rounded-xl border border-dashed">
          <ScanSearch size={30} />
          <p>{zh ? "还没有处理任务" : "No processing tasks yet"}</p>
          <span>
            {zh
              ? "导入素材后，选择需要的画面、声音或字幕能力开始处理。"
              : "Import media, then choose visual, sound or caption processing."}
          </span>
        </div>
      )}
      {(offset > 0 || runs.data?.has_more) && (
        <div className="flex justify-between gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={!offset || runs.isFetching}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            {zh ? "上一页" : "Previous"}
          </Button>
          <span className="text-xs text-muted-foreground">
            {zh ? "第" : "Page"} {offset / 20 + 1} {zh ? "页" : ""}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!runs.data?.has_more || runs.isFetching}
            onClick={() => setOffset(offset + 20)}
          >
            {zh ? "下一页" : "Next"}
          </Button>
        </div>
      )}
    </section>
  );
}

export function LibraryProcessingControl({
  projectId,
  versionId,
  mediaType = "video",
  hasAudio,
}: {
  projectId: string;
  versionId: string;
  mediaType?: MediaType;
  hasAudio?: boolean | null;
}) {
  const zh = useI18n().lang === "zh";
  const noAudioTrack = mediaType === "video" && hasAudio === false;
  const needsAudio = (cap: Capability) => cap === "audio" || cap === "speech";
  const choicesFor = (id: ProviderId): Capability[] =>
    id === "wise"
      ? mediaType === "image"
        ? ["visual", "face", "object"]
        : mediaType === "audio"
          ? ["audio"]
          : ["visual", "audio", "face", "object"]
      : mediaType === "image"
        ? []
        : ["preview", "speech"];
  const [expanded, setExpanded] = useState(false);
  const [provider, setProvider] = useState<ProviderId>("wise");
  const [capabilities, setCapabilities] = useState<Capability[]>(
    choicesFor("wise").filter((cap) => cap !== "face" && cap !== "object"),
  );
  const providers = useLibraryProviders(projectId);
  const action = useProcessingAction(projectId);
  const runs = useQuery({
    queryKey: ["library-processing-runs", projectId, "version", versionId],
    queryFn: () =>
      api.get<{ items: ProcessingRun[]; has_more: boolean }>(
        `/projects/${projectId}/library/processing/runs?version_id=${versionId}&limit=10`,
      ),
    enabled: expanded,
    refetchInterval: expanded ? 5000 : false,
  });
  const selectedProvider = providers.data?.providers.find(
    (p) => p.id === provider,
  );
  const availableCapabilities =
    selectedProvider?.media_capabilities?.[mediaType] ??
    selectedProvider?.capabilities ??
    [];
  const selectedCapabilities = capabilities.filter(
    (cap) =>
      choicesFor(provider).includes(cap) &&
      !(noAudioTrack && needsAudio(cap)) &&
      availableCapabilities.includes(cap) &&
      ((cap !== "face" && cap !== "object") ||
        selectedProvider?.optional_models?.[cap]?.ready === true),
  );
  const configured =
    !!providers.data?.enabled && !!selectedProvider?.configured;
  const active = runs.data?.items.find(
    (run) => run.provider === provider && activeStates.includes(run.status),
  );
  const choices = choicesFor(provider).filter(
    (cap) =>
      (cap !== "face" && cap !== "object") ||
      availableCapabilities.includes(cap),
  );
  return (
    <div className="library-processing-control">
      <button
        className="library-processing-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        <ScanSearch size={15} />
        <span>{zh ? "处理与检索" : "Processing & search"}</span>
        <ChevronDown size={14} className={expanded ? "rotate-180" : ""} />
      </button>
      {expanded && (
        <div className="library-processing-control-content">
          <div
            className="library-processing-provider-picker"
            role="group"
            aria-label={zh ? "处理类型" : "Processing type"}
          >
            {(["wise", "mediacms"] as const)
              .filter((id) => choicesFor(id).length)
              .map((id) => (
                <button
                  key={id}
                  aria-pressed={provider === id}
                  onClick={() => {
                    setProvider(id);
                    setCapabilities(
                      id === "wise"
                        ? choicesFor(id).filter(
                            (cap) => cap !== "face" && cap !== "object",
                          )
                        : ["preview"],
                    );
                    action.reset();
                  }}
                >
                  {id === "wise"
                    ? zh
                      ? "内容检索"
                      : "Content search"
                    : zh
                      ? "预览与字幕"
                      : "Previews & captions"}
                </button>
              ))}
          </div>
          <div className="library-processing-options">
            {choices.map((cap) => (
              <label key={cap}>
                <input
                  type="checkbox"
                  checked={selectedCapabilities.includes(cap)}
                  disabled={
                    !configured ||
                    !availableCapabilities.includes(cap) ||
                    (noAudioTrack && needsAudio(cap)) ||
                    ((cap === "face" || cap === "object") &&
                      selectedProvider?.optional_models?.[cap]?.ready !==
                        true) ||
                    !!active ||
                    action.isPending
                  }
                  onChange={(event) =>
                    setCapabilities((value) =>
                      event.target.checked
                        ? [...value, cap]
                        : value.filter((item) => item !== cap),
                    )
                  }
                />
                {capabilityName(cap, zh)}
                {noAudioTrack && needsAudio(cap) ? (
                  <span className="text-muted-foreground">
                    {zh ? " · 无音轨" : " · No audio track"}
                  </span>
                ) : (
                  selectedProvider &&
                  !availableCapabilities.includes(cap) && (
                    <span className="text-muted-foreground">
                      {zh ? " · 尚未启用" : " · Not enabled"}
                    </span>
                  )
                )}
              </label>
            ))}
          </div>
          {provider === "wise" && mediaType !== "audio" && (
            <OptionalModelsStatus provider={selectedProvider} />
          )}
          {!configured && (
            <p className="text-xs text-muted-foreground">
              {providers.isError
                ? zh
                  ? "服务配置加载失败，请稍后重试。"
                  : "Unable to load service configuration."
                : providers.isPending
                  ? zh
                    ? "正在检查服务配置…"
                    : "Checking configuration…"
                  : zh
                    ? "此处理服务尚未启用或连接，请由管理员完成配置。"
                    : "An administrator needs to enable and connect this service."}
            </p>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={
              !configured ||
              !!active ||
              !selectedCapabilities.length ||
              action.isPending ||
              runs.isPending ||
              runs.isError
            }
            onClick={() =>
              action.mutate({
                path: `versions/${versionId}/process`,
                body: { provider, capabilities: selectedCapabilities },
              })
            }
          >
            {action.isPending ? (
              <LoaderCircle className="animate-spin" size={14} />
            ) : (
              <ScanSearch size={14} />
            )}
            {active
              ? zh
                ? "任务进行中"
                : "Task in progress"
              : zh
                ? "开始处理"
                : "Start processing"}
          </Button>
          {action.error && (
            <p role="alert" className="text-xs text-danger">
              {action.error.message}
            </p>
          )}
          {runs.isError && (
            <p role="alert" className="text-xs text-danger">
              {runs.error.message}{" "}
              <button
                className="studio-link"
                onClick={() => void runs.refetch()}
              >
                {zh ? "重试" : "Retry"}
              </button>
            </p>
          )}
          {runs.data?.items
            .filter((run) => run.provider === provider)
            .slice(0, 2)
            .map((run) => (
              <ProcessingRunRow
                key={run.id}
                run={run}
                projectId={projectId}
                compact
              />
            ))}
        </div>
      )}
    </div>
  );
}
