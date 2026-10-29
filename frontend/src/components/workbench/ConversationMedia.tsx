import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type CSSProperties } from "react";
import { flushSync } from "react-dom";
import { Link } from "react-router-dom";
import {
  ImagePlus,
  X,
  Film,
  WandSparkles,
  ArrowUpRight,
  Maximize2,
  History,
  ChevronDown,
  Columns2,
  MessageSquare,
  RotateCcw,
  Scissors,
} from "lucide-react";
import { api, blobUrl, type ProviderConfig } from "@/lib/api";
import { usePersistentState } from "@/lib/usePersistentState";
import { MediaImage } from "@/components/MediaImage";
import { MediaVideo } from "@/components/MediaVideo";
import { Lightbox } from "@/components/ImageViewer";
import { Modal } from "@/components/ui/modal";
import { SelectField } from "@/components/ui/select-field";
import { ComposerParameters } from "./ComposerParameters";
import { referenceProblem } from "./referenceProblem";
import { referenceInput } from "./referenceInput";
import {
  ComposerReferences,
  ReferencePreview,
  type ComposerReference,
} from "./ComposerReferences";
import "./conversation-media-result.css";

export type CreationMode = "text" | "image" | "video";
export type MediaOutput = {
  id: string;
  type: "image" | "video";
  blob_hash: string;
  thumbnail_hash?: string;
};
export type MediaReferenceSelection = {
  /** Index within the current response, starting at zero. */
  index: number;
  startSeconds?: number;
  endSeconds?: number;
};
type ResultActions = {
  /** Restore the original request to the composer; this does not generate media. */
  onRecreate?: (output: MediaOutput) => void;
  /** Reference an existing result in a text discussion; ranges do not edit the file. */
  onReference?: (output: MediaOutput, selection: MediaReferenceSelection) => void;
};
type Caps = {
  modalities: string[];
  features: string[];
  max_reference_images: number;
  max_reference_videos?: number;
  reference_unavailable_reason?: string | null;
  param_schema: Record<string, { enum?: (string | number)[] }>;
};

export function useConversationMedia(
  projectId: string,
  key: string,
  mode: CreationMode,
  providers: ProviderConfig[],
) {
  const [choices, setChoices] = usePersistentState<Record<string, string>>(
    `${key}.mediaModels`,
    {},
  );
  const [parameters, setParameters] = usePersistentState<
    Record<string, Record<string, string | number>>
  >(`${key}.mediaParameters`, {});
  const [refs, setRefs] = usePersistentState<ComposerReference[]>(
    `${key}.references`,
    [],
  );
  const models = providers.filter(
    (p) =>
      p.kind === "generation" &&
      p.enabled &&
      !p.config.hidden_from_catalog &&
      p.config.modality === mode,
  );
  const unavailable =
    !!choices[mode] && !models.some((p) => p.provider_name === choices[mode]);
  const provider = unavailable
    ? ""
    : models.find((p) => p.provider_name === choices[mode])?.provider_name ||
      models.find((p) => p.config.is_default)?.provider_name ||
      models[0]?.provider_name ||
      "";
  const caps = useQuery({
    queryKey: ["capabilities", projectId, provider],
    queryFn: () =>
      api.get<Caps>(
        `/projects/${projectId}/providers/${provider}/capabilities`,
      ),
    enabled: mode !== "text" && !!provider,
  });
  const paramKey = `${mode}:${provider}`;
  const schema = caps.data?.param_schema || {};
  const controls = Object.fromEntries(
    Object.entries(schema)
      .filter(([, spec]) => spec.enum?.length)
      .map(([name, spec]) => [
        name,
        spec.enum!.includes(parameters[paramKey]?.[name])
          ? parameters[paramKey][name]
          : spec.enum![0],
      ]),
  );
  const references = refs.map((r) =>
    referenceInput(r, mode === "image" ? "reference" : r.role),
  );
  const problem = unavailable
    ? "原来选择的模型已不可用，请重新选择模型后发送。"
    : referenceProblem(caps.data, mode === "text" ? [] : refs.map(r => ({ ...r, role: mode === "image" ? "reference" : r.role })));
  return {
    models,
    provider,
    choices,
    parameters,
    restore: (
      kind: CreationMode,
      name: string,
      values: Record<string, string | number>,
    ) => {
      setChoices((v) => ({ ...v, [kind]: name }));
      setParameters((v) => ({ ...v, [`${kind}:${name}`]: values }));
    },
    refs,
    setRefs,
    schema,
    capabilities: caps.data,
    capabilitiesLoading: caps.isFetching,
    capabilitiesError: caps.error?.message || (unavailable ? "原来选择的模型已不可用，请重新选择模型后发送。" : null),
    videoReferencesAvailable: mode === "video" && !!caps.data?.features.includes("video_reference"),
    controls,
    choose: (value: string) => setChoices((v) => ({ ...v, [mode]: value })),
    parameter: (name: string, value: string | number) =>
      setParameters((v) => ({
        ...v,
        [paramKey]: { ...v[paramKey], [name]: value },
      })),
    ready:
      !!provider &&
      !!caps.data?.modalities.includes(mode) &&
      !caps.isFetching &&
      !caps.error &&
      !problem,
    error: problem || caps.error?.message,
    retry: () => void caps.refetch(),
    request: {
      provider,
      request_type: mode,
      provider_params: controls,
      references,
    },
  };
}

export function ConversationMediaControls({
  projectId,
  media,
  mode,
  disabled,
  showReferences = true,
}: {
  projectId: string;
  media: ReturnType<typeof useConversationMedia>;
  mode: CreationMode;
  disabled: boolean;
  showReferences?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <ComposerParameters
        controls={media.controls}
        schema={media.schema}
        disabled={disabled}
        onChange={media.parameter}
      />
      {showReferences && <button
        className="conversation-tool"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <ImagePlus size={17} />
        <span>
          参考素材{media.refs.length ? ` · ${media.refs.length}` : ""}
        </span>
      </button>}
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="选择参考素材"
        width={760}
      >
        {media.refs.length > 0 && (
          <div className="conversation-reference-strip">
            {media.refs.map((r) => (
              <div key={r.id}>
                <ReferencePreview projectId={projectId} item={r} />
                {r.kind === "video" && <span>参考视频</span>}
                <button
                  aria-label={`移除参考 ${r.title}`}
                  onClick={() =>
                    media.setRefs((v) => v.filter((x) => x.id !== r.id))
                  }
                >
                  <X size={14} />
                </button>
                {mode === "video" && r.kind !== "video" && (
                  <SelectField
                    label="参考用途"
                    value={r.role}
                    options={[
                      { value: "reference", label: "参考图" },
                      { value: "first_frame", label: "首帧" },
                      { value: "last_frame", label: "尾帧" },
                    ]}
                    onChange={(value) =>
                      media.setRefs((v) =>
                        v.map((x) =>
                          x.id === r.id
                            ? { ...x, role: value as ComposerReference["role"] }
                            : x,
                        ),
                      )
                    }
                  />
                )}
              </div>
            ))}
          </div>
        )}
        <ComposerReferences
          projectId={projectId}
          includeVideos={media.videoReferencesAvailable}
          selected={media.refs}
          pick={(item) =>
            media.setRefs((v) =>
              v.some((r) => r.id === item.id)
                ? v.filter((r) => r.id !== item.id)
                : v.length < 8
                  ? [...v, item]
                  : v,
            )
          }
        />
      </Modal>
    </>
  );
}

export function ConversationMediaResults({
  projectId,
  outputs,
  disabled,
  reuse,
  taskHref,
  returnTo,
  simulated,
  onRecreate,
  onReference,
}: {
  projectId: string;
  outputs: MediaOutput[];
  disabled: boolean;
  reuse: (output: MediaOutput, mode: CreationMode) => void;
  taskHref?: string;
  returnTo?: string;
  simulated?: boolean;
} & ResultActions) {
  const [comparing, setComparing] = useState(false);
  const canCompare = outputs.length === 2 && outputs[0].type === outputs[1].type;
  const leaveComparison = (action: () => void) => {
    // Finish the dialog's focus restoration before the action focuses the composer.
    flushSync(() => setComparing(false));
    action();
  };
  return (
    <>
    {canCompare && <div className="conversation-results-toolbar">
      <button type="button" onClick={() => setComparing(true)}><Columns2 size={15} />对比这两个结果</button>
    </div>}
    <div className="conversation-media-results" data-multiple={outputs.length > 1 || undefined}>
      {outputs.map((output, index) => (
        <ConversationMediaCard
          key={output.id}
          projectId={projectId}
          output={output}
          index={index}
          multiple={outputs.length > 1}
          outputCount={outputs.length}
          disabled={disabled}
          reuse={reuse}
          taskHref={taskHref}
          returnTo={returnTo}
          simulated={simulated}
          onRecreate={onRecreate}
          onReference={onReference}
          src={blobUrl(projectId, output.blob_hash)}
          poster={output.thumbnail_hash ? blobUrl(projectId, output.thumbnail_hash) : undefined}
        />
      ))}
    </div>
    {canCompare && <Modal open={comparing} onClose={() => setComparing(false)} title="对比本轮结果" width={1180}>
      <div className="conversation-result-comparison">
        {outputs.map((output, index) => <ConversationMediaCard key={output.id}
          projectId={projectId} output={output} index={index} multiple outputCount={outputs.length}
          disabled={disabled} reuse={(item, mode) => leaveComparison(() => reuse(item, mode))}
          taskHref={taskHref} returnTo={returnTo} simulated={simulated}
          onRecreate={onRecreate ? item => leaveComparison(() => onRecreate(item)) : undefined}
          onReference={onReference ? (item, selection) => leaveComparison(() => onReference(item, selection)) : undefined}
          src={blobUrl(projectId, output.blob_hash)}
          poster={output.thumbnail_hash ? blobUrl(projectId, output.thumbnail_hash) : undefined} />)}
      </div>
    </Modal>}
    </>
  );
}

export function ConversationMediaCard({
  projectId,
  output,
  index,
  multiple,
  outputCount,
  disabled,
  reuse,
  taskHref,
  returnTo,
  simulated,
  src,
  poster,
  onRecreate,
  onReference,
}: {
  projectId?: string;
  output: MediaOutput;
  index: number;
  multiple: boolean;
  outputCount?: number;
  disabled: boolean;
  reuse: (output: MediaOutput, mode: CreationMode) => void;
  taskHref?: string;
  returnTo?: string;
  simulated?: boolean;
  src: string;
  poster?: string;
} & ResultActions) {
  const [ratio, setRatio] = useState(16 / 9);
  const [preview, setPreview] = useState(false);
  const [rangeOpen, setRangeOpen] = useState(false);
  const [duration, setDuration] = useState(0);
  const [position, setPosition] = useState(0);
  const [rangeStart, setRangeStart] = useState("0");
  const [rangeEnd, setRangeEnd] = useState("");
  const isImage = output.type === "image";
  const resultLabel = `${isImage ? "图片" : "视频"} ${index + 1}${outputCount && multiple ? ` / ${outputCount}` : ""}`;
  const validRange = rangeStart.trim() !== "" && rangeEnd.trim() !== "" && Number.isFinite(Number(rangeStart))
    && Number.isFinite(Number(rangeEnd)) && Number(rangeStart) >= 0 && Number(rangeEnd) > Number(rangeStart)
    && Number(rangeEnd) <= duration;
  useEffect(() => { setDuration(0); setPosition(0); setRangeOpen(false); setRangeStart("0"); setRangeEnd(""); }, [src]);
  const dimensions = (width: number, height: number) => {
    if (width > 0 && height > 0) setRatio(width / height);
  };
  return (
    <figure
      className="conversation-media-result"
      onLoadedMetadataCapture={event => {
        const video = event.target;
        if (video instanceof HTMLVideoElement && Number.isFinite(video.duration) && video.duration > 0) {
          setDuration(video.duration);
          setRangeEnd(String(Math.floor(video.duration * 100) / 100));
        }
      }}
      onTimeUpdateCapture={event => {
        if (event.target instanceof HTMLVideoElement) setPosition(event.target.currentTime);
      }}
      style={
        {
          "--result-ratio": ratio,
          "--result-width": `${Math.min(800, Math.max(280, 460 * ratio))}px`,
        } as CSSProperties
      }
    >
      {isImage ? (
        <button
          className="conversation-image-preview"
          onClick={() => setPreview(true)}
          aria-label="放大查看图片"
        >
          <MediaImage
            src={src}
            alt={`本轮生成的${resultLabel}`}
            onDimensions={dimensions}
          />
          <span className="conversation-media-expand">
            <Maximize2 size={14} />
            查看原图
          </span>
        </button>
      ) : (
        <MediaVideo
          src={src}
          poster={poster}
          label={`本轮生成的${resultLabel}`}
          presentation="studio"
          onDimensions={dimensions}
        />
      )}
      <figcaption className="conversation-media-actions">
        <span className="conversation-result-caption">{simulated ? `模拟${resultLabel}` : resultLabel}</span>
        <div className="conversation-media-next">
          {onReference && <button type="button" disabled={disabled} onClick={() => onReference(output, { index })}>
            <MessageSquare size={14} />{isImage ? "讨论这张" : "讨论这段"}
          </button>}
          {isImage && <button type="button" disabled={disabled} onClick={() => reuse(output, "image")}>
              <WandSparkles size={14} />
              继续改图
          </button>}
          {!isImage && !onReference && <button type="button" disabled={disabled} onClick={() => reuse(output, "video")}><Film size={14} />用作视频参考</button>}
        </div>
        <details className="conversation-result-more">
          <summary>更多<ChevronDown size={13} /></summary>
          <div className="conversation-result-options">
          {(isImage || onReference) && <button type="button" disabled={disabled} onClick={() => reuse(output, "video")}>
            <Film size={14} />{isImage ? "生成视频" : "用作视频参考"}
          </button>}
          {onRecreate && <button type="button" disabled={disabled} title="将原要求放回输入框，调整后再发送" onClick={() => onRecreate(output)}><RotateCcw size={14} />调整原要求</button>}
          {!isImage && onReference && <button type="button" disabled={disabled || duration <= 0}
            title={duration > 0 ? "选择时间范围，带回对话讨论" : "视频加载后可选择片段"}
            onClick={() => { setRangeOpen(true); setRangeStart(String(Math.floor(position * 100) / 100)); }}>
            <Scissors size={14} />选择片段讨论
          </button>}
          {projectId && <Link
            to={`/projects/${projectId}/materials?generation=${output.id}${returnTo ? `&return=${encodeURIComponent(returnTo)}` : ""}`}
          >
            素材详情
            <ArrowUpRight size={14} />
          </Link>}
          {taskHref && (
            <Link to={taskHref} aria-label="查看生成任务" title="查看生成任务">
              <History size={15} />生成任务
            </Link>
          )}
          </div>
        </details>
      </figcaption>
      {rangeOpen && onReference && <form className="conversation-video-discussion" onSubmit={event => {
        event.preventDefault();
        if (!validRange || disabled) return;
        onReference(output, { index, startSeconds: Number(rangeStart), endSeconds: Number(rangeEnd) });
        setRangeOpen(false);
      }}>
        <div className="conversation-video-range">
          <label>开始（秒）<input aria-label={`${resultLabel}讨论开始秒数`} type="number" min="0" max={duration} step="0.01" value={rangeStart} onChange={event => setRangeStart(event.target.value)} /></label>
          <label>结束（秒）<input aria-label={`${resultLabel}讨论结束秒数`} type="number" min="0" max={duration} step="0.01" value={rangeEnd} onChange={event => setRangeEnd(event.target.value)} /></label>
        </div>
        <p>只标记讨论范围，视频文件保持原样。</p>
        <div className="conversation-video-range-actions">
          <button type="submit" disabled={disabled || !validRange}>带入对话</button>
          <button type="button" onClick={() => setRangeOpen(false)}>取消</button>
        </div>
      </form>}
      {preview && (
        <Lightbox
          src={src}
          alt="生成图片预览"
          filename="生成图片"
          onClose={() => setPreview(false)}
        />
      )}
    </figure>
  );
}
