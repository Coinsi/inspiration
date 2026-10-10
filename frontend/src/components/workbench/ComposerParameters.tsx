import { useEffect, useRef, useState } from "react";
import { ChevronDown, Clock3, SlidersHorizontal } from "lucide-react";

const labels: Record<string, string> = {
  size: "画面尺寸",
  aspect_ratio: "画面比例",
  resolution: "清晰度",
  duration: "时长",
  quality: "质量",
};
export function ComposerParameters({
  controls,
  schema,
  disabled,
  onChange,
}: {
  controls: Record<string, string | number>;
  schema: Record<string, { enum?: (string | number)[] }>;
  disabled: boolean;
  onChange: (key: string, value: string | number) => void;
}) {
  const [open, setOpen] = useState<"picture" | "duration" | null>(null);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (!root.current?.contains(e.target as Node)) setOpen(null);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("focusin", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("focusin", close);
    };
  }, [open]);
  const picture = Object.keys(controls).filter((key) => key !== "duration");
  const selected = open === "duration" ? ["duration"] : picture;
  return (
    <div
      className="composer-parameters"
      ref={root}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          e.preventDefault();
          root.current
            ?.querySelector<HTMLButtonElement>(`[data-parameter="${open}"]`)
            ?.focus();
          setOpen(null);
        }
      }}
    >
      {picture.length > 0 && (
        <button
          type="button"
          data-parameter="picture"
          className="composer-control"
          aria-label="画面参数"
          aria-expanded={open === "picture"}
          disabled={disabled}
          onClick={() => setOpen(open === "picture" ? null : "picture")}
        >
          <SlidersHorizontal size={16} />
          <span>{picture.map((key) => controls[key]).join(" · ")}</span>
          <ChevronDown size={13} />
        </button>
      )}
      {controls.duration !== undefined && (
        <button
          type="button"
          data-parameter="duration"
          className="composer-control"
          aria-label="视频时长"
          aria-expanded={open === "duration"}
          disabled={disabled}
          onClick={() => setOpen(open === "duration" ? null : "duration")}
        >
          <Clock3 size={16} />
          <span>{controls.duration} 秒</span>
          <ChevronDown size={13} />
        </button>
      )}
      {open && (
        <div
          className="composer-parameter-menu"
          role="group"
          aria-label={open === "picture" ? "画面参数" : "视频时长"}
        >
          {selected
            .filter((key) => schema[key]?.enum?.length)
            .map((key) => (
              <fieldset key={key}>
                <legend>{labels[key] || key}</legend>
                <div>
                  {schema[key].enum!.map((value) => (
                    <button
                      type="button"
                      key={String(value)}
                      disabled={disabled}
                      aria-pressed={controls[key] === value}
                      onClick={() => onChange(key, value)}
                    >
                      {value}
                      {key === "duration" ? " 秒" : ""}
                    </button>
                  ))}
                </div>
              </fieldset>
            ))}
        </div>
      )}
    </div>
  );
}
