import { Film } from "lucide-react";
import { useState } from "react";
import { useI18n } from "@/lib/i18n";

export function MediaVideo({ src, label }: { src: string; label: string }) {
  return <VideoContent key={src} src={src} label={label} />;
}
function VideoContent({ src, label }: { src: string; label: string }) {
  const { lang } = useI18n();
  const [failed, setFailed] = useState(false);
  if (failed)
    return (
      <div className="flex h-full min-h-28 flex-col items-center justify-center gap-2 bg-elevated p-3 text-xs text-muted-foreground">
        <Film className="h-5 w-5" />
        <span>{lang === "zh" ? "视频暂不可用" : "Video unavailable"}</span>
      </div>
    );
  return (
    <video
      src={src}
      aria-label={label}
      controls
      preload="metadata"
      onError={() => setFailed(true)}
      className="h-full w-full bg-elevated object-contain"
    />
  );
}
