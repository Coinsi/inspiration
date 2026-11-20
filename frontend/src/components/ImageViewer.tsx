import { createPortal } from "react-dom";
import { useDialogFocus } from "@/components/ui/useDialogFocus";
// 通用图片查看器:点开看大图(灯箱)+ 下载。ZoomableImage 可直接替换 <img>。
import { Download, Loader2, X, ImageOff } from "lucide-react";
import { useState } from "react";
import { useI18n } from "@/lib/i18n";
import { getToken } from "@/lib/api";
import { cn } from "@/lib/utils";

// 拉取图片(blob 端点带 token,同源)→ 触发浏览器下载,文件名可控
async function downloadImage(src: string, filename: string, referrerPolicy?: ReferrerPolicy) {
  const urlToFetch = new URL(src, window.location.href);
  const token = getToken();
  const authenticated = urlToFetch.origin === window.location.origin && urlToFetch.pathname.startsWith("/api/");
  const res = await fetch(src, {
    referrerPolicy,
    headers: authenticated && token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Image download failed (${res.status})`);
  const blob = await res.blob();
  const ext = blob.type.includes("png")
    ? "png"
    : blob.type.includes("jpeg")
      ? "jpg"
      : blob.type.includes("webp")
        ? "webp"
        : blob.type.includes("mp4")
          ? "mp4"
          : "png";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = /\.\w{2,4}$/.test(filename) ? filename : `${filename}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function Lightbox({
  src,
  previewSrc,
  alt,
  filename,
  referrerPolicy,
  onClose,
}: {
  src: string;
  previewSrc?: string;
  alt?: string;
  filename: string;
  referrerPolicy?: ReferrerPolicy;
  onClose: () => void;
}) {
  const { t: tr, lang } = useI18n();
  const [downloading, setDownloading] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const dialogRef = useDialogFocus(true, onClose);

  const doDownload = async () => {
    setDownloading(true);
    setDownloadFailed(false);
    try {
      await downloadImage(src, filename, referrerPolicy);
    } catch {
      setDownloadFailed(true);
    } finally {
      setDownloading(false);
    }
  };

  return createPortal(
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={alt || filename}
      tabIndex={-1}
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/80 px-1 py-12 sm:p-8"
      onClick={onClose}
    >
      {/* 工具条 */}
      <div
        className="absolute right-4 top-4 z-10 flex items-center gap-2"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={doDownload}
          disabled={downloading}
          title={tr("img.download")}
          className="flex items-center gap-1.5 rounded-lg bg-white/10 px-3 py-2 text-sm text-white  transition hover:bg-white/20"
        >
          {downloading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
          {tr("img.download")}
        </button>
        <button
          onClick={onClose}
          title={tr("common.cancel")}
          className="grid h-9 w-9 place-items-center rounded-lg bg-white/10 text-white  transition hover:bg-white/20"
        >
          <X className="h-5 w-5" />
        </button>
      </div>
      {previewSrc && previewSrc !== src && !loaded && (
        <img src={previewSrc} referrerPolicy={referrerPolicy} alt="" aria-hidden="true" onClick={event => event.stopPropagation()}
          className="absolute max-h-[calc(100%-4rem)] max-w-[calc(100%-4rem)] rounded-lg object-contain" />
      )}
      <img
        key={attempt}
        src={src}
        referrerPolicy={referrerPolicy}
        alt={alt}
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        onClick={(e) => e.stopPropagation()}
        className={cn("relative max-h-full max-w-full rounded-lg object-contain shadow-2xl",
          previewSrc && previewSrc !== src && !loaded && "opacity-0")}
      />
      {downloadFailed && !failed && (
        <p role="status" className="absolute bottom-4 rounded-lg bg-black/75 px-3 py-2 text-sm text-white">
          {lang === "zh" ? "下载失败，可以长按或右键保存图片。" : "Download failed. Touch and hold or right-click the image to save it."}
        </p>
      )}
      {failed && <button type="button"
        className="absolute bottom-5 rounded-lg bg-black/70 px-4 py-2 text-sm text-white"
        onClick={event => {
          event.stopPropagation();
          setFailed(false);
          setAttempt(value => value + 1);
        }}>
        {lang === "zh" ? "原图加载失败，点击重试" : "Original unavailable. Retry"}
      </button>}
    </div>,
    document.body,
  );
}

export function ZoomableImage({
  src,
  previewSrc,
  alt,
  filename,
  className,
}: {
  src: string;
  previewSrc?: string;
  alt?: string;
  filename: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  if (failed === src)
    return (
      <div
        className={cn(
          "grid place-items-center bg-elevated text-muted-foreground",
          className,
        )}
        role="status"
      >
        <div className="text-center p-4">
          <ImageOff size={24} className="mx-auto mb-2" />
          <span className="text-xs">画面暂不可用</span>
          <button
            className="block mx-auto text-xs mt-2 underline"
            onClick={() => setFailed(null)}
          >
            重新加载
          </button>
        </div>
      </div>
    );
  return (
    <>
      <img
        src={previewSrc || src}
        loading="lazy"
        alt={alt}
        role="button"
        onError={() => setFailed(src)}
        tabIndex={0}
        aria-label={alt || filename}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={cn("cursor-zoom-in", className)}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
      />
      {open && (
        <Lightbox
          src={src}
          previewSrc={previewSrc}
          alt={alt}
          filename={filename}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
