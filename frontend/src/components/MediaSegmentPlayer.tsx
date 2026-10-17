import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import type { RegionMatch } from "@/lib/library";
import { RegionOverlay } from "@/components/LibraryRegion";

export function MediaSegmentPlayer({
  src,
  start,
  end,
  label,
  autoPlay = false,
  showRangeControls = true,
  onMark,
  region,
}: {
  src: string;
  start: number;
  end: number;
  label: string;
  autoPlay?: boolean;
  showRangeControls?: boolean;
  onMark?: (kind: "start" | "end", ms: number) => void;
  region?: RegionMatch | null;
}) {
  const ref = useRef<HTMLVideoElement>(null),
    zh = useI18n().lang === "zh";
  const [failed, setFailed] = useState(false),
    [loaded, setLoaded] = useState(false),
    [playing, setPlaying] = useState(false);
  const [atMatch, setAtMatch] = useState(false);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const verifyMatch = () => {
    const video = ref.current;
    setAtMatch(
      !!video &&
        !!region &&
        region.matched_at_ms !== null &&
        !video.seeking &&
        video.paused &&
        video.readyState >= 2 &&
        Math.abs(video.currentTime * 1000 - region.matched_at_ms) < 25,
    );
  };
  return (
    <div className="space-y-2">
      <div className="relative">
        <video
          ref={ref}
          src={src}
          controls
          autoPlay={autoPlay}
          onPlay={() => {
            setPlaying(true);
            setAtMatch(false);
          }}
          onPause={() => {
            setPlaying(false);
            verifyMatch();
          }}
          onSeeking={() => setAtMatch(false)}
          onSeeked={verifyMatch}
          onLoadedData={verifyMatch}
          preload="metadata"
          aria-label={label}
          className="aspect-video w-full rounded-lg bg-black object-contain"
          onError={() => setFailed(true)}
          onLoadedMetadata={() => {
            setLoaded(true);
            if (ref.current) {
              setDimensions({
                width: ref.current.videoWidth,
                height: ref.current.videoHeight,
              });
              ref.current.currentTime = (region?.matched_at_ms ?? start) / 1000;
            }
          }}
          onTimeUpdate={() => {
            verifyMatch();
            const video = ref.current;
            if (playing && video && video.currentTime >= end / 1000) {
              video.pause();
              setPlaying(false);
            }
          }}
        />
        {region && atMatch && !playing && (
          <RegionOverlay bbox={region.bbox} {...dimensions} />
        )}
      </div>
      {region && (
        <p className="text-xs text-muted-foreground">
          {zh
            ? "区域框只在命中的采样时刻显示，播放或拖动时隐藏；它不代表连续追踪。"
            : "The region appears only at the matched sample. It hides during playback or seeking and is not object tracking."}
        </p>
      )}
      {failed && (
        <p role="alert" className="text-xs text-danger">
          {zh
            ? "视频预览无法加载，请稍后重试。"
            : "Preview unavailable. Try again later."}
        </p>
      )}
      <div
        className="flex flex-wrap gap-2"
        hidden={!showRangeControls}
        style={!showRangeControls ? { display: "none" } : undefined}
      >
        {region && region.matched_at_ms !== null && (
          <Button
            size="sm"
            variant="outline"
            disabled={!loaded || failed}
            onClick={() => {
              const video = ref.current;
              if (!video) return;
              video.pause();
              video.currentTime = region.matched_at_ms! / 1000;
              verifyMatch();
            }}
          >
            {zh ? "返回命中画面" : "Show matched frame"}
          </Button>
        )}
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
