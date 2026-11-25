import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, X, XCircle } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, type DecomposeResult, type Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useElapsedSeconds } from "@/lib/useElapsedSeconds";
import { sceneProgressLabel, type SceneProgress } from "@/lib/task-center";

type Status = "running" | "done" | "error";
type Kind = "breakdown" | "decompose";

interface Task {
  id: string;
  kind: Kind;
  projectId: string;
  status: Status;
  label: string;
  error?: string;
  // breakdown
  sceneId?: string;
  count?: number;
  unfinished?: number;
  progress?: SceneProgress;
  // decompose
  novelId?: string;
  chapterId?: string;
  result?: DecomposeResult;
}

type BreakdownArgs = { projectId: string; sceneId: string; sceneName: string };
type DecomposeArgs = {
  projectId: string;
  novelId: string;
  chapterId: string;
  chapterName: string;
};

const Ctx = createContext<{
  task: Task | null;
  startBreakdown: (a: BreakdownArgs) => void;
  startDecompose: (a: DecomposeArgs) => void;
  dismiss: () => void;
} | null>(null);

type Receipt = {
  id: string;
  operation: string;
  target_id: string;
  status: string;
  error: string | null;
  result: { output: DecomposeResult | Shot[]; novel_id?: string; progress?: SceneProgress } | null;
};

/** Durable server receipts remain discoverable after refresh or route changes. */
export function BackgroundTaskProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const location = useLocation();
  const projectId = location.pathname.match(/\/projects\/([^/]+)/)?.[1] ?? "";
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const receipts = useQuery({
    queryKey: ["background-tool-tasks", projectId],
    queryFn: () =>
      api.get<Receipt[]>(`/projects/${projectId}/creative-tool-tasks/recent`),
    enabled: !!projectId,
    refetchInterval: (q) =>
      q.state.data?.some((r) => ["queued", "running"].includes(r.status))
        ? 2000
        : 15_000,
  });
  const newestTargets = new Set<string>();
  const tasks: Task[] = (receipts.data ?? [])
    .filter((r) => {
      const target = `${r.operation}:${r.target_id}`;
      if (newestTargets.has(target)) return false;
      newestTargets.add(target);
      return !dismissed.includes(r.id);
    })
    .map((r) => {
      const breakdown = r.operation === "scene_breakdown";
      return {
        id: r.id,
        kind: breakdown ? "breakdown" : "decompose",
        projectId,
        status:
          r.status === "succeeded"
            ? "done"
            : r.status === "failed"
              ? "error"
              : "running",
        label: breakdown ? "AI 拆分镜" : "章节拆解",
        error: r.error ?? undefined,
        progress: r.result?.progress,
        unfinished: breakdown
          ? (r.result?.output as Shot[] | undefined)?.filter(
              (s) => s.storyboard?.breakdown_error,
            ).length
          : (r.result?.output as DecomposeResult | undefined)?.scenes?.filter(
              (s) => s.error,
            ).length,
        sceneId: breakdown ? r.target_id : undefined,
        chapterId: breakdown ? undefined : r.target_id,
        novelId: r.result?.novel_id,
        result: !breakdown ? (r.result?.output as DecomposeResult) : undefined,
        count: breakdown
          ? (r.result?.output as Shot[] | undefined)?.length
          : (r.result?.output as DecomposeResult | undefined)?.scenes.length,
      };
    });
  const completed = (receipts.data ?? [])
    .filter((r) => r.status === "succeeded")
    .map((r) => r.id)
    .join(",");
  useEffect(() => {
    if (!completed) return;
    void qc.invalidateQueries({ queryKey: ["scene-shots", projectId] });
    void qc.invalidateQueries({ queryKey: ["scenes-all", projectId] });
  }, [completed, projectId, qc]);
  const start = useCallback(
    async (pid: string, operation: string, targetId: string) => {
      try {
        setSubmitError(null);
        const receipt = await api.post<Receipt>(
          `/projects/${pid}/creative-tool-tasks`,
          {
            operation,
            target_id: targetId,
            request_key: crypto.randomUUID(),
          },
        );
        setDismissed(ids => ids.filter(id => id !== receipt.id));
        setFocusedId(receipt.id);
      } catch (e) {
        setSubmitError((e as Error).message);
      } finally {
        void qc.invalidateQueries({ queryKey: ["background-tool-tasks", pid] });
      }
    },
    [qc],
  );
  const task = tasks.find((item) => item.id === focusedId) ?? tasks[0] ?? null;
  const dismiss = useCallback(() => {
    if (task) setDismissed((ids) => [...ids, task.id]);
  }, [task]);
  return (
    <Ctx.Provider
      value={{
        task,
        startBreakdown: (a) => {
          void start(a.projectId, "scene_breakdown", a.sceneId);
        },
        startDecompose: (a) => {
          void start(a.projectId, "chapter_decompose", a.chapterId);
        },
        dismiss,
      }}
    >
      {children}
      {submitError && (
        <div
          role="alert"
          className="fixed bottom-5 left-5 z-[91] rounded-xl bg-card p-4"
        >
          {submitError}
          <button onClick={() => setSubmitError(null)}>关闭</button>
        </div>
      )}
      <div className="fixed bottom-5 left-5 z-[90] max-h-[60vh] space-y-2 overflow-auto">
        {tasks.map((item) => (
          <TaskBadge
            key={item.id}
            task={item}
            onView={() => setFocusedId(item.id)}
            onRetry={() => {
              if (item.kind === "decompose" && item.unfinished) {
                void api
                  .post(
                    "/projects/" +
                      projectId +
                      "/creative-tool-tasks/" +
                      item.id +
                      "/retry-scenes",
                  )
                  .then(() =>
                    qc.invalidateQueries({
                      queryKey: ["background-tool-tasks", projectId],
                    }),
                  )
                  .catch((e) => setSubmitError(e.message));
                return;
              }
              void start(
                projectId,
                item.kind === "breakdown"
                  ? "scene_breakdown"
                  : "chapter_decompose",
                (item.sceneId || item.chapterId)!,
              );
            }}
            onClose={() => setDismissed((ids) => [...ids, item.id])}
          />
        ))}
      </div>
    </Ctx.Provider>
  );
}

function TaskBadge({
  task,
  onClose,
  onView,
  onRetry,
}: {
  task: Task | null;
  onClose: () => void;
  onView: () => void;
  onRetry: () => void;
}) {
  const { t: tr, lang } = useI18n();
  const navigate = useNavigate();
  const sec = useElapsedSeconds(task?.status === "running");
  if (!task) return null;

  const running = task.status === "running";
  const done = task.status === "done";
  const isBreakdown = task.kind === "breakdown";

  const runningText = isBreakdown
    ? tr("sb.breakingDown")
    : tr("nar.decomposing");
  const doneText = isBreakdown
    ? tr("sb.breakdownDone").replace("{n}", String(task.count ?? 0))
    : tr("nar.decomposeDone").replace("{n}", String(task.count ?? 0));

  const view = () => {
    onView();
    try {
      if (isBreakdown && task.sceneId) {
        localStorage.setItem(
          `sb.scene.${task.projectId}`,
          JSON.stringify(task.sceneId),
        );
        navigate(`/projects/${task.projectId}/storyboard`);
      } else if (task.novelId && task.chapterId) {
        localStorage.setItem(
          `nar.novel.${task.projectId}`,
          JSON.stringify(task.novelId),
        );
        localStorage.setItem(
          `nar.chapter.${task.projectId}`,
          JSON.stringify(task.chapterId),
        );
        navigate(`/projects/${task.projectId}/narrative`);
      }
    } catch {
      /* ignore */
    }
    // 拆解结果需保留给目标页消费,这里不立即清除;拆分镜可直接关
    if (isBreakdown) onClose();
  };

  return (
    <div className="w-[320px] max-w-[calc(100vw-2.5rem)] animate-in">
      <div className="glass flex items-start gap-3 rounded-xl border border-border p-3 shadow-panel ">
        <span className="mt-0.5 shrink-0">
          {running ? (
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
          ) : done ? (
            <CheckCircle2 className="h-5 w-5 text-success" />
          ) : (
            <XCircle className="h-5 w-5 text-danger" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium">{task.label}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {running && (task.progress?.total ? sceneProgressLabel(task.progress, lang === "zh") : `${runningText} ${sec}s`)}
            {done && (task.unfinished ? "待补全 " + task.unfinished + (isBreakdown ? " 个镜头" : " 个场次") : doneText)}
            {task.status === "error" && (task.error || tr("bg.failed"))}
          </div>
          {running && <Link className="studio-link text-xs"
            to={`/projects/${task.projectId}/tasks?category=story&kind=creative&task=${task.id}`}>
            {lang === "zh" ? "查看进度" : "View progress"}
          </Link>}
          {(task.status === "error" || (done && !!task.unfinished)) && (
            <button className="mt-1.5 text-xs text-primary" onClick={onRetry}>
              {task.unfinished ? "只重试未完成项" : "重试"}
            </button>
          )}
          {done && (
            <button
              onClick={view}
              className="mt-1.5 text-xs font-medium text-primary hover:underline"
            >
              {tr("bg.view")} →
            </button>
          )}
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded-md p-0.5 text-faint hover:text-foreground"
          title={tr("common.cancel")}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

export function useBackgroundTasks() {
  const ctx = useContext(Ctx);
  if (!ctx)
    throw new Error(
      "useBackgroundTasks must be used within BackgroundTaskProvider",
    );
  return ctx;
}
