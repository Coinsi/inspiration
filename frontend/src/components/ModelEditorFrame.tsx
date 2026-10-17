import { useEffect, useRef } from "react";
import { useDialogFocus } from "./ui/useDialogFocus";
import { useConfirm } from "./ui/confirm";
import { useI18n } from "@/lib/i18n";
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
  const confirm = useConfirm();
  const zh = useI18n().lang === "zh";
  const confirming = useRef(false);
  const mounted = useRef(true);
  const dismiss = async () => {
    if (busy || confirming.current) return;
    if (!dirty) {
      close();
      return;
    }
    confirming.current = true;
    try {
      const discard = await confirm({
        title: zh ? "放弃这次修改？" : "Discard changes?",
        message: zh
          ? "模型配置尚未保存，关闭后将丢失本次修改。"
          : "Your model configuration has unsaved changes. Closing will discard them.",
        confirmText: zh ? "放弃修改" : "Discard changes",
        cancelText: zh ? "继续编辑" : "Keep editing",
        danger: true,
      });
      if (discard && mounted.current) close();
    } finally {
      confirming.current = false;
    }
  };
  const dialog = useDialogFocus(true, () => void dismiss());
  useEffect(() => {
    mounted.current = true;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      mounted.current = false;
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
          <button
            aria-label={zh ? "关闭编辑" : "Close editor"}
            disabled={busy}
            onClick={() => void dismiss()}
          >
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
