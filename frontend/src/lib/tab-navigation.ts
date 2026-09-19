import type { KeyboardEvent } from "react";

/** Keep arrow-key navigation within the current tab list. */
export function navigateTabs(event: KeyboardEvent<HTMLElement>) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = Array.from(
    event.currentTarget.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]:not(:disabled)',
    ),
  );
  const current = tabs.indexOf(
    (event.target as HTMLElement).closest('[role="tab"]') as HTMLButtonElement,
  );
  if (current < 0 || !tabs.length) return;
  event.preventDefault();
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
          tabs.length;
  tabs[next].focus();
  tabs[next].click();
}
