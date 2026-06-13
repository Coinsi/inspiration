import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, type DecomposeResult, type Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useElapsedSeconds } from "@/lib/useElapsedSeconds";

type Status = "running" | "done" | "error";
type Kind = "breakdown" | "decompose";

interface Task {
  id: number;
  kind: Kind;
  projectId: string;
  status: Status;
  label: string;
  error?: string;
  // breakdown
  sceneId?: string;
  count?: number;
  // decompose
  novelId?: string;
  chapterId?: string;
  result?: DecomposeResult;
}

type BreakdownArgs = { projectId: string; sceneId: string; sceneName: string };
type DecomposeArgs = { projectId: string; novelId: string; chapterId: string; chapterName: string };

const Ctx = createContext<{
  task: Task | null;
  startBreakdown: (a: BreakdownArgs) => void;
  startDecompose: (a: DecomposeArgs) => void;
  dismiss: () => void;
} | null>(null);

let seq = 1;

/** 全局后台任务(AI 拆分镜 / AI 拆解):脱离页面,切菜单也不丢,完成/失败有左下角角标。 */
export function BackgroundTaskProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [task, setTask] = useState<Task | null>(null);
  const runningId = useRef<number | null>(null);

  const startBreakdown = useCallback(
    (a: BreakdownArgs) => {
      const id = seq++;
      runningId.current = id;
      setTask({ id, kind: "breakdown", projectId: a.projectId, sceneId: a.sceneId, label: a.sceneName, status: "running" });
      api
        .post<Shot[]>(`/projects/${a.projectId}/scenes/${a.sceneId}/breakdown`, {})
        .then((shots) => {
          void qc.invalidateQueries({ queryKey: ["scene-shots", a.projectId, a.sceneId] });
          void qc.invalidateQueries({ queryKey: ["scenes-all", a.projectId] });
          if (runningId.current === id) setTask({ id, kind: "breakdown", projectId: a.projectId, sceneId: a.sceneId, label: a.sceneName, status: "done", count: shots.length });
        })
        .catch((e) => {
          if (runningId.current === id) setTask({ id, kind: "breakdown", projectId: a.projectId, sceneId: a.sceneId, label: a.sceneName, status: "error", error: (e as Error).message });
        });
    },
    [qc],
  );

  const startDecompose = useCallback((a: DecomposeArgs) => {
    const id = seq++;
    runningId.current = id;
    const baseTask = { id, kind: "decompose" as const, projectId: a.projectId, novelId: a.novelId, chapterId: a.chapterId, label: a.chapterName };
    setTask({ ...baseTask, status: "running" });
    api
      .post<DecomposeResult>(`/projects/${a.projectId}/novels/decompose?chapter_id=${a.chapterId}`)
      .then((r) => {
        if (runningId.current === id) setTask({ ...baseTask, status: "done", result: r, count: r.scenes.length });
      })
      .catch((e) => {
        if (runningId.current === id) setTask({ ...baseTask, status: "error", error: (e as Error).message });
      });
  }, []);

  const dismiss = useCallback(() => {
    runningId.current = null;
    setTask(null);
  }, []);

  return (
    <Ctx.Provider value={{ task, startBreakdown, startDecompose, dismiss }}>
      {children}
      <TaskBadge task={task} onClose={dismiss} />
    </Ctx.Provider>
  );
}

function TaskBadge({ task, onClose }: { task: Task | null; onClose: () => void }) {
  const { t: tr } = useI18n();
  const navigate = useNavigate();
  const sec = useElapsedSeconds(task?.status === "running");
  if (!task) return null;

  const running = task.status === "running";
  const done = task.status === "done";
  const isBreakdown = task.kind === "breakdown";

  const runningText = isBreakdown ? tr("sb.breakingDown") : tr("nar.decomposing");
  const doneText = isBreakdown
    ? tr("sb.breakdownDone").replace("{n}", String(task.count ?? 0))
    : tr("nar.decomposeDone").replace("{n}", String(task.count ?? 0));

  const view = () => {
    try {
      if (isBreakdown && task.sceneId) {
        localStorage.setItem(`sb.scene.${task.projectId}`, JSON.stringify(task.sceneId));
        navigate(`/projects/${task.projectId}/storyboard`);
      } else if (task.novelId && task.chapterId) {
        localStorage.setItem(`nar.novel.${task.projectId}`, JSON.stringify(task.novelId));
        localStorage.setItem(`nar.chapter.${task.projectId}`, JSON.stringify(task.chapterId));
        navigate(`/projects/${task.projectId}/narrative`);
      }
    } catch {
      /* ignore */
    }
    // 拆解结果需保留给目标页消费,这里不立即清除;拆分镜可直接关
    if (isBreakdown) onClose();
  };

  return (
    <div className="fixed bottom-5 left-5 z-[90] w-[320px] max-w-[calc(100vw-2.5rem)] animate-in">
      <div className="glass flex items-start gap-3 rounded-xl border border-border p-3 shadow-panel backdrop-blur-md">
        <span className="mt-0.5 shrink-0">
          {running ? <Loader2 className="h-5 w-5 animate-spin text-primary" />
            : done ? <CheckCircle2 className="h-5 w-5 text-success" />
            : <XCircle className="h-5 w-5 text-danger" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium">{task.label}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {running && `${runningText} ${sec}s`}
            {done && doneText}
            {task.status === "error" && (task.error || tr("bg.failed"))}
          </div>
          {done && (
            <button onClick={view} className="mt-1.5 text-xs font-medium text-primary hover:underline">
              {tr("bg.view")} →
            </button>
          )}
        </div>
        <button onClick={onClose} className="shrink-0 rounded-md p-0.5 text-faint hover:text-foreground" title={tr("common.cancel")}>
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

export function useBackgroundTasks() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useBackgroundTasks must be used within BackgroundTaskProvider");
  return ctx;
}
