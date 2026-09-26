import { useEffect, useState } from "react";
import { getToken } from "@/lib/api";

export function SiteMedia({
  id,
  mime,
  preview = false,
  alt,
}: {
  id: string;
  mime?: string;
  preview?: boolean;
  alt: string;
}) {
  const [source, setSource] = useState(""),
    [error, setError] = useState(false);
  useEffect(() => {
    setError(false);
    setSource("");
    if (!preview) {
      setSource(`/api/v1/website/media/${id}`);
      return;
    }
    const controller = new AbortController();
    let url = "";
    fetch(`/api/v1/admin/website/media/${id}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
      signal: controller.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw Error();
        return r.blob();
      })
      .then((b) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(b);
        setSource(url);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, preview]);
  if (error)
    return (
      <div className="site-media-error" role="status">
        展示素材暂不可用
      </div>
    );
  if (!source) return <div className="site-media-error">正在载入展示素材…</div>;
  return mime?.startsWith("video/") ? (
    <video
      src={source}
      controls
      playsInline
      preload="metadata"
      aria-label={alt}
      onError={() => setError(true)}
    />
  ) : (
    <img src={source} alt={alt} loading="lazy" onError={() => setError(true)} />
  );
}
