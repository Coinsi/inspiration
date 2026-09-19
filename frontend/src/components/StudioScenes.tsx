import { useI18n } from "@/lib/i18n";

/** Decorative example artwork, never presented as a project's actual output. */
export function StudioScenes() {
  const { lang } = useI18n();
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
      className="studio-scenes"
      aria-label={
        lang === "zh" ? "创作灵感示意图" : "Illustrative creative inspiration"
      }
    >
      {labels.map((label, i) => (
        <div key={label} className={`studio-scene studio-scene-${i}`}>
          <span>{label}</span>
        </div>
      ))}
      <span className="studio-scenes-caption">
        {lang === "zh"
          ? "灵感示意 · 每一帧，都可以是故事的开始"
          : "INSPIRATION · Every frame begins a story"}
      </span>
    </div>
  );
}
