import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Sheet({
  open,
  onClose,
  title,
  children,
  width = 520,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  return (
    <div className={cn("fixed inset-0 z-50", open ? "" : "pointer-events-none")}>
      <div
        className={cn(
          "absolute inset-0 bg-black/50 transition-opacity",
          open ? "opacity-100" : "opacity-0",
        )}
        onClick={onClose}
      />
      <div
        className={cn(
          "absolute right-0 top-0 h-full bg-surface border-l border-border shadow-2xl transition-transform overflow-y-auto",
          open ? "translate-x-0" : "translate-x-full",
        )}
        style={{ width }}
      >
        <div className="flex items-center justify-between px-5 h-14 border-b border-border sticky top-0 bg-surface">
          <div className="font-medium">{title}</div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-xl leading-none">
            ×
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
