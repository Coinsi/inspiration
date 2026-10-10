import { Maximize2 } from "lucide-react";
import { useState } from "react";
import { MediaImage } from "./MediaImage";
import { Lightbox } from "./ImageViewer";
import { useI18n } from "@/lib/i18n";
import "./media-preview.css";

/** Complete image on a quiet preview stage; the original opens in a lightbox. */
export function MediaPreview({
  src,
  label,
  filename,
}: {
  src: string;
  label: string;
  filename: string;
}) {
  const [open, setOpen] = useState(false);
  const zh = useI18n().lang === "zh";
  return (
    <>
      <button
        type="button"
        className="media-image-preview"
        aria-label={label}
        onClick={() => setOpen(true)}
      >
        <MediaImage src={src} alt={label} className="media-image-canvas" />
        <span className="media-expand">
          <Maximize2 size={14} />
          {zh ? "查看原图" : "View image"}
        </span>
      </button>
      {open && (
        <Lightbox
          src={src}
          alt={label}
          filename={filename}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
