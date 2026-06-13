import { useEffect, useState } from "react";

/**
 * 和 useState 一样,但值会持久化到 localStorage(按 key),刷新/切页都记得。
 * 用法:const [v, setV] = usePersistentState("key", "")
 */
export function usePersistentState<T>(key: string, initial: T) {
  const [state, setState] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw !== null ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch {
      /* 忽略写入失败(隐私模式/超额) */
    }
  }, [key, state]);

  return [state, setState] as const;
}
