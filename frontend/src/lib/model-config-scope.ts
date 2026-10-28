/** Admin configuration uses separate, platform-authorized endpoints. */
export function modelConfigRoot(projectId: string) {
  return projectId === "platform" ? "/admin/models" : `/projects/${projectId}`;
}

export function modelConfigQueryKey(key: string, projectId: string) {
  // Shared defaults/capabilities can affect every open project.
  return projectId === "platform" ? [key] : [key, projectId];
}
