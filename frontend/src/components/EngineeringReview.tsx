import { Link, useSearchParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, History, Loader2, Play } from "lucide-react";
import { api, blobUrl } from "@/lib/api";
import type { TimelineDocument } from "@/lib/timeline-edit";
import { usePersistentState } from "@/lib/usePersistentState";
import { WorkPicker } from "./WorkPicker";
import { MediaVideo } from "./MediaVideo";
import { Button } from "./ui/button";
import { Modal } from "./ui/modal";
import { useConfirm } from "./ui/confirm";
import { DocumentChangeReview } from "./WorkPackage";

type Capture = { document: TimelineDocument; source_revision: number };
type Snapshot = { timeline_captures?: Record<string, Capture> };
type Change = {
  id: string;
  fingerprint: string;
  base_revision: number;
  status: string;
  committed_revision: number | null;
  created_at: string;
  snapshot?: { sync_reviews?: unknown[] };
  preview_job_id: string | null;
  timeline_update: {
    timeline_id?: string;
    document?: TimelineDocument;
    expected_revision?: number;
    summary?: string[];
    additional_updates?: { timeline_id: string }[];
  };
  preview?: {
    status: string;
    error: string | null;
    blob_hash: string | null;
  } | null;
};
type Commit = { revision: number; snapshot: Snapshot; created_at: string };
const active = new Set(["pending", "submitted", "running"]);

export function EngineeringReview({
  projectId,
  timelineId,
  document,
  aspect,
  disabled,
  onApplied,
}: {
  projectId: string;
  timelineId: string;
  document: TimelineDocument;
  aspect: string;
  disabled: boolean;
  onApplied: (doc: TimelineDocument) => void;
}) {
  const [search] = useSearchParams();
  const [open, setOpen] = useState(search.get("review") === "1");
  const [workId, setWorkId] = usePersistentState(
    `timeline.work.${projectId}.${timelineId}`,
    search.get("work") || "",
  );
  const requestedWork = search.get("work");
  useEffect(() => {
    if (requestedWork) setWorkId(requestedWork);
  }, [requestedWork, setWorkId]);
  const [selected, setSelected] = useState(search.get("change") || "");
  const [error, setError] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const confirm = useConfirm();
  const qc = useQueryClient();
  const base = `/projects/${projectId}`;
  const path = `${base}/works/${workId}/engineering`;
  const current = useQuery({
    queryKey: ["engineering", projectId, workId],
    queryFn: () => api.get<{ revision: number }>(path),
    enabled: open && !!workId,
  });
  const changes = useQuery({
    queryKey: ["engineering-changes", projectId, workId],
    queryFn: () => api.get<Change[]>(`${path}/changes`),
    enabled: open && !!workId,
  });
  const history = useQuery({
    queryKey: ["engineering-history", projectId, workId],
    queryFn: () => api.get<Commit[]>(`${path}/history`),
    enabled: open && !!workId && showHistory,
  });
  const detail = useQuery({
    queryKey: ["engineering-change", projectId, workId, selected],
    queryFn: () => api.get<Change>(`${path}/changes/${selected}`),
    enabled: open && !!workId && !!selected,
    refetchInterval: (q) =>
      q.state.data?.preview && active.has(q.state.data.preview.status)
        ? 2000
        : false,
  });
  const candidate = detail.data;
  const preview = candidate?.preview;
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({
        queryKey: ["engineering-documents", projectId, workId],
      }),
      qc.invalidateQueries({ queryKey: ["engineering", projectId, workId] }),
      qc.invalidateQueries({
        queryKey: ["engineering-changes", projectId, workId],
      }),
      qc.invalidateQueries({
        queryKey: ["engineering-history", projectId, workId],
      }),
      qc.invalidateQueries({
        queryKey: ["engineering-change", projectId, workId],
      }),
    ]);
  };
  const propose = useMutation({
    mutationFn: async (restoreRevision?: number) => {
      const latest = await api.get<{ revision: number }>(path);
      return api.post<Change>(`${path}/changes`, {
        request_key: crypto.randomUUID(),
        base_revision: latest.revision,
        commands: [
          restoreRevision === undefined
            ? {
                operation: "timeline.edit",
                timeline_id: timelineId,
                document,
                aspect_ratio: aspect,
              }
            : {
                operation: "timeline.restore",
                timeline_id: timelineId,
                engineering_revision: restoreRevision,
                timeline_revision: document.revision,
                aspect_ratio: aspect,
              },
        ],
      });
    },
    onSuccess: async (c) => {
      setSelected(c.id);
      setError("");
      await refresh();
    },
    onError: (e: Error) => setError(e.message),
  });
  const render = useMutation({
    mutationFn: () =>
      api.post<Change>(`${path}/changes/${selected}/preview`, {
        fingerprint: candidate!.fingerprint,
        retry_job_id:
          preview && ["failed", "canceled"].includes(preview.status)
            ? candidate!.preview_job_id
            : null,
      }),
    onSuccess: async () => {
      setError("");
      await refresh();
    },
    onError: (e: Error) => setError(e.message),
  });
  const adopt = useMutation({
    mutationFn: async () => {
      await api.post(`${path}/changes/${selected}/accept`, {
        fingerprint: candidate!.fingerprint,
      });
      return api.get<TimelineDocument>(
        `${base}/timelines/${timelineId}/document`,
      );
    },
    onSuccess: async (doc) => {
      onApplied(doc);
      setError("");
      await refresh();
    },
    onError: (e: Error) => {
      setError(e.message);
      void refresh();
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      api.get<TimelineDocument>(`${base}/timelines/${timelineId}/document`),
    onSuccess: (doc) => {
      onApplied(doc);
      setError("");
      void refresh();
    },
    onError: (e: Error) => setError(e.message),
  });
  const busy =
    propose.isPending ||
    render.isPending ||
    adopt.isPending ||
    reload.isPending;
  const applicable = candidate?.timeline_update.timeline_id === timelineId;
  const needsEngineeringReview =
    !!candidate?.timeline_update.additional_updates?.length ||
    !!candidate?.snapshot?.sync_reviews?.length;
  const stale =
    candidate &&
    ((current.data && candidate.base_revision !== current.data.revision) ||
      (candidate.timeline_update.expected_revision !== undefined &&
        candidate.timeline_update.expected_revision !== document.revision));
  const candidates = (changes.data || []).filter(
    (c) =>
      c.timeline_update.timeline_id === timelineId ||
      c.timeline_update.additional_updates?.some(
        (u) => u.timeline_id === timelineId,
      ),
  );
  const queryError =
    current.error || changes.error || detail.error || history.error;
  return (
    <>
      <div className="flex items-center justify-end py-2">
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Check className="h-4 w-4" /> 作品审阅
        </Button>
      </div>
      <Modal
        open={open}
        onClose={() => {
          if (!busy) setOpen(false);
        }}
        title="作品审阅"
        width={1020}
      >
        <div className="space-y-5">
          <p className="text-sm text-muted-foreground">
            把当前剪辑保存为候选，预览满意后再采用。原时间线会保留历史版本。
          </p>
          <WorkPicker
            projectId={projectId}
            value={workId}
            disabled={busy}
            onChange={(id) => {
              setWorkId(id);
              setSelected("");
              setError("");
            }}
          />
          {(error || queryError) && (
            <div
              role="alert"
              className="rounded-lg border border-danger/30 p-3 text-sm text-danger"
            >
              {error || (queryError as Error)?.message}
              <Button size="sm" variant="ghost" onClick={() => void refresh()}>
                重新读取
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={async () => {
                  if (
                    await confirm({
                      title: "载入最新时间线",
                      message:
                        "将替换当前未保存的剪辑草稿，已保存的候选仍会保留。",
                    })
                  )
                    reload.mutate();
                }}
              >
                载入最新时间线
              </Button>
            </div>
          )}
          {workId && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
                <Link
                  to={`${base}/works/${workId}`}
                  className="text-sm text-primary"
                >
                  查看作品 →
                </Link>
                <span className="text-sm text-muted-foreground">
                  {current.isPending
                    ? "读取作品…"
                    : `作品版本 ${current.data?.revision ?? "—"}`}
                </span>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowHistory(!showHistory)}
                  >
                    <History className="h-4 w-4" /> 历史版本
                  </Button>
                  <Button
                    size="sm"
                    disabled={disabled || busy || !current.data}
                    onClick={() => propose.mutate(undefined)}
                  >
                    {propose.isPending ? "保存候选…" : "将当前剪辑保存为候选"}
                  </Button>
                </div>
              </div>
              {showHistory && (
                <div className="max-h-48 space-y-2 overflow-y-auto rounded-lg bg-muted/30 p-3">
                  <p className="text-xs text-muted-foreground">
                    恢复也会先生成候选，不会立即覆盖。最近 30 个工程版本。
                  </p>
                  {history.isPending ? (
                    <p>读取历史…</p>
                  ) : (
                    (history.data || [])
                      .filter((h) => h.snapshot.timeline_captures?.[timelineId])
                      .map((h) => (
                        <div
                          key={h.revision}
                          className="flex items-center justify-between gap-2 text-sm"
                        >
                          <span>
                            版本 {h.revision} ·{" "}
                            {new Date(h.created_at).toLocaleString()}
                          </span>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => propose.mutate(h.revision)}
                          >
                            恢复为候选
                          </Button>
                        </div>
                      ))
                  )}
                  {!history.isPending &&
                    !(history.data || []).some(
                      (h) => h.snapshot.timeline_captures?.[timelineId],
                    ) && (
                      <p className="text-sm">还没有这条时间线的工程版本。</p>
                    )}
                </div>
              )}
              <div className="grid gap-5 md:grid-cols-[220px_minmax(0,1fr)]">
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  <h3 className="text-sm font-medium">剪辑候选</h3>
                  {changes.isPending && <p className="text-sm">读取候选…</p>}
                  {candidates.map((c) => (
                    <button
                      key={c.id}
                      disabled={busy}
                      onClick={() => setSelected(c.id)}
                      className={`w-full rounded-lg border p-3 text-left text-sm ${selected === c.id ? "border-primary bg-primary/5" : "border-border hover:bg-muted"}`}
                    >
                      <span className="block font-medium">
                        {c.status === "accepted"
                          ? `已采用 · 版本 ${c.committed_revision}`
                          : "待审阅"}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {new Date(c.created_at).toLocaleString()}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {c.timeline_update.document?.items.length ?? 0} 个片段 ·{" "}
                        {c.timeline_update.document?.audio.length ?? 0} 条音轨
                      </span>
                    </button>
                  ))}
                  {!changes.isPending && !candidates.length && (
                    <p className="text-sm text-muted-foreground">
                      保存候选后会出现在这里。
                    </p>
                  )}
                </div>
                <div className="min-w-0 space-y-3">
                  <div className="flex min-h-52 items-center justify-center overflow-hidden rounded-xl bg-black/90 text-white/70">
                    {preview?.blob_hash ? (
                      <MediaVideo
                        src={blobUrl(projectId, preview.blob_hash)}
                        label="候选预览"
                      />
                    ) : (
                      <div
                        className="flex flex-col items-center gap-3 p-8 text-center text-sm"
                        role="status"
                      >
                        {preview && active.has(preview.status) ? (
                          <>
                            <Loader2 className="h-7 w-7 animate-spin" />
                            正在制作候选预览，可以关闭窗口稍后回来。
                          </>
                        ) : detail.isFetching ? (
                          "读取候选…"
                        ) : selected ? (
                          preview?.status === "failed" ? (
                            "预览失败，可在下方重试"
                          ) : (
                            "生成预览，检查本次修改的画面与声音"
                          )
                        ) : (
                          "选择一个候选查看预览"
                        )}
                      </div>
                    )}
                  </div>
                  {preview?.error && (
                    <p role="alert" className="text-sm text-danger">
                      {preview.error}
                    </p>
                  )}
                  {needsEngineeringReview && candidate && (
                    <DocumentChangeReview
                      projectId={projectId}
                      workId={workId}
                      changeId={candidate.id}
                    />
                  )}
                  {applicable && !needsEngineeringReview && (
                    <>
                      {candidate?.timeline_update.summary?.map(
                        (line, index) => (
                          <p key={index} className="text-sm">
                            {line}
                          </p>
                        ),
                      )}
                      <p className="text-xs text-muted-foreground">
                        预览使用已保存的候选。之后对剪辑草稿的调整，需要另存一个候选。
                      </p>
                      {stale && candidate?.status !== "accepted" && (
                        <p className="text-sm text-danger">
                          作品或时间线已有新版本，这个候选不能直接采用。请重新保存候选。
                        </p>
                      )}
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button
                          variant="outline"
                          disabled={
                            busy ||
                            candidate?.status === "accepted" ||
                            (!!preview && active.has(preview.status)) ||
                            preview?.status === "succeeded"
                          }
                          onClick={() => render.mutate()}
                        >
                          <Play className="h-4 w-4" />
                          {preview &&
                          ["failed", "canceled"].includes(preview.status)
                            ? "重试预览"
                            : "生成预览"}
                        </Button>
                        <Button
                          disabled={
                            busy ||
                            stale ||
                            preview?.status !== "succeeded" ||
                            !preview.blob_hash ||
                            candidate?.status === "accepted"
                          }
                          onClick={async () => {
                            if (
                              await confirm({
                                title: "采用这个候选",
                                message:
                                  "将用此候选更新工作时间线和当前剪辑草稿，原时间线保留在历史记录中。",
                              })
                            )
                              adopt.mutate();
                          }}
                        >
                          {adopt.isPending
                            ? "正在采用…"
                            : candidate?.status === "accepted"
                              ? "已采用"
                              : "确认采用"}
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </Modal>
    </>
  );
}
