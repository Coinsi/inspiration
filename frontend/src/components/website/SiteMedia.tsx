import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/api";

export function SiteMedia({
  id,
  mime,
  preview = false,
  alt,
  background = false,
  playing = false,
  localSource,
  poster,
}: {
  id?: string;
  mime?: string;
  preview?: boolean;
  alt: string;
  background?: boolean;
  playing?: boolean;
  localSource?: string;
  poster?: string;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [source, setSource] = useState(""),
    [error, setError] = useState(false);
  useEffect(() => {
    if (!background || !video.current) return;
    if (playing) void video.current.play().catch(() => {});
    else video.current.pause();
  }, [source, background, playing]);
  useEffect(() => {
    setError(false);
    setSource("");
    if (localSource) {
      setSource(localSource);
      return;
    }
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
  }, [id, preview, localSource]);
  if (error && background && poster) return <img src={poster} alt={alt} />;
  if (error)
    return (
      <div className="site-media-error" role="status">
        展示素材暂不可用
      </div>
    );
  if (!source) return <div className="site-media-error">正在载入展示素材…</div>;
  return mime?.startsWith("video/") ? (
    <video
      ref={video}
      src={source}
      poster={poster}
      controls={!background}
      autoPlay={background && playing}
      muted={background}
      loop={background}
      playsInline
      preload="metadata"
      aria-label={alt}
      onError={() => setError(true)}
    />
  ) : (
    <img src={source} alt={alt} loading="lazy" onError={() => setError(true)} />
  );
}
