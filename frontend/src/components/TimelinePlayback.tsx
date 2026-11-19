import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, RotateCcw, Maximize, Minimize, ChevronLeft, ChevronRight } from "lucide-react";
import { api, blobUrl, type Generation, type Shot } from "@/lib/api";
import { timelineDuration, type TimelineContent } from "@/lib/timeline-edit";
import { audioEnvelope, frameMs, timecode, transitionAt } from "@/lib/timeline-interaction";

type MediaProps = {
  projectId: string;
  generationId: string;
  mediaKey: string;
  time: number;
  playing: boolean;
  gain: number;
  fit?: "contain" | "cover";
  audioContext: AudioContext | null;
  status: (id: string, ready: boolean, error?: string) => void;
};
function PlaybackMedia({
  projectId,
  generationId,
  mediaKey,
  time,
  playing,
  gain,
  fit = "contain",
  audioContext,
  status,
}: MediaProps) {
  const ref = useRef<HTMLMediaElement | null>(null),
    imageRef = useRef<HTMLImageElement | null>(null),
    gainRef = useRef<GainNode | null>(null),
    sourceElement = useRef<HTMLMediaElement | null>(null),
    sourceRef = useRef<MediaElementAudioSourceNode | null>(null);
  const latest = useRef({ time, playing, gain });
  const [originalImage, setOriginalImage] = useState("");
  latest.current = { time, playing, gain };
  const q = useQuery({
    queryKey: ["generation", projectId, generationId],
    enabled: !!generationId,
    queryFn: () =>
      api.get<Generation>(`/projects/${projectId}/generations/${generationId}`),
  });
  const synchronize = () => {
    const el = ref.current;
    if (!el || !el.readyState) return;
    const end = Math.max(
      0,
      Number.isFinite(el.duration) ? el.duration - 0.025 : time / 1000,
    );
    const target = Math.max(0, Math.min(latest.current.time / 1000, end));
    if (!latest.current.playing || Math.abs(el.currentTime - target) > 0.18)
      el.currentTime = target;
    if (latest.current.playing && latest.current.time / 1000 < end)
      void el.play().catch((error: DOMException) => {
        if (latest.current.playing && error.name !== "AbortError")
          status(mediaKey, false, "浏览器未能播放素材，请重新打开预览");
      });
    else el.pause();
  };
  useEffect(() => {
    status(mediaKey, false);
    return () => {
      ref.current?.pause();
      status(mediaKey, false);
    };
  }, [mediaKey, generationId, status]);
  useEffect(() => {
    if (q.error || !generationId || (q.data && !q.data.output_blob_hash))
      status(mediaKey, false, "部分素材读取失败，请检查来源后重新打开预览");
  }, [q.error, q.data, generationId, mediaKey, status]);
  useEffect(() => {
    const el = ref.current;
    if (!el || !audioContext) return;
    try {
      if (!sourceRef.current || sourceElement.current !== el) {
        sourceRef.current = audioContext.createMediaElementSource(el);
        gainRef.current = audioContext.createGain();
        sourceElement.current = el;
      }
      sourceRef.current.connect(gainRef.current!);
      gainRef.current!.connect(audioContext.destination);
    } catch {
      status(mediaKey, false, "声音输出初始化失败，请重新打开预览");
    }
    const source = sourceRef.current,
      node = gainRef.current;
    return () => {
      source?.disconnect();
      node?.disconnect();
    };
  }, [audioContext, q.data?.output_blob_hash, mediaKey, status]);
  useEffect(() => {
    if (gainRef.current) gainRef.current.gain.value = playing ? gain : 0;
  }, [gain, playing, audioContext, q.data?.output_blob_hash]);
  useEffect(() => {
    const element = ref.current;
    return () => element?.pause();
  }, [q.data?.output_blob_hash]);
  useEffect(synchronize, [time, playing, q.data]);
  useEffect(() => {
    const img = imageRef.current;
    if (img?.complete && img.naturalWidth > 0) status(mediaKey, true);
    const media = ref.current;
    if (media && media.readyState >= 2) status(mediaKey, true);
  }, [q.data?.output_blob_hash, mediaKey, generationId, status]);

  if (!q.data?.output_blob_hash)
    return <span className="timeline-composition-missing">正在读取素材…</span>;
  const hash = q.data.output_blob_hash;
  const rendition = q.data.output_type === "video" ? "playback" :
    q.data.output_type === "image" && originalImage !== hash ? "display" : undefined;
  const src = blobUrl(projectId, hash, rendition),
    failed = () =>
      status(mediaKey, false, "素材无法解码，请检查来源后重新打开预览");
  if (q.data.output_type === "image")
    return (
      <img
        ref={imageRef}
        src={src}
        alt=""
        style={{ width: "100%", height: "100%", objectFit: fit }}
        onLoad={() => status(mediaKey, true)}
        onError={() => rendition === "display" ? setOriginalImage(hash) : failed()}
      />
    );
  const props = {
    src,
    preload: "auto",
    onLoadedData: () => {
      status(mediaKey, true);
      synchronize();
    },
    onCanPlay: () => status(mediaKey, true),
    onWaiting: () => {
      if (latest.current.playing) status(mediaKey, false);
    },
    onError: failed,
  };
  return q.data.output_type === "audio" ? (
    <audio
      {...props}
      ref={(el) => {
        ref.current = el;
      }}
    />
  ) : (
    <video
      {...props}
      ref={(el) => {
        ref.current = el;
      }}
      playsInline
      muted={!audioContext}
      style={{ width: "100%", height: "100%", objectFit: fit }}
    />
  );
}
export interface PlaybackControls {
  toggle(): void;
  audition(at: number): void;
  seek(at: number): void;
}
type PlaybackProps = {
  projectId: string;
  shots: Shot[];
  content: TimelineContent;
  aspect: string;
  time: number;
  onTime: (t: number) => void;
  selected: string;
  mute?: boolean;
  subtitleMode?: string;
  disabled?: boolean;
};
export const TimelinePlayback = forwardRef<PlaybackControls, PlaybackProps>(function TimelinePlayback({
  projectId,
  shots,
  content,
  aspect,
  time,
  onTime,
  selected,
  mute = false,
  subtitleMode = "burn",
  disabled = false,
}, ref) {
  const qc = useQueryClient();
  const shell = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const changed = () =>
      setFullscreen(document.fullscreenElement === shell.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  const audition = useRef<{ center: number; end: number } | null>(null);
  const root = useRef<HTMLDivElement>(null),
    [size, setSize] = useState({ width: 640, height: 360 }),
    [playing, setPlaying] = useState(false),
    [error, setError] = useState(""),
    [context, setContext] = useState<AudioContext | null>(null),
    [, refreshReady] = useState(0);
  const contextRef = useRef<AudioContext | null>(null),
    ready = useRef<Record<string, boolean>>({}),
    latest = useRef({ time, playing, onTime, total: 0, keys: [] as string[] });
  const duration = timelineDuration(content.items);
  const total = Number.isFinite(duration) ? Math.max(0, duration) : 0,
    now = Math.max(0, Math.min(time, Math.max(0, total - 1))),
    ratio = aspect === "9:16" ? 9 / 16 : aspect === "1:1" ? 1 : 16 / 9;
  const transition = transitionAt(content, now),
    visual = content.visuals
      .filter(
        (v) =>
          !v.hidden && now >= v.start_ms && now < v.start_ms + v.duration_ms,
      )
      .sort((a, b) => a.track - b.track),
    audio = content.audio
      .map((a, i) => ({ ...a, index: i }))
      .filter(
        (a) =>
          !a.muted && now >= a.start_ms && now < a.start_ms + a.duration_ms,
      );
  const keys = [
    ...transition.active.map((s) => `base-${s.index}`),
    ...visual.map((v) => v.id),
    ...(!mute ? audio.map((a) => `audio-${a.index}`) : []),
  ];
  const allReady = keys.every((key) => ready.current[key]);
  latest.current = { time: now, playing, onTime, total, keys };
  const status = useCallback((id: string, value: boolean, message?: string) => {
    if (ready.current[id] !== value) {
      ready.current[id] = value;
      refreshReady((n) => n + 1);
    }
    if (message) {
      setError(message);
      setPlaying(false);
    }
  }, []);
  useEffect(() => {
    const observer = new ResizeObserver(([e]) =>
      setSize({ width: e.contentRect.width, height: e.contentRect.height }),
    );
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setPlaying(false);
  }, [aspect, mute, subtitleMode, disabled]);
  useEffect(() => {
    const hide = () => {
      if (document.hidden) setPlaying(false);
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      void contextRef.current?.close();
    };
  }, []);
  useEffect(() => {
    if (!playing) return;
    let frame = 0,
      previous = performance.now(),
      acc = 0;
    const tick = (stamp: number) => {
      const state = latest.current,
        delta = Math.min(100, stamp - previous);
      previous = stamp;
      if (state.keys.every((k) => ready.current[k])) {
        acc += delta;
        if (acc >= 1000 / 24) {
          const until = Math.min(state.total - 1, audition.current?.end ?? state.total);
          const next = Math.min(until, state.time + acc);
          acc = 0;
          state.onTime(next);
          if (next >= until) {
            if (audition.current) state.onTime(audition.current.center);
            audition.current = null;
            setPlaying(false);
            return;
          }
        }
      } else acc = 0;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);
  async function play(force = false) {
    if (!force) audition.current = null;
    if (playing && !force) {
      setPlaying(false);
      return;
    }
    if (!total || error || disabled) return;
    try {
      if (!contextRef.current) {
        contextRef.current = new AudioContext();
        setContext(contextRef.current);
      }
      await contextRef.current.resume();
      if (!force && now >= total - 1) onTime(0);
      setPlaying(true);
    } catch {
      setError("声音输出不可用，请检查浏览器播放权限");
    }
  }
  const seek = (value: number) => {
      audition.current = null;
      const target = Math.max(0, Math.min(value, Math.max(0, total - 1)));
      // Seeking changes position, not the user's playback intent.
      // Update the clock immediately so the next animation frame cannot restore the old position.
      latest.current.time = target;
      onTime(target);
    },
    width = Math.min(size.width, Math.max(1, size.height) * ratio);
  useImperativeHandle(ref, () => ({
    toggle: () => { void play(); },
    seek,
    audition: (at) => {
      if (!total || disabled || error) return;
      seek(Math.max(0, at - 1500));
      audition.current = { center: at, end: Math.min(total - 1, at + 1500) };
      void play(true);
    },
  }));
  return (
    <div ref={shell} className="timeline-composition">
      <div ref={root} className="timeline-composition-stage">
        <div
          className="timeline-composition-frame"
          style={{ width, height: width / ratio }}
        >
          {transition.active.map((s, i) => {
            const incoming = i === 1 && transition.active.length === 2,
              p = transition.progress;
            let opacity = 1,
              clipPath: string | undefined;
            if (transition.active.length === 2) {
              if (transition.type === "fadeblack")
                opacity = incoming
                  ? Math.max(0, 2 * p - 1)
                  : Math.max(0, 1 - 2 * p);
              else if (incoming && transition.type === "wipeleft")
                clipPath = `inset(0 0 0 ${(1 - p) * 100}%)`;
              else if (incoming) opacity = p;
            }
            const gain =
              transition.active.length === 2 ? (incoming ? p : 1 - p) : 1;
            return (
              <div
                key={`${attempt}-base-${s.index}`}
                data-preview-base={s.index}
                style={{ position: "absolute", inset: 0, opacity, clipPath }}
              >
                <PlaybackMedia
                  projectId={projectId}
                  generationId={
                    s.clip.generation_id ||
                    shots.find((v) => v.id === s.clip.shot_id)
                      ?.selected_generation_id ||
                    ""
                  }
                  mediaKey={`base-${s.index}`}
                  time={s.clip.in_point_ms + now - s.start}
                  playing={playing && allReady}
                  gain={mute ? 0 : gain}
                  audioContext={context}
                  status={status}
                />
              </div>
            );
          })}
          {visual.map((v) => (
            <div
              key={`${attempt}-${v.id}`}
              data-layer-id={v.id}
              className={v.id === selected ? "is-selected" : ""}
              style={{
                position: "absolute",
                left: `${v.x * 100}%`,
                top: `${v.y * 100}%`,
                width: `${v.width * 100}%`,
                height: `${v.height * 100}%`,
                opacity: v.opacity,
              }}
            >
              <PlaybackMedia
                projectId={projectId}
                generationId={v.generation_id}
                mediaKey={v.id}
                time={v.in_point_ms + now - v.start_ms}
                playing={playing && allReady}
                gain={0}
                fit={v.fit}
                audioContext={context}
                status={status}
              />
            </div>
          ))}
          {subtitleMode !== "none" && (
            <div className="timeline-live-captions">
              {content.subtitles
                .filter((c) => now >= c.start_ms && now < c.end_ms)
                .map((c, i) => (
                  <div key={i}>{c.text}</div>
                ))}
            </div>
          )}
        </div>
      </div>
      {!mute &&
        audio.map((a) => (
          <PlaybackMedia
            key={`${attempt}-audio-${a.index}`}
            projectId={projectId}
            generationId={a.generation_id}
            mediaKey={`audio-${a.index}`}
            time={a.in_point_ms + now - a.start_ms}
            playing={playing && allReady}
            gain={audioEnvelope(
              now - a.start_ms,
              a.duration_ms,
              a.fade_in_ms,
              a.fade_out_ms,
              a.gain_db,
            )}
            audioContext={context}
            status={status}
          />
        ))}
      <div
        className="timeline-live-controls"

      >
        <button
          aria-label={playing ? "暂停时间线" : "播放时间线"}
          disabled={!total || !!error || disabled}
          onClick={() => void play()}
        >
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button aria-label="回到开头" onClick={() => seek(0)}>
          <RotateCcw size={14} />
        </button>
        <button aria-label="上一帧" onClick={() => seek(Math.round(now - frameMs))}><ChevronLeft size={15}/></button>
        <button aria-label="下一帧" onClick={() => seek(Math.round(now + frameMs))}><ChevronRight size={15}/></button>
        <label className="timeline-composition-scrub">
          <span>{timecode(now)}</span>
          <input
            aria-label="合成预览位置"
            aria-valuetext={`${timecode(now)} / ${timecode(total)}`}
            type="range"
            min={0}
            max={Math.max(0, total - 1)}
            step={1}
            value={now}
            onChange={(e) => seek(Number(e.target.value))}
          />
          <span>{timecode(total)}</span>
        </label>
        <button
          aria-label={fullscreen ? "退出全屏预览" : "全屏预览"}
          onClick={() => {
            const action = fullscreen
              ? document.exitFullscreen()
              : shell.current?.requestFullscreen();
            void action?.catch(() =>
              setError("无法进入全屏，请使用浏览器支持的预览方式"),
            );
          }}
        >
          {fullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
        </button>
      </div>
      {!allReady && !error && total > 0 && (
        <p role="status" className="timeline-preview-notice">
          正在载入当前画面与声音…
        </p>
      )}
      {error && (
        <p role="alert" className="text-danger text-xs p-2">
          {error}
          <button
            className="ml-3 underline"
            onClick={() => {
              setPlaying(false);
              setError("");
              ready.current = {};
              void qc.invalidateQueries({
                queryKey: ["generation", projectId],
              });
              setAttempt((n) => n + 1);
            }}
          >
            重新加载预览
          </button>
        </p>
      )}
    </div>
  );
});
