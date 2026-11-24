import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal } from "lucide-react";
import { Button } from "./button";
import { cn } from "@/lib/utils";

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
        className="flex h-11 md:h-9 max-w-full items-center gap-2 rounded-lg px-2 text-sm hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
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
          className={`absolute top-full z-[70] mt-2 w-64 max-w-[calc(100vw-32px)] max-h-[min(70dvh,480px)] overflow-y-auto overscroll-contain rounded-xl border bg-card p-2 shadow-xl ${align === "right" ? "right-0" : "left-0"}`}
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

/** Object actions use Radix for keyboard navigation, focus and viewport collision handling. */
export function ActionMenu({ label, children, trigger }: {
  label: string;
  children: ReactNode;
  trigger?: ReactNode;
}) {
  const opener = useRef<HTMLButtonElement>(null);
  return (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger asChild>
        <Button ref={opener} type="button" variant="ghost" size={trigger ? "sm" : "icon"}
          aria-label={label} title={label} onClick={event => event.stopPropagation()}>
          {trigger ?? <MoreHorizontal size={18} aria-hidden />}
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={4} collisionPadding={8} loop data-action-menu
          aria-label={label}
          onCloseAutoFocus={event => {
            const dialog = document.activeElement?.closest('[aria-modal="true"]');
            if (dialog && !dialog.contains(opener.current)) event.preventDefault();
          }}
          className="z-[110] min-w-44 max-w-[calc(100vw-16px)] max-h-[var(--radix-dropdown-menu-content-available-height)] overflow-y-auto rounded-lg border bg-card p-1 shadow-lg"
          onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
          {children}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function ActionMenuItem({ danger, className, ...props }: DropdownMenu.DropdownMenuItemProps & { danger?: boolean }) {
  return <DropdownMenu.Item {...props} className={cn(
    "flex min-h-10 cursor-pointer select-none items-center gap-2.5 rounded-md px-3 py-2 text-sm outline-none data-[highlighted]:bg-elevated data-[disabled]:cursor-default data-[disabled]:opacity-50 [&_svg]:size-4",
    danger && "text-danger", className,
  )} />;
}

export const ActionMenuSeparator = () => <DropdownMenu.Separator className="my-1 border-t" />;
