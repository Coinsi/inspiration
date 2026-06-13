// 通用图片查看器:点开看大图(灯箱)+ 下载。ZoomableImage 可直接替换 <img>。
import { Download, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { getToken } from "@/lib/api";
import { cn } from "@/lib/utils";

// 拉取图片(blob 端点带 token,同源)→ 触发浏览器下载,文件名可控
async function downloadImage(src: string, filename: string) {
  const res = await fetch(src, { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
  const blob = await res.blob();
  const ext = blob.type.includes("png") ? "png" : blob.type.includes("jpeg") ? "jpg" : blob.type.includes("webp") ? "webp" : blob.type.includes("mp4") ? "mp4" : "png";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = /\.\w{2,4}$/.test(filename) ? filename : `${filename}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function Lightbox({ src, alt, filename, onClose }: { src: string; alt?: string; filename: string; onClose: () => void }) {
  const { t: tr } = useI18n();
  const [downloading, setDownloading] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const doDownload = async () => {
    setDownloading(true);
    try {
      await downloadImage(src, filename);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-8" onClick={onClose}>
      {/* 工具条 */}
      <div className="absolute right-4 top-4 z-10 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={doDownload}
          disabled={downloading}
          title={tr("img.download")}
          className="flex items-center gap-1.5 rounded-lg bg-white/10 px-3 py-2 text-sm text-white backdrop-blur transition hover:bg-white/20"
        >
          {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {tr("img.download")}
        </button>
        <button
          onClick={onClose}
          title={tr("common.cancel")}
          className="grid h-9 w-9 place-items-center rounded-lg bg-white/10 text-white backdrop-blur transition hover:bg-white/20"
        >
          <X className="h-5 w-5" />
        </button>
      </div>
      <img
        src={src}
        alt={alt}
        onClick={(e) => e.stopPropagation()}
        className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
      />
    </div>
  );
}

export function ZoomableImage({
  src,
  alt,
  filename,
  className,
}: {
  src: string;
  alt?: string;
  filename: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <img
        src={src}
        alt={alt}
        className={cn("cursor-zoom-in", className)}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
      />
      {open && <Lightbox src={src} alt={alt} filename={filename} onClose={() => setOpen(false)} />}
    </>
  );
}
