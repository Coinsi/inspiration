import { useEffect, useRef } from "react";

const focusable =
  'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';
const stack: symbol[] = [];

/** Trap the topmost overlay only, and restore the invoking control on close. */
export function useDialogFocus(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  const wasOpen = useRef(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  // Capture before React commits an autoFocus input in the newly opened overlay.
  if (open && !wasOpen.current) returnFocus.current = document.activeElement as HTMLElement | null;
  wasOpen.current = open;
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    const id = Symbol();
    stack.push(id);
    const previous = returnFocus.current;
    const frame = requestAnimationFrame(() => {
      if (ref.current?.contains(document.activeElement)) return;
      const target =
        ref.current?.querySelector<HTMLElement>("[autofocus]") ??
        ref.current?.querySelector<HTMLElement>(focusable);
      (target ?? ref.current)?.focus();
    });
    const keydown = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== id) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close.current();
      }
      if (e.key !== "Tab") return;
      const items = Array.from(ref.current?.querySelectorAll<HTMLElement>(focusable) ?? []).filter(
        (el) => el.getClientRects().length > 0,
      );
      const first = items[0],
        last = items[items.length - 1];
      if (!first) {
        e.preventDefault();
        ref.current?.focus();
        return;
      }
      if (
        !ref.current?.contains(document.activeElement) ||
        (e.shiftKey && document.activeElement === first) ||
        (!e.shiftKey && document.activeElement === last)
      ) {
        e.preventDefault();
        (e.shiftKey ? last : first)?.focus();
      }
    };
    document.addEventListener("keydown", keydown, true);
    return () => {
      cancelAnimationFrame(frame);
      stack.splice(stack.indexOf(id), 1);
      document.removeEventListener("keydown", keydown, true);
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);
  return ref;
}
