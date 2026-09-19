import { useLayoutEffect } from "react";

/** Restore each workspace scope after its data renders; scrolling is saved within this browser tab. */
export function useWorkspaceScroll(key: string, ready: boolean) {
  useLayoutEffect(() => {
    if (!ready) return;
    const main = document.querySelector("main");
    if (!main) return;
    const storageKey = `workspace.scroll.${key}`;
    let saved = 0;
    try {
      saved = Number(sessionStorage.getItem(storageKey)) || 0;
    } catch {
      /* Optional preference. */
    }
    const frame = requestAnimationFrame(() => {
      main.scrollTop = saved;
    });
    const remember = () => {
      try {
        sessionStorage.setItem(storageKey, String(main.scrollTop));
      } catch {
        /* Scrolling remains usable. */
      }
    };
    main.addEventListener("scroll", remember, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      main.removeEventListener("scroll", remember);
    };
  }, [key, ready]);
}
