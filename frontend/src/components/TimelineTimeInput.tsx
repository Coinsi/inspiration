import { useEffect, useState } from "react";
/** Keep decimal/empty drafts local; one committed edit remains one undo step. */
export function TimelineTimeInput({
  value,
  onChange,
  min = 0,
  max = 1800000,
  ...props
}: {
  value: number;
  onChange: (ms: number) => void;
  min?: number;
  max?: number;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}) {
  const [draft, setDraft] = useState(String(value / 1000));
  useEffect(() => setDraft(String(value / 1000)), [value]);
  const commit = () => {
    const n = Number(draft);
    if (!draft.trim() || !Number.isFinite(n)) {
      setDraft(String(value / 1000));
      return;
    }
    const ms = Math.max(
      min,
      Math.min(Math.max(min, max), Math.round(n * 1000)),
    );
    setDraft(String(ms / 1000));
    if (ms !== value) onChange(ms);
  };
  return (
    <input
      {...props}
      type="number"
      step={0.1}
      min={min / 1000}
      max={max / 1000}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          e.currentTarget.blur();
        }
        if (e.key === "Escape") {
          e.preventDefault();
          setDraft(String(value / 1000));
        }
      }}
    />
  );
}
