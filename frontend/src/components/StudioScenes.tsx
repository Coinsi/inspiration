import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Pause, Play } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import "./studio-scenes.css";

const scenes = [
  {
    id: "rain",
    zh: { setting: "雨夜", title: "序章", description: "暖色灯光映在雨后的老街上。" },
    en: { setting: "Rain", title: "Prologue", description: "Warm lights reflect on a rain-soaked old street." },
  },
  {
    id: "city",
    zh: { setting: "城市", title: "未完的故事", description: "行人撑伞走过高架桥下，城市灯光倒映在街面。" },
    en: { setting: "City", title: "An unfinished story", description: "A figure with an umbrella crosses a wet street beneath an overpass." },
  },
  {
    id: "dawn",
    zh: { setting: "晨光", title: "新的开始", description: "透过窗框望向海湾，晨光落在远山和水面上。" },
    en: { setting: "Dawn", title: "A new beginning", description: "Morning light falls across the bay and distant hills beyond a window." },
  },
] as const;

const sceneCount = scenes.length;
const mobileFrames = [scenes[sceneCount - 1], ...scenes, scenes[0]];

function MobileSceneRail({ selected, description, reduced, onSelect, onBusy }: {
  selected: number;
  description: string;
  reduced: boolean;
  onSelect: (index: number) => void;
  onBusy: (busy: boolean) => void;
}) {
  const track = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const rail = track.current!;
    let settleTimer: number | undefined;
    let touching = false;

    function settle() {
      settleTimer = undefined;
      if (touching || !rail.clientWidth) return;
      const slot = Math.max(0, Math.min(mobileFrames.length - 1, Math.round(rail.scrollLeft / rail.clientWidth)));
      const index = (slot + sceneCount - 1) % sceneCount;
      // The identical end frames let a swipe wrap without scrolling back through the strip.
      if (slot === 0 || slot === sceneCount + 1) rail.scrollLeft = (index + 1) * rail.clientWidth;
      onSelect(index);
      onBusy(false);
    }
    function waitForSnap() {
      if (settleTimer === undefined) onBusy(true);
      window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(settle, 120);
    }
    function touchStart() {
      touching = true;
      onBusy(true);
    }
    function touchEnd(event: TouchEvent) {
      touching = event.touches.length > 0;
      if (!touching) waitForSnap();
    }
    function alignAfterResize() {
      window.clearTimeout(settleTimer);
      settleTimer = undefined;
      touching = false;
      onBusy(false);
      if (rail.clientWidth) rail.scrollLeft = (Number(rail.dataset.selected) + 1) * rail.clientWidth;
    }

    alignAfterResize();
    const observer = new ResizeObserver(alignAfterResize);
    observer.observe(rail);
    rail.addEventListener("scroll", waitForSnap, { passive: true });
    rail.addEventListener("touchstart", touchStart, { passive: true });
    rail.addEventListener("touchend", touchEnd);
    rail.addEventListener("touchcancel", touchEnd);
    return () => {
      observer.disconnect();
      window.clearTimeout(settleTimer);
      rail.removeEventListener("scroll", waitForSnap);
      rail.removeEventListener("touchstart", touchStart);
      rail.removeEventListener("touchend", touchEnd);
      rail.removeEventListener("touchcancel", touchEnd);
    };
  }, [onSelect, onBusy]);

  useLayoutEffect(() => {
    const rail = track.current!;
    if (!rail.clientWidth) return;
    const width = rail.clientWidth;
    const currentSlot = Math.round(rail.scrollLeft / width);
    let target = selected + 1;
    if (selected === 0 && currentSlot === sceneCount) target = sceneCount + 1;
    if (selected === sceneCount - 1 && currentSlot === 1) target = 0;
    if (Math.abs(rail.scrollLeft - target * width) > 1) {
      rail.scrollTo({ left: target * width, behavior: reduced ? "instant" : "smooth" });
    }
  }, [selected, reduced]);

  return (
    <div className="scene-mobile-rail" ref={track} data-selected={selected} tabIndex={-1}
      role="img" aria-label={description}>
      {mobileFrames.map((item, slot) => (
        <div key={slot} className={`scene-slide scene--${item.id}`} aria-hidden="true">
          <span className="scene-art" />
        </div>
      ))}
    </div>
  );
}

export function StudioScenes() {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const heading = useId();
  const [selected, setSelected] = useState(1);
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [scrolling, setScrolling] = useState(false);
  const [visible, setVisible] = useState(!document.hidden);
  const [reduced, setReduced] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const current = scenes[selected][lang];
  const rotating = !paused && !hovered && !focused && !scrolling && visible && !reduced;

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

  useEffect(() => {
    if (!rotating) return;
    const timer = window.setTimeout(() => setSelected(i => (i + 1) % sceneCount), 6000);
    return () => window.clearTimeout(timer);
  }, [rotating, selected]);

  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.metaKey || event.ctrlKey) return;
    let next: number;
    switch (event.key) {
      case "ArrowRight": next = (index + 1) % sceneCount; break;
      case "ArrowLeft": next = (index + sceneCount - 1) % sceneCount; break;
      case "Home": next = 0; break;
      case "End": next = sceneCount - 1; break;
      default: return;
    }
    event.preventDefault();
    setSelected(next);
    buttons.current[next]?.focus({ preventScroll: true });
  }

  const playbackLabel = paused ? (zh ? "继续轮播" : "Play slideshow") : (zh ? "暂停轮播" : "Pause slideshow");
  return (
    <section className="home-hero" aria-labelledby={heading} aria-roledescription={zh ? "轮播图" : "carousel"}
      onFocusCapture={e => setFocused(e.target.matches(":focus-visible"))}
      onBlurCapture={e => { if (!e.currentTarget.contains(e.relatedTarget)) setFocused(false); }}>
      <header className="scene-heading">
        <h1 id={heading}>{zh ? "镜头灵感" : "Scene inspiration"}</h1>
        {!reduced && (
          <button type="button" className="scene-playback" aria-label={playbackLabel}
            onClick={() => {
              if (paused) setFocused(false);
              setPaused(!paused);
            }}>
            {paused ? <Play size={15} /> : <Pause size={15} />}
            <span className="scene-playback-label">{playbackLabel}</span>
          </button>
        )}
      </header>
      <div className="scene-gallery" onPointerEnter={e => { if (e.pointerType === "mouse") setHovered(true); }}
        onPointerLeave={() => setHovered(false)}>
        <MobileSceneRail selected={selected} description={current.description} reduced={reduced}
          onSelect={setSelected} onBusy={setScrolling} />
        <div className="scene-filmstrip">
          <p className="scene-mobile-caption" aria-hidden="true">
            <span>{current.setting}</span>
            <strong>{current.title}</strong>
          </p>
          <div className="scene-panels" role="group" aria-label={zh ? "选择镜头" : "Choose a scene"}>
            {scenes.map((item, i) => {
              const scene = item[lang];
              const label = `${scene.setting} · ${scene.title}`;
              return (
                <button type="button" key={item.id} ref={el => { buttons.current[i] = el; }}
                  className={`scene-panel scene--${item.id}`}
                  aria-pressed={selected === i} aria-label={label} title={label}
                  onClick={() => setSelected(i)} onKeyDown={event => navigate(event, i)}>
                  <span className="scene-window" aria-hidden="true"><span className="scene-art" /></span>
                  <span className="scene-panel-caption" aria-hidden="true">
                    <span>{scene.setting}</span>
                    <strong>{scene.title}</strong>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      <span className="sr-only" aria-live={rotating ? "off" : "polite"} aria-atomic="true">
        {current.setting} · {current.title}{zh ? "。" : ". "}{current.description}
      </span>
    </section>
  );
}
