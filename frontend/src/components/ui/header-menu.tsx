import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

/** Small navigation menu: outside click, Escape, arrow keys and focus return. */
export function HeaderMenu({
  label,
  trigger,
  children,
  align = "right",
  className = "",
}: {
  label: string;
  trigger: ReactNode;
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null),
    button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null),
    id = useId(),
    location = useLocation();
  useEffect(() => setOpen(false), [location.pathname, location.search]);
  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() =>
      menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus(),
    );
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", outside);
    };
  }, [open]);
  const close = () => {
    button.current?.focus();
    setOpen(false);
  };
  return (
    <div
      ref={root}
      className={`relative min-w-0 ${className}`}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        className="flex h-9 max-w-full items-center gap-2 rounded-lg px-2 text-sm hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
          }
          if (e.key === "Escape") close();
        }}
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label={label}
          className={`absolute top-full z-[70] mt-2 w-64 max-w-[calc(100vw-32px)] rounded-xl border bg-card p-2 shadow-xl ${align === "right" ? "right-0" : "left-0"}`}
          onClick={(e) => {
            if ((e.target as HTMLElement).closest('[role="menuitem"]')) close();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              close();
            }
            if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key))
              return;
            e.preventDefault();
            const items = Array.from(
              menu.current?.querySelectorAll<HTMLElement>(
                '[role="menuitem"]',
              ) ?? [],
            );
            const current = items.indexOf(
              document.activeElement as HTMLElement,
            );
            const next =
              e.key === "Home"
                ? 0
                : e.key === "End"
                  ? items.length - 1
                  : (current +
                      (e.key === "ArrowDown" ? 1 : -1) +
                      items.length) %
                    items.length;
            items[next]?.focus();
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export const headerMenuItem =
  "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm hover:bg-elevated focus:bg-elevated focus:outline-none";
