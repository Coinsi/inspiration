import { Pause, Play, RotateCcw } from "lucide-react";
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
  const [activated, setActivated] = useState(!reducedMotion);
  const [playing, setPlaying] = useState(false);
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
  // Explicit play also works for users who prefer reduced motion.
  useEffect(() => {
    if (activated) void video.current?.play().catch(() => setPlaying(false));
  }, [activated]);
  const toggle = () => {
    if (playing) {
      video.current?.pause();
      return;
    }
    if (!activated) {
      setActivated(true);
      return;
    }
    if (failed) {
      setFailed(false);
      video.current?.load();
    }
    void video.current?.play().catch(() => setPlaying(false));
  };
  const label = failed
    ? zh
      ? "重试背景视频"
      : "Retry background video"
    : playing
      ? zh
        ? "暂停背景视频"
        : "Pause background video"
      : zh
        ? "播放背景视频"
        : "Play background video";
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
        src={activated ? LOGIN_APPEARANCE.videoUrl : undefined}
        autoPlay={!reducedMotion}
        muted
        loop
        playsInline
        preload="metadata"
        aria-hidden="true"
        onPlay={() => {
          setPlaying(true);
          setFailed(false);
        }}
        onPause={() => setPlaying(false)}
        onError={() => {
          setFailed(true);
          setPlaying(false);
        }}
      />
      <button
        type="button"
        className="login-video-control"
        onClick={toggle}
        aria-label={label}
      >
        {failed ? (
          <RotateCcw size={13} />
        ) : playing ? (
          <Pause size={13} />
        ) : (
          <Play size={13} />
        )}
        <span>{label}</span>
      </button>
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
