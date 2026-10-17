import { StudioSelect } from "@/components/ui/studio-select";
import { SelectField } from "@/components/ui/select-field";
import "./library-annotation.css";
import { SearchField } from "@/components/ui/search-field";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Search,
  ScanSearch,
  ImagePlus,
  X,
  AudioLines,
  Upload,
  Trash2,
  MessageSquareText,
} from "lucide-react";
import { api, blobUrl } from "@/lib/api";
import {
  mediaTime,
  mediaTypeName,
  type MediaType,
  type SearchBBox,
  type RegionMatch,
} from "@/lib/library";
import { LibraryRegionSelector } from "@/components/LibraryRegion";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { MediaImage } from "@/components/MediaImage";
import { useConfirm } from "@/components/ui/confirm";
import {
  useLibraryProviders,
  optionalModelReason,
  OptionalModelsStatus,
} from "@/components/LibraryProcessing";

type IndexRun = {
  id: string;
  status: string;
  total: number;
  completed: number;
  error: string | null;
  cancel_requested: boolean;
};
type Coverage = {
  versions: number;
  indexed: number;
  unindexed: number;
  runs: Record<string, IndexRun>;
  incompatible?: number;
};
export type SegmentResult = {
  id: string;
  media_id: string;
  version_id: string;
  ordinal: number;
  name: string;
  media_type: MediaType;
  start_ms: number | null;
  end_ms: number | null;
  thumbnail_hash: string | null;
  proxy_hash?: string | null;
  score: number | null;
  description: string;
  kind: string;
  provider?: string;
  match_type?: "frame" | "interval" | "image";
  matched_at_ms?: number | null;
  bbox?: SearchBBox;
  vector_id?: string;
  matches?: RegionMatch[];
};
type SearchInput = {
  query: string;
  image?: string;
  audio?: string;
  bbox?: SearchBBox;
  mode: "semantic" | "annotated";
  kind?: "visual" | "speech";
  offset: number;
  collapse_versions: boolean;
  engine: "local" | "wise";
  target: "visual" | "audio" | "face" | "object";
};
type SearchResult = {
  items: SegmentResult[];
  coverage: Coverage;
  total?: number;
  has_more?: boolean;
  candidate_limit?: number;
};
function useCoverage(projectId: string) {
  return useQuery({
    queryKey: ["library-index-status", projectId],
    queryFn: () =>
      api.get<Coverage>(`/projects/${projectId}/library/index-status`),
    refetchInterval: 5000,
  });
}

export function LibraryIndexControl({
  projectId,
  versionId,
}: {
  projectId: string;
  versionId: string;
}) {
  const zh = useI18n().lang === "zh",
    qc = useQueryClient(),
    coverage = useCoverage(projectId),
    run = coverage.data?.runs[versionId];
  const action = useMutation({
    mutationFn: (path: string) =>
      api.post(`/projects/${projectId}/library/${path}`),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["library-index-status", projectId],
      });
    },
  });
  const active = run && ["queued", "processing"].includes(run.status);
  return (
    <div className="space-y-2 border-t pt-3 text-xs">
      {coverage.isError ? (
        <button className="studio-link" onClick={() => void coverage.refetch()}>
          {zh ? "索引状态加载失败，重试" : "Retry index status"}
        </button>
      ) : active ? (
        <div className="space-y-2">
          <div className="flex justify-between gap-2">
            <span>
              {run.cancel_requested
                ? zh
                  ? "正在停止…"
                  : "Stopping…"
                : run.status === "queued"
                  ? zh
                    ? "等待内容索引"
                    : "Index queued"
                  : zh
                    ? `内容索引 ${run.completed} / ${run.total}`
                    : `Indexing ${run.completed} / ${run.total}`}
            </span>
            <button
              className="studio-link"
              disabled={action.isPending || run.cancel_requested}
              onClick={() => action.mutate(`indices/${run.id}/cancel`)}
            >
              {zh ? "停止" : "Stop"}
            </button>
          </div>
          <progress
            className="h-1.5 w-full accent-primary"
            aria-label={zh ? "索引进度" : "Index progress"}
            value={run.completed}
            max={run.total || 1}
          />
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-muted-foreground">
            {run?.status === "ready"
              ? zh
                ? `${run.total} 个片段可检索`
                : `${run.total} indexed segments`
              : run?.status === "failed"
                ? zh
                  ? "内容索引失败"
                  : "Index failed"
                : run?.status === "canceled"
                  ? zh
                    ? "内容索引已停止"
                    : "Index stopped"
                  : zh
                    ? "尚未建立内容索引"
                    : "Not indexed"}
          </span>
          <button
            className="studio-link"
            disabled={action.isPending || coverage.isPending}
            onClick={() => action.mutate(`versions/${versionId}/index`)}
          >
            {action.isPending
              ? zh
                ? "提交中…"
                : "Submitting…"
              : run?.status === "ready"
                ? zh
                  ? "重建索引"
                  : "Rebuild index"
                : zh
                  ? "建立内容索引"
                  : "Index content"}
          </button>
        </div>
      )}
      {run?.error && <p className="text-danger">{run.error}</p>}
      {action.error && (
        <p role="alert" className="text-danger">
          {action.error.message}
        </p>
      )}
    </div>
  );
}

export function LibrarySearch({
  projectId,
  onSelect,
}: {
  projectId: string;
  onSelect: (result: SegmentResult) => void;
}) {
  const zh = useI18n().lang === "zh",
    coverage = useCoverage(projectId);
  const providers = useLibraryProviders(projectId);
  const [chosenEngine, setEngine] = useState<"local" | "wise" | null>(null);
  const [target, setTarget] = useState<SearchInput["target"]>("visual");
  const [region, setRegion] = useState<SearchBBox | null>(null);
  const regional = target === "face" || target === "object";
  const targetLabel = {
    visual: zh ? "画面" : "visuals",
    audio: zh ? "声音" : "sound",
    face: zh ? "人脸" : "faces",
    object: zh ? "物体" : "objects",
  }[target];
  const wiseProvider = providers.data?.providers.find(
    (provider) => provider.id === "wise",
  );
  const wiseConfigured =
    !!providers.data?.enabled && !!wiseProvider?.configured;
  const engine =
    chosenEngine ??
    (wiseConfigured && wiseProvider?.capabilities.includes("visual")
      ? "wise"
      : "local");
  const wiseTargetEnabled =
    !!wiseProvider?.capabilities.includes(target) &&
    (!regional ||
      wiseProvider?.optional_models?.[target as "face" | "object"]?.ready ===
        true);
  const [query, setQuery] = useState(""),
    [mode, setMode] = useState<"semantic" | "annotated">("semantic"),
    [kind, setKind] = useState("");
  const [picture, setPicture] = useState<{
      data: string;
      mime: string;
      name: string;
    } | null>(null),
    [fileError, setFileError] = useState("");
  const [audio, setAudio] = useState<{
    data: string;
    mime: string;
    name: string;
  } | null>(null);
  const fileRead = useRef(0);
  const [submitted, setSubmitted] = useState<SearchInput | null>(null);
  const [collapse, setCollapse] = useState(true);
  const result = useQuery({
    queryKey: ["library-search", projectId, submitted],
    enabled: !!submitted,
    retry: false,
    queryFn: () =>
      api.post<SearchResult>(`/projects/${projectId}/library/search`, {
        ...submitted,
        limit: 24,
      }),
  });
  const submit = () => {
    if (
      mode === "semantic" &&
      engine === "wise" &&
      (!wiseConfigured || !wiseTargetEnabled)
    )
      return;
    if (mode === "semantic" && target === "face" && !picture) return;
    // Preserve an engine the user has used, including the basic visual engine,
    // when the provider status refreshes in the background.
    setEngine(engine);
    setSubmitted({
      query: mode === "semantic" && (picture || audio) ? "" : query,
      image:
        mode === "semantic" && target !== "audio" ? picture?.data : undefined,
      bbox:
        mode === "semantic" && regional && picture && region
          ? region
          : undefined,
      audio:
        mode === "semantic" && engine === "wise" && target === "audio"
          ? audio?.data
          : undefined,
      mode,
      kind:
        mode === "annotated" && kind
          ? (kind as "visual" | "speech")
          : undefined,
      offset: 0,
      collapse_versions: mode === "semantic" && collapse,
      engine: mode === "semantic" ? engine : "local",
      target,
    });
  };
  return (
    <div className="space-y-6">
      <div className="rounded-xl border bg-card p-4 sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <span className="rounded-lg bg-primary/10 p-2 text-primary">
            <ScanSearch className="h-6 w-6" />
          </span>
          <div>
            <h2 className="font-semibold">
              {zh ? "找到创作需要的那一刻" : "Find the moment your story needs"}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {zh
                ? "描述画面、寻找声音，或查找一句台词。播放命中的位置，选择片段用到创作中。"
                : "Find a visual, a sound or a line of dialogue. Preview a match and choose the range to use."}
            </p>
          </div>
        </div>
        <div className="mb-4 flex flex-wrap gap-2">
          {(["semantic", "annotated"] as const).map((m) => (
            <Button
              key={m}
              size="sm"
              variant={mode === m ? "default" : "ghost"}
              onClick={() => {
                setMode(m);
                fileRead.current++;
                setPicture(null);
                setRegion(null);
                setAudio(null);
                setFileError("");
                setSubmitted(null);
              }}
            >
              {m === "semantic"
                ? zh
                  ? "内容检索"
                  : "Content search"
                : zh
                  ? "字幕与标注"
                  : "Captions & annotations"}
            </Button>
          ))}
        </div>
        {mode === "semantic" && (
          <div className="mb-4 space-y-2">
            <div
              role="group"
              aria-label={zh ? "检索方式" : "Search method"}
              className="flex flex-wrap gap-2"
            >
              {(
                [
                  {
                    engine: "local",
                    target: "visual",
                    label: zh ? "基础画面" : "Basic visual",
                  },
                  {
                    engine: "wise",
                    target: "visual",
                    label: zh ? "WISE 画面" : "WISE visual",
                  },
                  {
                    engine: "wise",
                    target: "audio",
                    label: zh ? "WISE 声音" : "WISE sound",
                  },
                  {
                    engine: "wise",
                    target: "face",
                    label: zh ? "参考人脸" : "Reference face",
                  },
                  {
                    engine: "wise",
                    target: "object",
                    label: zh ? "物体区域" : "Objects",
                  },
                ] as const
              )
                .filter(
                  (option) =>
                    !["face", "object"].includes(option.target) ||
                    wiseProvider?.capabilities.includes(option.target),
                )
                .map((option) => (
                  <button
                    key={`${option.engine}-${option.target}`}
                    type="button"
                    disabled={
                      option.engine === "wise" &&
                      (!wiseProvider?.capabilities.includes(option.target) ||
                        ((option.target === "face" ||
                          option.target === "object") &&
                          wiseProvider?.optional_models?.[option.target]
                            ?.ready !== true))
                    }
                    title={
                      (option.target === "face" ||
                        option.target === "object") &&
                      wiseProvider?.optional_models?.[option.target]?.ready !==
                        true
                        ? optionalModelReason(
                            wiseProvider?.optional_models?.[option.target]
                              ?.reason,
                            zh,
                          )
                        : undefined
                    }
                    aria-pressed={
                      engine === option.engine && target === option.target
                    }
                    className={`rounded-full border px-3 py-1.5 text-xs transition disabled:cursor-not-allowed disabled:opacity-45 ${engine === option.engine && target === option.target ? "border-border-strong bg-elevated text-foreground" : "text-muted-foreground hover:bg-surface"}`}
                    onClick={() => {
                      setEngine(option.engine);
                      setTarget(option.target);
                      setSubmitted(null);
                      fileRead.current++;
                      setFileError("");
                      setRegion(null);
                      if (option.target !== "audio") setAudio(null);
                      if (option.target === "audio") setPicture(null);
                      if (option.target === "face") setQuery("");
                      if (option.engine === "wise" && picture) setQuery("");
                    }}
                  >
                    {option.label}
                  </button>
                ))}
            </div>
            <OptionalModelsStatus provider={wiseProvider} />
            {wiseProvider && !wiseProvider.capabilities.includes("audio") && (
              <p className="text-xs leading-6 text-muted-foreground">
                {zh ? "声音索引尚未启用" : "Sound indexing is not enabled"}
              </p>
            )}
            {regional && (
              <p className="text-xs leading-6 text-muted-foreground">
                {zh
                  ? target === "face"
                    ? "按参考脸寻找相似人脸。分数仅用于排序，不识别姓名或确认身份。"
                    : "描述要找的物体，或在参考图中框选目标。打开结果，核对图片区域或视频中的命中画面。"
                  : target === "face"
                    ? "Find similar faces from a reference. Scores rank candidates; they do not identify a person."
                    : "Describe an object or select it in a reference image. Open a result to inspect its region or matched video frame."}
              </p>
            )}
            {engine === "wise" && !wiseConfigured && (
              <p
                role="status"
                className="text-xs leading-6 text-muted-foreground"
              >
                {providers.isPending
                  ? zh
                    ? "正在读取检索服务配置…"
                    : "Checking search configuration…"
                  : providers.isError
                    ? zh
                      ? "无法读取检索服务配置，请稍后重试。"
                      : "Search configuration is unavailable."
                    : zh
                      ? "WISE 尚未启用或连接。管理员配置服务并完成素材处理后，可以在这里检索。"
                      : "WISE is not enabled or connected. An administrator must connect it and process media before searching."}
              </p>
            )}
          </div>
        )}
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <SearchField
            className="min-w-0 flex-1"
            label={zh ? "搜索素材内容" : "Search media content"}
            placeholder={
              mode === "semantic"
                ? target === "face"
                  ? zh
                    ? "请在下方选择人脸参考图片"
                    : "Choose a face reference image below"
                  : target === "object"
                    ? zh
                      ? "例如：红色杯子、背包，或框选参考图片中的物体"
                      : "A red cup, a backpack, or an object selected in a reference image"
                    : target === "audio"
                      ? zh
                        ? "例如：雨声、玻璃破碎、轻柔的钢琴"
                        : "Rain, breaking glass or soft piano"
                      : zh
                        ? "例如：雨中的街道，镜头跟随一个人向前走"
                        : "A person walking down a rainy street"
                : zh
                  ? "输入字幕、台词或画面标注中的关键词"
                  : "Search words in captions, dialogue or visual annotations"
            }
            value={query}
            onClear={() => setSubmitted(null)}
            disabled={mode === "semantic" && target === "face"}
            onValueChange={(value) => {
              setQuery(value);
              if (value.trim()) {
                fileRead.current++;
                setPicture(null);
                setRegion(null);
                setAudio(null);
              }
            }}
          />
          {mode === "annotated" && (
            <StudioSelect
              aria-label={zh ? "标注来源" : "Annotation source"}
              className="rounded-lg border bg-bg px-2 text-sm"
              value={kind}
              onValueChange={(selectedValue) => setKind(selectedValue)}
            >
              <option value="">{zh ? "全部标注" : "All annotations"}</option>
              <option value="visual">{zh ? "画面标注" : "Visual"}</option>
              <option value="speech">
                {zh ? "字幕与台词" : "Captions & dialogue"}
              </option>
            </StudioSelect>
          )}
          <Button
            type="submit"
            disabled={
              result.isFetching ||
              (mode === "semantic" && target === "face" && !picture) ||
              (mode === "semantic" &&
                engine === "wise" &&
                (!wiseConfigured || !wiseTargetEnabled)) ||
              (mode === "semantic" && !query.trim() && !picture && !audio)
            }
          >
            <Search className="h-4 w-4" />
            {result.isFetching
              ? zh
                ? "查找中…"
                : "Searching…"
              : zh
                ? "查找素材"
                : "Find media"}
          </Button>
        </form>
        {mode === "semantic" && (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={collapse}
                onChange={(e) => {
                  setCollapse(e.target.checked);
                  setSubmitted(null);
                }}
              />
              {regional
                ? zh
                  ? "按版本合并，保留所有命中区域"
                  : "Group by version, retain all regions"
                : zh
                  ? "每个版本只显示最佳匹配"
                  : "Best match per version"}
            </label>
            {target !== "audio" && (
              <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground hover:text-foreground">
                <ImagePlus className="h-4 w-4" />
                {regional
                  ? zh
                    ? "选择参考图片"
                    : "Choose a reference image"
                  : zh
                    ? "用图片找画面"
                    : "Search with an image"}
                <input
                  aria-label={zh ? "检索参考图片" : "Search reference image"}
                  type="file"
                  className="sr-only"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={async (e) => {
                    const f = e.target.files?.[0];
                    e.target.value = "";
                    if (!f) return;
                    const request = ++fileRead.current;
                    setPicture(null);
                    setRegion(null);
                    setAudio(null);
                    setSubmitted(null);
                    setFileError("");
                    if (
                      f.size > 2 * 1024 * 1024 ||
                      !["image/jpeg", "image/png", "image/webp"].includes(
                        f.type,
                      )
                    ) {
                      setFileError(
                        zh
                          ? "请选择2 MB以内的PNG、JPEG或WebP图片"
                          : "Choose a PNG, JPEG or WebP under 2 MB",
                      );
                      return;
                    }
                    try {
                      const data = await new Promise<string>(
                        (resolve, reject) => {
                          const reader = new FileReader();
                          reader.onload = () =>
                            resolve(String(reader.result).split(",")[1]);
                          reader.onerror = reject;
                          reader.readAsDataURL(f);
                        },
                      );
                      if (request !== fileRead.current) return;
                      setPicture({ data, mime: f.type, name: f.name });
                      setAudio(null);
                      setQuery("");
                      setSubmitted(null);
                    } catch {
                      if (request !== fileRead.current) return;
                      setFileError(
                        zh
                          ? "图片读取失败，请重新选择"
                          : "Unable to read image; select it again",
                      );
                    }
                  }}
                />
              </label>
            )}
            {target === "audio" && engine === "wise" && (
              <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground hover:text-foreground">
                <AudioLines className="h-4 w-4" />
                {zh ? "用音频找声音" : "Search with audio"}
                <input
                  aria-label={zh ? "检索参考音频" : "Search reference audio"}
                  type="file"
                  className="sr-only"
                  accept=".wav,.mp3,.flac,.m4a,.ogg"
                  onChange={async (event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (!file) return;
                    const request = ++fileRead.current;
                    setPicture(null);
                    setRegion(null);
                    setAudio(null);
                    setSubmitted(null);
                    setFileError("");
                    if (
                      file.size > 4 * 1024 * 1024 ||
                      !/\.(wav|mp3|flac|m4a|ogg)$/i.test(file.name)
                    ) {
                      setFileError(
                        zh
                          ? "请选择4 MB以内、30秒以内的WAV、MP3、FLAC、M4A或OGG片段。"
                          : "Choose a WAV, MP3, FLAC, M4A or OGG clip under 4 MB and 30 seconds.",
                      );
                      return;
                    }
                    try {
                      const data = await new Promise<string>(
                        (resolve, reject) => {
                          const reader = new FileReader();
                          reader.onload = () =>
                            resolve(String(reader.result).split(",")[1]);
                          reader.onerror = reject;
                          reader.readAsDataURL(file);
                        },
                      );
                      if (request !== fileRead.current) return;
                      setAudio({
                        data,
                        mime: file.type || "audio/mpeg",
                        name: file.name,
                      });
                      setPicture(null);
                      setQuery("");
                      setSubmitted(null);
                    } catch {
                      if (request !== fileRead.current) return;
                      setFileError(
                        zh
                          ? "音频读取失败，请重新选择。"
                          : "Unable to read the audio. Select it again.",
                      );
                    }
                  }}
                />
              </label>
            )}
            {audio && target === "audio" && (
              <div className="flex w-full flex-wrap items-center gap-3 rounded-lg border bg-muted/30 p-3">
                <AudioLines size={20} className="text-muted-foreground" />
                <span className="max-w-48 truncate text-xs">{audio.name}</span>
                <audio
                  controls
                  preload="metadata"
                  className="h-9 max-w-full"
                  src={`data:${audio.mime};base64,${audio.data}`}
                  aria-label={zh ? "试听检索参考音频" : "Preview search audio"}
                />
                <button
                  type="button"
                  aria-label={zh ? "移除检索音频" : "Remove search audio"}
                  onClick={() => {
                    fileRead.current++;
                    setAudio(null);
                  }}
                >
                  <X size={16} />
                </button>
                <span className="w-full text-xs text-muted-foreground">
                  {zh
                    ? "使用30秒以内的声音片段，最多4 MB；也可以直接输入声音描述。"
                    : "Use a clip up to 30 seconds and 4 MB, or type a sound description."}
                </span>
              </div>
            )}
            {picture && (
              <div className="flex items-center gap-2 rounded-md border p-1.5">
                <img
                  className="h-10 w-14 rounded object-cover"
                  src={`data:${picture.mime};base64,${picture.data}`}
                  alt={picture.name}
                />
                <span className="max-w-40 truncate text-xs">
                  {picture.name}
                </span>
                <button
                  aria-label={zh ? "移除检索图片" : "Remove search image"}
                  onClick={() => {
                    fileRead.current++;
                    setPicture(null);
                    setRegion(null);
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </div>
        )}
        {mode === "semantic" && regional && picture && (
          <LibraryRegionSelector
            key={picture.data}
            src={`data:${picture.mime};base64,${picture.data}`}
            label={picture.name}
            value={region}
            onChange={(box) => {
              setRegion(box);
              setSubmitted(null);
            }}
          />
        )}
        {fileError && (
          <p role="alert" className="mt-2 text-xs text-danger">
            {fileError}
          </p>
        )}
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          {mode === "semantic"
            ? target === "audio"
              ? zh
                ? "根据音轨寻找声音，不等同于搜索台词。台词请在“字幕与标注”中检索已保存的字幕。"
                : "Search acoustic content, not dialogue. Search saved captions under Captions & annotations."
              : zh
                ? "查找图片和视频中的相似画面。视频按采样画面检索，可能漏掉短暂内容；匹配分数仅供排序，请打开素材核对。"
                : "Find similar images and sampled video frames. Brief appearances may be missed. Match scores rank results; preview the source to verify."
            : zh
              ? "按关键词查找自动转写、导入的字幕和人工标注，可分页查看全部匹配。台词提到某物，不代表它出现在画面中。"
              : "Find words in automatic transcripts, imported captions and manual annotations. All matches are paginated. Spoken mentions do not establish visual presence."}
        </p>
      </div>
      {mode === "semantic" && (
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <span>
            {mode === "semantic" && engine === "wise"
              ? result.data?.coverage
                ? zh
                  ? `${result.data.coverage.indexed} / ${result.data.coverage.versions} 个版本已完成${targetLabel}索引 · ${result.data.coverage.unindexed} 个尚未完成`
                  : `${result.data.coverage.indexed} / ${result.data.coverage.versions} versions indexed for ${targetLabel}`
                : zh
                  ? "搜索时会核对当前能力的索引覆盖范围"
                  : "Search checks index coverage for the selected capability"
              : coverage.isPending
                ? zh
                  ? "正在读取索引覆盖情况…"
                  : "Loading index coverage…"
                : coverage.isError
                  ? zh
                    ? "索引覆盖情况暂不可用"
                    : "Index coverage unavailable"
                  : zh
                    ? `${coverage.data.indexed} / ${coverage.data.versions} 个视频版本已有索引 · ${coverage.data.unindexed} 个尚未完成`
                    : `${coverage.data.indexed} / ${coverage.data.versions} versions indexed · ${coverage.data.unindexed} unfinished`}
          </span>
          <span>
            {zh
              ? engine === "wise" && mode === "semantic"
                ? "WISE 内容检索 · 原片版本保持关联"
                : "基础检索 · 原片版本保持关联"
              : engine === "wise" && mode === "semantic"
                ? "WISE search · Linked to source versions"
                : "Basic search · Linked to source versions"}
          </span>
        </div>
      )}
      {result.data?.coverage.incompatible ? (
        <p className="text-xs text-warning">
          {zh
            ? `${result.data.coverage.incompatible} 个版本的索引使用旧模型，需要重建后才能参与这次检索。`
            : `${result.data.coverage.incompatible} versions use another model and need rebuilding for this search.`}
        </p>
      ) : null}
      {!submitted ? (
        <div className="studio-empty min-h-60 rounded-xl border border-dashed">
          <ScanSearch className="h-9 w-9" />
          <p>
            {mode === "annotated"
              ? zh
                ? "输入一句台词，找到它出现的位置"
                : "Find where a line is spoken"
              : target === "audio"
                ? zh
                  ? "描述你想找的声音"
                  : "Describe the sound you need"
                : target === "face"
                  ? zh
                    ? "选择一张参考图片，圈出要找的人脸"
                    : "Choose an image and select the face to find"
                  : zh
                    ? "试着描述一个人物、场景或动作"
                    : "Try describing a person, scene or action"}
          </p>
          <span className="max-w-lg text-center text-xs">
            {mode === "annotated"
              ? zh
                ? "完成字幕转写、导入 SRT 或保存标注后即可搜索，无需建立画面索引。"
                : "Search after transcription, importing SRT or saving annotations. No visual index is required."
              : zh
                ? engine === "wise"
                  ? "先在素材卡片的“处理与检索”中建立对应索引。未完成处理的素材不会参与搜索。"
                  : "先在视频卡片上建立基础画面索引。未索引的视频不会被当作“没有匹配”。"
                : "Index videos from their cards first. Unindexed videos are not treated as negative matches."}
          </span>
        </div>
      ) : result.isFetching ? (
        <div role="status" className="studio-empty">
          {zh ? "正在寻找相关片段…" : "Finding relevant segments…"}
        </div>
      ) : result.isError ? (
        <div role="alert" className="studio-empty">
          <p>{result.error.message}</p>
          <Button variant="outline" onClick={() => void result.refetch()}>
            {zh ? "重试搜索" : "Retry search"}
          </Button>
        </div>
      ) : (
        result.data && (
          <>
            {!result.data.items.length ? (
              <div className="studio-empty rounded-xl border">
                <p>
                  {mode === "annotated"
                    ? zh
                      ? "可以缩短关键词，或先为素材转写、导入字幕。自动转写可能存在错字。"
                      : "Try a shorter phrase or transcribe or import captions first. Automatic transcripts may contain errors."
                    : zh
                      ? "当前检索范围没有匹配片段"
                      : "No matches in the current indexed scope"}
                </p>
                <span className="text-xs">
                  {zh
                    ? "可以换一种描述，或为尚未处理的素材建立索引。"
                    : "Try another description or index the remaining media."}
                </span>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {result.data.items.map((r) => (
                  <button
                    key={r.id}
                    className="overflow-hidden rounded-xl border bg-card text-left transition hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                    onClick={() => onSelect(r)}
                  >
                    <div className="relative aspect-video">
                      {r.media_type === "audio" ? (
                        <div className="flex h-full flex-col items-center justify-center gap-2 bg-elevated text-muted-foreground">
                          <AudioLines size={44} strokeWidth={1.25} />
                          <span className="text-xs">
                            {zh ? "音频片段" : "Audio segment"}
                          </span>
                        </div>
                      ) : (
                        <MediaImage
                          src={
                            r.thumbnail_hash ||
                            (r.media_type === "image" && r.proxy_hash)
                              ? blobUrl(
                                  projectId,
                                  (r.thumbnail_hash || r.proxy_hash)!,
                                )
                              : null
                          }
                          alt={r.name}
                          className={
                            r.media_type === "image"
                              ? "[&_img]:object-contain"
                              : undefined
                          }
                        />
                      )}
                      <span className="absolute bottom-2 left-2 rounded bg-black/75 px-2 py-1 text-xs text-white">
                        {r.bbox && r.media_type === "image" ? (
                          r.kind === "face" ? (
                            zh ? (
                              "人脸区域"
                            ) : (
                              "Face region"
                            )
                          ) : zh ? (
                            "物体区域"
                          ) : (
                            "Object region"
                          )
                        ) : r.media_type === "image" ||
                          r.match_type === "image" ? (
                          zh ? (
                            "整张图片"
                          ) : (
                            "Whole image"
                          )
                        ) : r.match_type === "frame" ? (
                          `${zh ? "画面定位" : "Frame at"} ${mediaTime(r.matched_at_ms ?? r.start_ms)}`
                        ) : (
                          <>
                            {r.kind === "audio" && (
                              <AudioLines className="mr-1 inline h-3 w-3" />
                            )}
                            {mediaTime(r.start_ms)} — {mediaTime(r.end_ms)}
                          </>
                        )}
                      </span>
                      {r.provider === "wise" && r.media_type !== "audio" && (
                        <span className="absolute right-2 top-2 rounded bg-black/60 px-2 py-1 text-[10px] text-white">
                          {r.media_type === "image"
                            ? zh
                              ? "图片"
                              : "Image"
                            : zh
                              ? "原片封面"
                              : "Source cover"}
                        </span>
                      )}
                    </div>
                    <div className="space-y-2 p-4">
                      <h3 className="truncate text-sm font-medium">{r.name}</h3>
                      {!!r.matches?.length && (
                        <p className="text-xs text-muted-foreground">
                          {zh
                            ? `${r.matches.length} 个命中区域 · 打开后核对具体位置`
                            : `${r.matches.length} matched regions · Open to inspect their locations`}
                        </p>
                      )}
                      <p
                        className="text-xs text-muted-foreground"
                        title={
                          r.score !== null
                            ? zh
                              ? "匹配分数仅供本次检索排序，可以大于1或为负数；不同模型的分数不能直接比较。"
                              : "Match scores rank this search. They may exceed 1 or be negative, and cannot be compared across models."
                            : undefined
                        }
                      >
                        {mediaTypeName(r.media_type || "video", zh)} · v
                        {r.ordinal} ·{" "}
                        {r.score === null
                          ? r.kind === "visual"
                            ? zh
                              ? "画面标注"
                              : "Visual annotation"
                            : zh
                              ? "台词标注"
                              : "Dialogue annotation"
                          : zh
                            ? `匹配分数 ${r.score.toFixed(3)}`
                            : `Match score ${r.score.toFixed(3)}`}
                      </p>
                      {r.description && (
                        <p className="line-clamp-2 text-xs">{r.description}</p>
                      )}
                      <span className="studio-link text-xs">
                        {r.media_type === "image"
                          ? zh
                            ? "查看图片并引用"
                            : "View & reference image"
                          : r.match_type === "frame"
                            ? zh
                              ? "查看定位并选择片段"
                              : "View match & choose a range"
                            : zh
                              ? "播放片段并引用"
                              : "Play & reference"}{" "}
                        →
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            )}
            <div className="flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={!submitted.offset}
                onClick={() =>
                  setSubmitted({
                    ...submitted,
                    offset: Math.max(0, submitted.offset - 24),
                  })
                }
              >
                {zh ? "上一页" : "Previous"}
              </Button>
              <span className="text-xs text-muted-foreground">
                {zh
                  ? `第 ${submitted.offset / 24 + 1} 页`
                  : `Page ${submitted.offset / 24 + 1}`}
                {result.data.total !== undefined
                  ? ` · ${result.data.total} ${zh ? "处标注" : "annotations"}`
                  : ""}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={
                  result.data.total !== undefined
                    ? submitted.offset + 24 >= result.data.total
                    : !result.data.has_more
                }
                onClick={() =>
                  setSubmitted({ ...submitted, offset: submitted.offset + 24 })
                }
              >
                {zh ? "下一页" : "Next"}
              </Button>
            </div>
            {result.data.candidate_limit && (
              <p className="text-center text-xs leading-6 text-muted-foreground">
                {zh
                  ? `本次展示前 ${result.data.candidate_limit} 条候选范围内的结果，不代表全库匹配总数。`
                  : `Results are drawn from at most ${result.data.candidate_limit} candidates, not a full-library match count.`}
              </p>
            )}
          </>
        )
      )}
    </div>
  );
}

export function LibraryAnnotation({
  projectId,
  versionId,
  start,
  end,
  duration,
  mediaType = "video",
}: {
  projectId: string;
  versionId: string;
  start: number;
  end: number;
  duration: number;
  mediaType?: MediaType;
}) {
  const zh = useI18n().lang === "zh",
    qc = useQueryClient();
  const confirm = useConfirm();
  const [offset, setOffset] = useState(0);
  const [tab, setTab] = useState("annotation");
  const subtitleInput = useRef<HTMLInputElement>(null);
  const [rangeStart, setRangeStart] = useState(String(start / 1000));
  const [rangeEnd, setRangeEnd] = useState(String(end / 1000));
  const startMs = Math.round(Number(rangeStart) * 1000),
    endMs = Math.round(Number(rangeEnd) * 1000);
  const valid =
    rangeStart !== "" &&
    rangeEnd !== "" &&
    Number.isFinite(startMs) &&
    Number.isFinite(endMs) &&
    startMs >= 0 &&
    endMs - startMs >= 100 &&
    endMs <= duration;
  const [subtitleFile, setSubtitleFile] = useState<File | null>(null),
    [encoding, setEncoding] = useState("utf-8");
  const importSubtitles = useMutation({
    mutationFn: async () => {
      if (!subtitleFile) throw new Error("请选择字幕文件");
      if (subtitleFile.size > 1_000_000)
        throw new Error("字幕文件不能超过1 MB");
      let content: string;
      try {
        content = new TextDecoder(encoding, { fatal: true }).decode(
          await subtitleFile.arrayBuffer(),
        );
      } catch {
        throw new Error(
          zh
            ? "字幕编码无法识别，请切换编码后重试"
            : "Unable to decode subtitles; select the correct encoding",
        );
      }
      return api.post<{ added: number; skipped: number }>(
        `/projects/${projectId}/library/versions/${versionId}/subtitles`,
        { content },
      );
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["library-annotations", projectId, versionId],
      });
      void qc.invalidateQueries({ queryKey: ["library-search", projectId] });
    },
  });
  const annotations = useQuery({
    queryKey: ["library-annotations", projectId, versionId, offset],
    queryFn: () =>
      api.get<{
        total: number;
        items: {
          id: string;
          start_ms: number;
          end_ms: number;
          kind: string;
          text: string;
          source?: {
            model_key?: string;
            reviewed?: boolean;
            provider?: string;
          };
        }[];
      }>(
        `/projects/${projectId}/library/versions/${versionId}/annotations?offset=${offset}`,
      ),
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      api.del(`/projects/${projectId}/library/annotations/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["library-annotations", projectId, versionId],
      });
      void qc.invalidateQueries({ queryKey: ["library-search", projectId] });
    },
  });
  const [text, setText] = useState(""),
    [kind, setKind] = useState(mediaType === "audio" ? "speech" : "visual");
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `/projects/${projectId}/library/versions/${versionId}/annotations`,
        { start_ms: startMs, end_ms: endMs, kind, text: text.trim() },
      ),
    onSuccess: () => {
      setText("");
      void qc.invalidateQueries({ queryKey: ["library-search", projectId] });
      void qc.invalidateQueries({
        queryKey: ["library-annotations", projectId, versionId],
      });
    },
  });
  return (
    <div className="annotation-workspace">
      <section
        className="annotation-editor"
        aria-label={zh ? "添加标注或导入字幕" : "Add annotations or subtitles"}
      >
        <div
          className="annotation-tabs"
          role="group"
          aria-label={zh ? "添加方式" : "Add method"}
        >
          <button
            aria-pressed={tab === "annotation"}
            onClick={() => setTab("annotation")}
          >
            {zh ? "添加标注" : "Add annotation"}
          </button>
          <button
            aria-pressed={tab === "subtitles"}
            onClick={() => setTab("subtitles")}
          >
            {zh ? "导入字幕" : "Import subtitles"}
          </button>
        </div>
        {tab === "annotation" ? (
          <div className="annotation-form">
            <p className="annotation-help">
              {zh
                ? "记录画面内容或台词，方便之后搜索到这段素材。"
                : "Describe the scene or dialogue to find this segment later."}
            </p>
            <div className="annotation-range">
              <label>
                {zh ? "开始（秒）" : "Start (s)"}
                <input
                  aria-label={zh ? "标注开始时间" : "Annotation start"}
                  type="number"
                  min="0"
                  step="0.1"
                  value={rangeStart}
                  onChange={(e) => {
                    setRangeStart(e.target.value);
                    save.reset();
                  }}
                />
              </label>
              <label>
                {zh ? "结束（秒）" : "End (s)"}
                <input
                  aria-label={zh ? "标注结束时间" : "Annotation end"}
                  type="number"
                  min="0"
                  step="0.1"
                  value={rangeEnd}
                  onChange={(e) => {
                    setRangeEnd(e.target.value);
                    save.reset();
                  }}
                />
              </label>
            </div>
            {!valid && (
              <p className="text-xs text-danger" role="alert">
                {zh
                  ? "请选择素材时长内至少 0.1 秒的片段。"
                  : "Choose at least 0.1 seconds within the media."}
              </p>
            )}
            <SelectField
              label={zh ? "标注类型" : "Annotation type"}
              value={kind}
              onChange={(value) => {
                setKind(value);
                save.reset();
              }}
              options={[
                ...(mediaType !== "audio"
                  ? [
                      {
                        value: "visual",
                        label: zh ? "画面内容" : "Visual evidence",
                      },
                    ]
                  : []),
                { value: "speech", label: zh ? "台词内容" : "Dialogue" },
              ]}
            />
            <textarea
              aria-label={zh ? "片段检索标注" : "Segment search annotation"}
              placeholder={
                zh ? "这段素材里有什么？" : "What happens in this segment?"
              }
              rows={5}
              maxLength={4000}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                save.reset();
              }}
            />
            <div className="annotation-form-footer">
              <span>{text.length} / 4000</span>
              <Button
                size="sm"
                disabled={!valid || !text.trim() || save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending
                  ? zh
                    ? "保存中…"
                    : "Saving…"
                  : zh
                    ? "保存标注"
                    : "Save annotation"}
              </Button>
            </div>
            {save.isSuccess && (
              <p role="status" className="text-xs text-primary">
                {zh
                  ? "已保存，可通过字幕与标注搜索找到。"
                  : "Saved and available in annotation search."}
              </p>
            )}
            {save.error && (
              <p role="alert" className="text-xs text-danger">
                {save.error.message}
              </p>
            )}
          </div>
        ) : (
          <div className="annotation-form">
            <p className="annotation-help">
              {zh
                ? "导入 SRT 字幕后，可以按台词查找片段。"
                : "Import SRT subtitles to search by dialogue."}
            </p>
            <input
              ref={subtitleInput}
              type="file"
              accept=".srt"
              hidden
              aria-label={zh ? "字幕文件" : "Subtitle file"}
              onChange={(e) => {
                setSubtitleFile(e.target.files?.[0] || null);
                importSubtitles.reset();
              }}
            />
            <button
              className="annotation-file"
              onClick={() => subtitleInput.current?.click()}
            >
              <Upload size={22} />
              <strong>
                {subtitleFile?.name ||
                  (zh ? "选择字幕文件" : "Choose subtitles")}
              </strong>
              <span>{zh ? "SRT 格式 · 最大 1 MB" : "SRT · Up to 1 MB"}</span>
            </button>
            <SelectField
              label={zh ? "字幕编码" : "Subtitle encoding"}
              value={encoding}
              onChange={(value) => {
                setEncoding(value);
                importSubtitles.reset();
              }}
              options={[
                { value: "utf-8", label: "UTF-8" },
                { value: "gb18030", label: "GB18030 / GBK" },
              ]}
            />
            <Button
              size="sm"
              disabled={!subtitleFile || importSubtitles.isPending}
              onClick={() => importSubtitles.mutate()}
            >
              {importSubtitles.isPending
                ? zh
                  ? "导入中…"
                  : "Importing…"
                : zh
                  ? "导入字幕"
                  : "Import subtitles"}
            </Button>
            <p className="annotation-help">
              {zh
                ? "会检查时间范围并跳过重复字幕。台词中的提及不代表画面中出现了该内容。"
                : "Time ranges are validated and duplicates skipped. Spoken mentions are not visual detections."}
            </p>
            {importSubtitles.data && (
              <p role="status" className="text-xs text-primary">
                {zh
                  ? `已导入 ${importSubtitles.data.added} 条，跳过 ${importSubtitles.data.skipped} 条重复字幕`
                  : `Imported ${importSubtitles.data.added}, skipped ${importSubtitles.data.skipped} duplicates`}
              </p>
            )}
            {importSubtitles.error && (
              <p role="alert" className="text-xs text-danger">
                {importSubtitles.error.message}
              </p>
            )}
          </div>
        )}
      </section>
      <section
        className="annotation-history"
        aria-label={zh ? "已有标注" : "Saved annotations"}
      >
        <header>
          <h3>{zh ? "已有标注" : "Saved annotations"}</h3>
          <span>{annotations.data?.total ?? "—"}</span>
        </header>
        <div className="annotation-records">
          {annotations.isPending && (
            <p role="status" className="annotation-help">
              {zh ? "正在读取标注…" : "Loading annotations…"}
            </p>
          )}
          {annotations.isError && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void annotations.refetch()}
            >
              {zh ? "读取失败，重试" : "Retry annotations"}
            </Button>
          )}
          {annotations.data?.total === 0 && (
            <div className="annotation-empty">
              <MessageSquareText size={28} strokeWidth={1.25} />
              <p>{zh ? "还没有标注" : "No annotations yet"}</p>
              <span>
                {zh
                  ? "添加内容标注或导入字幕后显示在这里。"
                  : "Add an annotation or import subtitles to start."}
              </span>
            </div>
          )}
          {annotations.data?.items.map((a) => (
            <article className="annotation-record" key={a.id}>
              <div className="annotation-record-meta">
                <span>
                  {mediaTime(a.start_ms)} – {mediaTime(a.end_ms)}
                </span>
                <span>
                  {a.kind === "visual"
                    ? zh
                      ? "画面"
                      : "Visual"
                    : zh
                      ? "台词"
                      : "Dialogue"}
                </span>
              </div>
              <p>{a.text}</p>
              <footer>
                <span>
                  {a.source?.model_key || a.source?.provider === "mediacms"
                    ? a.source?.reviewed
                      ? zh
                        ? "自动转写 · 已核对"
                        : "Transcript · Reviewed"
                      : zh
                        ? "自动转写 · 待核对"
                        : "Transcript · Not reviewed"
                    : zh
                      ? "内容标注"
                      : "Annotation"}
                </span>
                <button
                  aria-label={
                    zh
                      ? `移除标注：${a.text.slice(0, 40)}`
                      : `Remove annotation: ${a.text.slice(0, 40)}`
                  }
                  title={zh ? "移除标注" : "Remove annotation"}
                  disabled={remove.isPending}
                  onClick={async () => {
                    if (
                      await confirm({
                        message: zh
                          ? "移除这条检索标注？不影响原素材与镜头引用。"
                          : "Remove this annotation? Media and references remain.",
                      })
                    )
                      remove.mutate(a.id);
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </footer>
            </article>
          ))}
        </div>
        {annotations.data && annotations.data.total > 50 && (
          <div className="annotation-pagination">
            <Button
              size="sm"
              variant="ghost"
              disabled={!offset}
              onClick={() => setOffset(offset - 50)}
            >
              {zh ? "上一页" : "Previous"}
            </Button>
            <span>
              {offset / 50 + 1} / {Math.ceil(annotations.data.total / 50)}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={offset + 50 >= annotations.data.total}
              onClick={() => setOffset(offset + 50)}
            >
              {zh ? "下一页" : "Next"}
            </Button>
          </div>
        )}
        {remove.error && (
          <p role="alert" className="text-xs text-danger">
            {remove.error.message}
          </p>
        )}
      </section>
    </div>
  );
}
