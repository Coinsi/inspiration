import { useDialogFocus } from "./useDialogFocus";
import { createContext, useCallback, useContext, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface ConfirmOptions {
  title?: string;
  message?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  /** 危险操作(删除等):确认按钮用红色 */
  danger?: boolean;
}

type ConfirmFn = (opts?: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/**
 * 全局确认弹窗:替代 window.confirm。
 * 用法:const confirm = useConfirm(); if (await confirm({ message, danger:true })) { … }
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  const close = useCallback((v: boolean) => {
    setOpts(null);
    resolver.current?.(v);
    resolver.current = null;
  }, []);

  const confirm = useCallback<ConfirmFn>((o = {}) => {
    setOpts(o);
    return new Promise<boolean>((res) => {
      resolver.current = res;
    });
  }, []);

  const titleId = useId();
  const dialogRef = useDialogFocus(!!opts, () => close(false));

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts &&
        createPortal(
          <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/60  animate-in" onClick={() => close(false)} />
            <div
              ref={dialogRef}
              tabIndex={-1}
              aria-labelledby={titleId}
              role="alertdialog"
              aria-modal="true"
              className="relative z-10 w-full max-w-[420px] rounded-xl border border-border bg-card p-5 shadow-2xl animate-in"
            >
              <div className="flex items-start gap-3">
                <span
                  className={cn(
                    "mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full",
                    opts.danger ? "bg-danger/15 text-danger" : "bg-primary/15 text-primary",
                  )}
                >
                  <AlertTriangle className="h-5 w-5" />
                </span>
                <div className="min-w-0 flex-1 pt-0.5">
                  <div id={titleId} className="text-[15px] font-semibold">
                    {opts.title ?? t("common.confirmTitle")}
                  </div>
                  {opts.message != null && (
                    <div className="mt-1.5 break-words text-sm leading-6 text-muted-foreground">
                      {opts.message}
                    </div>
                  )}
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  onClick={() => close(false)}
                  autoFocus
                  className="h-9 rounded-md border border-border bg-elevated px-4 text-sm font-medium text-muted-foreground transition hover:border-border-strong hover:text-foreground"
                >
                  {opts.cancelText ?? t("common.cancel")}
                </button>
                <button
                  onClick={() => close(true)}
                  className={cn(
                    "h-9 rounded-md px-4 text-sm font-semibold transition",
                    opts.danger
                      ? "bg-danger text-white hover:bg-danger/90"
                      : "bg-primary text-primary-foreground  hover:bg-primary-strong",
                  )}
                >
                  {opts.confirmText ?? t("common.confirm")}
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within ConfirmProvider");
  return ctx;
}
