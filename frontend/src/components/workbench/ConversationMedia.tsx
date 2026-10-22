import { useQuery } from "@tanstack/react-query";
import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import {
  ImagePlus,
  X,
  Image,
  Film,
  Check,
  WandSparkles,
  ArrowUpRight,
  Maximize2,
  History,
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
import {
  ComposerReferences,
  ReferencePreview,
  type ComposerReference,
} from "./ComposerReferences";

export type CreationMode = "text" | "image" | "video";
export type MediaOutput = {
  id: string;
  type: "image" | "video";
  blob_hash: string;
  thumbnail_hash?: string;
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
  const references = refs.map((r) => ({
    generation_id: r.id,
    role: mode === "image" ? "reference" : r.role,
  }));
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
}: {
  projectId: string;
  media: ReturnType<typeof useConversationMedia>;
  mode: CreationMode;
  disabled: boolean;
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
      <button
        className="conversation-tool"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <ImagePlus size={17} />
        <span>
          参考素材{media.refs.length ? ` · ${media.refs.length}` : ""}
        </span>
      </button>
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
}: {
  projectId: string;
  outputs: MediaOutput[];
  disabled: boolean;
  reuse: (output: MediaOutput, mode: CreationMode) => void;
  taskHref?: string;
  returnTo?: string;
  simulated?: boolean;
}) {
  return (
    <div className="conversation-media-results">
      {outputs.map((output, index) => (
        <ConversationMediaCard
          key={output.id}
          projectId={projectId}
          output={output}
          index={index}
          multiple={outputs.length > 1}
          disabled={disabled}
          reuse={reuse}
          taskHref={taskHref}
          returnTo={returnTo}
          simulated={simulated}
        />
      ))}
    </div>
  );
}

function ConversationMediaCard({
  projectId,
  output,
  index,
  multiple,
  disabled,
  reuse,
  taskHref,
  returnTo,
  simulated,
}: {
  projectId: string;
  output: MediaOutput;
  index: number;
  multiple: boolean;
  disabled: boolean;
  reuse: (output: MediaOutput, mode: CreationMode) => void;
  taskHref?: string;
  returnTo?: string;
  simulated?: boolean;
}) {
  const [ratio, setRatio] = useState(16 / 9);
  const [preview, setPreview] = useState(false);
  const isImage = output.type === "image";
  const src = blobUrl(projectId, output.blob_hash);
  const dimensions = (width: number, height: number) => {
    if (width > 0 && height > 0) setRatio(width / height);
  };
  return (
    <div
      className="conversation-media-result"
      style={
        {
          "--result-ratio": ratio,
          "--result-width": `${Math.min(640, Math.max(260, 380 * ratio))}px`,
        } as CSSProperties
      }
    >
      <div className="conversation-result-heading">
        <span>
          {isImage ? <Image size={15} /> : <Film size={15} />}
          {isImage ? "图片" : "视频"}
          {multiple ? ` · ${index + 1}` : ""}
        </span>
        <span className="conversation-result-ready">
          <Check size={12} />
          {simulated ? "模拟结果" : "已生成"}
        </span>
      </div>
      {isImage ? (
        <button
          className="conversation-image-preview"
          onClick={() => setPreview(true)}
          aria-label="放大查看图片"
        >
          <MediaImage
            src={src}
            alt="本轮生成的图片"
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
          poster={
            output.thumbnail_hash
              ? blobUrl(projectId, output.thumbnail_hash)
              : undefined
          }
          label="本轮生成的视频"
          presentation="studio"
          onDimensions={dimensions}
        />
      )}
      <div className="conversation-media-actions">
        {isImage ? (
          <div className="conversation-media-next">
            <button disabled={disabled} onClick={() => reuse(output, "image")}>
              <WandSparkles size={14} />
              继续改图
            </button>
            <button disabled={disabled} onClick={() => reuse(output, "video")}>
              <Film size={14} />
              生成视频
            </button>
          </div>
        ) : <button disabled={disabled} onClick={() => reuse(output, "video")}><Film size={14} />用作视频参考</button>}
        <div className="conversation-media-links">
          <Link
            to={`/projects/${projectId}/materials?generation=${output.id}${returnTo ? `&return=${encodeURIComponent(returnTo)}` : ""}`}
          >
            素材详情
            <ArrowUpRight size={14} />
          </Link>
          {taskHref && (
            <Link to={taskHref} aria-label="查看生成任务" title="查看生成任务">
              <History size={15} />
            </Link>
          )}
        </div>
      </div>
      {preview && (
        <Lightbox
          src={src}
          alt="生成图片预览"
          filename="生成图片"
          onClose={() => setPreview(false)}
        />
      )}
    </div>
  );
}
