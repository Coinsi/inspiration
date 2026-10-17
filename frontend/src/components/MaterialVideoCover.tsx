import { useEffect, useRef, useState } from "react";
import { Film } from "lucide-react";

/** Only load a still frame when the card enters the viewport; never autoplay. */
export function MaterialVideoCover({ src }: { src: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "100px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return (
    <div ref={container} className="material-video-cover" aria-hidden="true">
      {visible && !failed && (
        <video
          src={src}
          preload="metadata"
          muted
          playsInline
          tabIndex={-1}
          className={ready ? "is-ready" : ""}
          onLoadedData={() => setReady(true)}
          onLoadedMetadata={(e) => {
            const video = e.currentTarget;
            if (Number.isFinite(video.duration) && video.duration > 0.1)
              video.currentTime = 0.1;
          }}
          onError={() => setFailed(true)}
        />
      )}
      {(!ready || failed) && <Film size={34} />}
    </div>
  );
}
