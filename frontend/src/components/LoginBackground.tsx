import { useEffect, useRef, useState } from "react";
import { LOGIN_APPEARANCE } from "@/lib/login-appearance";
import { useI18n } from "@/lib/i18n";

export function LoginBackground() {
  const { lang } = useI18n(),
    zh = lang === "zh";
  const video = useRef<HTMLVideoElement>(null);
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [failed, setFailed] = useState(false);
  const [posterFailed, setPosterFailed] = useState(false);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => {
      setReducedMotion(preference.matches);
      if (preference.matches) video.current?.pause();
    };
    preference.addEventListener("change", changed);
    return () => preference.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (reducedMotion) video.current?.pause();
    else void video.current?.play().catch(() => {});
  }, [reducedMotion]);
  return (
    <>
      {!posterFailed && (
        <img
          className="login-video-poster"
          src={LOGIN_APPEARANCE.posterUrl}
          alt=""
          referrerPolicy="no-referrer"
          onError={() => setPosterFailed(true)}
        />
      )}
      <video
        ref={video}
        className="login-background-video"
        style={{ opacity: failed ? 0 : 1 }}
        src={!reducedMotion ? LOGIN_APPEARANCE.videoUrl : undefined}
        autoPlay={!reducedMotion}
        muted
        loop
        playsInline
        preload="metadata"
        aria-hidden="true"
        onPlay={() => {
          setFailed(false);
        }}
        onError={() => {
          setFailed(true);
        }}
      />
      {failed && (
        <span role="status" className="login-video-status">
          {zh
            ? "视频暂时无法加载，已显示封面"
            : "Video unavailable. Showing the cover."}
        </span>
      )}
    </>
  );
}
