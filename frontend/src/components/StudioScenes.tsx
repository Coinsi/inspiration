import { useI18n } from "@/lib/i18n";
import { useEffect, useState } from "react";
import { Pause, Play } from "lucide-react";

/** Decorative example artwork, never presented as a project's actual output. */
export function StudioScenes() {
  const { lang } = useI18n();
  const [selected, setSelected] = useState(1);
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [visible, setVisible] = useState(!document.hidden);
  const [reduced, setReduced] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const motion = () => setReduced(media.matches);
    const visibility = () => setVisible(!document.hidden);
    media.addEventListener("change", motion);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      media.removeEventListener("change", motion);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);
  const rotating = !paused && !hovered && !focused && visible && !reduced;
  useEffect(() => {
    if (!rotating) return;
    const timer = setInterval(() => setSelected(i => (i + 1) % 3), 6000);
    return () => clearInterval(timer);
  }, [rotating, selected]);
  const labels =
    lang === "zh"
      ? ["雨夜 · 序章", "城市 · 未完的故事", "晨光 · 新的开始"]
      : [
          "Rain · Prologue",
          "City · An unfinished story",
          "Dawn · A new beginning",
        ];
  return (
    <div
      className="studio-scenes inspiration-carousel"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={e => { if (!e.currentTarget.contains(e.relatedTarget)) setFocused(false); }}
      role="region"
      aria-roledescription={lang === "zh" ? "轮播图" : "carousel"}
      aria-label={
        lang === "zh" ? "创作灵感示意图" : "Illustrative creative inspiration"
      }
    >
      {labels.map((label, i) => <div key={i} className={`studio-scene studio-scene-${i} ${selected === i ? "is-active" : ""}`} aria-hidden={selected !== i}>
        <span>{label}</span>
      </div>)}
      <div className="scene-controls" aria-label={lang === "zh" ? "选择灵感画面" : "Choose inspiration"}>
        {labels.map((label, i) => <button className="scene-dot" key={label} aria-label={label} aria-pressed={selected === i} onClick={() => setSelected(i)}><span /></button>)}
        {!reduced && <button className="scene-pause" aria-label={lang === "zh" ? (paused ? "自动轮播" : "暂停轮播") : (paused ? "Play slideshow" : "Pause slideshow")} onClick={() => setPaused(v => !v)}>{paused ? <Play size={14} /> : <Pause size={14} />}</button>}
      </div>
    </div>
  );
}
