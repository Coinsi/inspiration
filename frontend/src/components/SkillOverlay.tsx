import { useEffect, useId, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useDialogFocus } from "@/components/ui/useDialogFocus";

export function SkillOverlay({
  open,
  onClose,
  title,
  subtitle,
  actions,
  children,
  drawer = false,
  dismissDisabled = false,
  className = "",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  drawer?: boolean;
  dismissDisabled?: boolean;
  className?: string;
}) {
  const titleId = useId(),
    ref = useDialogFocus(open, () => {
      if (!dismissDisabled) onClose();
    });
  useEffect(() => {
    if (!open) return;
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = old;
    };
  }, [open]);
  if (!open) return null;
  return createPortal(
    <div
      className={`knowledge-overlay ${drawer ? "is-drawer" : ""} ${className}`}
    >
      <div
        className="knowledge-backdrop"
        onClick={() => {
          if (!dismissDisabled) onClose();
        }}
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="knowledge-dialog"
      >
        <header className="knowledge-dialog-heading">
          <div className="min-w-0">
            <div className="knowledge-dialog-eyebrow">{subtitle}</div>
            <h2 id={titleId}>{title}</h2>
          </div>
          <div className="knowledge-dialog-actions">
            {actions}
            <button
              className="knowledge-icon-button"
              onClick={onClose}
              disabled={dismissDisabled}
              aria-label="关闭"
              title="关闭"
            >
              <X size={19} />
            </button>
          </div>
        </header>
        {children}
      </div>
    </div>,
    document.body,
  );
}
