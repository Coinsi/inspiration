import { useEffect } from "react";
import { useDialogFocus } from "./ui/useDialogFocus";
import { X } from "lucide-react";
import "./model-channels.css";

export function EditorFrame({
  title,
  children,
  close,
  dirty,
  busy,
}: {
  title: string;
  children: React.ReactNode;
  close: () => void;
  dirty: boolean;
  busy: boolean;
}) {
  const dismiss = () => {
    if (!busy && (!dirty || window.confirm("放弃尚未保存的修改？"))) close();
  };
  const dialog = useDialogFocus(true, dismiss);
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, []);
  return (
    <div className="model-overlay">
      <section
        className="model-dialog"
        ref={dialog}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <button aria-label="关闭编辑" disabled={busy} onClick={dismiss}>
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
