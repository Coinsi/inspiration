import { ImageOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type MediaImageProps = {
  src?: string | null;
  alt: string;
  className?: string;
  priority?: boolean;
  deferUntilVisible?: boolean;
  onDimensions?: (width: number, height: number) => void;
};

export function MediaImage(props: MediaImageProps) {
  return <MediaImageContent key={props.src ?? "empty"} {...props} />;
}
function MediaImageContent({
  src,
  alt,
  className,
  priority,
  deferUntilVisible,
  onDimensions,
}: MediaImageProps) {
  const { lang } = useI18n();
  const [state, setState] = useState<"loading" | "loaded" | "error">(
    src ? "loading" : "error",
  );
  const [attempt, setAttempt] = useState(0);
  const container = useRef<HTMLDivElement>(null);
  const [nearby, setNearby] = useState(false);
  const deferred = deferUntilVisible && !priority && typeof IntersectionObserver !== "undefined";
  useEffect(() => {
    const node = container.current;
    if (!deferred || !node || !src) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setNearby(true);
        observer.disconnect();
      }
    }, { rootMargin: "160px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [deferred, src]);
  const retryTimer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(retryTimer.current), []);
  function onError() {
    // A freshly published file can appear before its browsing copy is ready.
    if (src?.includes("&view=") && attempt < 6) {
      clearTimeout(retryTimer.current);
      retryTimer.current = setTimeout(() => setAttempt(value => value + 1), Math.min(1000 * 2 ** attempt, 12000));
      return;
    }
    setState("error");
  }
  return (
    <div
      ref={container}
      className={cn(
        "relative h-full w-full overflow-hidden bg-elevated",
        className,
      )}
    >
      {src && (!deferred || nearby) && (
        <img
          key={attempt}
          src={src}
          alt={alt}
          loading={priority ? "eager" : "lazy"}
          {...(priority && { fetchpriority: "high" })}
          decoding="async"
          onLoad={(e) => {
            clearTimeout(retryTimer.current);
            setState("loaded");
            onDimensions?.(
              e.currentTarget.naturalWidth,
              e.currentTarget.naturalHeight,
            );
          }}
          onError={onError}
          className={cn(
            "h-full w-full object-cover",
            state !== "loaded" && "opacity-0",
          )}
        />
      )}
      {state !== "loaded" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-2 text-center text-muted-foreground">
          {state === "loading" ? (
            <span className="media-image-placeholder" aria-label={lang === "zh" ? "正在加载图片" : "Loading image"} />
          ) : (
            <ImageOff className="h-6 w-6" strokeWidth={1.5} />
          )}
          <span className="text-xs" hidden={state === "loading"}>
            {state === "loading"
              ? lang === "zh"
                ? "加载画面…"
                : "Loading image…"
              : src
                ? lang === "zh"
                  ? "图片暂不可用"
                  : "Image unavailable"
                : lang === "zh"
                  ? "暂无代表图"
                  : "No cover image"}
          </span>
        </div>
      )}
    </div>
  );
}
