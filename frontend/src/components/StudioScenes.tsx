import { useI18n } from "@/lib/i18n";
import { useState } from "react";

/** Decorative example artwork, never presented as a project's actual output. */
export function StudioScenes() {
  const { lang } = useI18n();
  const [selected, setSelected] = useState(1);
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
      role="region"
      aria-roledescription={lang === "zh" ? "轮播图" : "carousel"}
      aria-label={
        lang === "zh" ? "创作灵感示意图" : "Illustrative creative inspiration"
      }
    >
      <div className={`studio-scene studio-scene-${selected}`}>
        <span aria-live="polite">{labels[selected]}</span>
      </div>
      <div className="scene-controls" aria-label={lang === "zh" ? "选择灵感画面" : "Choose inspiration"}>
        {labels.map((label, i) => <button key={label} aria-label={label} aria-pressed={selected === i} onClick={() => setSelected(i)}>{i + 1}</button>)}
      </div>
    </div>
  );
}
