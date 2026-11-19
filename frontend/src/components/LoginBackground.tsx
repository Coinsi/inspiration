import { useEffect, useState } from "react";
import { LOGIN_APPEARANCE } from "@/lib/login-appearance";
import { useI18n } from "@/lib/i18n";

export function LoginBackground() {
  const { lang } = useI18n(),
    zh = lang === "zh";
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [poster, setPoster] = useState<"loading" | "ready" | "failed">("loading");
  const [playback, setPlayback] = useState<"waiting" | "playing" | "failed">("waiting");
  const [startVideo, setStartVideo] = useState(false);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => {
      setReducedMotion(preference.matches);
      setPlayback("waiting");
    };
    preference.addEventListener("change", changed);
    return () => preference.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (reducedMotion || poster === "loading") return;
    // Paint the small cover and login form before starting the decorative video.
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => setStartVideo(true));
    });
    return () => cancelAnimationFrame(frame);
  }, [poster, reducedMotion]);
  const showVideo = startVideo && !reducedMotion;
  return (
    <>
      {poster !== "failed" && (
        <img
          className="login-video-poster"
          src={LOGIN_APPEARANCE.posterUrl}
          alt=""
          referrerPolicy="no-referrer"
          onLoad={() => setPoster("ready")}
          onError={() => setPoster("failed")}
        />
      )}
      {showVideo && (
        <video
          className="login-background-video"
          style={{ opacity: playback === "playing" ? 1 : 0 }}
          src={LOGIN_APPEARANCE.videoUrl}
          autoPlay
          muted
          loop
          playsInline
          preload="none"
          aria-hidden="true"
          onPlaying={() => setPlayback("playing")}
          onError={() => setPlayback("failed")}
        />
      )}
      {showVideo && playback === "failed" && (
        <span role="status" className="login-video-status">
          {zh
            ? "视频暂时无法加载，已显示封面"
            : "Video unavailable. Showing the cover."}
        </span>
      )}
    </>
  );
}
