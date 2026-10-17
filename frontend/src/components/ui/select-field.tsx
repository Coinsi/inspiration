import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import "./select-field.css";

type Choice = {
  value: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  disabled?: boolean;
};

/** Single-choice field with a themed, viewport-bounded list and native-like keyboard navigation. */
export function SelectField({
  label,
  value,
  options,
  onChange,
  disabled,
  className,
  title,
  menuWidth = 240,
}: {
  label?: string;
  title?: string;
  value: string;
  options: Choice[];
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  menuWidth?: number;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [fieldLabel, setFieldLabel] = useState(label);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState({
    left: 0,
    top: 0,
    width: menuWidth,
    maxHeight: 320,
  });
  const typed = useRef({ text: "", at: 0 });
  const chosen = options.find((o) => o.value === value);
  const enabled = options
    .map((o, i) => (o.disabled ? -1 : i))
    .filter((i) => i >= 0);
  const close = (restore = false) => {
    setOpen(false);
    if (restore) trigger.current?.focus();
  };
  const show = (last = false) => {
    if (disabled || trigger.current?.matches(":disabled") || !enabled.length)
      return;
    // Preserve the accessible name of legacy fields wrapped in a label.
    const wrapper = trigger.current?.closest("label")?.cloneNode(true) as
      HTMLElement | undefined;
    wrapper
      ?.querySelectorAll("button, input, textarea, select")
      .forEach((node) => node.remove());
    setFieldLabel(
      label || wrapper?.textContent?.trim() || chosen?.label || "请选择",
    );
    const selected = options.findIndex((o) => o.value === value && !o.disabled);
    setActive(
      selected >= 0
        ? selected
        : last
          ? enabled[enabled.length - 1]
          : enabled[0],
    );
    typed.current = { text: "", at: 0 };
    setOpen(true);
  };
  const pick = (index: number) => {
    const option = options[index];
    if (
      disabled ||
      trigger.current?.matches(":disabled") ||
      !option ||
      option.disabled
    )
      return;
    onChange(option.value);
    close(true);
  };
  useLayoutEffect(() => {
    if (!open) return;
    // The top layer escapes clipping/transform contexts, including drawers.
    popup.current?.setAttribute("popover", "manual");
    popup.current?.showPopover?.();
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(
        Math.max(menuWidth, rect.width),
        window.innerWidth - 24,
      );
      const below = window.innerHeight - rect.bottom - 16;
      const above = rect.top - 16;
      const desired = Math.min(popup.current?.scrollHeight || 320, 320);
      const upward = below < desired && above > below;
      const maxHeight = Math.max(48, Math.min(320, upward ? above : below));
      setPosition({
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        top: upward
          ? Math.max(12, rect.top - Math.min(desired, maxHeight) - 8)
          : rect.bottom + 8,
        width,
        maxHeight,
      });
    };
    place();
    popup.current?.focus();
    window.addEventListener("resize", place);
    const scroll = (e: Event) => {
      if (!popup.current?.contains(e.target as Node)) place();
    };
    window.addEventListener("scroll", scroll, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [open, menuWidth, options.length]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => {
      if (
        !trigger.current?.contains(e.target as Node) &&
        !popup.current?.contains(e.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
    };
  }, [open]);
  useEffect(() => {
    if (disabled || !enabled.length) setOpen(false);
    else if (open && !enabled.includes(active)) setActive(enabled[0]);
  }, [disabled, options, open, active]);
  useEffect(() => {
    if (open)
      popup.current
        ?.querySelector(`[data-index="${active}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [active, open, position.width]);
  function keys(e: KeyboardEvent<HTMLDivElement>) {
    if (
      ["ArrowDown", "ArrowUp", "Home", "End", "Enter", " ", "Escape"].includes(
        e.key,
      )
    ) {
      e.preventDefault();
      e.stopPropagation();
    }
    const at = enabled.indexOf(active);
    if (e.key === "ArrowDown") setActive(enabled[(at + 1) % enabled.length]);
    else if (e.key === "ArrowUp")
      setActive(enabled[(at - 1 + enabled.length) % enabled.length]);
    else if (e.key === "Home") setActive(enabled[0]);
    else if (e.key === "End") setActive(enabled[enabled.length - 1]);
    else if (e.key === "Enter" || e.key === " ") pick(active);
    else if (e.key === "Escape") close(true);
    else if (e.key === "Tab") {
      const dialog = trigger.current?.closest('[role="dialog"]');
      if (dialog) {
        e.preventDefault();
        const fields = Array.from(
          dialog.querySelectorAll<HTMLElement>(
            'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
          ),
        ).filter(
          (el) =>
            el.getClientRects().length && !el.closest("[data-select-popup]"),
        );
        const current = fields.indexOf(trigger.current!);
        close();
        fields[
          (current + (e.shiftKey ? -1 : 1) + fields.length) % fields.length
        ]?.focus();
      } else close(true);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const now = Date.now();
      typed.current = {
        text:
          (now - typed.current.at < 700 ? typed.current.text : "") +
          e.key.toLowerCase(),
        at: now,
      };
      const match = enabled.find((i) =>
        options[i].label.toLowerCase().startsWith(typed.current.text),
      );
      if (match !== undefined) setActive(match);
    }
  }
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={cn("studio-select-trigger", className)}
        title={title}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        disabled={disabled || !enabled.length}
        onClick={() => (open ? close() : show())}
        onKeyDown={(e) => {
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
            e.preventDefault();
            show(e.key === "ArrowUp" || e.key === "End");
            if (e.key === "Home") setActive(enabled[0]);
            if (e.key === "End") setActive(enabled[enabled.length - 1]);
          }
        }}
      >
        {chosen?.icon}
        <span>{chosen?.label || "请选择"}</span>
        <ChevronDown size={14} className="studio-select-chevron" />
      </button>
      {open &&
        createPortal(
          <div
            ref={popup}
            id={id}
            role="listbox"
            tabIndex={0}
            aria-label={fieldLabel}
            aria-activedescendant={`${id}-${active}`}
            data-select-popup
            className="studio-select-popup"
            style={position}
            onKeyDown={keys}
          >
            {options.map((o, i) => (
              <div
                key={o.value}
                id={`${id}-${i}`}
                role="option"
                aria-selected={o.value === value}
                aria-disabled={o.disabled || undefined}
                data-index={i}
                data-active={i === active || undefined}
                className="studio-select-option"
                onPointerMove={() => !o.disabled && setActive(i)}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  pick(i);
                }}
              >
                {o.icon && <span className="studio-select-icon">{o.icon}</span>}
                <span className="studio-select-copy">
                  <span>{o.label}</span>
                  {o.description && <small>{o.description}</small>}
                </span>
                {o.value === value && (
                  <Check size={16} className="studio-select-check" />
                )}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
