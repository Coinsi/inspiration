export function isGeneratedUsername(username?: string | null): boolean {
  return typeof username === "string" && /^[ug]_[a-f0-9]{32}$/.test(username);
}
