import { StoryAssetMatch, assetMatch, type AssetMatch } from "@/components/StoryAssetMatch";
import { SceneTaskProgress, SceneTaskRows } from "@/components/SceneTaskProgress";
import { useCreativeToolTask } from "@/lib/useCreativeToolTask";
import { refreshAppliedScenes, sceneAdoptionMessage } from "@/lib/scene-adoption";
import { StudioSelect } from "@/components/ui/studio-select";
import { ScriptNameDialog } from "@/components/ScriptNameDialog";
import { ScriptSourceReview } from "@/components/ScriptSourceReview";
import { SearchField } from "@/components/ui/search-field";
// 剧本编辑器:分块(typed blocks)编辑 + AI 侧栏改稿 + 派生场次/资产 + 导出 Fountain。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowLeftRight,
  Clapperboard,
  Download,
  Ellipsis,
  FileText,
  Maximize2,
  Minimize2,
  List,
  Loader2,
  MessageCircle,
  Parentheses,
  Pencil,
  Plus,
  Save,
  Sparkles,
  Trash2,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import AssistPanel from "@/components/AssistPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { cn } from "@/lib/utils";
import {
  api,
  getToken,
  SCRIPT_BLOCK_TYPES,
  type DecomposeResult,
  type EntityDraft,
  type ScriptBlock,
  type ScriptDetail,
} from "@/lib/api";

import "./writing.css";

let nextKey = 1;
type EditBlock = ScriptBlock & { key: number };

const withKeys = (blocks: ScriptBlock[]): EditBlock[] =>
  blocks.map((b) => ({ ...b, key: nextKey++ }));
const stripKeys = (blocks: EditBlock[]): ScriptBlock[] =>
  blocks.map(({ key: _key, ...b }) => b).filter((b) => b.text.trim());

export default function ScriptEditor() {
  const { projectId, scriptId } = useParams();
  return <ScriptEditorWorkspace key={`${projectId}:${scriptId}`} />;
}
function ScriptEditorWorkspace() {
  const { projectId, scriptId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr, lang } = useI18n();

  const {
    data: script,
    error: loadError,
    refetch,
  } = useQuery({
    queryKey: ["script", scriptId],
    queryFn: () => api.get<ScriptDetail>(`${base}/scripts/${scriptId}`),
  });
  // 剧本数据(落库口径):派生场次/分镜 + 项目资产分类数
  const {
    data: dbStats,
    isError: statsError,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ["script-stats", scriptId],
    queryFn: () =>
      api.get<{
        scenes: number;
        shots: number;
        assets: Record<string, number>;
      }>(`${base}/scripts/${scriptId}/stats`),
  });
  const [assistOpen, setAssistOpen] = usePersistentState<boolean>(
    "se.assistOpen",
    false,
  );
  const [outlineOpen, setOutlineOpen] = usePersistentState<boolean>(
    "se.outlineOpen",
    true,
  );
  const [readingFont, setReadingFont] = usePersistentState<"serif" | "sans">(
    "se.readingFont",
    "serif",
  );

  const [renameOpen, setRenameOpen] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [outlineQuery, setOutlineQuery] = useState("");
  const { me } = useAuth();
  const draftKey = `script-draft:${me?.user.id}:${projectId}:${scriptId}`;
  const [blocks, setBlocks] = useState<EditBlock[]>([]);
  const blocksRef = useRef(blocks);
  blocksRef.current = blocks;
  const [baseline, setBaseline] = useState<ScriptDetail | null>(null);
  const [loadedFor, setLoadedFor] = useState("");
  const [draftError, setDraftError] = useState("");
  const [recovery, setRecovery] = useState<{
    blocks: ScriptBlock[];
    revision: string;
  } | null>(null);
  const dirty = useMemo(
    () =>
      !!baseline &&
      JSON.stringify(stripKeys(blocks)) !==
        JSON.stringify(baseline.content_blocks.filter((b) => b.text.trim())),
    [blocks, baseline],
  );
  useEffect(() => {
    if (script && me && loadedFor !== draftKey) {
      setBlocks(withKeys(script.content_blocks));
      setBaseline(script);
      setLoadedFor(draftKey);
      setRecovery(null);
      try {
        const saved = JSON.parse(localStorage.getItem(draftKey) || "null");
        if (
          saved &&
          Array.isArray(saved.blocks) &&
          typeof saved.revision === "string" &&
          saved.blocks.every(
            (b: ScriptBlock) =>
              typeof b.text === "string" && typeof b.block_type === "string",
          )
        )
          setRecovery(saved);
      } catch {
        /* invalid local draft does not prevent opening a script */
      }
    }
  }, [script, me, loadedFor, draftKey]);
  useEffect(() => {
    if (loadedFor !== draftKey || !baseline) return;
    const timer = setTimeout(() => {
      try {
        if (dirty)
          localStorage.setItem(
            draftKey,
            JSON.stringify({
              blocks: stripKeys(blocks),
              revision: baseline.content_revision,
            }),
          );
        else if (!recovery) localStorage.removeItem(draftKey);
        setDraftError("");
      } catch {
        setDraftError("本地草稿保存失败，请及时保存到项目或复制正文。");
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [blocks, baseline, dirty, loadedFor, draftKey, recovery]);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  const serverChanged =
    !!baseline &&
    !!script &&
    script.content_revision !== baseline.content_revision;

  // 剧本数据:场景 / 角色 / 对白 / 字数(实时,纯前端统计)
  const stats = useMemo(() => {
    const scenes = blocks.filter((b) => b.block_type === "scene_heading");
    const chars = new Set(
      blocks
        .filter((b) => b.block_type === "character")
        .map((b) => b.text.trim())
        .filter(Boolean),
    );
    const dialogues = blocks.filter((b) => b.block_type === "dialogue").length;
    const words = blocks.reduce(
      (n, b) => n + b.text.replace(/\s/g, "").length,
      0,
    );
    return { scenes, characters: chars.size, dialogues, words };
  }, [blocks]);

  const save = useMutation({
    mutationFn: (payload: {
      blocks: ScriptBlock[];
      expected_revision: string;
    }) => api.put<ScriptDetail>(`${base}/scripts/${scriptId}/blocks`, payload),
    onSuccess: (saved, payload) => {
      if (
        JSON.stringify(stripKeys(blocksRef.current)) ===
        JSON.stringify(payload.blocks)
      )
        setBlocks(withKeys(saved.content_blocks));
      setBaseline(saved);
      setRecovery(null);
      qc.setQueryData(["script", scriptId], saved);
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
      void qc.invalidateQueries({ queryKey: ["script-source-review", projectId, scriptId] });
      void refreshAppliedScenes(qc, projectId!, scriptId!);
      toast.push(tr("se.savedToast"), "success");
    },
    onError: (e) => {
      toast.push((e as Error).message, "error");
      void qc.invalidateQueries({ queryKey: ["script", scriptId] });
    },
  });
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (baseline && dirty && !save.isPending && !serverChanged)
          save.mutate({
            blocks: stripKeys(blocks),
            expected_revision: baseline.content_revision,
          });
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [baseline, blocks, dirty, save, serverChanged]);

  const exportFountain = async () => {
    const res = await fetch(`/api/v1${base}/scripts/${scriptId}/fountain`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
    });
    if (!res.ok) {
      toast.push("剧本导出失败，请重试", "error");
      return;
    }
    const text = await res.text();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(
      new Blob([text], { type: "text/plain;charset=utf-8" }),
    );
    a.download = `${script?.title ?? "script"}.fountain`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // ── 派生:场次/镜头 ──
  const [params] = useSearchParams();
  const [scenePreviewOpen, setScenePreviewOpen] = useState(params.get("tool") === "scenes");
  const seenSceneTask = useRef<string | null>(null);
  const decomposition = useCreativeToolTask<DecomposeResult>(
    projectId!,
    "script_decompose",
    scriptId!,
  );
  const sceneTask = decomposition.task;
  const dec = sceneTask?.result?.output;
  const progress = sceneTask?.result?.progress;
  const sceneActive = decomposition.busy || decomposition.retryScenes.isPending;
  const startSceneTask = () => {
    setScenePreviewOpen(true);
    decomposition.submit.mutate({}, {
      onError: (e) => { setScenePreviewOpen(false); toast.push(e.message, "error"); },
    });
  };
  const showSceneTask = () => {
    if (sceneTask) setScenePreviewOpen(true);
    else startSceneTask();
  };
  useEffect(() => {
    if (!sceneTask || seenSceneTask.current === sceneTask.id) return;
    seenSceneTask.current = sceneTask.id;
    if (sceneTask.status === "succeeded" || params.get("tool") === "scenes") setScenePreviewOpen(true);
  }, [sceneTask, params]);
  const applyScenes = useMutation({
    mutationFn: () => decomposition.apply.mutateAsync({
      fill_empty_scenes: decomposition.task?.status === "applied",
    }),
    onSuccess: (result) => {
      const failed = dec?.scenes.filter(scene => scene.error).length ?? 0;
      if (!failed) setScenePreviewOpen(false);
      toast.push(sceneAdoptionMessage(result.scenes ?? [], failed, lang), "success");
      void refreshAppliedScenes(qc, projectId!, scriptId!);
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  // ── 派生:实体→资产 ──
  const [drafts, setDrafts] = useState<
    (EntityDraft & { checked: boolean })[] | null
  >(null);
  const extraction = useCreativeToolTask<EntityDraft[]>(
    projectId!,
    "script_entities",
    scriptId!,
  );
  const extract = {
    isPending: extraction.busy,
    mutate: () =>
      extraction.submit.mutate(
        {},
        {
          onSuccess: (task) => {
            if (task.status === "succeeded" && task.result)
              setDrafts(
                task.result.output.map((item) => ({ ...item, checked: true })),
              );
            if (task.status === "applied")
              toast.push("已采用这次提取的资产", "success");
          },
          onError: (e) => toast.push(e.message, "error"),
        },
      ),
  };
  useEffect(() => {
    if (extraction.task?.status === "succeeded") {
      setDrafts(
        extraction.task.result!.output.map((item) => ({
          ...item,
          checked: true,
        })),
      );
    }
    if (extraction.task?.status === "applied") setDrafts(null);
  }, [extraction.task?.id, extraction.task?.status]);
  const [matching, setMatching] = useState<AssetMatch | null>(null);
  const [matches, setMatches] = useState<Record<number, string | null>>({});
  useEffect(() => {
    setMatches({});
    setMatching(null);
  }, [extraction.task?.id]);
  const applyEntities = useMutation({
    mutationFn: (choices: Record<number, string | null>) =>
      extraction.apply.mutateAsync({
        selected: drafts?.flatMap((item, i) => (item.checked ? [i] : [])) || [],
        matches: choices,
      }),
    onSuccess: () => {
      setMatching(null);
      setDrafts(null);
      toast.push(tr("se.entitiesApplied"), "success");
      void qc.invalidateQueries({ queryKey: ["assets", projectId] });
      void qc.invalidateQueries({ queryKey: ["script-stats", scriptId] });
    },
    onError: (e) => {
      const detail = assetMatch(e);
      if (detail && detail.index !== undefined) setMatching(detail);
      else toast.push(e.message, "error");
    },
  });

  const update = (key: number, patch: Partial<ScriptBlock>) =>
    setBlocks((bs) => bs.map((b) => (b.key === key ? { ...b, ...patch } : b)));
  const removeBlock = (key: number) =>
    setBlocks((bs) => bs.filter((b) => b.key !== key));
  const insertAfter = (key: number | null, block_type = "action") =>
    setBlocks((bs) => {
      const node: EditBlock = { key: nextKey++, id: crypto.randomUUID(), block_type, text: "" };
      if (key === null) return [...bs, node];
      const i = bs.findIndex((b) => b.key === key);
      return [...bs.slice(0, i + 1), node, ...bs.slice(i + 1)];
    });
  // 工具栏插块:优先插在光标所在块之后,否则追加到末尾
  const [focusedKey, setFocusedKey] = useState<number | null>(null);
  const insertFromToolbar = (block_type: string) =>
    insertAfter(
      focusedKey ?? blocks[blocks.length - 1]?.key ?? null,
      block_type,
    );
  const jumpTo = (key: number) => {
    const row = document.getElementById(`blk-${key}`);
    row?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      block: "center",
    });
    row?.querySelector("textarea")?.focus({ preventScroll: true });
  };
  const activeScene = blocks
    .slice(0, blocks.findIndex((b) => b.key === focusedKey) + 1)
    .filter((b) => b.block_type === "scene_heading")
    .slice(-1)[0]?.key;
  useEffect(() => {
    const dismissBlockMenus = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Element) || target.closest("[data-select-popup]"))
        return;
      document
        .querySelectorAll<HTMLDetailsElement>(".writing-block-menu[open]")
        .forEach((menu) => {
          if (!menu.contains(target)) menu.open = false;
        });
    };
    document.addEventListener("pointerdown", dismissBlockMenus);
    document.addEventListener("focusin", dismissBlockMenus);
    return () => {
      document.removeEventListener("pointerdown", dismissBlockMenus);
      document.removeEventListener("focusin", dismissBlockMenus);
    };
  }, []);
  useEffect(() => {
    if (!focusMode) return;
    const close = (e: KeyboardEvent) => {
      if (
        e.key === "Tab" &&
        !document.querySelector('[role="dialog"], [role="alertdialog"]')
      ) {
        const targets = Array.from(
          document.querySelectorAll<HTMLElement>(
            ".is-writing-focus button:not(:disabled), .is-writing-focus input:not(:disabled), .is-writing-focus textarea:not(:disabled), .is-writing-focus select:not(:disabled), .is-writing-focus a[href], .is-writing-focus summary",
          ),
        ).filter((el) => el.getClientRects().length > 0);
        const first = targets[0],
          last = targets[targets.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            !targets.includes(document.activeElement as HTMLElement))
        ) {
          e.preventDefault();
          last?.focus();
        } else if (
          !e.shiftKey &&
          (document.activeElement === last ||
            !targets.includes(document.activeElement as HTMLElement))
        ) {
          e.preventDefault();
          first?.focus();
        }
      }
      if (
        e.key === "Escape" &&
        !document.querySelector('[role="dialog"], [role="alertdialog"]')
      )
        setFocusMode(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [focusMode]);

  if (loadError)
    return (
      <div role="alert" className="studio-empty m-6">
        <p>{loadError.message}</p>
        <Button variant="outline" onClick={() => void refetch()}>
          重新读取剧本
        </Button>
      </div>
    );
  if (!script)
    return <p className="p-6 text-muted-foreground">{tr("common.loading")}</p>;

  return (
    <div
      className={cn("writing-workspace", focusMode && "is-writing-focus")}
      data-reading-font={readingFont}
    >
      {renameOpen && (
        <ScriptNameDialog
          projectId={projectId!}
          script={script}
          onClose={() => setRenameOpen(false)}
        />
      )}
      {/* 头部 */}
      <div className="writing-editor-header">
        <div className="writing-document-identity">
          <button
            aria-label="返回剧本库"
            onClick={() => navigate(`${base}/scripts`)}
            className="writing-icon-button"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <h1 title={script.title}>{script.title}</h1>
              <button
                type="button"
                aria-label="重命名剧本"
                title="重命名剧本"
                onClick={() => setRenameOpen(true)}
                className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-elevated hover:text-foreground"
              >
                <Pencil size={14} />
              </button>
            </div>
            <p className="writing-save-state" aria-live="polite">
              {save.isPending
                ? "保存中…"
                : dirty
                  ? "草稿待保存"
                  : "已保存到项目"}
            </p>
          </div>
        </div>
        <div className="writing-header-actions">
          <ScriptSourceReview projectId={projectId!} scriptId={scriptId!} />
          <Button
            size="sm"
            variant="ghost"
            className="writing-view-button"
            title={focusMode ? "退出专注" : "专注写作"}
            aria-label={focusMode ? "退出专注" : "专注写作"}
            aria-pressed={focusMode}
            onClick={() => setFocusMode(!focusMode)}
          >
            {focusMode ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            <span>{focusMode ? "退出专注" : "专注写作"}</span>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="writing-view-button"
            title="场景大纲"
            aria-label="场景大纲"
            aria-pressed={outlineOpen}
            onClick={() => setOutlineOpen(!outlineOpen)}
          >
            <List className="h-4 w-4" />
            <span>场景大纲</span>
          </Button>
          <details
            className="writing-production-tools"
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.currentTarget.open = false;
                e.currentTarget.querySelector("summary")?.focus();
              }
            }}
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null))
                e.currentTarget.open = false;
            }}
          >
            <summary>
              <Clapperboard size={15} />
              制作工具
            </summary>
            <div>
              {((sceneTask && !sceneActive) ||
                extraction.task?.status === "succeeded") && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={dirty || serverChanged || save.isPending || sceneActive || extraction.busy}
                  onClick={() => {
                    if (sceneTask) startSceneTask();
                    if (extraction.task?.status === "succeeded")
                      extraction.submit.mutate({});
                  }}
                >
                  按当前剧本重新分析
                </Button>
              )}
              {(decomposition.task?.error || extraction.task?.error) && (
                <p role="alert" className="text-sm text-danger">
                  {decomposition.task?.error || extraction.task?.error}
                </p>
              )}
              {(decomposition.query.isError || extraction.query.isError) && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    void decomposition.query.refetch();
                    void extraction.query.refetch();
                  }}
                >
                  重新读取任务状态
                </Button>
              )}
              <p className="text-xs text-muted-foreground">
                写好并保存正文后，再拆分场景、整理角色或导出。
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={showSceneTask}
                disabled={
                  !decomposition.ready ||
                  decomposition.submit.isPending ||
                  (!sceneTask && (dirty || save.isPending || serverChanged))
                }
              >
                {sceneActive ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Clapperboard className="mr-1 h-4 w-4" />
                )}
                {sceneActive ? (lang === "zh" ? "查看拆镜进度" : "View breakdown progress")
                  : sceneTask?.status === "failed" ? (lang === "zh" ? "查看拆镜结果" : "View breakdown result")
                  : dec ? tr("se.viewSceneSuggestions") : tr("se.decompose")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => extract.mutate()}
                disabled={
                  !extraction.ready ||
                  extract.isPending ||
                  dirty ||
                  save.isPending ||
                  serverChanged
                }
              >
                {extract.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Users className="mr-1 h-4 w-4" />
                )}
                {extraction.task?.status === "succeeded"
                  ? "查看角色与资产建议"
                  : tr("se.extract")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={exportFountain}
                disabled={dirty || save.isPending || serverChanged}
              >
                <Download className="mr-1 h-4 w-4" /> Fountain
              </Button>
            </div>
          </details>
          <Button
            size="sm"
            onClick={() =>
              baseline &&
              save.mutate({
                blocks: stripKeys(blocks),
                expected_revision: baseline.content_revision,
              })
            }
            disabled={!dirty || save.isPending || serverChanged}
          >
            {save.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Save className="mr-1 h-4 w-4" />
            )}
            {tr("common.save")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="writing-view-button"
            title="创作助手"
            aria-label="创作助手"
            aria-pressed={assistOpen}
            onClick={() => setAssistOpen(!assistOpen)}
          >
            <MessageCircle className="h-4 w-4" />
            <span>创作助手</span>
          </Button>
        </div>
      </div>

      {recovery && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-3 rounded-lg border p-3 text-sm"
        >
          <span>发现未保存的剧本草稿</span>
          <Button
            size="sm"
            onClick={() => {
              setBlocks(withKeys(recovery.blocks));
              if (baseline)
                setBaseline({
                  ...baseline,
                  content_revision: recovery.revision,
                });
              setRecovery(null);
            }}
          >
            恢复剧本草稿
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setRecovery(null);
              try {
                localStorage.removeItem(draftKey);
              } catch {}
            }}
          >
            忽略草稿
          </Button>
        </div>
      )}
      {serverChanged && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-warning/40 p-3 text-sm"
        >
          <span>
            剧本已有新版本，当前草稿保留。请先备份或复制需要保留的文字，再载入新版本。
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              if (
                !dirty ||
                (await confirm({
                  message:
                    "载入服务器新版本将替换当前编辑内容。请先保留需要的草稿文字。",
                }))
              ) {
                setBaseline(script!);
                setBlocks(withKeys(script!.content_blocks));
                setRecovery(null);
              }
            }}
          >
            载入新版本
          </Button>
        </div>
      )}
      {draftError && (
        <p role="alert" className="text-sm text-danger">
          {draftError}
        </p>
      )}
      <div
        className={cn(
          "writing-editor-layout",
          assistOpen && !focusMode && "has-assistant",
        )}
      >
        {/* 左:分块编辑器 */}
        <div className={cn("writing-editor-panel")}>
          <div className="writing-editor-body">
            {/* 大纲:场景头列表,点击跳转 */}
            {outlineOpen && !focusMode && (
              <nav aria-label="剧本场景大纲" className="writing-outline">
                <div className="writing-outline-heading">
                  <span>场景大纲</span>
                  <span>{stats.scenes.length}</span>
                </div>
                <SearchField
                  className="mb-2.5 w-full"
                  label="搜索场景"
                  placeholder="搜索场景…"
                  value={outlineQuery}
                  onValueChange={(value) => setOutlineQuery(value)}
                />
                {stats.scenes.length === 0 ? (
                  <p className="px-2 text-xs text-muted-foreground">
                    {tr("se.noScenesYet")}
                  </p>
                ) : (
                  stats.scenes
                    .map((s, i) => ({ s, i }))
                    .filter(({ s }) =>
                      s.text
                        .toLocaleLowerCase()
                        .includes(outlineQuery.trim().toLocaleLowerCase()),
                    )
                    .map(({ s, i }) => (
                      <button
                        key={s.key}
                        onClick={() => jumpTo(s.key)}
                        aria-current={
                          activeScene === s.key ? "location" : undefined
                        }
                        className="writing-outline-item"
                        title={s.text}
                      >
                        <span className="writing-scene-index">
                          {String(i + 1).padStart(2, "0")}
                        </span>
                        <span className="writing-scene-name">{s.text}</span>
                      </button>
                    ))
                )}
                {stats.scenes.length > 0 &&
                  !stats.scenes.some((s) =>
                    s.text
                      .toLocaleLowerCase()
                      .includes(outlineQuery.trim().toLocaleLowerCase()),
                  ) && (
                    <p className="p-2 text-xs text-muted-foreground">
                      没有匹配的场景
                    </p>
                  )}
              </nav>
            )}

            <div className="writing-reading-area">
              <div className="writing-reading-toolbar">
                <div
                  className="writing-block-toolbar"
                  role="group"
                  aria-label="插入剧本段落"
                >
                  {(
                    [
                      {
                        bt: "scene_heading",
                        icon: Clapperboard,
                        label: "场景",
                      },
                      { bt: "action", icon: Activity, label: "动作" },
                      { bt: "character", icon: UserRound, label: "角色" },
                      { bt: "dialogue", icon: MessageCircle, label: "对白" },
                      { bt: "parenthetical", icon: Parentheses, label: "括号" },
                      { bt: "transition", icon: ArrowLeftRight, label: "转场" },
                    ] as const
                  ).map((item) => (
                    <button
                      key={item.bt}
                      onClick={() => insertFromToolbar(item.bt)}
                      title={`${tr("se.insertBlock")}: ${tr(`se.bt.${item.bt}`)}`}
                      aria-label={`${tr("se.insertBlock")}: ${tr(`se.bt.${item.bt}`)}`}
                    >
                      <item.icon size={16} />
                      <span>{item.label}</span>
                    </button>
                  ))}
                </div>
                <StudioSelect
                  aria-label="正文字体"
                  value={readingFont}
                  onValueChange={(value) =>
                    setReadingFont(value as "serif" | "sans")
                  }
                  className="writing-font-select"
                >
                  <option value="serif">宋体</option>
                  <option value="sans">黑体</option>
                </StudioSelect>
              </div>
              <div className="writing-paper-scroll">
                {blocks.length === 0 ? (
                  <div className="flex flex-col items-center gap-3 py-16 text-center text-muted-foreground">
                    <FileText className="h-8 w-8 opacity-40" />
                    <p className="text-sm">{tr("se.empty")}</p>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => insertAfter(null, "scene_heading")}
                    >
                      <Plus className="mr-1 h-4 w-4" /> {tr("se.addBlock")}
                    </Button>
                  </div>
                ) : (
                  <div className="writing-paper">
                    {blocks.map((b) => (
                      <BlockRow
                        key={b.key}
                        block={b}
                        onChange={(patch) => update(b.key, patch)}
                        onRemove={() => removeBlock(b.key)}
                        onInsertAfter={() => insertAfter(b.key)}
                        onFocus={() => setFocusedKey(b.key)}
                      />
                    ))}
                    <button
                      onClick={() =>
                        insertAfter(blocks[blocks.length - 1]?.key ?? null)
                      }
                      className="writing-add-block"
                    >
                      <Plus className="h-3.5 w-3.5" /> {tr("se.addBlock")}
                    </button>
                  </div>
                )}
              </div>
              <footer className="writing-document-status">
                <span>{stats.scenes.length} 场景</span>
                <span>{stats.characters} 角色</span>
                <span>{stats.dialogues} 对白</span>
                <span className="writing-word-count">
                  {stats.words.toLocaleString()} 字
                </span>
                <span
                  className="writing-save-hint"
                  title="Ctrl / ⌘ + S 保存正文"
                >
                  {dirty ? "草稿保留在当前浏览器" : "Ctrl / ⌘ + S 保存"}
                </span>
              </footer>
            </div>
          </div>
        </div>

        {/* 右:剧本数据 + AI 助手(可收起) */}
        {assistOpen && (
          <aside className="writing-assistant" hidden={focusMode}>
            {/* 剧本数据(落库口径,对齐 Laper 右栏) */}
            <div className="writing-production-stats">
              <div className="writing-stats-heading">{tr("se.dbStats")}</div>
              {statsError ? (
                <div role="alert" className="text-xs text-muted-foreground">
                  制作数据读取失败。
                  <button
                    className="underline"
                    onClick={() => void refetchStats()}
                  >
                    重试
                  </button>
                </div>
              ) : (
                <div className="space-y-1.5 text-sm">
                  <StatRow
                    label={tr("se.dbScenes")}
                    value={dbStats?.scenes ?? "—"}
                  />
                  <StatRow
                    label={tr("se.dbShots")}
                    value={dbStats?.shots ?? "—"}
                  />
                  {["character", "location", "prop"].map((t) => (
                    <StatRow
                      key={t}
                      label={tr(`asset.${t}`)}
                      value={dbStats ? (dbStats.assets[t] ?? 0) : "—"}
                    />
                  ))}
                </div>
              )}
            </div>
            <AssistPanel
              projectId={projectId!}
              targetType="script"
              targetId={scriptId!}
              className="min-h-0 flex-1"
              onCollapse={() => setAssistOpen(false)}
              onApplied={() => {
                void qc.invalidateQueries({ queryKey: ["script", scriptId] });
                void refreshAppliedScenes(qc, projectId!, scriptId!);
              }}
            />
          </aside>
        )}
      </div>

      {/* 派生场次浮层 */}
      {scenePreviewOpen && (
        <Modal
          open
          width={580}
          bodyClassName="flex min-h-0 flex-col"
          title={tr("se.deriveScenes")}
          onClose={() => setScenePreviewOpen(false)}
        >
          {(sceneTask || sceneActive) && (
            <div className="shrink-0 space-y-2 border-b border-border px-4 py-3">
              <SceneTaskProgress
                status={sceneTask?.status ?? "queued"}
                progress={progress}
              />
              {sceneTask && (
                <Link
                  className="studio-link text-xs"
                  to={`${base}/tasks?category=story&kind=creative&task=${sceneTask.id}`}
                >
                  {lang === "zh" ? "在任务中心查看" : "View in task center"} →
                </Link>
              )}
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-auto px-4">
            {!sceneTask && !sceneActive && decomposition.query.isPending && (
              <p className="py-4 text-sm text-muted-foreground" role="status">
                {lang === "zh" ? "正在读取任务进度…" : "Loading task progress…"}
              </p>
            )}
            {decomposition.query.isError && (
              <p role="alert" className="py-2 text-sm text-danger">
                {lang === "zh"
                  ? "暂时无法刷新进度"
                  : "Progress could not be refreshed"}
                <button
                  className="studio-link ml-2"
                  onClick={() => void decomposition.query.refetch()}
                >
                  {lang === "zh" ? "重新读取" : "Refresh"}
                </button>
              </p>
            )}
            {sceneTask?.error && (
              <p role="alert" className="py-2 text-sm text-danger">
                {sceneTask.error}
              </p>
            )}
            {dec && (
              <SceneTaskRows
                stopped={sceneTask?.status === "failed"}
                rows={dec.scenes.map((s) => ({
                  title: s.title,
                  state: s.breakdown_status ?? (s.error ? "failed" : "succeeded"),
                  shots: s.shots.length,
                  shotTitles: s.shots.map((shot) => shot.title),
                  error: s.error,
                }))}
              />
            )}
            {!sceneActive &&
              !decomposition.query.isPending &&
              !decomposition.query.isError &&
              !dec?.scenes.length && (
                <p className="py-4 text-sm text-muted-foreground">
                  {lang === "zh" ? "暂无场次建议" : "No scene suggestions yet"}
                </p>
              )}
          </div>
          <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-border p-3">
            {!sceneActive &&
              !!dec?.scenes.length &&
              (dec.scenes.some((s) => s.error) || sceneTask?.status === "failed") && (
                <Button
                  variant="outline"
                  disabled={
                    decomposition.busy ||
                    decomposition.retryScenes.isPending ||
                    applyScenes.isPending
                  }
                  onClick={() =>
                    decomposition.retryScenes.mutate(undefined, {
                      onError: (e) => toast.push(e.message, "error"),
                    })
                  }
                >
                  {dec.scenes.some((s) => s.error)
                    ? lang === "zh"
                      ? "只重试未完成场次"
                      : "Retry unfinished scenes"
                    : lang === "zh"
                      ? "恢复已完成结果"
                      : "Recover completed scenes"}
                </Button>
              )}
            {sceneActive && (
              <Button variant="outline" onClick={() => setScenePreviewOpen(false)}>
                {lang === "zh" ? "在后台继续" : "Continue in background"}
              </Button>
            )}
            {!sceneActive &&
              sceneTask?.status === "failed" &&
              !dec?.scenes.length && (
                <Button
                  disabled={dirty || save.isPending || serverChanged}
                  onClick={startSceneTask}
                >
                  {lang === "zh" ? "重新拆镜" : "Retry breakdown"}
                </Button>
              )}
            {!sceneTask && !sceneActive && decomposition.ready && (
              <Button
                disabled={dirty || save.isPending || serverChanged}
                onClick={startSceneTask}
              >
                {tr("se.decompose")}
              </Button>
            )}
            {!sceneActive &&
              !!dec?.scenes.length &&
              sceneTask?.status !== "failed" && (
                <Button
                  disabled={
                    applyScenes.isPending ||
                    decomposition.busy ||
                    decomposition.retryScenes.isPending ||
                    !dec.scenes.some((s) => !s.error)
                  }
                  onClick={async () => {
                    if (
                      await confirm({
                        title: tr("se.applyScenes"),
                        message: tr("se.applyScenesConfirm"),
                        confirmText: tr("se.applyScenes"),
                      })
                    ) {
                      applyScenes.mutate();
                    }
                  }}
                >
                  {applyScenes.isPending && (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  )}
                  {tr("se.applyScenes")}
                </Button>
              )}
          </div>
        </Modal>
      )}

      {matching && (
        <StoryAssetMatch
          key={matching.index}
          base={base}
          match={matching}
          busy={applyEntities.isPending}
          close={() => setMatching(null)}
          choose={(id) => {
            const next = { ...matches, [matching.index!]: id };
            setMatches(next);
            setMatching(null);
            if (!matching.manual) applyEntities.mutate(next);
          }}
        />
      )}
      {/* 实体草稿浮层 */}
      {drafts && !matching && (
        <Overlay
          title={`${tr("se.entities")} · ${drafts.length}`}
          onClose={() => setDrafts(null)}
        >
          <div className="min-h-0 flex-1 space-y-1.5 overflow-auto p-4">
            {drafts.map((d, i) => (
              <div
                key={i}
                className="flex cursor-pointer items-center gap-2.5 rounded-md border border-border px-2.5 py-2 transition hover:border-primary/40"
              >
                <input
                  type="checkbox"
                  aria-label={d.name}
                  checked={d.checked}
                  onChange={(e) =>
                    setDrafts((ds) =>
                      ds!.map((x, j) =>
                        j === i ? { ...x, checked: e.target.checked } : x,
                      ),
                    )
                  }
                  className="accent-[var(--primary)]"
                />
                <Badge variant="primary">{tr(`asset.${d.type}`)}</Badge>
                <span className="min-w-0 flex-1 truncate text-sm">
                  {d.name}
                </span>
                <span className="max-w-[48%] text-xs text-muted-foreground">
                  <span className="block line-clamp-2">{d.summary}</span>
                  {d.evidence && <q className="mt-1 block">{d.evidence}</q>}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={applyEntities.isPending}
                  onClick={() =>
                    setMatching({
                      name: d.name,
                      kind: d.type,
                      index: i,
                      manual: true,
                      candidates: [],
                    })
                  }
                >
                  {i in matches
                    ? matches[i]
                      ? "已指定资产"
                      : "单独新建"
                    : "关联已有资产"}
                </Button>
              </div>
            ))}
          </div>
          <div className="shrink-0 border-t border-border p-3">
            <Button
              className="w-full"
              disabled={
                applyEntities.isPending || !drafts.some((d) => d.checked)
              }
              onClick={() => applyEntities.mutate(matches)}
            >
              {applyEntities.isPending && (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              )}
              {tr("se.applyEntities")}
            </Button>
          </div>
        </Overlay>
      )}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-code">{value}</span>
    </div>
  );
}

function BlockRow({
  block,
  onChange,
  onRemove,
  onInsertAfter,
  onFocus,
}: {
  block: ScriptBlock & { key: number };
  onChange: (patch: Partial<ScriptBlock>) => void;
  onRemove: () => void;
  onInsertAfter: () => void;
  onFocus?: () => void;
}) {
  const { t: tr } = useI18n();
  return (
    <div
      id={`blk-${block.key}`}
      onFocusCapture={onFocus}
      className="writing-block"
      data-block-type={block.block_type}
    >
      <details
        className="writing-block-menu"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            event.currentTarget.open = false;
            event.currentTarget.querySelector("summary")?.focus();
          }
        }}
      >
        <summary aria-label="段落操作" title="段落操作">
          <Ellipsis size={17} />
        </summary>
        <div className="writing-block-actions">
          <StudioSelect
            value={block.block_type}
            onValueChange={(selectedValue) =>
              onChange({ block_type: selectedValue })
            }
            className="writing-block-type"
            title={tr("se.blockType")}
            aria-label={tr("se.blockType")}
          >
            {SCRIPT_BLOCK_TYPES.map((bt) => (
              <option key={bt} value={bt}>
                {tr(`se.bt.${bt}`)}
              </option>
            ))}
          </StudioSelect>
          <button
            onClick={(event) => {
              const menu = event.currentTarget.closest("details");
              if (menu) menu.open = false;
              onInsertAfter();
            }}
            title={tr("se.addBlock")}
            className="writing-block-action"
          >
            <Plus size={15} />
            在下方添加段落
          </button>
          <button
            onClick={onRemove}
            title={tr("common.delete")}
            className="writing-block-action is-destructive"
          >
            <Trash2 size={15} />
            删除段落
          </button>
        </div>
      </details>
      <AutoTextarea
        value={block.text}
        onChange={(v) => onChange({ text: v })}
        className="writing-block-input"
        placeholder={tr(`se.bt.${block.block_type}`)}
      />
    </div>
  );
}

function AutoTextarea({
  value,
  onChange,
  className,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  className?: string;
  placeholder?: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    let lastWidth = textarea.clientWidth;
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth === lastWidth) return;
      lastWidth = textarea.clientWidth;
      textarea.style.height = "auto";
      textarea.style.height = `${textarea.scrollHeight}px`;
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, []);
  return (
    <textarea
      value={value}
      placeholder={placeholder}
      aria-label={placeholder}
      onChange={(e) => onChange(e.target.value)}
      rows={1}
      ref={(el) => {
        textareaRef.current = el;
        if (el) {
          el.style.height = "auto";
          el.style.height = `${el.scrollHeight}px`;
        }
      }}
      onInput={(e) => {
        const el = e.currentTarget;
        el.style.height = "auto";
        el.style.height = `${el.scrollHeight}px`;
      }}
      className={className}
    />
  );
}

function Overlay({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const { t: tr } = useI18n();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    dialog?.querySelector<HTMLElement>("button")?.focus();
    const handle = (e: KeyboardEvent) => {
      if (document.querySelector('[role="alertdialog"]')) return;
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
      }
      if (e.key !== "Tab") return;
      const items = Array.from(
        dialog?.querySelectorAll<HTMLElement>(
            'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        ) ?? [],
      ).filter((el) => el.getClientRects().length);
      if (e.shiftKey && document.activeElement === items[0]) {
        e.preventDefault();
        items[items.length - 1]?.focus();
      } else if (
        !e.shiftKey &&
        document.activeElement === items[items.length - 1]
      ) {
        e.preventDefault();
        items[0]?.focus();
      }
    };
    document.addEventListener("keydown", handle);
    return () => {
      document.removeEventListener("keydown", handle);
      previous?.focus();
    };
  }, []);
  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-[560px] flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex h-11 shrink-0 items-center justify-between border-b border-border px-4">
          <span className="flex items-center gap-1.5 text-sm font-medium">
            <Sparkles className="h-4 w-4 text-primary" /> {title}
          </span>
          <button
            onClick={onClose}
            title={tr("common.cancel")}
            className="grid h-7 w-7 place-items-center rounded-md text-faint hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
