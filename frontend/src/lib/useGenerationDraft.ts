import { useEffect, useState } from "react";

// Caller remounts per account/project/object, so a draft never crosses scopes.
export function useGenerationDraft(key: string) {
  const [prompt, setPrompt] = useState(() => {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(key) ?? '""');
      return typeof value === "string" ? value.slice(0, 10000) : "";
    } catch {
      return "";
    }
  });
  const [storageError, setStorageError] = useState(false);
  useEffect(() => {
    try {
      if (prompt) localStorage.setItem(key, JSON.stringify(prompt));
      else localStorage.removeItem(key);
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }, [key, prompt]);
  return { prompt, setPrompt, storageError };
}
