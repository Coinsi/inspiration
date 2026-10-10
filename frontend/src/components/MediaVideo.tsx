import {
  Film,
  Loader2,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  Volume2,
  VolumeX,
} from "lucide-react";
import { useRef, useState } from "react";
import { useI18n } from "@/lib/i18n";
import "./media-preview.css";

const time = (value: number) => {
  const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
};
export function MediaVideo({
  src,
  label,
  presentation = "native",
}: {
  src: string;
  label: string;
  presentation?: "native" | "studio";
}) {
  return presentation === "studio" ? (
    <VideoContent key={src} src={src} label={label} />
  ) : (
    <NativeVideo key={src} src={src} label={label} />
  );
}
function NativeVideo({ src, label }: { src: string; label: string }) {
  const zh = useI18n().lang === "zh";
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <div className="flex h-full min-h-28 flex-col items-center justify-center gap-2 bg-elevated p-3 text-xs text-muted-foreground">
        <Film size={20} />
        <span>{zh ? "视频暂不可用" : "Video unavailable"}</span>
      </div>
    );
  return (
    <video
      src={src}
      aria-label={label}
      controls
      playsInline
      preload="metadata"
      onError={() => setFailed(true)}
      className="h-full w-full bg-elevated object-contain"
    />
  );
}
function VideoContent({ src, label }: { src: string; label: string }) {
  const zh = useI18n().lang === "zh";
  const video = useRef<HTMLVideoElement>(null),
    root = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false),
    [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0),
    [position, setPosition] = useState(0);
  const [muted, setMuted] = useState(false),
    [waiting, setWaiting] = useState(false);
  const [message, setMessage] = useState("");
  async function toggle() {
    const el = video.current;
    if (!el) return;
    if (!el.paused) {
      el.pause();
      return;
    }
    setMessage("");
    try {
      await el.play();
    } catch {
      setWaiting(false);
      setMessage(
        zh ? "暂时无法播放，请再试一次" : "Unable to play. Please retry.",
      );
    }
  }
  async function fullscreen() {
    try {
      if (document.fullscreenElement === root.current)
        await document.exitFullscreen();
      else if (root.current?.requestFullscreen)
        await root.current.requestFullscreen();
      else {
        const el = video.current as HTMLVideoElement & {
          webkitEnterFullscreen?: () => void;
        };
        el?.webkitEnterFullscreen?.();
      }
    } catch {
      setMessage(
        zh
          ? "当前窗口不支持全屏播放"
          : "Fullscreen is unavailable in this window",
      );
    }
  }
  return (
    <div className="media-player" ref={root}>
      <div className="media-player-screen">
        {failed ? (
          <div className="media-preview-empty" role="status">
            <Film size={26} />
            <span>{zh ? "视频暂不可用" : "Video unavailable"}</span>
            <button
              type="button"
              onClick={() => {
                setFailed(false);
                setMessage("");
                setWaiting(false);
                setDuration(0);
                setPosition(0);
              }}
            >
              <RotateCcw size={14} />
              {zh ? "重新加载" : "Retry"}
            </button>
          </div>
        ) : (
          <>
            <video
              ref={video}
              src={src}
              aria-label={label}
              preload="metadata"
              playsInline
              muted={muted}
              onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
              onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onWaiting={() => setWaiting(true)}
              onPlaying={() => setWaiting(false)}
              onCanPlay={() => setWaiting(false)}
              onError={() => {
                setFailed(true);
                setPlaying(false);
                setWaiting(false);
              }}
            />
            <button
              type="button"
              className={`media-play-surface ${playing ? "is-playing" : ""}`}
              aria-label={
                playing
                  ? zh
                    ? "暂停视频"
                    : "Pause video"
                  : zh
                    ? "播放视频"
                    : "Play video"
              }
              onClick={() => void toggle()}
            >
              <span>
                {waiting ? (
                  <Loader2 className="animate-spin" />
                ) : playing ? (
                  <Pause />
                ) : (
                  <Play fill="currentColor" />
                )}
              </span>
            </button>
            {duration > 0 && !playing && (
              <span className="media-duration">{time(duration)}</span>
            )}
          </>
        )}
      </div>
      {!failed && (
        <div className="media-player-controls">
          <button
            type="button"
            aria-label={
              playing ? (zh ? "暂停" : "Pause") : zh ? "播放" : "Play"
            }
            onClick={() => void toggle()}
          >
            {playing ? (
              <Pause size={16} />
            ) : (
              <Play size={16} fill="currentColor" />
            )}
          </button>
          <span className="media-time">{time(position)}</span>
          <input
            aria-label={zh ? "播放进度" : "Playback progress"}
            type="range"
            min={0}
            max={Number.isFinite(duration) ? duration : 0}
            step={0.05}
            value={Number.isFinite(duration) ? Math.min(position, duration) : 0}
            disabled={!Number.isFinite(duration) || duration <= 0}
            onChange={(e) => {
              if (video.current) {
                const next = Number(e.target.value);
                video.current.currentTime = next;
                setPosition(next);
              }
            }}
          />
          <span className="media-time">{time(duration)}</span>
          <button
            type="button"
            aria-label={
              muted ? (zh ? "开启声音" : "Unmute") : zh ? "静音" : "Mute"
            }
            aria-pressed={muted}
            onClick={() => setMuted((v) => !v)}
          >
            {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
          </button>
          <button
            type="button"
            aria-label={zh ? "全屏播放" : "Fullscreen"}
            onClick={() => void fullscreen()}
          >
            <Maximize2 size={16} />
          </button>
        </div>
      )}
      {message && (
        <p className="media-player-message" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
