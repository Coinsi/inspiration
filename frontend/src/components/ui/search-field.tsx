import { useRef, useState, type InputHTMLAttributes } from "react";
import { Search, X } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import "./search-field.css";

type Props = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  | "value"
  | "defaultValue"
  | "onChange"
  | "type"
  | "size"
  | "onCompositionStart"
  | "onCompositionEnd"
> & {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  onClear?: () => void;
};

/** A search input; callers retain ownership of filtering and submission. */
export function SearchField({
  label,
  value,
  onValueChange,
  onClear,
  className,
  disabled,
  onKeyDown,
  ...props
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const [composition, setComposition] = useState<string | null>(null);
  const zh = useI18n().lang === "zh";
  const clear = () => {
    if (disabled) return;
    composing.current = false;
    setComposition(null);
    onValueChange("");
    onClear?.();
    input.current?.focus();
  };
  return (
    <div
      className={cn("search-field", className)}
      data-disabled={disabled || undefined}
    >
      <Search size={16} aria-hidden="true" />
      <input
        {...props}
        ref={input}
        type="search"
        data-search-input
        aria-label={label}
        disabled={disabled}
        value={composition ?? value}
        onCompositionStart={(e) => {
          composing.current = true;
          setComposition(e.currentTarget.value);
        }}
        onCompositionEnd={(e) => {
          composing.current = false;
          setComposition(null);
          onValueChange(e.currentTarget.value);
        }}
        onChange={(e) => {
          if (composing.current) setComposition(e.target.value);
          else onValueChange(e.target.value);
        }}
        onKeyDown={(e) => {
          if (composing.current || e.nativeEvent.isComposing) {
            if (e.key === "Enter") e.preventDefault();
            e.stopPropagation();
            return;
          }
          if (e.key === "Escape" && value) {
            e.preventDefault();
            e.stopPropagation();
            clear();
            return;
          }
          onKeyDown?.(e);
        }}
      />
      <span className="search-field-end">
        {(composition ?? value) && !disabled && (
          <button
            type="button"
            onClick={clear}
            aria-label={zh ? `清空${label}` : `Clear ${label}`}
            title={zh ? "清空搜索" : "Clear search"}
          >
            <X size={14} aria-hidden="true" />
          </button>
        )}
      </span>
    </div>
  );
}
