import { Maximize2, Minimize2, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function Modal({
  open,
  onClose,
  title,
  children,
  width = 760,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  const [big, setBig] = useState(false);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-start justify-center p-4 sm:p-8">
      {/* 不透明底:完全遮住背景,随主题变色 */}
      <div className="absolute inset-0 bg-bg" onClick={onClose} />
      <div
        className="relative z-10 flex flex-col rounded-xl border border-border bg-elevated shadow-2xl w-full"
        style={{
          maxWidth: big ? "min(1180px, 95vw)" : width,
          height: big ? "90vh" : "auto",
          maxHeight: "90vh",
        }}
      >
        <div className="flex items-center justify-between px-5 h-14 border-b border-border shrink-0">
          <div className="font-medium">{title}</div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setBig((b) => !b)}
              className="h-8 w-8 rounded-md hover:bg-muted flex items-center justify-center text-muted-foreground"
              title={big ? "还原" : "最大化"}
            >
              {big ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
            <button
              onClick={onClose}
              className="h-8 w-8 rounded-md hover:bg-muted flex items-center justify-center text-muted-foreground"
              title="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
