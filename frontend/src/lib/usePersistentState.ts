import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 和 useState 一样,但值会持久化到 localStorage(按 key),刷新/切页都记得。
 * 用法:const [v, setV] = usePersistentState("key", "")
 */
export function usePersistentState<T>(key: string, initial: T) {
  const initialRef = useRef(initial);
  initialRef.current = initial;
  const read = useCallback(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw !== null ? (JSON.parse(raw) as T) : initialRef.current;
    } catch {
      return initialRef.current;
    }
  }, [key]);
  const [stored, setStored] = useState(() => ({ key, value: read() }));
  // A route may change the key without unmounting this component. Read the new
  // scope before rendering or persisting, never copy the previous project's value.
  const current = stored.key === key ? stored : { key, value: read() };
  if (stored.key !== key) setStored(current);
  const state = current.value;
  const setState = useCallback(
    (next: T | ((previous: T) => T)) => {
      setStored((previous) => {
        const value = previous.key === key ? previous.value : read();
        const updated =
          typeof next === "function" ? (next as (value: T) => T)(value) : next;
        return previous.key === key && Object.is(value, updated)
          ? previous
          : { key, value: updated };
      });
    },
    [key, read],
  );

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch {
      /* 忽略写入失败(隐私模式/超额) */
    }
  }, [key, state]);

  return [state, setState] as const;
}
