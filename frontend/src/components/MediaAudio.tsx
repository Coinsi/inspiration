import { useState } from "react";
import { useI18n } from "@/lib/i18n";

export function MediaAudio({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  const zh = useI18n().lang === "zh";
  return (
    <div className="space-y-3 bg-elevated p-3">
      <p className="text-xs text-muted-foreground">
        {zh ? "提取的音轨" : "Extracted audio"}
      </p>
      <audio
        src={src}
        controls
        preload="metadata"
        aria-label={zh ? "音轨试听" : "Audio preview"}
        className="w-full"
        onError={() => setFailed(true)}
      />
      {failed && (
        <p role="alert" className="text-xs text-danger">
          {zh ? "音频暂不可用" : "Audio unavailable"}
        </p>
      )}
    </div>
  );
}
