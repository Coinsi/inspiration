import { useEffect } from "react";

/** Keep fixed workspaces and dialogs above the software keyboard on mobile Safari. */
export function useMobileViewport() {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const root = document.documentElement;
    const update = () => {
      // Do not resize the app while the user pinch-zooms to read content.
      if (viewport.scale !== 1) return;
      root.style.setProperty("--visual-viewport-height", `${viewport.height}px`);
      root.style.setProperty("--visual-viewport-top", `${viewport.offsetTop}px`);
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      root.style.removeProperty("--visual-viewport-height");
      root.style.removeProperty("--visual-viewport-top");
    };
  }, []);
}
