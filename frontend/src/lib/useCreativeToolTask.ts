import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { api, type AppliedScene } from "./api";

export type ToolTask<T> = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "applied";
  result: { output: T } | null;
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
      cache.setQueryData(key, task);
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
    },
  });
  const retryScenes = useMutation({
    mutationFn: () =>
      api.post<ToolTask<T>>(`${base}/${query.data!.id}/retry-scenes`),
    onSuccess: (task) => cache.setQueryData(key, task),
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
