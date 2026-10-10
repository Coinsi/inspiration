import { useRef, useState } from "react";
import { AudioLines, ImageOff } from "lucide-react";
import { MediaSegmentPlayer } from "@/components/MediaSegmentPlayer";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import type { MediaType, RegionMatch } from "@/lib/library";
import { RegionOverlay } from "@/components/LibraryRegion";

type LibraryMediaPreviewProps = {
  mediaType: MediaType;
  src: string;
  label: string;
  start?: number | null;
  end?: number | null;
  autoPlay?: boolean;
  region?: RegionMatch | null;
  onMark?: (kind: "start" | "end", ms: number) => void;
};

export function LibraryMediaPreview(props: LibraryMediaPreviewProps) {
  return (
    <LibraryMediaPreviewContent
      key={`${props.mediaType}:${props.src}:${props.region?.id || ""}`}
      {...props}
    />
  );
}

function LibraryMediaPreviewContent({
  mediaType,
  src,
  label,
  start = 0,
  end = 0,
  autoPlay = false,
  onMark,
  region,
}: LibraryMediaPreviewProps) {
  const zh = useI18n().lang === "zh";
  const [failed, setFailed] = useState(false);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  if (mediaType === "image")
    return (
      <div className="flex min-h-52 items-center justify-center overflow-hidden rounded-xl border bg-elevated p-3">
        {failed ? (
          <p
            role="alert"
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <ImageOff size={20} />
            {zh ? "图片预览暂不可用" : "Image preview unavailable"}
          </p>
        ) : (
          <div className="relative w-full">
            <img
              src={src}
              alt={label}
              className="max-h-[60vh] w-full object-contain"
              onError={() => setFailed(true)}
              onLoad={(event) =>
                setDimensions({
                  width: event.currentTarget.naturalWidth,
                  height: event.currentTarget.naturalHeight,
                })
              }
            />
            {region && <RegionOverlay bbox={region.bbox} {...dimensions} />}
          </div>
        )}
      </div>
    );
  if (mediaType === "audio")
    return (
      <AudioPreview
        key={src}
        src={src}
        label={label}
        start={start ?? 0}
        end={end ?? 0}
        autoPlay={autoPlay}
        onMark={onMark}
      />
    );
  return (
    <MediaSegmentPlayer
      src={src}
      label={label}
      start={start ?? 0}
      end={end ?? 0}
      autoPlay={autoPlay}
      onMark={onMark}
      region={region}
    />
  );
}

function AudioPreview({
  src,
  label,
  start,
  end,
  autoPlay,
  onMark,
}: {
  src: string;
  label: string;
  start: number;
  end: number;
  autoPlay: boolean;
  onMark?: (kind: "start" | "end", ms: number) => void;
}) {
  const zh = useI18n().lang === "zh";
  const audio = useRef<HTMLAudioElement>(null);
  const [loaded, setLoaded] = useState(false),
    [failed, setFailed] = useState(false);
  const [playError, setPlayError] = useState(false);
  return (
    <div className="space-y-3 rounded-xl border bg-elevated p-5">
      <div className="flex min-h-28 flex-col items-center justify-center gap-3 text-muted-foreground">
        <AudioLines size={48} strokeWidth={1.2} aria-hidden="true" />
        <p className="max-w-full truncate text-sm">{label}</p>
      </div>
      <audio
        ref={audio}
        src={src}
        controls
        autoPlay={autoPlay}
        preload="metadata"
        aria-label={label}
        className="w-full"
        onLoadedMetadata={() => {
          setLoaded(true);
          if (audio.current) audio.current.currentTime = start / 1000;
        }}
        onError={() => setFailed(true)}
        onTimeUpdate={() => {
          const element = audio.current;
          if (
            element &&
            !element.paused &&
            end > start &&
            element.currentTime >= end / 1000
          )
            element.pause();
        }}
      />
      {(failed || playError) && (
        <p role="alert" className="text-xs text-danger">
          {failed
            ? zh
              ? "音频预览无法加载，请稍后重试。"
              : "Audio preview unavailable. Try again later."
            : zh
              ? "播放未能开始，请点击播放器重试。"
              : "Playback did not start. Try the player controls."}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!loaded || failed || start >= end}
          onClick={() => {
            const element = audio.current;
            if (!element) return;
            element.currentTime = start / 1000;
            setPlayError(false);
            void element.play().catch(() => setPlayError(true));
          }}
        >
          {zh ? "播放所选片段" : "Play selected segment"}
        </Button>
        {onMark &&
          (["start", "end"] as const).map((kind) => (
            <Button
              key={kind}
              size="sm"
              variant="ghost"
              disabled={!loaded || failed}
              onClick={() =>
                onMark(
                  kind,
                  Math.round((audio.current?.currentTime ?? 0) * 1000),
                )
              }
            >
              {kind === "start"
                ? zh
                  ? "当前位置设为入点"
                  : "Mark in"
                : zh
                  ? "当前位置设为出点"
                  : "Mark out"}
            </Button>
          ))}
      </div>
    </div>
  );
}
