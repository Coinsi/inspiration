import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type CSSProperties,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import "./select-field.css";

type Choice = {
  value: string;
  label: string;
  selectedLabel?: string;
  description?: string;
  keywords?: string;
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
  searchable = false,
  placeholder = "请选择",
  searchPlaceholder,
}: {
  label?: string;
  title?: string;
  value: string;
  options: Choice[];
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  menuWidth?: number;
  searchable?: boolean;
  placeholder?: string;
  searchPlaceholder?: string;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [fieldLabel, setFieldLabel] = useState(label);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState<CSSProperties>({
    left: 0,
    top: 0,
    width: menuWidth,
    maxHeight: 320,
  });
  const typed = useRef({ text: "", at: 0 });
  const chosen = options.find((o) => o.value === value);
  const term = query.trim().toLowerCase();
  const visible = options
    .map((option, index) => ({ option, index }))
    .filter(
      ({ option }) =>
        !searchable ||
        [option.label, option.description, option.keywords, option.value]
          .join(" ").toLowerCase().includes(term),
    );
  const enabled = visible
    .filter(({ option }) => !option.disabled)
    .map(({ index }) => index);
  const available = options.some((option) => !option.disabled);
  const close = (restore = false) => {
    setOpen(false);
    setQuery("");
    if (restore) trigger.current?.focus();
  };
  const show = (last = false) => {
    if (disabled || trigger.current?.matches(":disabled") || !available) return;
    // Preserve the accessible name of legacy fields wrapped in a label.
    const wrapper = trigger.current?.closest("label")?.cloneNode(true) as
      | HTMLElement
      | undefined;
    wrapper
      ?.querySelectorAll("button, input, textarea, select")
      .forEach((node) => node.remove());
    setFieldLabel(
      label || wrapper?.textContent?.trim() || chosen?.label || placeholder,
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
    setQuery("");
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
      const dialog = trigger.current?.closest('[role="dialog"], dialog')?.getBoundingClientRect();
      const leftEdge = Math.max(12, (dialog?.left ?? 0) + 8);
      const rightEdge = Math.min(window.innerWidth - 12, (dialog?.right ?? window.innerWidth) - 8);
      const width = Math.min(
        Math.max(menuWidth, rect.width),
        rightEdge - leftEdge,
      );
      const below = window.innerHeight - rect.bottom - 16;
      const above = rect.top - 16;
      const desired = Math.min(
        320,
        (list.current?.scrollHeight ?? 320) +
          (search.current?.parentElement?.offsetHeight ?? 0) +
          14,
      );
      const upward = below < desired && above > below;
      const maxHeight = Math.max(48, Math.min(320, upward ? above : below));
      setPosition({
        left: Math.max(leftEdge, Math.min(rect.left, rightEdge - width)),
        top: upward ? "auto" : rect.bottom + 6,
        bottom: upward ? window.innerHeight - rect.top + 6 : "auto",
        width,
        maxHeight,
      });
    };
    place();
    (search.current ?? list.current)?.focus({ preventScroll: true });
    window.addEventListener("resize", place);
    const scroll = (e: Event) => {
      if (!popup.current?.contains(e.target as Node)) place();
    };
    window.addEventListener("scroll", scroll, true);
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) setOpen(false);
    });
    if (trigger.current) observer.observe(trigger.current);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", scroll, true);
      observer.disconnect();
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
    if (disabled || !available) setOpen(false);
    else if (open && !enabled.includes(active)) setActive(enabled[0] ?? -1);
  }, [disabled, available, enabled, open, active]);
  useEffect(() => {
    const box = list.current;
    const option = box?.querySelector(`[data-index="${active}"]`);
    if (!open || !box || !option) return;
    const row = option.getBoundingClientRect();
    const bounds = box.getBoundingClientRect();
    if (row.top < bounds.top) box.scrollTop -= bounds.top - row.top;
    else if (row.bottom > bounds.bottom)
      box.scrollTop += row.bottom - bounds.bottom;
  }, [active, open, position.width]);
  function keys(e: KeyboardEvent<HTMLDivElement>) {
    if (e.nativeEvent.isComposing) return;
    const inSearch = e.target === search.current;
    if (inSearch && [" ", "Home", "End"].includes(e.key)) return;
    if (
      ["ArrowDown", "ArrowUp", "Home", "End", "Enter", " ", "Escape"].includes(
        e.key,
      )
    ) {
      e.preventDefault();
      e.stopPropagation();
    }
    const at = enabled.indexOf(active);
    if (e.key === "ArrowDown" && enabled.length)
      setActive(enabled[(at + 1) % enabled.length]);
    else if (e.key === "ArrowUp" && enabled.length)
      setActive(enabled[(at - 1 + enabled.length) % enabled.length]);
    else if (e.key === "Home" && enabled.length) setActive(enabled[0]);
    else if (e.key === "End" && enabled.length)
      setActive(enabled[enabled.length - 1]);
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
    } else if (
      !inSearch &&
      e.key.length === 1 &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey
    ) {
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
        disabled={disabled || !available}
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
        <span>{chosen?.selectedLabel || chosen?.label || placeholder}</span>
        <ChevronDown size={14} className="studio-select-chevron" />
      </button>
      {open &&
        createPortal(
          <div
            ref={popup}
            data-select-popup
            className="studio-select-popup"
            style={position}
            onKeyDown={keys}
          >
            {searchable && (
              <div className="studio-select-search">
                <Search size={14} aria-hidden="true" />
                <input
                  ref={search}
                  type="text"
                  role="combobox"
                  aria-expanded="true"
                  aria-autocomplete="list"
                  aria-controls={id}
                  aria-activedescendant={
                    active >= 0 ? `${id}-${active}` : undefined
                  }
                  aria-label={searchPlaceholder || `搜索${fieldLabel}`}
                  placeholder={searchPlaceholder || `搜索${fieldLabel}`}
                  autoComplete="off"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            )}
            <div
              ref={list}
              id={id}
              role="listbox"
              tabIndex={-1}
              className="studio-select-list"
              aria-label={fieldLabel}
              aria-activedescendant={
                active >= 0 ? `${id}-${active}` : undefined
              }
            >
              {visible.map(({ option: o, index: i }) => (
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
                  {o.icon && (
                    <span className="studio-select-icon">{o.icon}</span>
                  )}
                  <span className="studio-select-copy">
                    <span>{o.label}</span>
                    {o.description && <small>{o.description}</small>}
                  </span>
                  {o.value === value && (
                    <Check size={16} className="studio-select-check" />
                  )}
                </div>
              ))}
            </div>
            {!visible.length && (
              <p className="studio-select-empty" role="status">
                没有匹配项
              </p>
            )}
          </div>,
          trigger.current?.closest('[role="dialog"], dialog') ?? document.body,
        )}
    </>
  );
}
