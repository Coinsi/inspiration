import { useEffect, useState } from "react";
import {
  edit,
  undo,
  redo,
  splitClip,
  timelineDuration,
  visualIssue,
  type Clip,
  type History,
  type TimelineContent,
  type TimelineDocument,
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
import { TimelineVisuals } from "@/components/TimelineVisuals";
import { TimelinePlayback } from "@/components/TimelinePlayback";
import { TimelineOverview } from "@/components/TimelineOverview";
import { MediaVideo } from "@/components/MediaVideo";
import { MediaAudio } from "@/components/MediaAudio";
import { VideoTools } from "@/components/VideoTools";
import { ZoomableImage } from "@/components/ImageViewer";
import { JobStatus } from "@/components/JobStatus";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { useConfirm } from "@/components/ui/confirm";
import { navigateTabs } from "@/lib/tab-navigation";
import {
  TimelineTracks,
  TimelineSavedHistory,
} from "@/components/TimelineTracks";

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
  const [workingBusy, setWorkingBusy] = useState(false);
  const confirm = useConfirm();
  const requested = search.get("timeline");
  const id =
    timelines.find((t) => t.id === requested)?.id || timelines[0]?.id || "";
  useEffect(() => {
    if (id && requested !== id)
      setSearch(
        (previous) => {
          const next = new URLSearchParams(previous);
          next.set("timeline", id);
          return next;
        },
        { replace: true },
      );
  }, [id, requested, setSearch]);
  const q = useQuery({
    queryKey: ["timeline-document", projectId, id],
    queryFn: () =>
      api.get<TimelineDocument>(
        `/projects/${projectId}/timelines/${id}/document`,
      ),
    enabled: !!id,
  });
  return (
    <section className="timeline-studio">
      <div className="timeline-document-bar">
        <h2>
          <Film className="h-4 w-4" />
          {zh ? "剪辑与成片" : "Timeline & film"}
        </h2>
        <label className="text-xs text-muted-foreground">
          {zh ? "工作时间线" : "Working timeline"}
          <select
            aria-label={zh ? "工作时间线" : "Working timeline"}
            className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-sm text-foreground"
            value={id}
            disabled={workingBusy}
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
              {zh ? "请先新建时间线" : "Create a timeline first"}
            </option>
            {timelines.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {q.error && (
        <p role="alert" className="text-sm text-danger">
          {(q.error as Error).message}
          <Button variant="ghost" onClick={() => void q.refetch()}>
            {zh ? "重试" : "Retry"}
          </Button>
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
            onBusy={setWorkingBusy}
          />
        )
      )}
    </section>
  );
}

function TimelineDraft({
  projectId,
  timelineId,
  shots,
  initial,
  onDirty,
  onBusy,
}: {
  projectId: string;
  timelineId: string;
  shots: Shot[];
  initial: TimelineDocument;
  onDirty: (v: boolean) => void;
  onBusy: (v: boolean) => void;
}) {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const qc = useQueryClient();
  const toast = useToast();
  const base = `/projects/${projectId}`;
  const [revision, setRevision] = useState(initial.revision);
  const [history, setHistory] = useState<History<TimelineContent>>(() => ({
    past: [],
    future: [],
    present: {
      visuals: initial.visuals || [],
      audio: initial.audio,
      subtitles: initial.subtitles,
      items: initial.items.map((c) => ({
        ...c,
        duration_ms: c.out_point_ms
          ? c.out_point_ms - c.in_point_ms
          : c.duration_ms || 3000,
        out_point_ms: 0,
      })),
    },
  }));
  const content = history.present;
  const clips = content.items;
  const [savedSnapshot, setSavedSnapshot] = useState(() =>
    JSON.stringify(content),
  );
  const saved = savedSnapshot === JSON.stringify(content);
  const [splitOffsets, setSplitOffsets] = useState<Record<number, number>>({});
  useEffect(() => {
    onDirty(!saved);
  }, [saved, onDirty]);
  const [selectedShot, setSelectedShot] = useState("");
  const [height, setHeight] = useState(720);
  const [aspect, setAspect] = useState("16:9");
  const [mute, setMute] = useState(false);
  const [subtitleMode, setSubtitleMode] = useState("burn");
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [tracksBusy, setTracksBusy] = useState(false);
  const [lastJob, setLastJob] = usePersistentState<string>(
    `render.job.${projectId}.${timelineId}`,
    "",
  );
  const [preview, setPreview] = useState("");
  const [clipPreview, setClipPreview] = useState<number | null>(null);
  const [panel, setPanel] = useState<
    "clips" | "visuals" | "tracks" | "history"
  >("clips");
  const [composition, setComposition] = useState(false);
  const [compositionTime, setCompositionTime] = useState(0);
  const [selectedVisual, setSelectedVisual] = useState("");
  const [trackTab, setTrackTab] = useState<"audio" | "subtitles">("audio");
  const activeClip = Math.min(clipPreview ?? 0, Math.max(0, clips.length - 1));
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
    setHistory((h) => edit(h, { ...h.present, items: next }));
  };
  const update = (index: number, patch: Partial<Clip>) =>
    change(clips.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const move = (index: number, by: number) => {
    const next = [...clips];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    change(next);
    setClipPreview(index + by);
  };
  const persist = async () => {
    const result = await api.put<TimelineDocument>(
      `${base}/timelines/${timelineId}/document`,
      { ...content, revision },
    );
    setRevision(result.revision);
    setSavedSnapshot(JSON.stringify(content));
    void qc.invalidateQueries({
      queryKey: ["timeline-document", projectId, timelineId],
    });
    void qc.invalidateQueries({
      queryKey: ["timeline-history", projectId, timelineId],
    });
    return result.revision;
  };
  const save = useMutation({
    mutationFn: persist,
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const render = useMutation({
    mutationFn: async () => {
      const savedRevision = await persist();
      return api.post<GenJob>(`${base}/timelines/${timelineId}/render`, {
        height,
        aspect_ratio: aspect,
        mute,
        subtitle_mode: subtitleMode,
        revision: savedRevision,
      });
    },
    onSuccess: (j) => {
      setLastJob(j.id);
      setPreview("");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const total = timelineDuration(clips) / 1000;
  const invalid =
    visualIssue(content.visuals, total * 1000) ||
    clips.length > 100 ||
    total > 1800 ||
    clips.some(
      (c) =>
        c.duration_ms <= 0 ||
        c.in_point_ms < 0 ||
        !Number.isFinite(c.duration_ms) ||
        !Number.isFinite(c.in_point_ms),
    ) ||
    clips.some(
      (c, i) =>
        c.transition &&
        (i === clips.length - 1 ||
          !["dissolve", "fadeblack", "wipeleft"].includes(
            String(c.transition.type),
          ) ||
          !Number.isInteger(c.transition.duration_ms) ||
          Number(c.transition.duration_ms) < 100 ||
          Number(c.transition.duration_ms) >
            Math.min(
              2000,
              c.duration_ms / 2,
              (clips[i + 1]?.duration_ms || 0) / 2,
            )),
    ) ||
    content.subtitles.some(
      (c) =>
        !c.text.trim() ||
        c.end_ms - c.start_ms < 100 ||
        c.end_ms > total * 1000 ||
        c.start_ms < 0,
    ) ||
    content.audio.some(
      (a) =>
        a.duration_ms < 100 ||
        a.start_ms + a.duration_ms > total * 1000 ||
        a.fade_in_ms + a.fade_out_ms > a.duration_ms,
    );
  const pending =
    save.isPending || render.isPending || remoteBusy || tracksBusy;
  useEffect(() => {
    onBusy(pending);
    return () => onBusy(false);
  }, [pending, onBusy]);
  useEffect(() => {
    if (saved) return;
    const leave = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", leave);
    return () => window.removeEventListener("beforeunload", leave);
  }, [saved]);
  return (
    <div className="timeline-draft">
      <div className="timeline-edit-bar">
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
          <span className="timeline-undo-hint self-center text-xs text-muted-foreground">
            {zh
              ? "本次编辑保留最近 100 步，保存后仍可撤销。"
              : "Up to 100 steps per editing session; saving keeps history."}
          </span>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium">
            {zh ? "剪辑草稿" : "Editing draft"}
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
      </div>
      {videoTools && (
        <VideoTools
          projectId={projectId}
          generation={videoTools}
          onClose={() => setVideoTools(null)}
          onJob={setLastJob}
        />
      )}
      <div className="timeline-preview-grid">
        <div className="timeline-monitor">
          <div className="timeline-monitor-screen">
            {composition ? (
              <TimelinePlayback
                projectId={projectId}
                shots={shots}
                content={content}
                aspect={aspect}
                time={compositionTime}
                onTime={setCompositionTime}
                selected={selectedVisual}
                mute={mute}
                subtitleMode={subtitleMode}
                disabled={invalid || pending}
              />
            ) : (clipPreview !== null || !result) && clips.length > 0 ? (
              <ClipPreview
                key={`${clipPreview ?? 0}-${clips[clipPreview ?? 0]?.generation_id}`}
                projectId={projectId}
                clip={clips[Math.min(clipPreview ?? 0, clips.length - 1)]}
                shot={shots.find(
                  (s) =>
                    s.id ===
                    clips[Math.min(clipPreview ?? 0, clips.length - 1)].shot_id,
                )}
              />
            ) : result ? (
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
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>
              {composition
                ? zh
                  ? "实时预览 · 声音、字幕与转场；最终编码与字幕排版以导出为准"
                  : "Live preview · audio, captions and transitions; export for final encoding"
                : clipPreview !== null || !result
                  ? zh
                    ? "片段素材预览 · 完整混音与转场请导出预览"
                    : "Source preview · export to view the full mix and transitions"
                  : zh
                    ? "已导出成片"
                    : "Exported film"}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setComposition(!composition)}
            >
              {composition
                ? zh
                  ? "片段素材"
                  : "Source preview"
                : zh
                  ? "合成布局"
                  : "Composition layout"}
            </Button>
            {result && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setComposition(false);
                  setClipPreview(null);
                }}
              >
                {zh ? "查看成片" : "View film"}
              </Button>
            )}
          </div>
          {clips.length > 0 && (
            <div
              className="mt-3 flex gap-2 overflow-x-auto rounded-lg border bg-bg p-2"
              aria-label={zh ? "画面序列" : "Picture sequence"}
            >
              {clips.map((c, i) => (
                <ClipThumbnail
                  key={`${i}-${c.shot_id}`}
                  projectId={projectId}
                  clip={c}
                  shot={shots.find((s) => s.id === c.shot_id)}
                  index={i}
                  selected={
                    clipPreview === i ||
                    (!result && clipPreview === null && i === 0)
                  }
                  onSelect={() => {
                    setComposition(false);
                    setClipPreview(i);
                    setPanel("clips");
                  }}
                />
              ))}
            </div>
          )}
          {outputs.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <select
                aria-label={zh ? "历史导出" : "Export history"}
                className="h-8 min-w-0 flex-1 rounded-md border bg-bg px-2 text-xs"
                value={result?.id || ""}
                onChange={(e) => {
                  setComposition(false);
                  setPreview(e.target.value);
                  setClipPreview(null);
                }}
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
        <fieldset
          disabled={pending}
          className="timeline-export-panel space-y-3"
        >
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
            {zh ? "全部音轨静音导出" : "Mute all audio"}
          </label>
          <label className="block text-xs">
            {zh ? "字幕导出方式" : "Subtitles"}
            <select
              aria-label={zh ? "字幕导出方式" : "Subtitle export mode"}
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
              value={subtitleMode}
              onChange={(e) => setSubtitleMode(e.target.value)}
            >
              <option value="burn">
                {zh ? "写入画面（所有播放器可见）" : "Burn into video"}
              </option>
              <option value="track">
                {zh ? "可切换字幕轨" : "Selectable subtitle track"}
              </option>
              <option value="none">{zh ? "不带字幕" : "No subtitles"}</option>
            </select>
          </label>
          <p className="text-xs leading-5 text-muted-foreground">
            {zh
              ? "MP4 · 24 fps。转场会缩短成片时长；图片按时长停留，视频不足时保持末帧。原声与附加音轨一起混合。"
              : "MP4 · 24 fps. Transitions overlap clips. Still images hold; short videos freeze on the last frame. Source and added audio are mixed."}
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
        </fieldset>
      </div>
      <TimelineOverview
        content={content}
        shots={shots}
        selected={activeClip}
        disabled={pending}
        time={compositionTime}
        onTime={(t) => {
          setComposition(true);
          setCompositionTime(t);
        }}
        onChange={(next) => setHistory((h) => edit(h, next))}
        onSelect={(index) => {
          setComposition(false);
          setClipPreview(index);
          setPanel("clips");
        }}
        onVisualSelect={(id) => {
          setPanel("visuals");
          setComposition(true);
          setSelectedVisual(id);
          const v = content.visuals.find((v) => v.id === id);
          if (v) setCompositionTime(v.start_ms);
        }}
        onTracks={(tab) => {
          setTrackTab(tab);
          setPanel("tracks");
        }}
      />
      <div
        className="timeline-editor-tabs"
        role="tablist"
        aria-label={zh ? "剪辑面板" : "Editing panels"}
        onKeyDown={navigateTabs}
      >
        {(["clips", "visuals", "tracks", "history"] as const).map((p, i) => (
          <button
            key={p}
            role="tab"
            tabIndex={panel === p ? 0 : -1}
            aria-selected={panel === p}
            aria-controls={`timeline-panel-${p}`}
            id={`timeline-tab-${p}`}
            onClick={() => {
              setPanel(p);
              if (p === "visuals") setComposition(true);
            }}
          >
            {
              (zh
                ? ["镜头剪辑", "叠加画面", "声音与字幕", "保存历史"]
                : [
                    "Clip editing",
                    "Visual layers",
                    "Audio & captions",
                    "Saved history",
                  ])[i]
            }
          </button>
        ))}
      </div>
      <section
        hidden={panel !== "visuals"}
        className="timeline-editor-panel"
        role="tabpanel"
        id="timeline-panel-visuals"
        aria-labelledby="timeline-tab-visuals"
      >
        <TimelineVisuals
          projectId={projectId}
          shots={shots}
          content={content}
          selected={selectedVisual}
          disabled={pending}
          onChange={(next) => setHistory((h) => edit(h, next))}
          onSelect={(id, time) => {
            setSelectedVisual(id);
            setComposition(true);
            setCompositionTime(time);
          }}
        />
      </section>
      <div
        hidden={panel !== "tracks"}
        className="timeline-editor-panel"
        role="tabpanel"
        id="timeline-panel-tracks"
        aria-labelledby="timeline-tab-tracks"
      >
        <TimelineTracks
          projectId={projectId}
          timelineId={timelineId}
          content={content}
          durationMs={total * 1000}
          disabled={pending}
          onChange={(next) => setHistory((h) => edit(h, next))}
          activeTab={trackTab}
          onTabChange={setTrackTab}
          onBusy={setTracksBusy}
        />
      </div>
      <div
        hidden={panel !== "history"}
        className="timeline-editor-panel"
        role="tabpanel"
        id="timeline-panel-history"
        aria-labelledby="timeline-tab-history"
      >
        <TimelineSavedHistory
          projectId={projectId}
          timelineId={timelineId}
          revision={revision}
          dirty={!saved}
          disabled={pending}
          onBusy={setRemoteBusy}
          onRestore={(doc) => {
            const next = {
              visuals: doc.visuals || [],
              items: doc.items,
              audio: doc.audio,
              subtitles: doc.subtitles,
            };
            setRevision(doc.revision);
            setHistory({ past: [], future: [], present: next });
            setSavedSnapshot(JSON.stringify(next));
          }}
        />
      </div>
      {invalid && (
        <p role="alert" className="text-sm text-danger">
          {zh
            ? "请检查时间范围：叠加画面、字幕和音轨须在时间线内；叠加画框不能越界，同轨片段不能重叠。"
            : "Check clip, caption and audio ranges before saving."}
        </p>
      )}
      <section
        hidden={panel !== "clips"}
        className="timeline-editor-panel space-y-3"
        role="tabpanel"
        id="timeline-panel-clips"
        aria-labelledby="timeline-tab-clips"
      >
        <h3 className="text-sm font-medium">
          {zh ? "镜头顺序" : "Clip order"}
        </h3>
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
              setClipPreview(clips.length);
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
            hidden={i !== activeClip}
            className="timeline-clip-inspector rounded-lg border border-border bg-bg p-3"
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
                {zh ? "接下一镜" : "Transition"}
                <select
                  aria-label={`${zh ? "转场" : "Transition"} ${i + 1}`}
                  className="h-8 rounded-md border bg-bg px-2 text-foreground"
                  disabled={pending}
                  value={String(c.transition?.type || "none")}
                  onChange={(e) =>
                    update(i, {
                      transition:
                        e.target.value === "none"
                          ? null
                          : {
                              type: e.target.value,
                              duration_ms: Math.max(
                                100,
                                Math.min(
                                  500,
                                  Math.floor(
                                    Math.min(
                                      c.duration_ms,
                                      clips[i + 1]?.duration_ms || 0,
                                    ) / 200,
                                  ) * 100,
                                ),
                              ),
                            },
                    })
                  }
                >
                  <option value="none">{zh ? "直切" : "Cut"}</option>
                  <option value="dissolve" disabled={i === clips.length - 1}>
                    {zh ? "叠化" : "Dissolve"}
                  </option>
                  <option value="fadeblack" disabled={i === clips.length - 1}>
                    {zh ? "淡黑" : "Fade through black"}
                  </option>
                  <option value="wipeleft" disabled={i === clips.length - 1}>
                    {zh ? "向左擦除" : "Wipe left"}
                  </option>
                </select>
              </label>
              {c.transition && (
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  {zh ? "转场时长 (s)" : "Transition duration (s)"}
                  <input
                    aria-label={`${zh ? "转场时长" : "Transition duration"} ${i + 1}`}
                    type="number"
                    step={0.1}
                    min={0.1}
                    max={Math.min(
                      2,
                      c.duration_ms / 2000,
                      (clips[i + 1]?.duration_ms || 0) / 2000,
                    )}
                    value={Number(c.transition.duration_ms) / 1000}
                    disabled={pending}
                    className="h-8 w-20 rounded-md border bg-bg px-2 text-foreground"
                    onChange={(e) =>
                      update(i, {
                        transition: {
                          ...c.transition,
                          duration_ms: Math.round(
                            Number(e.target.value) * 1000,
                          ),
                        },
                      })
                    }
                  />
                </label>
              )}
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
    </div>
  );
}

function useClipMedia(projectId: string, clip: Clip, shot?: Shot) {
  const query = useQuery({
    queryKey: ["gens", "shot", clip.shot_id],
    queryFn: () =>
      api.get<Generation[]>(
        `/projects/${projectId}/generations?target_type=shot&target_id=${clip.shot_id}`,
      ),
  });
  return {
    ...query,
    media: query.data?.find(
      (g) => g.id === (clip.generation_id || shot?.selected_generation_id),
    ),
  };
}

function ClipThumbnail({
  projectId,
  clip,
  shot,
  index,
  selected,
  onSelect,
}: {
  projectId: string;
  clip: Clip;
  shot?: Shot;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const zh = useI18n().lang === "zh",
    { media } = useClipMedia(projectId, clip, shot);
  return (
    <button
      type="button"
      aria-label={`${zh ? "预览片段" : "Preview clip"} ${index + 1}`}
      aria-pressed={selected}
      onClick={onSelect}
      className={`timeline-thumbnail group relative w-32 flex-shrink-0 overflow-hidden rounded-md border text-left transition ${selected ? "border-primary ring-1 ring-primary" : "border-border hover:border-border-strong"}`}
    >
      <div className="flex h-20 items-center justify-center bg-elevated">
        {media?.output_type === "image" && media.output_blob_hash ? (
          <img
            src={blobUrl(projectId, media.output_blob_hash)}
            loading="lazy"
            alt=""
            className="h-full w-full object-cover"
          />
        ) : (
          <Film className="h-5 w-5 text-muted-foreground" />
        )}
      </div>
      <div className="p-1.5">
        <p className="truncate text-xs">
          {String(index + 1).padStart(2, "0")} {shot?.title || shot?.code || ""}
        </p>
        <p className="mt-1 text-[10px] tabular-nums text-muted-foreground">
          {(clip.duration_ms / 1000).toFixed(1)} s
          {clip.transition ? ` · ${zh ? "转场" : "Transition"}` : ""}
        </p>
      </div>
    </button>
  );
}

function ClipPreview({
  projectId,
  clip,
  shot,
}: {
  projectId: string;
  clip: Clip;
  shot?: Shot;
}) {
  const zh = useI18n().lang === "zh",
    { media, error, isLoading } = useClipMedia(projectId, clip, shot);
  const [failed, setFailed] = useState(false);
  if (isLoading || error || failed || !media?.output_blob_hash)
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
        {error
          ? (error as Error).message
          : isLoading
            ? zh
              ? "读取素材…"
              : "Loading…"
            : failed
              ? zh
                ? "素材暂不可用"
                : "Media unavailable"
              : zh
                ? "这个片段尚未选择素材"
                : "Choose media for this clip"}
      </div>
    );
  const src = blobUrl(projectId, media.output_blob_hash);
  if (media.output_type === "image")
    return (
      <img
        src={src}
        alt={shot?.title || (zh ? "镜头素材" : "Shot media")}
        className="h-full w-full object-contain"
        onError={() => setFailed(true)}
      />
    );
  return (
    <video
      src={src}
      controls
      preload="metadata"
      aria-label={zh ? "片段素材预览" : "Clip source preview"}
      className="h-full w-full object-contain"
      onError={() => setFailed(true)}
      onLoadedMetadata={(e) => {
        e.currentTarget.currentTime = clip.in_point_ms / 1000;
      }}
      onPlay={(e) => {
        const v = e.currentTarget;
        if (
          v.currentTime < clip.in_point_ms / 1000 ||
          v.currentTime >= (clip.in_point_ms + clip.duration_ms) / 1000
        )
          v.currentTime = clip.in_point_ms / 1000;
      }}
      onTimeUpdate={(e) => {
        if (
          e.currentTarget.currentTime >=
          (clip.in_point_ms + clip.duration_ms) / 1000
        )
          e.currentTarget.pause();
      }}
    />
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
