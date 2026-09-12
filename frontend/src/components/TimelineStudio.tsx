import { useEffect, useState } from "react";
import {
  edit,
  undo,
  redo,
  splitClip,
  type Clip,
  type History,
} from "@/lib/timeline-edit";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, Download, Film, Plus, Trash2 } from "lucide-react";
import {
  api,
  blobUrl,
  downloadBlob,
  type Generation,
  type GenJob,
  type Shot,
  type Timeline,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { MediaVideo } from "@/components/MediaVideo";
import { MediaAudio } from "@/components/MediaAudio";
import { VideoTools } from "@/components/VideoTools";
import { ZoomableImage } from "@/components/ImageViewer";
import { JobStatus } from "@/components/JobStatus";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { useConfirm } from "@/components/ui/confirm";

export function TimelineStudio({
  projectId,
  timelines,
  shots,
}: {
  projectId: string;
  timelines: Timeline[];
  shots: Shot[];
}) {
  const [search, setSearch] = useSearchParams();
  const { lang } = useI18n();
  const zh = lang === "zh";
  const [dirty, setDirty] = useState(false);
  const confirm = useConfirm();
  const requested = search.get("timeline");
  const id =
    timelines.find((t) => t.id === requested)?.id || timelines[0]?.id || "";
  const q = useQuery({
    queryKey: ["timeline-items", projectId, id],
    queryFn: () =>
      api.get<Clip[]>(`/projects/${projectId}/timelines/${id}/items`),
    enabled: !!id,
  });
  return (
    <Panel title={zh ? "剪辑与成片" : "Timeline & film"}>
      <label className="mb-4 block text-xs text-muted-foreground">
        {zh ? "工作时间线" : "Working timeline"}
        <select
          aria-label={zh ? "工作时间线" : "Working timeline"}
          className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-sm text-foreground"
          value={id}
          onChange={async (e) => {
            const value = e.target.value;
            if (
              !dirty ||
              (await confirm({
                title: zh ? "切换时间线" : "Switch timeline",
                message: zh
                  ? "当前未保存的调整将丢失。"
                  : "Unsaved changes will be lost.",
              }))
            ) {
              setDirty(false);
              setSearch({ timeline: value }, { replace: true });
            }
          }}
        >
          <option value="" disabled>
            {zh ? "请先在下方创建时间线" : "Create a timeline below"}
          </option>
          {timelines.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      {q.error && (
        <p role="alert" className="text-sm text-danger">
          {(q.error as Error).message}
        </p>
      )}
      {!id ? (
        <div className="studio-empty">
          <Film className="mb-3 h-6 w-6" />
          {zh
            ? "创建时间线后，添加镜头素材并导出 MP4。"
            : "Create a timeline, add shot media, and export MP4."}
        </div>
      ) : q.isLoading ? (
        <p>{zh ? "读取时间线…" : "Loading timeline…"}</p>
      ) : (
        q.data && (
          <TimelineDraft
            key={id}
            projectId={projectId}
            timelineId={id}
            shots={shots}
            initial={q.data}
            onDirty={setDirty}
          />
        )
      )}
    </Panel>
  );
}

function TimelineDraft({
  projectId,
  timelineId,
  shots,
  initial,
  onDirty,
}: {
  projectId: string;
  timelineId: string;
  shots: Shot[];
  initial: Clip[];
  onDirty: (v: boolean) => void;
}) {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const qc = useQueryClient();
  const toast = useToast();
  const base = `/projects/${projectId}`;
  const [history, setHistory] = useState<History<Clip[]>>(() => ({
    past: [],
    future: [],
    present: initial.map((c) => ({
      ...c,
      duration_ms: c.out_point_ms
        ? c.out_point_ms - c.in_point_ms
        : c.duration_ms || 3000,
      out_point_ms: 0,
    })),
  }));
  const clips = history.present;
  const [savedSnapshot, setSavedSnapshot] = useState(() =>
    JSON.stringify(clips),
  );
  const saved = savedSnapshot === JSON.stringify(clips);
  const [splitOffsets, setSplitOffsets] = useState<Record<number, number>>({});
  useEffect(() => {
    onDirty(!saved);
  }, [saved, onDirty]);
  const [selectedShot, setSelectedShot] = useState("");
  const [height, setHeight] = useState(720);
  const [aspect, setAspect] = useState("16:9");
  const [mute, setMute] = useState(false);
  const [lastJob, setLastJob] = usePersistentState<string>(
    `render.job.${projectId}.${timelineId}`,
    "",
  );
  const [preview, setPreview] = useState("");
  const [videoTools, setVideoTools] = useState<Generation | null>(null);
  const gens = useQuery({
    queryKey: ["gens", "timeline", timelineId],
    queryFn: () =>
      api.get<Generation[]>(
        `${base}/generations?target_type=timeline&target_id=${timelineId}`,
      ),
  });
  const outputs = (gens.data || []).filter(
    (g) => g.output_type === "video" && g.output_blob_hash,
  );
  const result = outputs.find((g) => g.id === preview) || outputs[0];
  const download = useMutation({
    mutationFn: () =>
      downloadBlob(
        projectId,
        result!.output_blob_hash!,
        `inspiration-${timelineId.slice(0, 8)}.mp4`,
      ),
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const change = (next: Clip[]) => {
    setHistory((h) => edit(h, next));
  };
  const update = (index: number, patch: Partial<Clip>) =>
    change(clips.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const move = (index: number, by: number) => {
    const next = [...clips];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    change(next);
  };
  const persist = async () => {
    await api.put(`${base}/timelines/${timelineId}/items`, { items: clips });
    setSavedSnapshot(JSON.stringify(clips));
    void qc.invalidateQueries({
      queryKey: ["timeline-items", projectId, timelineId],
    });
  };
  const save = useMutation({
    mutationFn: persist,
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const render = useMutation({
    mutationFn: async () => {
      await persist();
      return api.post<GenJob>(`${base}/timelines/${timelineId}/render`, {
        height,
        aspect_ratio: aspect,
        mute,
      });
    },
    onSuccess: (j) => {
      setLastJob(j.id);
      setPreview("");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const total = clips.reduce((n, c) => n + c.duration_ms, 0) / 1000;
  const invalid =
    clips.length > 100 ||
    total > 1800 ||
    clips.some(
      (c) =>
        c.duration_ms <= 0 ||
        c.in_point_ms < 0 ||
        !Number.isFinite(c.duration_ms) ||
        !Number.isFinite(c.in_point_ms),
    );
  const pending = save.isPending || render.isPending;
  return (
    <div className="space-y-5">
      {videoTools && (
        <VideoTools
          projectId={projectId}
          generation={videoTools}
          onClose={() => setVideoTools(null)}
          onJob={setLastJob}
        />
      )}
      {(gens.data || []).some(
        (g) => g.output_type !== "video" && g.output_blob_hash,
      ) && (
        <details className="rounded-lg border p-3">
          <summary className="cursor-pointer text-sm">
            {zh ? "从成片提取的素材" : "Media extracted from films"}
          </summary>
          <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
            {(gens.data || [])
              .filter((g) => g.output_type !== "video" && g.output_blob_hash)
              .map((g) => (
                <div key={g.id} className="min-w-0 rounded-lg border p-2">
                  {g.output_type === "audio" ? (
                    <MediaAudio src={blobUrl(projectId, g.output_blob_hash!)} />
                  ) : (
                    <ZoomableImage
                      filename={`frame-${g.id.slice(0, 8)}`}
                      src={blobUrl(projectId, g.output_blob_hash!)}
                      className="aspect-video w-full object-contain"
                    />
                  )}
                  <p className="mt-2 text-xs text-muted-foreground">
                    {g.id.slice(0, 8)}
                    {g.input_refs?.actual_media?.source_time_ms != null
                      ? ` · ${g.input_refs.actual_media.source_time_ms / 1000} s`
                      : ""}
                  </p>
                  <a
                    className="studio-link text-xs"
                    href={`${blobUrl(projectId, g.output_blob_hash!)}&download=true`}
                    download
                  >
                    {zh ? "下载素材" : "Download"}
                  </a>
                </div>
              ))}
          </div>
        </details>
      )}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          <div className="aspect-video overflow-hidden rounded-lg border bg-elevated">
            {result ? (
              <MediaVideo
                src={blobUrl(projectId, result.output_blob_hash!)}
                label={zh ? "成片预览" : "Film preview"}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 p-5 text-center text-sm text-muted-foreground">
                <Film className="h-7 w-7" />
                {zh
                  ? "导出完成后在这里预览"
                  : "Preview appears when export completes"}
              </div>
            )}
          </div>
          {outputs.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <select
                aria-label={zh ? "历史导出" : "Export history"}
                className="h-8 min-w-0 flex-1 rounded-md border bg-bg px-2 text-xs"
                value={result?.id || ""}
                onChange={(e) => setPreview(e.target.value)}
              >
                {outputs.map((g) => (
                  <option key={g.id} value={g.id}>
                    {new Date(g.created_at).toLocaleString()} ·{" "}
                    {g.id.slice(0, 8)}
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                variant="ghost"
                disabled={download.isPending}
                onClick={() => download.mutate()}
              >
                <Download className="h-3.5 w-3.5" />
                {download.isPending
                  ? zh
                    ? "下载中…"
                    : "Downloading…"
                  : zh
                    ? "下载 MP4"
                    : "Download MP4"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setVideoTools(result)}
              >
                {zh ? "视频工具" : "Video tools"}
              </Button>
            </div>
          )}
          {gens.error && (
            <p className="mt-2 text-xs text-danger">
              {(gens.error as Error).message}
            </p>
          )}
        </div>
        <div className="space-y-3">
          <h3 className="text-sm font-medium">
            {zh ? "导出设置" : "Export settings"}
          </h3>
          <p className="text-xs text-muted-foreground">
            {clips.length} {zh ? "段" : "clips"} · {total.toFixed(1)} s
          </p>
          <label className="block text-xs">
            {zh ? "画面比例" : "Aspect ratio"}
            <select
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
              value={aspect}
              onChange={(e) => setAspect(e.target.value)}
            >
              {["16:9", "9:16", "1:1"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs">
            {zh ? "短边分辨率" : "Short edge resolution"}
            <select
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
              value={height}
              onChange={(e) => setHeight(Number(e.target.value))}
            >
              <option value={720}>720p</option>
              <option value={1080}>1080p</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={mute}
              onChange={(e) => setMute(e.target.checked)}
            />
            {zh ? "静音导出" : "Mute audio"}
          </label>
          <p className="text-xs leading-5 text-muted-foreground">
            {zh
              ? "MP4 · 24 fps · 按顺序直切。图片按时长停留，视频不足时长时保持末帧；原音轨默认保留。"
              : "MP4 · 24 fps · straight cuts. Still images hold; short videos freeze on the last frame. Source audio is preserved by default."}
          </p>
          <Button
            className="w-full"
            disabled={!clips.length || invalid || pending}
            onClick={() => render.mutate()}
          >
            {render.isPending
              ? zh
                ? "提交中…"
                : "Submitting…"
              : zh
                ? "保存并导出 MP4"
                : "Save & export MP4"}
          </Button>
          {lastJob && (
            <JobStatus key={lastJob} projectId={projectId} jobId={lastJob} />
          )}
        </div>
      </div>
      <section className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={!history.past.length || pending}
            onClick={() => setHistory(undo)}
          >
            {zh ? "撤销剪辑" : "Undo edit"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!history.future.length || pending}
            onClick={() => setHistory(redo)}
          >
            {zh ? "重做剪辑" : "Redo edit"}
          </Button>
          <span className="self-center text-xs text-muted-foreground">
            {zh
              ? "本次编辑保留最近 100 步，保存后仍可撤销。"
              : "Up to 100 steps per editing session; saving keeps history."}
          </span>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium">
            {zh ? "镜头顺序" : "Clip order"}
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {saved
                ? zh
                  ? "已保存"
                  : "Saved"
                : zh
                  ? "有未保存调整"
                  : "Unsaved changes"}
            </span>
          </h3>
          <Button
            size="sm"
            variant="outline"
            disabled={saved || invalid || pending}
            onClick={() => save.mutate()}
          >
            {zh ? "保存时间线" : "Save timeline"}
          </Button>
        </div>
        <div className="flex gap-2">
          <select
            aria-label={zh ? "添加镜头" : "Add shot"}
            className="h-9 min-w-0 flex-1 rounded-md border bg-bg px-2 text-sm"
            value={selectedShot}
            onChange={(e) => setSelectedShot(e.target.value)}
          >
            <option value="">{zh ? "选择镜头素材…" : "Choose a shot…"}</option>
            {shots.map((s) => (
              <option key={s.id} value={s.id}>
                {s.code} · {s.title || ""}
                {s.selected_generation_id
                  ? ""
                  : zh
                    ? "（尚未钦定素材）"
                    : " (no selected media)"}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            disabled={!selectedShot || clips.length >= 100 || pending}
            onClick={() => {
              const shot = shots.find((s) => s.id === selectedShot)!;
              change([
                ...clips,
                {
                  shot_id: shot.id,
                  generation_id: shot.selected_generation_id,
                  in_point_ms: 0,
                  out_point_ms: 0,
                  duration_ms: 3000,
                },
              ]);
              setSelectedShot("");
            }}
          >
            <Plus className="mr-1 h-4 w-4" />
            {zh ? "添加" : "Add"}
          </Button>
        </div>
        {clips.length === 0 && (
          <p className="rounded-lg border border-dashed p-5 text-center text-sm text-muted-foreground">
            {zh ? "时间线为空，添加第一个镜头。" : "Add your first clip."}
          </p>
        )}
        {clips.map((c, i) => (
          <div
            key={`${i}-${c.shot_id}`}
            className="rounded-lg border border-border bg-bg p-3"
          >
            <div className="mb-3 flex items-center gap-2">
              <span className="text-xs text-faint">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm">
                {shots.find((s) => s.id === c.shot_id)?.title ||
                  shots.find((s) => s.id === c.shot_id)?.code ||
                  c.shot_id.slice(0, 8)}
              </span>
              <Button
                size="sm"
                variant="ghost"
                title={zh ? "复制片段" : "Duplicate clip"}
                disabled={pending || clips.length >= 100}
                onClick={() =>
                  change([
                    ...clips.slice(0, i + 1),
                    { ...c },
                    ...clips.slice(i + 1),
                  ])
                }
              >
                {zh ? "复制" : "Duplicate"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                title={zh ? "上移片段" : "Move up"}
                disabled={i === 0 || pending}
                onClick={() => move(i, -1)}
              >
                <ArrowUp className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                title={zh ? "下移片段" : "Move down"}
                disabled={i === clips.length - 1 || pending}
                onClick={() => move(i, 1)}
              >
                <ArrowDown className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                title={zh ? "移除片段" : "Remove clip"}
                disabled={pending}
                onClick={() => change(clips.filter((_, n) => n !== i))}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_130px]">
              <ClipSource
                projectId={projectId}
                clip={c}
                disabled={pending}
                onChange={(v) => update(i, { generation_id: v })}
              />
              <label className="text-xs text-muted-foreground">
                {zh ? "入点（秒）" : "Start (seconds)"}
                <input
                  aria-label={`${zh ? "入点" : "Start"} ${i + 1}`}
                  type="number"
                  min={0}
                  max={1800}
                  step={0.1}
                  disabled={pending}
                  value={c.in_point_ms / 1000}
                  onChange={(e) =>
                    update(i, {
                      in_point_ms: Math.round(Number(e.target.value) * 1000),
                    })
                  }
                  className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-foreground"
                />
              </label>
              <label className="text-xs text-muted-foreground">
                {zh ? "使用时长（秒）" : "Duration (seconds)"}
                <input
                  aria-label={`${zh ? "时长" : "Duration"} ${i + 1}`}
                  type="number"
                  min={0.1}
                  max={1800}
                  step={0.1}
                  disabled={pending}
                  value={c.duration_ms / 1000}
                  onChange={(e) =>
                    update(i, {
                      duration_ms: Math.round(Number(e.target.value) * 1000),
                    })
                  }
                  className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-foreground"
                />
              </label>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                {zh ? "在片段内第" : "Split at"}
                <input
                  aria-label={`${zh ? "拆分位置" : "Split position"} ${i + 1}`}
                  type="number"
                  min={0.1}
                  max={Math.max(0.1, (c.duration_ms - 100) / 1000)}
                  step={0.1}
                  disabled={pending}
                  value={
                    (splitOffsets[i] ?? Math.floor(c.duration_ms / 200) * 100) /
                    1000
                  }
                  onChange={(e) =>
                    setSplitOffsets({
                      ...splitOffsets,
                      [i]: Math.round(Number(e.target.value) * 1000),
                    })
                  }
                  className="h-8 w-20 rounded-md border bg-bg px-2 text-foreground"
                />
                {zh ? "秒拆分" : "seconds"}
              </label>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  pending ||
                  clips.length >= 100 ||
                  !Number.isFinite(splitOffsets[i] ?? c.duration_ms / 2) ||
                  (splitOffsets[i] ?? Math.floor(c.duration_ms / 200) * 100) <
                    100 ||
                  (splitOffsets[i] ?? Math.floor(c.duration_ms / 200) * 100) >
                    c.duration_ms - 100
                }
                onClick={() => {
                  change(
                    splitClip(
                      clips,
                      i,
                      splitOffsets[i] ?? Math.floor(c.duration_ms / 200) * 100,
                    ),
                  );
                  setSplitOffsets({});
                }}
              >
                {zh ? "拆分片段" : "Split clip"}
              </Button>
            </div>
          </div>
        ))}
        {invalid && (
          <p role="alert" className="text-xs text-danger">
            {zh
              ? "请检查片段时长。最多 100 段，总长不超过 30 分钟。"
              : "Check durations. Maximum 100 clips / 30 minutes."}
          </p>
        )}
      </section>
    </div>
  );
}

function ClipSource({
  projectId,
  clip,
  disabled,
  onChange,
}: {
  projectId: string;
  clip: Clip;
  disabled: boolean;
  onChange: (id: string | null) => void;
}) {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const { data, error } = useQuery({
    queryKey: ["gens", "shot", clip.shot_id],
    queryFn: () =>
      api.get<Generation[]>(
        `/projects/${projectId}/generations?target_type=shot&target_id=${clip.shot_id}`,
      ),
  });
  return (
    <label className="min-w-0 text-xs text-muted-foreground">
      {zh ? "使用素材" : "Media variant"}
      <select
        className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-foreground"
        disabled={disabled}
        value={clip.generation_id || ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">
          {zh ? "使用镜头当前钦定素材" : "Use shot selection"}
        </option>
        {data
          ?.filter(
            (g) =>
              g.output_blob_hash && ["image", "video"].includes(g.output_type),
          )
          .map((g, i) => (
            <option key={g.id} value={g.id}>
              {g.output_type === "video"
                ? zh
                  ? "视频"
                  : "Video"
                : zh
                  ? "图片"
                  : "Image"}{" "}
              {i + 1} · {g.provider} · {g.id.slice(0, 8)}
            </option>
          ))}
      </select>
      {error && <span className="text-danger">{(error as Error).message}</span>}
    </label>
  );
}
