import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function MediaSegmentPlayer({
  src,
  start,
  end,
  label,
  autoPlay = false,
  onMark,
}: {
  src: string;
  start: number;
  end: number;
  label: string;
  autoPlay?: boolean;
  onMark?: (kind: "start" | "end", ms: number) => void;
}) {
  const ref = useRef<HTMLVideoElement>(null),
    zh = useI18n().lang === "zh";
  const [failed, setFailed] = useState(false),
    [loaded, setLoaded] = useState(false),
    [playing, setPlaying] = useState(false);
  return (
    <div className="space-y-2">
      <video
        ref={ref}
        src={src}
        controls
        autoPlay={autoPlay}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        preload="metadata"
        aria-label={label}
        className="aspect-video w-full rounded-lg bg-black object-contain"
        onError={() => setFailed(true)}
        onLoadedMetadata={() => {
          setLoaded(true);
          if (ref.current) ref.current.currentTime = start / 1000;
        }}
        onTimeUpdate={() => {
          const video = ref.current;
          if (playing && video && video.currentTime >= end / 1000) {
            video.pause();
            setPlaying(false);
          }
        }}
      />
      {failed && (
        <p role="alert" className="text-xs text-danger">
          {zh
            ? "视频预览无法加载，请稍后重试。"
            : "Preview unavailable. Try again later."}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!loaded || failed || start >= end}
          onClick={() => {
            const video = ref.current;
            if (video) {
              video.currentTime = start / 1000;
              setPlaying(true);
              void video.play().catch(() => setPlaying(false));
            }
          }}
        >
          {zh ? "播放所选片段" : "Play selected segment"}
        </Button>
        {onMark && (
          <>
            <Button
              size="sm"
              variant="ghost"
              disabled={!loaded}
              onClick={() =>
                onMark(
                  "start",
                  Math.round((ref.current?.currentTime || 0) * 1000),
                )
              }
            >
              {zh ? "当前画面设为入点" : "Mark in"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={!loaded}
              onClick={() =>
                onMark(
                  "end",
                  Math.round((ref.current?.currentTime || 0) * 1000),
                )
              }
            >
              {zh ? "当前画面设为出点" : "Mark out"}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
