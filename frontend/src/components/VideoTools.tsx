import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, blobUrl, type GenJob, type Generation } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";

export function VideoTools({
  projectId,
  generation,
  onClose,
  onJob,
}: {
  projectId: string;
  generation: Generation;
  onClose: () => void;
  onJob: (id: string) => void;
}) {
  const zh = useI18n().lang === "zh";
  const toast = useToast();
  const base = `/projects/${projectId}/generations/${generation.id}`;
  const video = useRef<HTMLVideoElement>(null);
  const [operation, setOperation] = useState<"frames" | "audio" | "trim">(
    "frames",
  );
  const [times, setTimes] = useState("0");
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("");
  const [failed, setFailed] = useState(false);
  const info = useQuery({
    queryKey: ["video-info", generation.id],
    queryFn: () =>
      api.get<{
        duration_ms: number;
        width: number;
        height: number;
        has_audio: boolean;
      }>(`${base}/video-info`),
    retry: false,
  });
  useEffect(() => {
    if (info.data) setEnd(String(info.data.duration_ms / 1000));
  }, [info.data]);
  const timesMs = times
    .split(/[,，\s]+/)
    .filter(Boolean)
    .map((t) => Math.round(Number(t) * 1000));
  const startMs = Math.round(Number(start) * 1000),
    endMs = Math.round(Number(end) * 1000);
  const duration = info.data?.duration_ms ?? 0;
  const valid =
    !!info.data &&
    (operation === "frames"
      ? timesMs.length > 0 &&
        timesMs.length <= 12 &&
        new Set(timesMs).size === timesMs.length &&
        timesMs.every((t) => Number.isFinite(t) && t >= 0 && t < duration)
      : start !== "" &&
        end !== "" &&
        Number.isFinite(startMs) &&
        Number.isFinite(endMs) &&
        startMs >= 0 &&
        endMs <= duration &&
        endMs - startMs >= 100 &&
        (operation !== "audio" || info.data.has_audio));
  const save = useMutation({
    mutationFn: () =>
      api.post<GenJob>(`${base}/video-tools`, {
        operation,
        times_ms: operation === "frames" ? timesMs : [],
        start_ms: operation === "frames" ? 0 : startMs,
        end_ms: operation === "frames" ? null : endMs,
      }),
    onSuccess: (j) => {
      onJob(j.id);
      onClose();
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const labels = zh
    ? { frames: "抽取画面", audio: "提取音轨", trim: "导出片段" }
    : {
        frames: "Extract frames",
        audio: "Extract audio",
        trim: "Export segment",
      };
  return (
    <Modal
      open
      onClose={onClose}
      title={zh ? "视频工具" : "Video tools"}
      width={820}
    >
      <p className="mb-4 text-sm text-muted-foreground">
        {zh
          ? "处理结果保存为新素材，原视频保留。支持 30 分钟、256 MB 以内的视频。"
          : "Save results as new media. Supports videos up to 30 minutes and 256 MB."}
      </p>
      <div className="grid gap-5 md:grid-cols-2">
        <div className="min-w-0 space-y-3">
          <video
            ref={video}
            src={blobUrl(projectId, generation.output_blob_hash!)}
            controls
            preload="metadata"
            aria-label={zh ? "源视频" : "Source video"}
            onError={() => setFailed(true)}
            className="aspect-video w-full rounded-lg bg-black"
          />
          {failed && (
            <p role="alert" className="text-sm text-danger">
              {zh
                ? "浏览器无法预览此视频，可尝试导出兼容片段。"
                : "Browser preview unavailable. Try exporting a compatible segment."}
            </p>
          )}
          {info.isPending && (
            <p className="text-sm">
              {zh ? "正在读取视频信息…" : "Reading video information…"}
            </p>
          )}
          {info.error && (
            <p role="alert" className="text-sm text-danger">
              {info.error.message}
            </p>
          )}
          {info.data && (
            <p className="text-xs text-muted-foreground">
              {info.data.width} × {info.data.height} ·{" "}
              {(duration / 1000).toFixed(2)} {zh ? "秒" : "s"} ·{" "}
              {info.data.has_audio
                ? zh
                  ? "含音轨"
                  : "Has audio"
                : zh
                  ? "无音轨"
                  : "No audio"}
            </p>
          )}
        </div>
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap gap-2">
            {(["frames", "audio", "trim"] as const).map((op) => (
              <Button
                key={op}
                size="sm"
                variant={operation === op ? "default" : "outline"}
                onClick={() => setOperation(op)}
              >
                {labels[op]}
              </Button>
            ))}
          </div>
          {operation === "frames" ? (
            <>
              <label className="block space-y-2 text-sm">
                <span>
                  {zh
                    ? "抽帧时间（秒，用逗号分隔）"
                    : "Frame times (seconds, comma separated)"}
                </span>
                <input
                  className="mt-1 h-9 w-full rounded-md border border-border bg-bg px-2 text-foreground"
                  value={times}
                  onChange={(e) => setTimes(e.target.value)}
                />
              </label>
              <Button
                variant="outline"
                size="sm"
                disabled={!info.data || timesMs.length >= 12}
                onClick={() => {
                  const ms = Math.round(
                    (video.current?.currentTime ?? 0) * 1000,
                  );
                  if (ms < duration && !timesMs.includes(ms))
                    setTimes(
                      [...timesMs.filter(Number.isFinite), ms]
                        .map((t) => t / 1000)
                        .join(", "),
                    );
                }}
              >
                {zh ? "加入当前画面" : "Add current frame"}
              </Button>
              <p className="text-xs text-muted-foreground">
                {zh
                  ? "可拖动视频进度后加入时间点。每次 1–12 张，保存为 PNG。"
                  : "Seek the video to choose a frame. Extract 1–12 PNG images per task."}
              </p>
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <label className="space-y-2 text-sm">
                  <span>{zh ? "开始（秒）" : "Start (s)"}</span>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    className="mt-1 h-9 w-full rounded-md border border-border bg-bg px-2 text-foreground"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label className="space-y-2 text-sm">
                  <span>{zh ? "结束（秒）" : "End (s)"}</span>
                  <input
                    type="number"
                    min="0.1"
                    step="0.01"
                    className="mt-1 h-9 w-full rounded-md border border-border bg-bg px-2 text-foreground"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </label>
              </div>
              <p className="text-xs text-muted-foreground">
                {operation === "audio"
                  ? zh
                    ? "保存为 M4A 音频，可试听、下载；后续配音轨道将在下一阶段接入。"
                    : "Save as M4A for preview and download. Timeline audio tracks are planned separately."
                  : zh
                    ? "保存为 MP4，保留原有声音，可作为新的剪辑素材。"
                    : "Save as MP4 with original audio, ready for editing."}
              </p>
              {operation === "audio" && info.data && !info.data.has_audio && (
                <p role="alert" className="text-sm text-danger">
                  {zh
                    ? "这个视频没有音轨，无法提取音频。"
                    : "This video has no audio track to extract."}
                </p>
              )}
            </>
          )}
          {!valid && info.data && (
            <p className="text-xs text-muted-foreground">
              {zh
                ? "请填写视频范围内的有效时间；片段至少 0.1 秒，抽帧时间不能重复。"
                : "Use valid times within the video; segments need 0.1 s minimum and frame times must be unique."}
            </p>
          )}
          <Button
            disabled={!valid || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending
              ? zh
                ? "正在提交…"
                : "Submitting…"
              : zh
                ? "开始处理"
                : "Start processing"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
