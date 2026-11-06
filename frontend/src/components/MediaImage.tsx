import { ImageOff } from "lucide-react";
import { useState } from "react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Keep slow or missing media explicit without blocking the surrounding content. */
export function MediaImage({
  src,
  alt,
  className,
  onDimensions,
}: {
  src?: string | null;
  alt: string;
  className?: string;
  onDimensions?: (width: number, height: number) => void;
}) {
  return (
    <MediaImageContent
      key={src ?? "empty"}
      src={src}
      alt={alt}
      className={className}
      onDimensions={onDimensions}
    />
  );
}
function MediaImageContent({
  src,
  alt,
  className,
  onDimensions,
}: {
  src?: string | null;
  alt: string;
  className?: string;
  onDimensions?: (width: number, height: number) => void;
}) {
  const { lang } = useI18n();
  const [state, setState] = useState<"loading" | "loaded" | "error">(
    src ? "loading" : "error",
  );
  return (
    <div
      className={cn(
        "relative h-full w-full overflow-hidden bg-elevated",
        className,
      )}
    >
      {src && (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          onLoad={(e) => {
            setState("loaded");
            onDimensions?.(
              e.currentTarget.naturalWidth,
              e.currentTarget.naturalHeight,
            );
          }}
          onError={() => setState("error")}
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
