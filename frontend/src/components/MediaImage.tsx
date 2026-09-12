import { ImageOff, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Keep slow or missing media explicit without blocking the surrounding content. */
export function MediaImage({
  src,
  alt,
  className,
}: {
  src?: string | null;
  alt: string;
  className?: string;
}) {
  return <MediaImageContent key={src ?? "empty"} src={src} alt={alt} className={className} />;
}
function MediaImageContent({
  src,
  alt,
  className,
}: {
  src?: string | null;
  alt: string;
  className?: string;
}) {
  const { lang } = useI18n();
  const [state, setState] = useState<"loading" | "loaded" | "error">(src ? "loading" : "error");
  useEffect(() => {
    if (!src || state !== "loading") return;
    const timer = window.setTimeout(() => setState("error"), 12000);
    return () => window.clearTimeout(timer);
  }, [src, state]);
  return (
    <div className={cn("relative h-full w-full overflow-hidden bg-elevated", className)}>
      {src && (
        <img
          src={src}
          alt={alt}
          onLoad={() => setState("loaded")}
          onError={() => setState("error")}
          className={cn("h-full w-full object-cover", state !== "loaded" && "opacity-0")}
        />
      )}
      {state !== "loaded" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-2 text-center text-muted-foreground">
          {state === "loading" ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <ImageOff className="h-6 w-6" strokeWidth={1.5} />
          )}
          <span className="text-xs">
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
