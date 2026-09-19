import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Captions,
  Music2,
  Plus,
  Trash2,
  Upload,
  History as HistoryIcon,
  Download,
} from "lucide-react";
import { api, apiUpload, blobUrl } from "@/lib/api";
import type {
  AudioClip,
  SubtitleCue,
  TimelineContent,
  TimelineDocument,
} from "@/lib/timeline-edit";
import { Button } from "@/components/ui/button";
import { MediaAudio } from "@/components/MediaAudio";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";

type AudioSource = {
  id: string;
  name: string;
  blob_hash: string;
  duration_ms: number;
};
const input = "h-9 min-w-0 w-full rounded-md border bg-bg px-2 text-sm";

function Seconds({
  label,
  value,
  onChange,
  max,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  max?: number;
  disabled: boolean;
}) {
  return (
    <label className="min-w-0 text-xs text-muted-foreground">
      {label}
      <input
        aria-label={label}
        type="number"
        min={0}
        max={max == null ? undefined : max / 1000}
        step={0.1}
        className={`${input} mt-1 text-foreground tabular-nums`}
        value={value / 1000}
        disabled={disabled}
        onChange={(e) => onChange(Math.round(Number(e.target.value) * 1000))}
      />
    </label>
  );
}

function downloadSrt(cues: SubtitleCue[]) {
  const stamp = (v: number) =>
    `${String(Math.floor(v / 3600000)).padStart(2, "0")}:${String(Math.floor(v / 60000) % 60).padStart(2, "0")}:${String(Math.floor(v / 1000) % 60).padStart(2, "0")},${String(v % 1000).padStart(3, "0")}`;
  const text =
    [...cues]
      .sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms)
      .map(
        (c, n) =>
          `${n + 1}\n${stamp(c.start_ms)} --> ${stamp(c.end_ms)}\n${c.text}`,
      )
      .join("\n\n") + "\n";
  const url = URL.createObjectURL(
    new Blob([text], { type: "application/x-subrip;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = "subtitles.srt";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function TimelineTracks({
  projectId,
  timelineId,
  content,
  durationMs,
  disabled,
  onChange,
  activeTab,
  onTabChange,
  onBusy,
}: {
  projectId: string;
  timelineId: string;
  content: TimelineContent;
  durationMs: number;
  disabled: boolean;
  onChange: (next: TimelineContent) => void;
  activeTab?: "audio" | "subtitles";
  onTabChange?: (tab: "audio" | "subtitles") => void;
  onBusy?: (busy: boolean) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [localTab, setLocalTab] = useState<"audio" | "subtitles">("audio");
  const tab = activeTab ?? localTab;
  const setTab = (next: "audio" | "subtitles") => {
    setLocalTab(next);
    onTabChange?.(next);
  };
  const [sourceId, setSourceId] = useState("");
  const [sourcePage, setSourcePage] = useState(0);
  const [cuePage, setCuePage] = useState(0);
  const [encoding, setEncoding] = useState("utf-8");
  const [fileError, setFileError] = useState("");
  const [captionPreview, setCaptionPreview] = useState<{
    items: SubtitleCue[];
    matched_clips: number;
    skipped_clips: number[];
    fingerprint: string;
  } | null>(null);
  const [localSources, setLocalSources] = useState<AudioSource[]>([]);
  const qc = useQueryClient();
  const base = `/projects/${projectId}/timelines/${timelineId}`;
  const sources = useQuery({
    queryKey: ["audio-sources", projectId, sourcePage],
    queryFn: () =>
      api.get<{ items: AudioSource[]; has_more: boolean }>(
        `/projects/${projectId}/audio-sources?offset=${sourcePage * 50}`,
      ),
  });
  const allSources = [
    ...localSources,
    ...(sources.data?.items || []).filter(
      (s) => !localSources.some((l) => l.id === s.id),
    ),
  ];
  const source = allSources.find((s) => s.id === sourceId);
  const upload = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > 100 * 1024 * 1024)
        throw new Error(
          zh ? "音频须在100 MB以内" : "Audio must be under 100 MB",
        );
      const form = new FormData();
      form.append("file", file);
      return apiUpload<AudioSource>(`${base}/audio`, form);
    },
    onSuccess: (s) => {
      setLocalSources((previous) => [s, ...previous]);
      setSourceId(s.id);
      void qc.invalidateQueries({ queryKey: ["audio-sources", projectId] });
    },
  });
  const parse = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > 1_000_000)
        throw new Error(
          zh ? "字幕文件须在1 MB以内" : "Subtitles must be under 1 MB",
        );
      const text = new TextDecoder(encoding, { fatal: true }).decode(
        await file.arrayBuffer(),
      );
      return api.post<{ items: SubtitleCue[] }>(`${base}/subtitles/parse`, {
        content: text,
      });
    },
    onSuccess: (result) => {
      if (content.subtitles.length + result.items.length > 10000) {
        setFileError(zh ? "最多10000条字幕" : "Maximum 10,000 cues");
        return;
      }
      onChange({
        ...content,
        subtitles: [...content.subtitles, ...result.items].sort(
          (a, b) => a.start_ms - b.start_ms,
        ),
      });
      setCuePage(0);
    },
  });
  const mapCaptions = useMutation({
    mutationFn: async (snapshot: TimelineContent) => ({
      ...(await api.post<{
        items: SubtitleCue[];
        matched_clips: number;
        skipped_clips: number[];
      }>(`${base}/subtitles/from-sources`, { items: snapshot.items })),
      fingerprint: JSON.stringify(snapshot),
    }),
    onSuccess: setCaptionPreview,
  });
  const busy =
    disabled || upload.isPending || parse.isPending || mapCaptions.isPending;
  const localBusy =
    upload.isPending || parse.isPending || mapCaptions.isPending;
  useEffect(() => {
    onBusy?.(localBusy);
    return () => onBusy?.(false);
  }, [localBusy, onBusy]);
  const changeAudio = (i: number, patch: Partial<AudioClip>) =>
    onChange({
      ...content,
      audio: content.audio.map((a, n) => (n === i ? { ...a, ...patch } : a)),
    });
  const changeCue = (i: number, patch: Partial<SubtitleCue>) =>
    onChange({
      ...content,
      subtitles: content.subtitles.map((c, n) =>
        n === i ? { ...c, ...patch } : c,
      ),
    });
  const page = Math.min(
    cuePage,
    Math.max(0, Math.ceil(content.subtitles.length / 50) - 1),
  );
  return (
    <section
      className="overflow-hidden rounded-xl border bg-panel"
      aria-label={zh ? "音轨与字幕" : "Audio and captions"}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <div
          className="flex gap-1"
          role="tablist"
          aria-label={zh ? "轨道类型" : "Track type"}
        >
          <Button
            role="tab"
            aria-selected={tab === "audio"}
            variant={tab === "audio" ? "outline" : "ghost"}
            size="sm"
            onClick={() => setTab("audio")}
          >
            <Music2 className="h-4 w-4" />
            {zh ? "声音" : "Audio"}{" "}
            <span className="text-muted-foreground">
              {content.audio.length}
            </span>
          </Button>
          <Button
            role="tab"
            aria-selected={tab === "subtitles"}
            variant={tab === "subtitles" ? "outline" : "ghost"}
            size="sm"
            onClick={() => setTab("subtitles")}
          >
            <Captions className="h-4 w-4" />
            {zh ? "字幕" : "Captions"}{" "}
            <span className="text-muted-foreground">
              {content.subtitles.length}
            </span>
          </Button>
        </div>
        <span className="text-xs tabular-nums text-muted-foreground">
          00:00 — {(durationMs / 1000).toFixed(1)} s
        </span>
      </div>
      <div className="space-y-4 p-4" role="tabpanel">
        {tab === "audio" ? (
          <>
            <p className="text-xs leading-5 text-muted-foreground">
              {zh
                ? "将配乐、对白和音效放到时间线上，可叠加播放。上传的原音频会保留在项目中，移除音轨不删除素材。"
                : "Place music, dialogue and effects on the timeline. Removing a track keeps the original in the project."}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <select
                aria-label={zh ? "音频素材" : "Audio source"}
                className={`${input} flex-1 basis-48`}
                value={sourceId}
                onChange={(e) => setSourceId(e.target.value)}
                disabled={busy}
              >
                <option value="">
                  {zh ? "选择项目音频…" : "Choose project audio…"}
                </option>
                {allSources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {((s.duration_ms || 0) / 1000).toFixed(1)} s
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                disabled={
                  busy ||
                  !source?.duration_ms ||
                  durationMs < 100 ||
                  content.audio.length >= 24
                }
                onClick={() =>
                  source &&
                  onChange({
                    ...content,
                    audio: [
                      ...content.audio,
                      {
                        generation_id: source.id,
                        name: source.name,
                        kind: "music",
                        start_ms: 0,
                        in_point_ms: 0,
                        duration_ms: Math.min(source.duration_ms, durationMs),
                        gain_db: 0,
                        fade_in_ms: 0,
                        fade_out_ms: 0,
                        muted: false,
                      },
                    ],
                  })
                }
              >
                <Plus className="h-4 w-4" />
                {zh ? "添加音轨" : "Add audio"}
              </Button>
              <label className="relative inline-flex h-9 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm">
                <Upload className="h-4 w-4" />
                {upload.isPending
                  ? zh
                    ? "读取音频…"
                    : "Reading…"
                  : zh
                    ? "上传音频"
                    : "Upload audio"}
                <input
                  aria-label={zh ? "上传音频文件" : "Upload audio file"}
                  type="file"
                  accept=".wav,.mp3,.m4a,.ogg,.flac"
                  className="absolute inset-0 w-full cursor-pointer opacity-0"
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) upload.mutate(file);
                    e.target.value = "";
                  }}
                />
              </label>
            </div>
            {(sourcePage > 0 || sources.data?.has_more) && (
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!sourcePage}
                  onClick={() => setSourcePage((p) => p - 1)}
                >
                  {zh ? "上一页素材" : "Previous sources"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!sources.data?.has_more}
                  onClick={() => setSourcePage((p) => p + 1)}
                >
                  {zh ? "下一页素材" : "Next sources"}
                </Button>
              </div>
            )}
            {sources.error && (
              <div role="alert" className="text-sm text-danger">
                {(sources.error as Error).message}
                <Button variant="ghost" onClick={() => void sources.refetch()}>
                  {zh ? "重试" : "Retry"}
                </Button>
              </div>
            )}
            {source && (
              <MediaAudio src={blobUrl(projectId, source.blob_hash)} />
            )}
            {!content.audio.length && (
              <div className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">
                {zh
                  ? "给画面加一点声音。从项目音频选择，或上传一段配乐。"
                  : "Choose or upload audio to add sound to your film."}
              </div>
            )}
            {content.audio.map((a, i) => (
              <article
                key={i}
                className="space-y-3 rounded-lg border p-3"
                aria-label={`${zh ? "音轨" : "Audio clip"} ${i + 1}`}
              >
                <div className="flex items-center gap-2">
                  <span className="rounded bg-accent/10 p-1.5 text-accent">
                    <Music2 className="h-4 w-4" />
                  </span>
                  <input
                    className={`${input} flex-1`}
                    aria-label={zh ? "音轨名称" : "Audio name"}
                    value={a.name}
                    maxLength={255}
                    disabled={busy}
                    onChange={(e) => changeAudio(i, { name: e.target.value })}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={
                      zh ? `移除音轨 ${i + 1}` : `Remove audio ${i + 1}`
                    }
                    disabled={busy}
                    onClick={() =>
                      onChange({
                        ...content,
                        audio: content.audio.filter((_, n) => n !== i),
                      })
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                <div
                  className="relative h-7 overflow-hidden rounded bg-elevated"
                  aria-label={`${a.start_ms / 1000}–${(a.start_ms + a.duration_ms) / 1000} s`}
                >
                  <div
                    className={`absolute inset-y-1 min-w-1 rounded ${a.muted ? "bg-muted-foreground/30" : "bg-accent/60"}`}
                    style={{
                      left: `${(a.start_ms / Math.max(1, durationMs)) * 100}%`,
                      width: `${(a.duration_ms / Math.max(1, durationMs)) * 100}%`,
                    }}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
                  <Seconds
                    label={zh ? "时间线位置 (s)" : "Timeline start (s)"}
                    value={a.start_ms}
                    onChange={(n) => changeAudio(i, { start_ms: n })}
                    max={durationMs}
                    disabled={busy}
                  />
                  <Seconds
                    label={zh ? "源音频入点 (s)" : "Source in (s)"}
                    value={a.in_point_ms}
                    onChange={(n) => changeAudio(i, { in_point_ms: n })}
                    disabled={busy}
                  />
                  <Seconds
                    label={zh ? "音轨时长 (s)" : "Audio duration (s)"}
                    value={a.duration_ms}
                    onChange={(n) => changeAudio(i, { duration_ms: n })}
                    max={durationMs}
                    disabled={busy}
                  />
                  <Seconds
                    label={zh ? "淡入 (s)" : "Fade in (s)"}
                    value={a.fade_in_ms}
                    onChange={(n) => changeAudio(i, { fade_in_ms: n })}
                    max={60000}
                    disabled={busy}
                  />
                  <Seconds
                    label={zh ? "淡出 (s)" : "Fade out (s)"}
                    value={a.fade_out_ms}
                    onChange={(n) => changeAudio(i, { fade_out_ms: n })}
                    max={60000}
                    disabled={busy}
                  />
                  <label className="text-xs text-muted-foreground">
                    {zh ? "音量 (dB)" : "Gain (dB)"}
                    <input
                      aria-label={zh ? "音量 (dB)" : "Gain (dB)"}
                      type="number"
                      min={-60}
                      max={12}
                      step={1}
                      className={`${input} mt-1 text-foreground`}
                      value={a.gain_db}
                      disabled={busy}
                      onChange={(e) =>
                        changeAudio(i, { gain_db: Number(e.target.value) })
                      }
                    />
                  </label>
                </div>
                <div className="flex flex-wrap items-center gap-4">
                  <select
                    aria-label={zh ? "声音用途" : "Audio kind"}
                    className="h-8 rounded border bg-bg px-2 text-xs"
                    value={a.kind}
                    disabled={busy}
                    onChange={(e) =>
                      changeAudio(i, {
                        kind: e.target.value as AudioClip["kind"],
                      })
                    }
                  >
                    <option value="music">{zh ? "配乐" : "Music"}</option>
                    <option value="dialogue">{zh ? "对白" : "Dialogue"}</option>
                    <option value="effect">{zh ? "音效" : "Effect"}</option>
                  </select>
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={a.muted}
                      disabled={busy}
                      onChange={(e) =>
                        changeAudio(i, { muted: e.target.checked })
                      }
                    />
                    {zh ? "静音此轨" : "Mute this clip"}
                  </label>
                </div>
              </article>
            ))}
          </>
        ) : (
          <>
            <div className="space-y-3 rounded-lg border border-border bg-bg p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="max-w-xl text-xs leading-5 text-muted-foreground">
                  {zh
                    ? "使用素材库已核对发布的字幕，按当前剪辑入点和转场自动换算时间。仅匹配直接从原片制作的视频片段。"
                    : "Map reviewed library captions to this cut, including trims and transitions. Supports clips created directly from library originals."}
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !content.items.length}
                  onClick={() => mapCaptions.mutate(structuredClone(content))}
                >
                  {mapCaptions.isPending
                    ? zh
                      ? "匹配中…"
                      : "Matching…"
                    : zh
                      ? "匹配原片字幕"
                      : "Match source captions"}
                </Button>
              </div>
              {mapCaptions.error && (
                <p role="alert" className="text-xs text-danger">
                  {mapCaptions.error.message}
                </p>
              )}
              {captionPreview && (
                <div className="space-y-2 border-t pt-3">
                  <p role="status" className="text-xs">
                    {zh
                      ? `匹配 ${captionPreview.items.length} 条字幕，覆盖 ${captionPreview.matched_clips} 个画面片段。${captionPreview.skipped_clips.length ? `第 ${captionPreview.skipped_clips.join("、")} 段未匹配。` : ""}`
                      : `${captionPreview.items.length} captions across ${captionPreview.matched_clips} clips. ${captionPreview.skipped_clips.length} clips unmatched.`}
                  </p>
                  {captionPreview.items.slice(0, 3).map((cue, i) => (
                    <p
                      key={i}
                      className="truncate text-xs text-muted-foreground"
                    >
                      {(cue.start_ms / 1000).toFixed(2)}–
                      {(cue.end_ms / 1000).toFixed(2)}s · {cue.text}
                    </p>
                  ))}
                  <Button
                    size="sm"
                    disabled={
                      busy ||
                      !captionPreview.items.length ||
                      captionPreview.fingerprint !== JSON.stringify(content)
                    }
                    onClick={() => {
                      onChange({ ...content, subtitles: captionPreview.items });
                      setCaptionPreview(null);
                      setCuePage(0);
                    }}
                  >
                    {zh
                      ? "用匹配结果替换字幕草稿"
                      : "Replace caption draft with matches"}
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    {captionPreview.fingerprint !== JSON.stringify(content)
                      ? zh
                        ? "剪辑或字幕已变化，请重新匹配。"
                        : "The cut or captions changed. Match again."
                      : zh
                        ? "替换可撤销，保存剪辑后才写入。转场处的重叠字幕请预览核对。"
                        : "Undo is available; changes persist only when saving the cut. Review overlapping transition captions."}
                  </p>
                </div>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="relative inline-flex h-9 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm">
                <Upload className="h-4 w-4" />
                {parse.isPending
                  ? zh
                    ? "读取字幕…"
                    : "Reading…"
                  : zh
                    ? "追加 SRT"
                    : "Append SRT"}
                <input
                  aria-label={
                    zh ? "导入时间线字幕" : "Import timeline subtitles"
                  }
                  type="file"
                  accept=".srt"
                  className="absolute inset-0 w-full cursor-pointer opacity-0"
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    setFileError("");
                    if (file) parse.mutate(file);
                    e.target.value = "";
                  }}
                />
              </label>
              <select
                aria-label={zh ? "字幕文件编码" : "Subtitle encoding"}
                className="h-9 rounded-md border bg-bg px-2 text-xs"
                value={encoding}
                onChange={(e) => setEncoding(e.target.value)}
              >
                <option value="utf-8">UTF-8</option>
                <option value="gb18030">GB18030</option>
              </select>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  busy || durationMs < 100 || content.subtitles.length >= 10000
                }
                onClick={() => {
                  const start = Math.min(
                    content.subtitles[content.subtitles.length - 1]?.end_ms ||
                      0,
                    Math.max(0, durationMs - 100),
                  );
                  onChange({
                    ...content,
                    subtitles: [
                      ...content.subtitles,
                      {
                        start_ms: start,
                        end_ms: Math.min(start + 2000, durationMs),
                        text: "",
                      },
                    ],
                  });
                  setCuePage(Math.floor(content.subtitles.length / 50));
                }}
              >
                <Plus className="h-4 w-4" />
                {zh ? "添加字幕" : "Add caption"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={
                  !content.subtitles.length ||
                  content.subtitles.some(
                    (c) =>
                      !c.text.trim() ||
                      c.start_ms < 0 ||
                      c.end_ms <= c.start_ms,
                  )
                }
                onClick={() => downloadSrt(content.subtitles)}
              >
                <Download className="h-4 w-4" />
                {zh ? "导出 SRT 草稿" : "Export SRT draft"}
              </Button>
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              {zh
                ? "字幕按时间线时间定位。导入会追加，可撤销；与画面、声音一起保存。重叠字幕会同时显示，请根据成片检查排版。"
                : "Captions use timeline time. Imports append and can be undone. Overlapping captions appear together."}
            </p>
            {!content.subtitles.length && (
              <div className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">
                {zh
                  ? "导入已有台词，或为这段画面添加一句字幕。"
                  : "Import your dialogue or add a caption."}
              </div>
            )}
            {content.subtitles
              .slice(page * 50, page * 50 + 50)
              .map((c, offset) => {
                const i = page * 50 + offset;
                return (
                  <article
                    key={i}
                    className="grid gap-3 rounded-lg border p-3 sm:grid-cols-[9rem_minmax(0,1fr)_2rem]"
                    aria-label={`${zh ? "字幕" : "Caption"} ${i + 1}`}
                  >
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-1">
                      <Seconds
                        label={zh ? "开始 (s)" : "Start (s)"}
                        value={c.start_ms}
                        onChange={(n) => changeCue(i, { start_ms: n })}
                        max={durationMs}
                        disabled={busy}
                      />
                      <Seconds
                        label={zh ? "结束 (s)" : "End (s)"}
                        value={c.end_ms}
                        onChange={(n) => changeCue(i, { end_ms: n })}
                        max={durationMs}
                        disabled={busy}
                      />
                    </div>
                    <textarea
                      aria-label={`${zh ? "字幕文字" : "Caption text"} ${i + 1}`}
                      className="min-h-24 w-full resize-y rounded-md border bg-bg p-3 text-sm leading-6"
                      maxLength={4000}
                      value={c.text}
                      disabled={busy}
                      placeholder={zh ? "输入字幕…" : "Caption…"}
                      onChange={(e) => changeCue(i, { text: e.target.value })}
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={
                        zh ? `删除字幕 ${i + 1}` : `Delete caption ${i + 1}`
                      }
                      disabled={busy}
                      onClick={() =>
                        onChange({
                          ...content,
                          subtitles: content.subtitles.filter(
                            (_, n) => n !== i,
                          ),
                        })
                      }
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </article>
                );
              })}
            {content.subtitles.length > 50 && (
              <div className="flex items-center justify-between text-xs">
                <Button
                  variant="ghost"
                  disabled={!page}
                  onClick={() => setCuePage(page - 1)}
                >
                  {zh ? "上一页" : "Previous"}
                </Button>
                <span>
                  {page + 1} / {Math.ceil(content.subtitles.length / 50)}
                </span>
                <Button
                  variant="ghost"
                  disabled={(page + 1) * 50 >= content.subtitles.length}
                  onClick={() => setCuePage(page + 1)}
                >
                  {zh ? "下一页" : "Next"}
                </Button>
              </div>
            )}
          </>
        )}
        {(upload.error || parse.error || fileError) && (
          <p role="alert" className="text-sm text-danger">
            {fileError ||
              (upload.error as Error)?.message ||
              (parse.error as Error)?.message}
          </p>
        )}
      </div>
    </section>
  );
}

export function TimelineSavedHistory({
  projectId,
  timelineId,
  revision,
  dirty,
  disabled,
  onRestore,
  onBusy,
}: {
  projectId: string;
  timelineId: string;
  revision: number;
  dirty: boolean;
  disabled: boolean;
  onRestore: (doc: TimelineDocument) => void;
  onBusy: (busy: boolean) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const confirm = useConfirm();
  const qc = useQueryClient();
  const base = `/projects/${projectId}/timelines/${timelineId}`;
  const history = useQuery({
    queryKey: ["timeline-history", projectId, timelineId, page],
    enabled: open,
    queryFn: () =>
      api.get<{
        items: {
          revision: number;
          label: string;
          created_at: string;
          clips: number;
          audio: number;
          subtitles: number;
          visuals: number;
        }[];
        has_more: boolean;
      }>(`${base}/history?offset=${page * 50}`),
  });
  const restore = useMutation({
    mutationFn: (target: number) =>
      api.post<TimelineDocument>(`${base}/history/${target}/restore`, {
        revision,
      }),
    onSuccess: (doc) => {
      onRestore(doc);
      setPage(0);
      void qc.invalidateQueries({
        queryKey: ["timeline-history", projectId, timelineId],
      });
      void qc.invalidateQueries({
        queryKey: ["timeline-document", projectId, timelineId],
      });
    },
  });
  const reload = useMutation({
    mutationFn: () => api.get<TimelineDocument>(`${base}/document`),
    onSuccess: onRestore,
  });
  useEffect(() => {
    onBusy(restore.isPending || reload.isPending);
  }, [restore.isPending, reload.isPending, onBusy]);
  return (
    <section className="rounded-xl border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <HistoryIcon className="h-4 w-4" />
          {zh ? "保存历史" : "Saved history"}
          <span className="text-muted-foreground">v{revision}</span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={disabled || reload.isPending || restore.isPending}
          onClick={async () => {
            if (
              !dirty ||
              (await confirm({
                title: zh ? "重新载入时间线" : "Reload timeline",
                message: zh
                  ? "未保存的草稿会丢失。请先导出需要保留的字幕或记录调整。"
                  : "Unsaved changes will be lost.",
              }))
            )
              reload.mutate();
          }}
        >
          {zh ? "重新载入" : "Reload"}
        </Button>
      </div>
      {open && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-muted-foreground">
            {zh
              ? "每次保存保留完整画面、音轨与字幕。恢复会生成新版本，不删除后续历史。"
              : "Each save preserves clips, audio and captions. Restoring creates a new revision."}
          </p>
          {history.isLoading && (
            <p className="text-sm">{zh ? "读取历史…" : "Loading history…"}</p>
          )}
          {history.data?.items.length === 0 && (
            <p className="py-3 text-sm text-muted-foreground">
              {zh
                ? "保存后将在这里记录版本。"
                : "Your saved revisions will appear here."}
            </p>
          )}
          {history.data?.items.map((r) => (
            <div
              key={r.revision}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-elevated px-3 py-2"
            >
              <div className="min-w-0 text-xs">
                <p className="font-medium">
                  v{r.revision} · {r.label}
                </p>
                <p className="mt-1 text-muted-foreground">
                  {new Date(r.created_at).toLocaleString()} · {r.clips}{" "}
                  {zh ? "画面" : "clips"} / {r.audio} {zh ? "音轨" : "audio"} /{" "}
                  {r.subtitles} {zh ? "字幕" : "captions"}
                  {" · "}
                  {r.visuals || 0} {zh ? "叠加画面" : "visual layers"}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  disabled ||
                  restore.isPending ||
                  reload.isPending ||
                  r.revision === revision
                }
                onClick={async () => {
                  if (
                    await confirm({
                      title: zh
                        ? `恢复到版本 ${r.revision}`
                        : `Restore revision ${r.revision}`,
                      message: dirty
                        ? zh
                          ? "当前未保存草稿会被替换。已保存历史仍会保留。"
                          : "Unsaved changes will be replaced. Saved history is preserved."
                        : zh
                          ? "将按此版本的画面、音轨和字幕创建新的保存记录。"
                          : "Create a new revision using this snapshot.",
                    })
                  )
                    restore.mutate(r.revision);
                }}
              >
                {zh ? "恢复" : "Restore"}
              </Button>
            </div>
          ))}
          {(page > 0 || history.data?.has_more) && (
            <div className="flex justify-between">
              <Button
                size="sm"
                variant="ghost"
                disabled={!page}
                onClick={() => setPage((p) => p - 1)}
              >
                {zh ? "较新记录" : "Newer"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={!history.data?.has_more}
                onClick={() => setPage((p) => p + 1)}
              >
                {zh ? "较早记录" : "Older"}
              </Button>
            </div>
          )}
        </div>
      )}
      {(history.error || restore.error || reload.error) && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {((restore.error || reload.error || history.error) as Error).message}
        </p>
      )}
    </section>
  );
}
