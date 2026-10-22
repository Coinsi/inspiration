import { Modal } from "./ui/modal";
import { TimelineTimeInput } from "./TimelineTimeInput";
import { useTimelineBounds } from "@/lib/use-timeline-bounds";
import { clipSpans, timelineEditIssue } from "@/lib/timeline-interaction";
import { TimelineAddMedia } from "./TimelineAddMedia";
import { TimelineWorks } from "./WorkEditing";
import { StudioSelect } from "@/components/ui/studio-select";
import { ObjectPicker } from "@/components/ObjectPicker";
import { useEffect, useState, type ReactNode } from "react";
import {
  edit,
  editableContent,
  withInstanceIds,
  undo,
  redo,
  splitClip,
  duplicateClip,
  timelineDuration,
  visualIssue,
  type Clip,
  type History,
  type TimelineContent,
  type TimelineDocument,
} from "@/lib/timeline-edit";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowDown, ArrowUp, Download, Film, PanelRight, Plus,
  Redo2, Save, Trash2, Undo2, X,
} from "lucide-react";
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
import { EngineeringReview } from "@/components/EngineeringReview";
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
  headerAction,
}: {
  projectId: string;
  timelines: Timeline[];
  shots: Shot[];
  headerAction?: ReactNode;
}) {
  const [search, setSearch] = useSearchParams();
  const { lang } = useI18n();
  const zh = lang === "zh";
  const [dirty, setDirty] = useState(false);
  const [workingBusy, setWorkingBusy] = useState(false);
  const confirm = useConfirm();
  const requested = search.get("timeline");
  const id = requested
    ? timelines.find((t) => t.id === requested)?.id || ""
    : timelines[0]?.id || "";
  useEffect(() => {
    if (id && !requested)
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
      <div className="editing-project-bar">
        <div className="timeline-document-bar">
          <label className="text-xs text-muted-foreground">
            {zh ? "时间线" : "Timeline"}
            <StudioSelect
              aria-label={zh ? "工作时间线" : "Working timeline"}
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-sm text-foreground"
              value={id}
              disabled={workingBusy}
              onValueChange={async (selectedValue) => {
                const value = selectedValue;
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
                  const next = new URLSearchParams(search);
                  next.set("timeline", value);
                  setSearch(next, { replace: true });
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
            </StudioSelect>
          </label>
        </div>
        <div className="editing-project-tools">
          <TimelineWorks key={id} projectId={projectId} timelineId={id} />
          {headerAction}
        </div>
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
  const sourceBounds = useTimelineBounds(projectId, content, shots);
  const clips = content.items;
  const hasContent = Boolean(
    clips.length ||
    content.visuals.length ||
    content.audio.length ||
    content.subtitles.length,
  );
  const [savedSnapshot, setSavedSnapshot] = useState(() =>
    JSON.stringify(content),
  );
  const saved = savedSnapshot === JSON.stringify(content);
  const [splitOffsets, setSplitOffsets] = useState<Record<number, number>>({});
  useEffect(() => {
    onDirty(!saved);
  }, [saved, onDirty]);
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
  const renderJob = useQuery({
    queryKey: ["job", projectId, lastJob],
    queryFn: () => api.get<GenJob>(`${base}/jobs/${lastJob}`),
    enabled: !!lastJob,
    refetchInterval: (query) =>
      ["queued", "submitted", "running", "pending"].includes(
        query.state.data?.status || "pending",
      )
        ? 2000
        : false,
  });
  const [preview, setPreview] = useState("");
  const [clipPreview, setClipPreview] = useState<number | null>(null);
  const [panel, setPanel] = useState<
    "clips" | "visuals" | "tracks" | "history"
  >("clips");
  const [composition, setComposition] = useState(true);
  const [exportOpen, setExportOpen] = useState(false);
  const [addingMedia, setAddingMedia] = useState(false);
  const [compositionTime, setCompositionTime] = useState(0);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [addingType, setAddingType] = useState<"all" | "audio">("all");
  const [exportsOpen, setExportsOpen] = useState(false);
  const [trackHeight, setTrackHeight] = useState(180);
  const [propertiesOpen, setPropertiesOpen] = useState(hasContent);
  const [selectedVisual, setSelectedVisual] = useState("");
  const [trackTab, setTrackTab] = useState<"audio" | "subtitles">("audio");
  const openPanel = (next: typeof panel) => {
    setPanel(next);
    setPropertiesOpen(true);
  };
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
    (g) =>
      g.output_type === "video" &&
      g.output_blob_hash &&
      !g.input_refs?.engineering_change_id &&
      g.input_refs?.operation !== "timeline_library_source",
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
    setHistory((h) => edit(h, withInstanceIds({ ...h.present, items: next })));
  };
  const update = (index: number, patch: Partial<Clip>) =>
    change(clips.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const move = (index: number, by: number) => {
    const next = [...clips];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    change(
      next.map((c, i) =>
        i === next.length - 1 ? { ...c, transition: null } : c,
      ),
    );
    setClipPreview(index + by);
    setComposition(true);
  };
  const persist = async () => {
    const result = await api.put<TimelineDocument>(
      `${base}/timelines/${timelineId}/document`,
      { ...content, revision },
    );
    setRevision(result.revision);
    const savedContent = editableContent(result);
    setHistory((h) => ({ ...h, present: savedContent }));
    setSavedSnapshot(JSON.stringify(savedContent));
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
      setExportOpen(false);
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const renderRunning = ["pending", "queued", "submitted", "running"].includes(
    renderJob.data?.status || "",
  );
  const total = timelineDuration(clips) / 1000;
  const sourceRangeInvalid = [
    ...content.items,
    ...content.audio,
    ...content.visuals,
  ].some((c) => {
    const id =
      c.generation_id ||
      ("shot_id" in c
        ? shots.find((s) => s.id === c.shot_id)?.selected_generation_id
        : undefined);
    const bound = id ? sourceBounds[id] : undefined;
    return (
      bound &&
      !bound.image &&
      bound.duration_ms !== undefined &&
      c.in_point_ms + c.duration_ms > bound.duration_ms
    );
  });
  const invalid =
    sourceRangeInvalid ||
    (clips.length > 0 && !!timelineEditIssue(content)) ||
    visualIssue(content.visuals, total * 1000) ||
    clips.length > 100 ||
    total > 1800 ||
    clips.some(
      (c) =>
        c.duration_ms < 100 ||
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
  useEffect(() => {
    setCompositionTime((t) => Math.min(t, Math.max(0, total * 1000 - 1)));
  }, [total]);
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
  const shotPicker = shots.some((s) => s.selected_generation_id) && (
    <ObjectPicker
      title={zh ? "添加镜头" : "Add a shot"}
      closeOnSelect
      disabled={
        pending ||
        clips.length >= 100 ||
        !shots.some((s) => s.selected_generation_id)
      }
      selected={[]}
      items={shots
        .filter((s) => s.selected_generation_id)
        .map((s) => ({
          id: s.id,
          name: s.title || s.description || s.code,
          context: s.code,
        }))}
      onSelect={(id) => {
        const shot = shots.find((s) => s.id === id);
        if (!shot || !shot.selected_generation_id) return;
        change([
          ...clips,
          {
            shot_id: shot.id,
            id: crypto.randomUUID(),
            generation_id: shot.selected_generation_id,
            in_point_ms: 0,
            out_point_ms: 0,
            duration_ms: 3000,
          },
        ]);
        setClipPreview(clips.length);
        setComposition(true);
        openPanel("clips");
      }}
    />
  );
  return (
    <div
      className={`timeline-draft editing-workspace ${hasContent ? "has-content" : "is-empty"} ${clips.length ? "has-clips" : ""} ${propertiesOpen ? "has-properties" : ""} ${exportOpen ? "is-exporting" : ""}`}
      style={{ "--track-height": `${trackHeight}px` } as React.CSSProperties}
      onKeyDown={(e) => {
        if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
        const target = e.target as HTMLElement;
        if (e.key.toLowerCase() === "s") {
          e.preventDefault();
          const root = e.currentTarget;
          target.blur();
          requestAnimationFrame(() =>
            root
              .querySelector<HTMLButtonElement>("[data-save-timeline]")
              ?.click(),
          );
        } else if (
          !target.closest("input, textarea, select, [contenteditable=true]") &&
          !pending
        ) {
          if (e.key.toLowerCase() === "z") {
            e.preventDefault();
            setHistory(e.shiftKey ? redo : undo);
          }
          if (e.key.toLowerCase() === "y") {
            e.preventDefault();
            setHistory(redo);
          }
        }
      }}
    >
      <div className="timeline-edit-bar">
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="ghost"
            className="editing-history-action"
            title={zh ? "撤销剪辑 · Ctrl / ⌘ + Z" : "Undo edit · Ctrl / ⌘ + Z"}
            aria-label={zh ? "撤销剪辑" : "Undo edit"}
            disabled={!history.past.length || pending}
            onClick={() => setHistory(undo)}
          >
            <Undo2 size={16} />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="editing-history-action"
            title={zh ? "重做剪辑 · Ctrl / ⌘ + Shift + Z" : "Redo edit · Ctrl / ⌘ + Shift + Z"}
            aria-label={zh ? "重做剪辑" : "Redo edit"}
            disabled={!history.future.length || pending}
            onClick={() => setHistory(redo)}
          >
            <Redo2 size={16} />
          </Button>
          <span className="timeline-undo-hint self-center text-xs text-muted-foreground">
            {zh
              ? "Ctrl/⌘ + Z 撤销 · Ctrl/⌘ + S 保存"
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
            data-save-timeline
            title="Ctrl / ⌘ + S"
            disabled={saved || invalid || pending}
            onClick={() => save.mutate()}
          >
            <Save size={14} />
            {zh ? "保存" : "Save"}
          </Button>
        </div>
        <div className="editing-main-actions">
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={propertiesOpen}
            aria-controls="editing-properties"
            onClick={() => setPropertiesOpen(!propertiesOpen)}
          >
            <PanelRight size={15} />
            {zh ? "属性" : "Inspector"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setExportsOpen(true)}
          >
            历史导出
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setReviewOpen(true)}>
            作品版本
          </Button>
          <Button size="sm" onClick={() => setExportOpen(true)}>
            <Download size={14} />
            导出成片
          </Button>
        </div>
      </div>
      {addingMedia && (
        <TimelineAddMedia
          projectId={projectId}
          timelineId={timelineId}
          remainingMs={total * 1000}
          pictureFull={clips.length >= 100 || total >= 1800}
          mediaType={addingType}
          audioFull={content.audio.length >= 24}
          onClose={() => setAddingMedia(false)}
          onAdd={(source) => {
            if (source.media_type === "audio") {
              const start = Math.min(
                compositionTime,
                Math.max(0, total * 1000 - 100),
              );
              setHistory((h) =>
                edit(
                  h,
                  withInstanceIds({
                    ...h.present,
                    audio: [
                      ...h.present.audio,
                      {
                        id: crypto.randomUUID(),
                        generation_id: source.id,
                        name: source.name,
                        kind: "music",
                        start_ms: start,
                        in_point_ms: 0,
                        duration_ms: Math.min(
                          source.duration_ms || total * 1000,
                          total * 1000 - start,
                        ),
                        gain_db: 0,
                        fade_in_ms: 0,
                        fade_out_ms: 0,
                        muted: false,
                      },
                    ],
                  }),
                ),
              );
              openPanel("tracks");
              setTrackTab("audio");
            } else {
              change([
                ...clips,
                {
                  id: crypto.randomUUID(),
                  shot_id: null,
                  generation_id: source.id,
                  in_point_ms: 0,
                  out_point_ms: 0,
                  duration_ms: Math.min(
                    source.media_type === "image"
                      ? 3000
                      : source.duration_ms || 3000,
                    1800000 - total * 1000,
                  ),
                  note: source.name,
                },
              ]);
              setClipPreview(clips.length);
              openPanel("clips");
            }
            setComposition(true);
            void qc.invalidateQueries({
              queryKey: ["audio-sources", projectId],
            });
          }}
        />
      )}
      <aside className="editing-media" aria-label="剪辑素材" hidden={!hasContent}>
        <header>
          <strong>剪辑素材</strong>
          <span>{clips.length} 个画面</span>
        </header>
        <Button
          disabled={pending}
          onClick={() => {
            setAddingType("all");
            setAddingMedia(true);
          }}
        >
          <Plus size={15} />
          {zh ? "添加素材" : "Add media"}
        </Button>
        <div className="editing-media-list" aria-label="画面序列">
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
                setComposition(true);
                setCompositionTime(clipSpans(content)[i]?.start || 0);
                setClipPreview(i);
                openPanel("clips");
              }}
            />
          ))}

        </div>
      </aside>
      <Modal
        open={exportsOpen}
        onClose={() => setExportsOpen(false)}
        title="历史导出"
        width={560}
      >
        {outputs.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StudioSelect
              aria-label={zh ? "历史导出" : "Export history"}
              className="h-8 min-w-0 flex-1 rounded-md border bg-bg px-2 text-xs"
              value={result?.id || ""}
              onValueChange={(selectedValue) => {
                setComposition(false);
                setPreview(selectedValue);
                setClipPreview(null);
                setExportsOpen(false);
              }}
            >
              {outputs.map((g) => (
                <option key={g.id} value={g.id}>
                  {new Date(g.created_at).toLocaleString()} · {g.id.slice(0, 8)}
                </option>
              ))}
            </StudioSelect>
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
                      <MediaAudio
                        src={blobUrl(projectId, g.output_blob_hash!)}
                      />
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
        {!outputs.length && (
          <p className="p-4 text-sm text-muted-foreground">
            还没有导出的成片。
          </p>
        )}
      </Modal>
      <Modal
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        title="导出成片"
        width={440}
      >
        <fieldset
          disabled={pending}
          className="timeline-export-panel space-y-3"
          id="timeline-export-settings"
          hidden={!exportOpen}
        >
          {render.error && (
            <p role="alert" className="text-danger">
              导出提交失败：{render.error.message}
              。当前剪辑仍然保留，可修正后重试。
            </p>
          )}
          <h3 className="text-sm font-medium">
            {zh ? "导出设置" : "Export settings"}
          </h3>
          <p className="text-xs text-muted-foreground">
            {clips.length} {zh ? "段" : "clips"} · {total.toFixed(1)} s
          </p>
          <label className="block text-xs">
            {zh ? "画面比例" : "Aspect ratio"}
            <StudioSelect
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
              value={aspect}
              onValueChange={(selectedValue) => setAspect(selectedValue)}
            >
              {["16:9", "9:16", "1:1"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </StudioSelect>
          </label>
          <label className="block text-xs">
            {zh ? "短边分辨率" : "Short edge resolution"}
            <StudioSelect
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
              value={height}
              onValueChange={(selectedValue) =>
                setHeight(Number(selectedValue))
              }
            >
              <option value={720}>720p</option>
              <option value={1080}>1080p</option>
            </StudioSelect>
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
            <StudioSelect
              aria-label={zh ? "字幕导出方式" : "Subtitle export mode"}
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
              value={subtitleMode}
              onValueChange={(selectedValue) => setSubtitleMode(selectedValue)}
            >
              <option value="burn">
                {zh ? "写入画面（所有播放器可见）" : "Burn into video"}
              </option>
              <option value="track">
                {zh ? "可切换字幕轨" : "Selectable subtitle track"}
              </option>
              <option value="none">{zh ? "不带字幕" : "No subtitles"}</option>
            </StudioSelect>
          </label>
          <p className="text-xs leading-5 text-muted-foreground">
            {zh
              ? "MP4 · 24 fps。转场会缩短成片时长；图片按设置时长展示。视频、音频使用范围不能超出原素材。原声与附加音轨一起混合。"
              : "MP4 · 24 fps. Transitions overlap clips. Still images hold; video and audio ranges must fit their source. Source and added audio are mixed."}
          </p>
          <Button
            className="w-full"
            disabled={!clips.length || invalid || pending || renderRunning}
            onClick={() => render.mutate()}
          >
            {render.isPending || renderRunning
              ? zh
                ? "正在导出…"
                : "Submitting…"
              : zh
                ? "保存并导出 MP4"
                : "Save & export MP4"}
          </Button>
        </fieldset>{" "}
      </Modal>
      <aside
        className="editing-properties"
        id="editing-properties"
        aria-label={zh ? "剪辑属性" : "Editing inspector"}
        hidden={!propertiesOpen}
      >
        <header className="editing-properties-header">
          <strong>{zh ? "属性" : "Inspector"}</strong>
          <button
            type="button"
            aria-label={zh ? "收起属性" : "Close inspector"}
            onClick={() => setPropertiesOpen(false)}
          >
            <X size={16} />
          </button>
        </header>
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
                openPanel(p);
                if (p === "visuals") setComposition(true);
              }}
            >
              {
                (zh
                  ? ["片段", "画面", "音频/字幕", "历史"]
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
            onChange={(next) =>
              setHistory((h) => edit(h, withInstanceIds(next)))
            }
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
            onChange={(next) =>
              setHistory((h) => edit(h, withInstanceIds(next)))
            }
            activeTab={trackTab}
            onTabChange={setTrackTab}
            onBusy={setTracksBusy}
            onAddLibraryAudio={() => {
              setAddingType("audio");
              setAddingMedia(true);
            }}
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
              const next = editableContent(doc);
              setRevision(doc.revision);
              setHistory({ past: [], future: [], present: next });
              setSavedSnapshot(JSON.stringify(next));
            }}
          />
        </div>
        <section
          hidden={panel !== "clips"}
          className="timeline-editor-panel space-y-3"
          role="tabpanel"
          id="timeline-panel-clips"
          aria-labelledby="timeline-tab-clips"
        >
          {clips.length > 0 && shotPicker && (
            <div className="timeline-add-clip">{shotPicker}</div>
          )}
          {!clips.length && (
            <div className="editing-inspector-empty">
              <Film size={24} strokeWidth={1.4} />
              <p>{zh ? "添加画面后，在这里调整片段。" : "Clip controls appear here after you add media."}</p>
            </div>
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
                    c.shot_id?.slice(0, 8) ||
                    c.note ||
                    "独立素材片段"}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  title={zh ? "复制片段" : "Duplicate clip"}
                  disabled={pending || clips.length >= 100}
                  onClick={() => {
                    change(duplicateClip(clips, i));
                    setClipPreview(i + 1);
                    setComposition(true);
                  }}
                >
                  {zh ? "复制" : "Duplicate"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  title={zh ? "上移片段" : "Move up"}
                  aria-label={zh ? "上移片段" : "Move up"}
                  disabled={i === 0 || pending}
                  onClick={() => move(i, -1)}
                >
                  <ArrowUp className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  title={zh ? "下移片段" : "Move down"}
                  aria-label={zh ? "下移片段" : "Move down"}
                  disabled={i === clips.length - 1 || pending}
                  onClick={() => move(i, 1)}
                >
                  <ArrowDown className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  title={zh ? "移除片段" : "Remove clip"}
                  aria-label={zh ? "移除片段" : "Remove clip"}
                  disabled={pending}
                  onClick={() => {
                    const next = clips
                      .filter((_, n) => n !== i)
                      .map((c, n, all) =>
                        n === all.length - 1 ? { ...c, transition: null } : c,
                      );
                    change(next);
                    setClipPreview(Math.max(0, i - 1));
                    setComposition(true);
                  }}
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
                <label
                  hidden={
                    sourceBounds[
                      c.generation_id ||
                        shots.find((s) => s.id === c.shot_id)
                          ?.selected_generation_id ||
                        ""
                    ]?.image
                  }
                  className="text-xs text-muted-foreground"
                >
                  {zh ? "从素材第几秒开始" : "Start (seconds)"}
                  <TimelineTimeInput
                    aria-label={`${zh ? "入点" : "Start"} ${i + 1}`}
                    min={0}
                    disabled={pending}
                    value={c.in_point_ms}
                    onChange={(ms) => update(i, { in_point_ms: ms })}
                    className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-foreground"
                  />
                </label>
                <label className="text-xs text-muted-foreground">
                  {zh ? "播放多久（秒）" : "Duration (seconds)"}
                  <TimelineTimeInput
                    aria-label={`${zh ? "时长" : "Duration"} ${i + 1}`}
                    min={100}
                    disabled={pending}
                    value={c.duration_ms}
                    onChange={(ms) => update(i, { duration_ms: ms })}
                    className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-foreground"
                  />
                </label>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  {zh ? "接下一镜" : "Transition"}
                  <StudioSelect
                    aria-label={`${zh ? "转场" : "Transition"} ${i + 1}`}
                    className="h-8 rounded-md border bg-bg px-2 text-foreground"
                    disabled={pending}
                    value={String(c.transition?.type || "none")}
                    onValueChange={(selectedValue) =>
                      update(i, {
                        transition:
                          selectedValue === "none"
                            ? null
                            : {
                                type: selectedValue,
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
                  </StudioSelect>
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
                      (splitOffsets[i] ??
                        Math.floor(c.duration_ms / 200) * 100) / 1000
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
                        splitOffsets[i] ??
                          Math.floor(c.duration_ms / 200) * 100,
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
        </section>
      </aside>
      {videoTools && (
        <VideoTools
          projectId={projectId}
          generation={videoTools}
          onClose={() => setVideoTools(null)}
          onJob={setLastJob}
        />
      )}
      <div
        className={`timeline-preview-grid ${exportOpen ? "is-exporting" : ""}`}
      >
        <div className="timeline-monitor" data-aspect={aspect}>
          <div className="timeline-monitor-screen">
            {composition && clips.length > 0 ? (
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
              <div className="editing-start">
                <span className="editing-start-icon"><Film size={30} strokeWidth={1.3} /></span>
                <h2>{zh ? "让故事在这里连起来" : "Bring your story together"}</h2>
                <p>{zh ? "添加图片或视频，再编排镜头、声音与字幕。" : "Add images or video, then arrange your shots, sound and captions."}</p>
                <Button disabled={pending} onClick={() => { setAddingType("all"); setAddingMedia(true); }}>
                  <Plus size={17} />
                  {zh ? "添加素材" : "Add media"}
                </Button>
                {shotPicker && <div className="editing-start-existing">{shotPicker}</div>}
                {!shots.some((shot) => shot.selected_generation_id) && (
                  <Link to={`/projects/${projectId}/shots`} className="editing-start-link">{zh ? "前往分镜准备画面" : "Prepare shots in the storyboard"}</Link>
                )}
              </div>
            )}
          </div>
          {(clips.length > 0 || !!result) && <div className="editing-preview-caption mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>
              {composition
                ? zh
                  ? "实时预览 · 最终效果以导出为准"
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
                  ? "整体预览"
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
          </div>}
          {gens.error && (
            <p className="mt-2 text-xs text-danger">
              {(gens.error as Error).message}
            </p>
          )}
        </div>
      </div>
      {lastJob && (
        <div className="timeline-render-feedback" aria-live="polite">
          <JobStatus key={lastJob} projectId={projectId} jobId={lastJob} />
          {["failed", "canceled"].includes(renderJob.data?.status || "") && (
            <Button
              size="sm"
              variant="outline"
              disabled={!clips.length || invalid || pending}
              onClick={() => render.mutate()}
            >
              {zh ? "按当前剪辑重新导出" : "Export current edit again"}
            </Button>
          )}
        </div>
      )}
      {render.isError && (
        <div className="timeline-render-feedback" role="alert">
          <p>{(render.error as Error).message}</p>
          <Button
            size="sm"
            variant="outline"
            disabled={pending || invalid || !clips.length}
            onClick={() => render.mutate()}
          >
            {zh ? "重新提交导出" : "Retry export"}
          </Button>
        </div>
      )}

      {clips.length > 0 && (
        <div className="timeline-selection-tools">
          <span>
            已选片段 {activeClip + 1} / {clips.length}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={
              pending ||
              clips.length >= 100 ||
              !composition ||
              compositionTime - (clipSpans(content)[activeClip]?.start || 0) <
                100 ||
              compositionTime - (clipSpans(content)[activeClip]?.start || 0) >
                clips[activeClip].duration_ms - 100
            }
            title="先点选片段，再将播放头移到要切开的位置；两侧至少保留 0.1 秒"
            onClick={() => {
              change(
                splitClip(
                  clips,
                  activeClip,
                  Math.round(
                    compositionTime - clipSpans(content)[activeClip].start,
                  ),
                ),
              );
              setSplitOffsets({});
            }}
          >
            在播放头处拆分
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              openPanel("clips");
              requestAnimationFrame(() =>
                document
                  .getElementById("timeline-panel-clips")
                  ?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
              );
            }}
          >
            调整选中片段
          </Button>
          <span className="text-xs text-muted-foreground">
            拖动中间排序 · 拖动两侧调整时长
          </span>
        </div>
      )}
      {hasContent && <label className="editing-track-size">
        时间线高度
        <input
          aria-label="时间线高度"
          type="range"
          min={140}
          max={320}
          step={10}
          value={trackHeight}
          onChange={(e) => setTrackHeight(Number(e.target.value))}
        />
      </label>}
      {hasContent && (
        <TimelineOverview
          bounds={sourceBounds}
          content={content}
          shots={shots}
          selected={activeClip}
          disabled={pending}
          time={compositionTime}
          onTime={(t) => {
            setComposition(true);
            setCompositionTime(t);
          }}
          onChange={(next) => setHistory((h) => edit(h, withInstanceIds(next)))}
          onSelect={(index) => {
            setComposition(true);
            setCompositionTime(clipSpans(content)[index]?.start || 0);
            setClipPreview(index);
            openPanel("clips");
          }}
          onVisualSelect={(id) => {
            openPanel("visuals");
            setComposition(true);
            setSelectedVisual(id);
            const v = content.visuals.find((v) => v.id === id);
            if (v) setCompositionTime(v.start_ms);
          }}
          onTracks={(tab) => {
            setTrackTab(tab);
            openPanel("tracks");
          }}
        />
      )}
      {save.isError && (
        <p role="alert" className="text-sm text-danger">
          {save.error.message} ·{" "}
          {zh
            ? "当前调整仍保留在页面中，请处理后重新保存。"
            : "Your edits remain on this page. Resolve the issue and save again."}
        </p>
      )}
      {invalid && (
        <div role="alert" className="timeline-validation">
          <p>
            {sourceRangeInvalid
              ? "有片段超出原视频或音频时长，请缩短时长或调整素材起点。"
              : timelineEditIssue(content) ||
                "请检查时间与转场设置；最多 100 个画面片段。"}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!history.past.length || pending}
              onClick={() => setHistory(undo)}
            >
              撤销上一步
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                const message = timelineEditIssue(content);
                const target =
                  message.includes("声音") || message.includes("字幕")
                    ? "tracks"
                    : message.includes("叠加")
                      ? "visuals"
                      : "clips";
                openPanel(target);
                if (target === "tracks")
                  setTrackTab(message.includes("字幕") ? "subtitles" : "audio");
                const match = message.match(/第 (\d+) 个画面/);
                if (match) setClipPreview(Number(match[1]) - 1);
                requestAnimationFrame(() =>
                  document
                    .getElementById(`timeline-panel-${target}`)
                    ?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
                );
              }}
            >
              前往调整
            </Button>
          </div>
        </div>
      )}
      <Modal
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        title="作品版本与审核"
        width={1000}
      >
        <EngineeringReview
          projectId={projectId}
          timelineId={timelineId}
          document={{ ...content, revision }}
          aspect={aspect}
          disabled={pending || invalid || !clips.length}
          onApplied={(doc) => {
            const next = editableContent(doc);
            setRevision(doc.revision);
            setHistory({ past: [], future: [], present: next });
            setSavedSnapshot(JSON.stringify(next));
            void qc.invalidateQueries({
              queryKey: ["timeline-document", projectId, timelineId],
            });
            void qc.invalidateQueries({
              queryKey: ["timeline-history", projectId, timelineId],
            });
          }}
        />
      </Modal>
    </div>
  );
}

function useClipMedia(projectId: string, clip: Clip, shot?: Shot) {
  const query = useQuery({
    queryKey: ["gens", "clip", projectId, clip.generation_id, clip.shot_id],
    queryFn: () =>
      clip.generation_id
        ? api
            .get<Generation>(
              `/projects/${projectId}/generations/${clip.generation_id}`,
            )
            .then((g) => [g])
        : api.get<Generation[]>(
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
          {String(index + 1).padStart(2, "0")}{" "}
          {shot?.title || clip.note || shot?.code || ""}
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
    queryKey: ["gens", "shot", projectId, clip.shot_id],
    enabled: !!clip.shot_id,
    queryFn: () =>
      api.get<Generation[]>(
        `/projects/${projectId}/generations?target_type=shot&target_id=${clip.shot_id}`,
      ),
  });
  if (!clip.shot_id)
    return (
      <div className="min-w-0 text-sm">
        <span className="text-muted-foreground">
          {zh ? "素材库 · " : "Library · "}
        </span>
        {clip.note || (zh ? "独立素材" : "Independent media")}
      </div>
    );
  return (
    <label className="min-w-0 text-xs text-muted-foreground">
      {zh ? "使用素材" : "Media variant"}
      <StudioSelect
        className="mt-1 h-9 w-full rounded-md border bg-bg px-2 text-foreground"
        disabled={disabled}
        value={clip.generation_id || ""}
        onValueChange={(selectedValue) => onChange(selectedValue || null)}
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
      </StudioSelect>
      {error && <span className="text-danger">{(error as Error).message}</span>}
    </label>
  );
}
