/** Admin configuration uses separate, platform-authorized endpoints. */
export function modelConfigRoot(projectId: string) {
  return projectId === "platform" ? "/admin/models" : `/projects/${projectId}`;
}

export function modelConfigQueryKey(key: string, projectId: string) {
  // Shared defaults/capabilities can affect every open project.
  return projectId === "platform" ? [key] : [key, projectId];
}

export type ModelVerification = { status: string; message?: string };

export function verificationLabel(value?: ModelVerification) {
  if (value?.status === "verified") return "已验证生成";
  if (value?.status === "failed") return "上次调用失败";
  return "已配置 · 待验证生成";
}
