import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { api, type AppliedScene } from "./api";
import type { SceneProgress } from "./task-center";

export type ToolTask<T> = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "applied";
  result: { output: T; progress?: SceneProgress } | null;
  payload: { content?: string; skills?: unknown[] };
  error: string | null;
};

/** Server receipts survive navigation; retry a lost submission with the same key. */
export function useCreativeToolTask<T>(
  projectId: string,
  operation: string,
  targetId: string,
) {
  const cache = useQueryClient();
  const base = `/projects/${projectId}/creative-tool-tasks`;
  const key = ["creative-tool-task", projectId, operation, targetId];
  const pendingRequest = useRef<{ signature: string; key: string } | null>(
    null,
  );
  function remember(task: ToolTask<T>) {
    cache.setQueryData(key, task);
    void cache.invalidateQueries({ queryKey: ["jobs", projectId] });
    void cache.invalidateQueries({ queryKey: ["activity-detail", projectId, "creative", task.id] });
  }
  const query = useQuery({
    queryKey: key,
    queryFn: () =>
      api.get<ToolTask<T> | null>(
        `${base}?operation=${operation}&target_id=${targetId}`,
      ),
    enabled: !!targetId,
    refetchInterval: (q) =>
      q.state.data && ["queued", "running"].includes(q.state.data.status)
        ? 2000
        : 15_000,
  });
  const submit = useMutation({
    mutationFn: (payload: Record<string, unknown> = {}) => {
      const signature = JSON.stringify([
        projectId,
        operation,
        targetId,
        payload,
      ]);
      if (pendingRequest.current?.signature !== signature) {
        pendingRequest.current = { signature, key: crypto.randomUUID() };
      }
      return api.post<ToolTask<T>>(base, {
        operation,
        target_id: targetId,
        request_key: pendingRequest.current.key,
        ...payload,
      });
    },
    onSuccess: (task) => {
      remember(task);
      pendingRequest.current = null;
    },
    onError: () => {
      void cache.invalidateQueries({ queryKey: key });
    },
  });
  const apply = useMutation({
    mutationFn: (
      choice?:
        | number[]
        | { fill_empty_scenes: boolean }
        | { selected: number[]; matches: Record<number, string | null> },
    ) =>
      api.post<{ applied: boolean; scenes?: AppliedScene[] }>(
        `${base}/${query.data!.id}/apply`,
        Array.isArray(choice) ? { selected: choice } : choice || {},
      ),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: key });
      void cache.invalidateQueries({ queryKey: ["jobs", projectId] });
      void cache.invalidateQueries({ queryKey: ["activity-detail", projectId] });
    },
  });
  const retryScenes = useMutation({
    mutationFn: () =>
      api.post<ToolTask<T>>(`${base}/${query.data!.id}/retry-scenes`),
    onSuccess: remember,
  });
  const busy =
    submit.isPending ||
    (!!query.data && ["queued", "running"].includes(query.data.status));
  return {
    query,
    task: query.data,
    busy,
    submit,
    apply,
    retryScenes,
    ready: !!targetId && !query.isPending && !query.isError,
  };
}
