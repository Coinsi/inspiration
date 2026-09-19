import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  blobUrl,
  type Generation,
  type GenJob,
  type Shot,
} from "@/lib/api";
import {
  LibraryVersionPicker,
  useLibraryVersion,
} from "./LibraryVersionPicker";
import { Button } from "./ui/button";
import { Modal } from "./ui/modal";

export function CanvasLibraryPanel({
  projectId,
  shots,
  onClose,
  onDone,
}: {
  projectId: string;
  shots: Shot[];
  onClose: () => void;
  onDone: (g: Generation, label: string) => void;
}) {
  const root = `/projects/${projectId}`;
  const [version, setVersion] = useState(""),
    [shot, setShot] = useState(shots[0]?.id ?? ""),
    [start, setStart] = useState(0),
    [end, setEnd] = useState(5),
    [type, setType] = useState<"image" | "video" | "audio">("image");
  const chosen = useLibraryVersion(projectId, version),
    [job, setJob] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [resultAttempt, setResultAttempt] = useState(0);
  const request = useRef({ signature: "", key: "" }),
    [result, setResult] = useState<Generation | null>(null),
    pending = useRef(false),
    cache = useQueryClient();
  const state = useQuery({
    queryKey: ["canvas-library-job", job],
    queryFn: () => api.get<GenJob>(`${root}/jobs/${job}`),
    enabled: !!job,
    refetchInterval: (q) =>
      ["succeeded", "failed", "canceled"].includes(q.state.data?.status ?? "")
        ? false
        : 1000,
  });
  const running =
    !!job &&
    !["succeeded", "failed", "canceled"].includes(state.data?.status ?? "");
  useEffect(() => {
    if (chosen.data?.duration_ms)
      setEnd(Math.min(5, chosen.data.duration_ms / 1000));
  }, [chosen.data?.id]);
  useEffect(() => {
    if (state.data?.status !== "succeeded") return;
    let canceled = false;
    api
      .get<Generation[]>(
        `${root}/generations?target_type=shot&target_id=${shot}`,
      )
      .then((items) => {
        if (!canceled) {
          setResult(items.find((g) => g.job_id === job) ?? null);
          void cache.invalidateQueries({ queryKey: ["gens", "shot", shot] });
        }
      })
      .catch((e) => {
        if (!canceled) setError(e.message);
      });
    return () => {
      canceled = true;
    };
  }, [state.data?.status, job, shot, resultAttempt]);
  async function retry() {
    setBusy(true);
    setError("");
    try {
      const next = await api.post<GenJob>(`${root}/jobs/${job}/retry`);
      setJob(next.id);
      setResultAttempt((n) => n + 1);
      setResult(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function extract() {
    if (pending.current || running) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      if (
        !version ||
        !shot ||
        end <= start ||
        start < 0 ||
        end * 1000 > (chosen.data?.duration_ms ?? 0)
      )
        throw new Error("请选择有效版本、所属镜头和片段范围");
      if (type !== "image" && end - start > 60)
        throw new Error("视频或音轨每次提取最多60秒");
      const options = {
        version_id: version,
        shot_id: shot,
        start_ms: Math.round(start * 1000),
        end_ms: Math.round(end * 1000),
        purpose: "visual_reference",
        note: "从画布选取素材库片段",
      };
      const signature = JSON.stringify({ options, type });
      if (signature !== request.current.signature)
        request.current = { signature, key: crypto.randomUUID() };
      const usage = await api.post<{ id: string }>(
        `${root}/library/usages`,
        options,
      );
      const next = await api.post<GenJob>(
        `${root}/library/usages/${usage.id}/materialize`,
        {
          request_key: request.current.key,
          output_type: type,
          time_ms: Math.round(start * 1000),
        },
      );
      setJob(next.id);
      setResultAttempt((n) => n + 1);
      setResult(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title="从素材库选取画面"
      width={880}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="text-sm text-muted-foreground mb-4">
        在画布中直接选原片、截取时间范围。结果保存到所属镜头并保留原片来源。
      </p>
      <LibraryVersionPicker
        projectId={projectId}
        value={version}
        onChange={(id) => {
          setVersion(id);
          setStart(0);
          setJob("");
          setResult(null);
        }}
        label="选择素材库版本"
        disabled={busy || running}
      />
      {chosen.data?.original_hash && (
        <video
          className="canvas-library-preview"
          src={blobUrl(
            projectId,
            chosen.data.proxy_hash || chosen.data.original_hash,
          )}
          controls
          preload="metadata"
        />
      )}
      <div className="canvas-library-controls">
        <label>
          所属镜头
          <select
            aria-label="片段所属镜头"
            value={shot}
            disabled={busy || running}
            onChange={(e) => {
              setShot(e.target.value);
              setJob("");
              setResult(null);
            }}
          >
            <option value="">选择镜头</option>
            {shots.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title || s.code}
              </option>
            ))}
          </select>
        </label>
        <label>
          开始（秒）
          <input
            aria-label="片段开始秒"
            type="number"
            min="0"
            step="0.1"
            value={start}
            disabled={busy || running}
            onChange={(e) => {
              setStart(+e.target.value);
              setJob("");
              setResult(null);
            }}
          />
        </label>
        <label>
          结束（秒）
          <input
            aria-label="片段结束秒"
            type="number"
            min="0.1"
            step="0.1"
            value={end}
            disabled={busy || running}
            onChange={(e) => {
              setEnd(+e.target.value);
              setJob("");
              setResult(null);
            }}
          />
        </label>
        <label>
          用于
          <select
            aria-label="片段输出类型"
            value={type}
            disabled={busy || running}
            onChange={(e) => {
              setType(e.target.value as typeof type);
              setJob("");
              setResult(null);
            }}
          >
            <option value="image">开始位置的参考帧</option>
            <option value="video">视频片段</option>
            <option value="audio">音轨片段</option>
          </select>
        </label>
      </div>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
      {state.data?.error && (
        <p role="alert" className="text-danger">
          {state.data.error}
        </p>
      )}
      {state.isError && (
        <p role="alert">
          任务状态读取失败。
          <Button onClick={() => void state.refetch()}>重新读取状态</Button>
        </p>
      )}
      {state.data?.status === "succeeded" && !result && (
        <Button
          onClick={() => {
            setError("");
            setResultAttempt((n) => n + 1);
          }}
        >
          重新读取作品
        </Button>
      )}
      {running && (
        <p role="status" className="text-sm">
          正在处理片段，可关闭窗口，任务仍会保留在任务中心。
        </p>
      )}
      <div className="flex justify-end gap-2 mt-4">
        <Button
          disabled={busy || running || !version || !shot}
          onClick={() => void extract()}
        >
          {busy || running ? "处理中…" : "提取所选片段"}
        </Button>
        {state.data?.status === "failed" && (
          <Button disabled={busy} onClick={() => void retry()}>
            重试处理任务
          </Button>
        )}
        {result && (
          <Button
            onClick={() => {
              onDone(
                result,
                `${chosen.data?.name ?? "素材库"} · ${type === "image" ? "参考帧" : type === "video" ? "片段" : "音轨"}`,
              );
              onClose();
            }}
          >
            放入画布
          </Button>
        )}
      </div>
    </Modal>
  );
}
