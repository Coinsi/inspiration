import { useEffect, useRef, useState } from "react";
import { Film } from "lucide-react";
import { MediaImage } from "./MediaImage";

/** The library owns the active card. No video request is made before preview. */
export function MaterialVideoPreview({
  src,
  poster,
  label,
  active,
  onActiveChange,
  onOpen,
}: {
  src: string;
  poster?: string;
  label: string;
  active: boolean;
  onActiveChange: (active: boolean) => void;
  onOpen: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const stop = useRef(() => onActiveChange(false));
  stop.current = () => onActiveChange(false);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const clear = () => {
    clearTimeout(timer.current);
    timer.current = undefined;
  };
  useEffect(() => {
    if (!active) {
      setPlaying(false);
      return;
    }
    setFailed(false);
    const player = video.current;
    player?.setAttribute("src", src);
    let cancelled = false;
    player?.play().catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
      player?.pause();
      // Release the request as well as playback when leaving the card.
      player?.removeAttribute("src");
      player?.load();
    };
  }, [active, src]);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) {
        clear();
        stop.current();
      }
    });
    if (root.current) observer.observe(root.current);
    const hide = () => {
      if (document.hidden) {
        clear();
        stop.current();
      }
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      clear();
      observer.disconnect();
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);
  return (
    <div
      ref={root}
      className="material-hover-video"
      onPointerEnter={(e) => {
        if (
          e.pointerType !== "mouse" ||
          !matchMedia("(hover: hover)").matches ||
          matchMedia("(prefers-reduced-motion: reduce)").matches
        )
          return;
        clear();
        timer.current = setTimeout(() => onActiveChange(true), 450);
      }}
      onPointerLeave={(e) => {
        if (e.pointerType === "mouse") {
          clear();
          onActiveChange(false);
        }
      }}
    >
      <button
        className="material-preview-open"
        aria-label={`预览 ${label}`}
        onClick={() => {
          clear();
          onActiveChange(false);
          onOpen();
        }}
      >
        {poster ? (
          <MediaImage src={poster} alt={label} />
        ) : (
          <span className="material-video-placeholder">
            <Film size={32} />
            <span>视频素材</span>
          </span>
        )}
        {active && (
          <video
            ref={video}
            src={src}
            muted
            playsInline
            loop
            preload="none"
            aria-hidden="true"
            className={playing && !failed ? "is-playing" : ""}
            onPlaying={() => setPlaying(true)}
            onError={() => setFailed(true)}
          />
        )}
      </button>
    </div>
  );
}
