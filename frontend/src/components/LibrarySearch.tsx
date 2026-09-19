import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, ScanSearch, ImagePlus, X } from "lucide-react";
import { api, blobUrl } from "@/lib/api";
import { mediaTime } from "@/lib/library";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { MediaImage } from "@/components/MediaImage";
import { useConfirm } from "@/components/ui/confirm";

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
  start_ms: number;
  end_ms: number;
  thumbnail_hash: string;
  score: number | null;
  description: string;
  kind: string;
};
type SearchInput = {
  query: string;
  image?: string;
  mode: "semantic" | "annotated";
  kind?: "visual" | "speech";
  offset: number;
  collapse_versions: boolean;
};
type SearchResult = {
  items: SegmentResult[];
  coverage: Coverage;
  total?: number;
  has_more?: boolean;
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
  const [query, setQuery] = useState(""),
    [mode, setMode] = useState<"semantic" | "annotated">("semantic"),
    [kind, setKind] = useState("");
  const [picture, setPicture] = useState<{
      data: string;
      mime: string;
      name: string;
    } | null>(null),
    [fileError, setFileError] = useState("");
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
  const submit = () =>
    setSubmitted({
      query,
      image: mode === "semantic" ? picture?.data : undefined,
      mode,
      kind:
        mode === "annotated" && kind
          ? (kind as "visual" | "speech")
          : undefined,
      offset: 0,
      collapse_versions: mode === "semantic" && collapse,
    });
  return (
    <div className="space-y-6">
      <div className="rounded-xl border bg-card p-4 sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <span className="rounded-lg bg-primary/10 p-2 text-primary">
            <ScanSearch className="h-6 w-6" />
          </span>
          <div>
            <h2 className="font-semibold">
              {zh ? "从画面开始寻找灵感" : "Find inspiration in your footage"}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {zh
                ? "描述你要找的画面，或上传一张参考图。找到后，直接引用到镜头。"
                : "Describe a scene or choose a reference image, then use the matching segment in a shot."}
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
                setSubmitted(null);
              }}
            >
              {m === "semantic"
                ? zh
                  ? "相似画面"
                  : "Similar scenes"
                : zh
                  ? "明确标注"
                  : "Annotations"}
            </Button>
          ))}
        </div>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <input
            aria-label={zh ? "搜索画面描述" : "Scene search"}
            className="h-11 min-w-0 flex-1 rounded-lg border bg-bg px-3 text-sm"
            placeholder={
              mode === "semantic"
                ? zh
                  ? "例如：雨中的街道，镜头跟随一个人向前走"
                  : "A person walking down a rainy street"
                : zh
                  ? "搜索人工画面标注或台词"
                  : "Search visual annotations or dialogue"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {mode === "annotated" && (
            <select
              aria-label={zh ? "标注来源" : "Annotation source"}
              className="rounded-lg border bg-bg px-2 text-sm"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="">{zh ? "全部标注" : "All annotations"}</option>
              <option value="visual">{zh ? "画面标注" : "Visual"}</option>
              <option value="speech">{zh ? "台词标注" : "Dialogue"}</option>
            </select>
          )}
          <Button
            type="submit"
            disabled={
              result.isFetching ||
              (mode === "semantic" && !query.trim() && !picture)
            }
          >
            <Search className="h-4 w-4" />
            {result.isFetching
              ? zh
                ? "查找中…"
                : "Searching…"
              : zh
                ? "查找片段"
                : "Find segments"}
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
              {zh ? "每个版本只显示最佳片段" : "Best segment per version"}
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground hover:text-foreground">
              <ImagePlus className="h-4 w-4" />
              {zh ? "用图片找片段" : "Search with an image"}
              <input
                aria-label={zh ? "检索参考图片" : "Search reference image"}
                type="file"
                className="sr-only"
                accept="image/jpeg,image/png,image/webp"
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  setFileError("");
                  if (
                    f.size > 2 * 1024 * 1024 ||
                    !["image/jpeg", "image/png", "image/webp"].includes(f.type)
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
                    setPicture({ data, mime: f.type, name: f.name });
                  } catch {
                    setFileError(
                      zh
                        ? "图片读取失败，请重新选择"
                        : "Unable to read image; select it again",
                    );
                  }
                  e.target.value = "";
                }}
              />
            </label>
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
                  onClick={() => setPicture(null)}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </div>
        )}
        {fileError && (
          <p role="alert" className="mt-2 text-xs text-danger">
            {fileError}
          </p>
        )}
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          {mode === "semantic"
            ? zh
              ? "根据采样画面寻找相似片段，可能漏掉短暂出现的内容。相似度不是识别置信度，请播放原片核对。"
              : "Matches use sampled video. Brief appearances may be missed. Similarity is not confidence; verify by playing the source."
            : zh
              ? "仅搜索已保存的人工画面与台词标注，可分页查看全部匹配。台词提到某物，不代表它出现在画面中。"
              : "Search saved visual and dialogue annotations, with all matches paginated. Spoken mentions do not establish visual presence."}
        </p>
      </div>
      <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {coverage.isPending
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
            ? "本地检索 · 原片保留在自己的存储中"
            : "Self-hosted search · Originals remain in your storage"}
        </span>
      </div>
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
            {zh
              ? "试着描述一个人物、场景或动作"
              : "Try describing a person, scene or action"}
          </p>
          <span className="max-w-lg text-center text-xs">
            {zh
              ? "先在视频卡片上建立内容索引。未索引的视频不会被当作“没有匹配”。"
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
                  {zh
                    ? "当前检索范围没有匹配片段"
                    : "No matches in the current indexed scope"}
                </p>
                <span className="text-xs">
                  {zh
                    ? "可以换一种描述，或为尚未处理的视频建立索引。"
                    : "Try another description or index the remaining videos."}
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
                      <MediaImage
                        src={blobUrl(projectId, r.thumbnail_hash)}
                        alt={r.name}
                      />
                      <span className="absolute bottom-2 left-2 rounded bg-black/75 px-2 py-1 text-xs text-white">
                        {mediaTime(r.start_ms)} — {mediaTime(r.end_ms)}
                      </span>
                    </div>
                    <div className="space-y-2 p-4">
                      <h3 className="truncate text-sm font-medium">{r.name}</h3>
                      <p className="text-xs text-muted-foreground">
                        v{r.ordinal} ·{" "}
                        {r.score === null
                          ? r.kind === "visual"
                            ? zh
                              ? "画面标注"
                              : "Visual annotation"
                            : zh
                              ? "台词标注"
                              : "Dialogue annotation"
                          : zh
                            ? `相似度 ${r.score.toFixed(3)}`
                            : `Similarity ${r.score.toFixed(3)}`}
                      </p>
                      {r.description && (
                        <p className="line-clamp-2 text-xs">{r.description}</p>
                      )}
                      <span className="studio-link text-xs">
                        {zh ? "播放片段并引用" : "Play & reference"} →
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
  valid,
}: {
  projectId: string;
  versionId: string;
  start: number;
  end: number;
  valid: boolean;
}) {
  const zh = useI18n().lang === "zh",
    qc = useQueryClient();
  const confirm = useConfirm();
  const [expanded, setExpanded] = useState(false),
    [offset, setOffset] = useState(0);
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
    enabled: expanded,
    queryFn: () =>
      api.get<{
        total: number;
        items: {
          id: string;
          start_ms: number;
          end_ms: number;
          kind: string;
          text: string;
          source?: { model_key?: string; reviewed?: boolean };
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
    [kind, setKind] = useState("visual");
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `/projects/${projectId}/library/versions/${versionId}/annotations`,
        { start_ms: start, end_ms: end, kind, text: text.trim() },
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
    <details
      className="rounded-lg border p-3 text-xs"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary className="cursor-pointer font-medium">
        {zh ? "为所选片段添加检索标注" : "Annotate the selected segment"}
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-muted-foreground">
          {zh
            ? "记录你实际看见的内容或听见的台词，方便之后精确查找。标注与视频版本一起保留。"
            : "Record visible evidence or dialogue for later lookup. Annotations stay with this video version."}
        </p>
        <select
          aria-label={zh ? "新标注来源" : "New annotation source"}
          className="h-8 w-full rounded border bg-bg px-2"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            save.reset();
          }}
        >
          <option value="visual">{zh ? "画面内容" : "Visual evidence"}</option>
          <option value="speech">{zh ? "台词内容" : "Dialogue"}</option>
        </select>
        <textarea
          aria-label={zh ? "片段检索标注" : "Segment search annotation"}
          className="w-full rounded border bg-bg p-2"
          rows={3}
          maxLength={4000}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            save.reset();
          }}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={!valid || !text.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          {zh ? "保存标注" : "Save annotation"}
        </Button>
        {save.isSuccess && (
          <p role="status">
            {zh
              ? "标注已保存，可在明确标注中检索。"
              : "Saved. Find it in annotations search."}
          </p>
        )}
        {save.error && (
          <p role="alert" className="text-danger">
            {save.error.message}
          </p>
        )}
        {annotations.isError ? (
          <button
            className="studio-link"
            onClick={() => void annotations.refetch()}
          >
            {zh ? "标注读取失败，重试" : "Retry annotations"}
          </button>
        ) : (
          annotations.data?.items.map((a) => (
            <div className="space-y-1 border-t pt-2" key={a.id}>
              <p className="text-muted-foreground">
                {mediaTime(a.start_ms)} — {mediaTime(a.end_ms)} ·{" "}
                {a.kind === "visual"
                  ? zh
                    ? "画面"
                    : "Visual"
                  : zh
                    ? "台词"
                    : "Dialogue"}
              </p>
              <p className="break-words">{a.text}</p>
              {a.source?.model_key && (
                <p className="text-xs text-muted-foreground">
                  {zh ? "自动转写后人工核对" : "Transcribed, then reviewed"}
                </p>
              )}
              <button
                className="studio-link"
                disabled={remove.isPending}
                onClick={async () => {
                  if (
                    await confirm({
                      message: zh
                        ? "移除这条检索标注？不影响原视频与镜头引用。"
                        : "Remove this annotation? The video and shot references remain.",
                    })
                  )
                    remove.mutate(a.id);
                }}
              >
                {zh ? "移除标注" : "Remove annotation"}
              </button>
            </div>
          ))
        )}
        {annotations.data && annotations.data.total > 50 && (
          <div className="flex justify-between">
            <button disabled={!offset} onClick={() => setOffset(offset - 50)}>
              {zh ? "上一页" : "Previous"}
            </button>
            <button
              disabled={offset + 50 >= annotations.data.total}
              onClick={() => setOffset(offset + 50)}
            >
              {zh ? "下一页" : "Next"}
            </button>
          </div>
        )}
        {remove.error && (
          <p role="alert" className="text-danger">
            {remove.error.message}
          </p>
        )}
        <div className="space-y-2 border-t pt-3">
          <p className="font-medium">
            {zh
              ? "导入字幕并按台词检索"
              : "Import subtitles for dialogue search"}
          </p>
          <input
            aria-label={zh ? "字幕文件" : "Subtitle file"}
            type="file"
            accept=".srt"
            className="max-w-full"
            onChange={(e) => {
              setSubtitleFile(e.target.files?.[0] || null);
              importSubtitles.reset();
            }}
          />
          <div className="flex flex-wrap gap-2">
            <select
              aria-label={zh ? "字幕编码" : "Subtitle encoding"}
              value={encoding}
              onChange={(e) => setEncoding(e.target.value)}
              className="h-8 rounded border bg-bg px-2"
            >
              <option value="utf-8">UTF-8</option>
              <option value="gb18030">GB18030 / GBK</option>
            </select>
            <Button
              size="sm"
              variant="outline"
              disabled={!subtitleFile || importSubtitles.isPending}
              onClick={() => importSubtitles.mutate()}
            >
              {zh ? "导入SRT" : "Import SRT"}
            </Button>
          </div>
          <p className="text-muted-foreground">
            {zh
              ? "导入前检查全部时间范围，重复字幕会跳过。字幕中的提及不视为画面识别结果。"
              : "All ranges are validated before import; duplicate cues are skipped. Spoken mentions are not visual detections."}
          </p>
          {importSubtitles.data && (
            <p role="status">
              {zh
                ? `已导入 ${importSubtitles.data.added} 条，跳过 ${importSubtitles.data.skipped} 条重复字幕`
                : `Imported ${importSubtitles.data.added}, skipped ${importSubtitles.data.skipped} duplicates`}
            </p>
          )}
          {importSubtitles.error && (
            <p role="alert" className="text-danger">
              {importSubtitles.error.message}
            </p>
          )}
        </div>
      </div>
    </details>
  );
}
