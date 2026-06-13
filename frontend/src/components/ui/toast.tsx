import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Kind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: Kind;
  msg: string;
}

const ToastContext = createContext<{ push: (msg: string, kind?: Kind) => void } | null>(null);

let seq = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((msg: string, kind: Kind = "info") => {
    const id = seq++;
    setToasts((t) => [...t, { id, kind, msg }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="rounded-md border border-border bg-elevated px-4 py-2.5 text-sm shadow-lg min-w-[220px] animate-in"
          >
            <span
              className={
                t.kind === "success"
                  ? "text-success"
                  : t.kind === "error"
                    ? "text-danger"
                    : "text-info"
              }
            >
              {t.kind === "success" ? "✓ " : t.kind === "error" ? "✕ " : "• "}
            </span>
            {t.msg}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
