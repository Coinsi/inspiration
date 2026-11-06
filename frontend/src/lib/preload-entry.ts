/** Warm only the destination while the user is typing; navigation never waits on this. */
export function preloadEntry(path: string) {
  const jobs: Promise<unknown>[] = [];
  if (path.split("?")[0] === "/projects") jobs.push(import("@/pages/Projects"));
  else if (path.startsWith("/projects/")) {
    jobs.push(import("@/components/AppShell"));
    if (/\/workbench(?:\?|$)/.test(path)) jobs.push(import("@/pages/Workbench"));
    if (/\/materials(?:\?|$)/.test(path)) jobs.push(import("@/pages/Materials"));
  }
  void Promise.allSettled(jobs);
}
