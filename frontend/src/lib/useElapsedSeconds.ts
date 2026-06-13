import { useEffect, useRef, useState } from "react";

/**
 * running 为 true 时每秒返回已用秒数(从 false→true 那刻起计);false 时归零。
 * 用于长耗时操作(AI 拆解/拆分镜)展示"已用时 Xs"。
 */
export function useElapsedSeconds(running: boolean): number {
  const [sec, setSec] = useState(0);
  const start = useRef(0);

  useEffect(() => {
    if (!running) {
      setSec(0);
      return;
    }
    start.current = Date.now();
    setSec(0);
    const id = setInterval(() => setSec(Math.floor((Date.now() - start.current) / 1000)), 1000);
    return () => clearInterval(id);
  }, [running]);

  return sec;
}
