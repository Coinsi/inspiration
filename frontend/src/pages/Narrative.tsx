import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, FileText, Loader2, Pencil, Plus, RotateCcw, ScrollText, Sparkles, Trash2, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { usePersistentState } from "@/lib/usePersistentState";
import { useElapsedSeconds } from "@/lib/useElapsedSeconds";
import { useBackgroundTasks } from "@/components/BackgroundTasks";
import {
  api,
  apiUpload,
  type DecomposeResult,
  type Novel,
  type NovelDetail,
  type Script,
} from "@/lib/api";

export default function Narrative() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const { t: tr, lang } = useI18n();
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const fmtTime = (iso: string) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const [novelId, setNovelId] = usePersistentState<string | null>(`nar.novel.${projectId}`, null);
  const [chapterId, setChapterId] = usePersistentState<string | null>(`nar.chapter.${projectId}`, null);
  const [dec, setDec] = useState<DecomposeResult | null>(null);
  const [scriptId, setScriptId] = useState("");
  const [showTrash, setShowTrash] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [chapterQuery, setChapterQuery] = useState("");

  const { data: novels } = useQuery({ queryKey: ["novels", projectId], queryFn: () => api.get<Novel[]>(`${base}/novels`) });
  const { data: trashNovels } = useQuery({
    queryKey: ["novels-trash", projectId],
    queryFn: () => api.get<Novel[]>(`${base}/novels/trash`),
    enabled: showTrash,
  });
  const { data: detail } = useQuery({
    queryKey: ["novel", novelId],
    queryFn: () => api.get<NovelDetail>(`${base}/novels/${novelId}`),
    enabled: !!novelId,
  });
  const { data: scripts } = useQuery({ queryKey: ["scripts", projectId], queryFn: () => api.get<Script[]>(`${base}/scripts`) });

  // 选中章节从已加载的小说详情里按 id 取(只持久化 id,不存正文)
  const chapter = detail?.chapters.find((c) => c.id === chapterId) ?? null;

  const importNovel = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return apiUpload<Novel>(`${base}/novels/import`, fd);
    },
    onSuccess: (n) => {
      setNovelId(n.id);
      toast.push(tr("nar.novelImported"), "success");
      void qc.invalidateQueries({ queryKey: ["novels", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const delNovel = useMutation({
    mutationFn: (id: string) => api.del(`${base}/novels/${id}`),
    onSuccess: (_d, id) => {
      if (novelId === id) { setNovelId(null); setChapterId(null); setDec(null); }
      toast.push(tr("nar.novelDeleted"), "success");
      void qc.invalidateQueries({ queryKey: ["novels", projectId] });
      void qc.invalidateQueries({ queryKey: ["novels-trash", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const renameNovel = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.patch(`${base}/novels/${id}`, { title }),
    onSuccess: () => {
      setRenamingId(null);
      toast.push(tr("nar.novelRenamed"), "success");
      void qc.invalidateQueries({ queryKey: ["novels", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const startRename = (id: string, current: string) => { setRenamingId(id); setRenameValue(current); };
  const commitRename = () => {
    const v = renameValue.trim();
    if (renamingId && v) renameNovel.mutate({ id: renamingId, title: v });
    else setRenamingId(null);
  };
  const restoreNovel = useMutation({
    mutationFn: (id: string) => api.post(`${base}/novels/${id}/restore`),
    onSuccess: () => {
      toast.push(tr("nar.novelRestored"), "success");
      void qc.invalidateQueries({ queryKey: ["novels", projectId] });
      void qc.invalidateQueries({ queryKey: ["novels-trash", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const delScript = useMutation({
    mutationFn: (id: string) => api.del(`${base}/scripts/${id}`),
    onSuccess: (_d, id) => {
      if (scriptId === id) setScriptId("");
      toast.push(tr("nar.scriptDeleted"), "success");
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  // AI 拆解走全局后台任务:切菜单也有角标,完成回来能显示建议(按钮已隐藏,保留对历史任务的展示)
  const { task, dismiss } = useBackgroundTasks();
  const decRunning = task?.kind === "decompose" && task.status === "running" && task.chapterId === chapter?.id;
  const decSec = useElapsedSeconds(decRunning);
  useEffect(() => {
    if (task?.kind === "decompose" && task.status === "done" && task.chapterId === chapter?.id && task.result && !dec) {
      setDec(task.result);
    }
  }, [task, chapter?.id, dec]);
  const createScript = useMutation({
    mutationFn: (title: string) => api.post<Script>(`${base}/scripts`, { title }),
    onSuccess: (s) => {
      setScriptId(s.id);
      toast.push(tr("nar.scriptCreated"), "success");
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
    },
  });
  // 小说章节 → AI 改编为剧本正文,完成后直接进入剧本编辑器
  const genScript = useMutation({
    mutationFn: () =>
      api.post<Script>(`${base}/scripts/from-chapter`, { chapter_id: chapter?.id, title: chapter?.title ?? detail?.title }),
    onSuccess: (s) => {
      toast.push(tr("nar.scriptGenerated"), "success");
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
      navigate(`${base}/scripts/${s.id}`);
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const applyScenes = useMutation({
    mutationFn: () =>
      api.post(`${base}/scripts/${scriptId}/apply-scenes`, { chapter_id: chapter?.id, scenes: dec?.scenes }),
    onSuccess: () => {
      dismiss();
      setDec(null);
      toast.push(tr("nar.applied"), "success");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  return (
    <div className="flex h-full flex-col p-6">
      {/* 头部 */}
      <div className="mb-4 flex shrink-0 items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-xl font-semibold leading-tight">{tr("nar.title")}</h1>
            <p className="text-sm text-muted-foreground">{tr("nar.subtitle")}</p>
          </div>
        </div>
        <input ref={fileRef} type="file" accept=".txt,.md,.markdown" hidden
          onChange={(e) => e.target.files?.[0] && importNovel.mutate(e.target.files[0])} />
        <Button onClick={() => fileRef.current?.click()}>
          <Upload className="h-4 w-4 mr-1" /> {tr("nar.import")}
        </Button>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-12 grid-rows-1 gap-4">
        {/* 左:小说 + 章节 */}
        <div className="col-span-3 flex min-h-0 flex-col gap-4">
          <Panel
            className="max-h-[36%] flex-none"
            title={showTrash ? tr("nar.trash") : tr("nar.novel")}
            icon={showTrash ? Trash2 : BookOpen}
            action={
              <button
                onClick={() => setShowTrash((s) => !s)}
                title={showTrash ? tr("nar.backToNovels") : tr("nar.viewTrash")}
                className={`grid h-6 w-6 place-items-center rounded-md transition hover:bg-muted ${
                  showTrash ? "text-primary" : "text-faint hover:text-foreground"
                }`}
              >
                {showTrash ? <BookOpen className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
              </button>
            }
          >
            {showTrash ? (
              trashNovels && trashNovels.length > 0 ? (
                <div className="space-y-0.5">
                  {trashNovels.map((n) => (
                    <div key={n.id} className="group/novel flex items-center rounded-md hover:bg-muted">
                      <div className="min-w-0 flex-1 px-2.5 py-1.5">
                        <span className="block truncate font-medium text-muted-foreground line-through">{n.title || tr("nar.untitled")}</span>
                        <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-faint">
                          <span className="font-code">{n.code}</span>
                          {n.created_at && <span title={tr("nar.importedAt")}>· {fmtTime(n.created_at)}</span>}
                        </span>
                      </div>
                      <button
                        onClick={() => restoreNovel.mutate(n.id)}
                        title={tr("nar.restore")}
                        className="mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-faint opacity-0 transition hover:bg-success/15 hover:text-success group-hover/novel:opacity-100"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty text={tr("nar.trashEmpty")} />
              )
            ) : novels && novels.length > 0 ? (
              <div className="space-y-0.5">
                {novels.map((n) => (
                  <div
                    key={n.id}
                    className={`group/novel relative flex items-center rounded-md transition-colors ${
                      novelId === n.id ? "bg-primary/15 text-primary" : "hover:bg-muted text-foreground/90"
                    }`}
                  >
                    {renamingId === n.id ? (
                      <div className="flex flex-1 items-center gap-1 px-1.5 py-1">
                        <input
                          autoFocus
                          value={renameValue}
                          onChange={(e) => setRenameValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") commitRename();
                            else if (e.key === "Escape") setRenamingId(null);
                          }}
                          className="h-7 min-w-0 flex-1 rounded-md border border-primary/50 bg-bg px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                        />
                        <button onClick={commitRename} title={tr("common.save")} className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-success hover:bg-success/15">
                          <Check className="h-3.5 w-3.5" />
                        </button>
                        <button onClick={() => setRenamingId(null)} title={tr("common.cancel")} className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-faint hover:bg-elevated hover:text-foreground">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <>
                        <button
                          onClick={() => { setNovelId(n.id); setChapterId(null); setDec(null); }}
                          onDoubleClick={() => startRename(n.id, n.title || "")}
                          className="min-w-0 flex-1 px-2.5 py-1.5 text-left"
                        >
                          <span className="block truncate font-medium">{n.title || tr("nar.untitled")}</span>
                          <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-faint">
                            <span className="font-code">{n.code}</span>
                            {n.created_at && <span title={tr("nar.importedAt")}>· {fmtTime(n.created_at)}</span>}
                          </span>
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); startRename(n.id, n.title || ""); }}
                          title={tr("nar.renameNovel")}
                          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-faint opacity-0 transition hover:bg-primary/15 hover:text-primary group-hover/novel:opacity-100"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={async (e) => {
                            e.stopPropagation();
                            const ok = await confirm({
                              title: tr("nar.deleteNovel"),
                              message: tr("nar.deleteConfirm").replace("{name}", n.title || tr("nar.untitled")),
                              confirmText: tr("common.delete"),
                              danger: true,
                            });
                            if (ok) delNovel.mutate(n.id);
                          }}
                          title={tr("nar.deleteNovel")}
                          className="mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-faint opacity-0 transition hover:bg-danger/15 hover:text-danger group-hover/novel:opacity-100"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <Empty text={tr("nar.importStart")} />
            )}
          </Panel>

          <Panel
            className="flex-1"
            bodyClassName="flex flex-col overflow-hidden p-0"
            title={detail ? `${tr("nar.chapters")} · ${detail.chapters.length}` : tr("nar.chapters")}
            icon={ScrollText}
          >
            {detail ? (
              <>
                <div className="shrink-0 border-b border-border p-2">
                  <Input
                    value={chapterQuery}
                    onChange={(e) => setChapterQuery(e.target.value)}
                    placeholder={tr("nar.searchChapter")}
                    className="h-8"
                  />
                </div>
                <div className="min-h-0 flex-1 space-y-0.5 overflow-auto p-2">
                  {detail.chapters
                    .filter((ch) => {
                      const q = chapterQuery.trim().toLowerCase();
                      if (!q) return true;
                      return (ch.title ?? "").toLowerCase().includes(q) || String(ch.ordinal) === q;
                    })
                    .map((ch) => (
                      <Row key={ch.id} active={chapter?.id === ch.id} onClick={() => { setChapterId(ch.id); setDec(null); }}>
                        <span className="truncate">{ch.title ?? tr("nar.chapterN").replace("{n}", String(ch.ordinal))}</span>
                      </Row>
                    ))}
                </div>
              </>
            ) : (
              <Empty text={tr("nar.selectNovelForChapters")} />
            )}
          </Panel>

          <Panel
            className="max-h-[30%] flex-none"
            title={scripts?.length ? `${tr("nar.scriptsPanel")} · ${scripts.length}` : tr("nar.scriptsPanel")}
            icon={FileText}
          >
            {scripts && scripts.length > 0 ? (
              <div className="space-y-0.5">
                {scripts.map((s) => (
                  <div key={s.id} className="group/script flex items-center rounded-md transition-colors hover:bg-muted">
                    <button
                      onClick={() => navigate(`${base}/scripts/${s.id}`)}
                      title={tr("nar.openEditor")}
                      className="min-w-0 flex-1 px-2.5 py-1.5 text-left"
                    >
                      <span className="block truncate font-medium">{s.title || tr("nar.untitled")}</span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-faint">
                        <span className="font-code">{s.code}</span>
                        {s.created_at && <span>· {fmtTime(s.created_at)}</span>}
                      </span>
                    </button>
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        const ok = await confirm({
                          title: tr("nar.deleteScript"),
                          message: tr("nar.deleteScriptConfirm").replace("{name}", s.title || tr("nar.untitled")),
                          confirmText: tr("common.delete"),
                          danger: true,
                        });
                        if (ok) delScript.mutate(s.id);
                      }}
                      title={tr("nar.deleteScript")}
                      className="mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-faint opacity-0 transition hover:bg-danger/15 hover:text-danger group-hover/script:opacity-100"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <Empty text={tr("nar.noScripts")} />
            )}
          </Panel>
        </div>

        {/* 中:阅读区 */}
        <div className="col-span-5 flex min-h-0 flex-col">
          <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
              <span className="truncate font-medium">
                {chapter ? (chapter.title ?? tr("nar.chapterN").replace("{n}", String(chapter.ordinal))) : tr("nar.chapterContent")}
              </span>
              {chapter && (
                <div className="flex items-center gap-2">
                  {/* 「AI 拆解」(章节直接拆场次)已被「AI 生成剧本 → 编辑器拆场次」新链路取代,按钮暂隐藏;
                      恢复:加回 startDecompose 按钮即可,后端 /novels/decompose 接口仍在 */}
                  <Button size="sm" disabled={genScript.isPending} onClick={() => genScript.mutate()}>
                    {genScript.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
                    {genScript.isPending ? tr("nar.generatingScript") : tr("nar.genScript")}
                  </Button>
                </div>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              {chapter ? (
                <p className="whitespace-pre-wrap text-sm leading-7 text-foreground/90">{chapter.content}</p>
              ) : (
                <Empty text={tr("nar.selectChapter")} tall />
              )}
            </div>
          </div>
        </div>

        {/* 右:拆解建议 */}
        <div className="col-span-4 flex min-h-0 flex-col">
          <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
              <span className="flex items-center gap-1.5 font-medium">
                <Sparkles className="h-4 w-4 text-primary" /> {tr("nar.suggest")}
                {dec && <span className="text-xs text-muted-foreground">· {dec.scenes.length}</span>}
              </span>
              {dec && <Badge variant="primary">{dec.strategy}</Badge>}
            </div>
            {!dec ? (
              <div className="flex min-h-0 flex-1 items-center justify-center">
                {decRunning ? (
                  <div className="flex flex-col items-center gap-3 px-4 text-center">
                    <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    <p className="text-sm text-muted-foreground">{tr("nar.decomposingBanner").replace("{n}", String(decSec))}</p>
                  </div>
                ) : (
                  <Empty text={tr("nar.suggestEmpty")} tall />
                )}
              </div>
            ) : (
              <>
                <div className="min-h-0 flex-1 space-y-2 overflow-auto p-4">
                  {dec.scenes.map((s, i) => (
                    <div key={i} className="rounded-md border border-border p-2.5">
                      <div className="text-sm font-medium">{s.title}</div>
                      {s.shots.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {s.shots.map((sh, j) => (
                            <span key={j} className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                              {sh.title}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="shrink-0 space-y-2 border-t border-border p-4">
                  <div className="flex gap-2">
                    <select
                      className="h-9 flex-1 rounded-md border border-border bg-bg px-2 text-sm"
                      value={scriptId}
                      onChange={(e) => setScriptId(e.target.value)}
                    >
                      <option value="">{tr("nar.selectScript")}</option>
                      {scripts?.map((s) => (
                        <option key={s.id} value={s.id}>{s.title}</option>
                      ))}
                    </select>
                    <Button size="sm" variant="outline" onClick={() => createScript.mutate(detail?.title ?? tr("nar.newScript"))}>
                      <Plus className="h-4 w-4" />
                    </Button>
                    {scriptId && (
                      <Button size="sm" variant="outline" title={tr("nar.openEditor")} onClick={() => navigate(`${base}/scripts/${scriptId}`)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                  <Button
                    className="w-full"
                    disabled={!scriptId || applyScenes.isPending}
                    onClick={async () => {
                      if (await confirm({ title: tr("nar.applyScenes"), message: tr("nar.applyConfirm"), confirmText: tr("nar.applyScenes") })) {
                        applyScenes.mutate();
                      }
                    }}
                  >
                    {tr("nar.applyScenes")}
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Panel({
  title,
  icon: Icon,
  action,
  className,
  bodyClassName,
  children,
}: {
  title: string;
  icon: React.ElementType;
  action?: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("flex min-h-0 flex-col rounded-lg border border-border bg-card", className)}>
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border px-3 text-sm font-medium">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="flex-1 truncate">{title}</span>
        {action}
      </div>
      <div className={cn("min-h-0 flex-1 overflow-auto p-2", bodyClassName)}>{children}</div>
    </div>
  );
}

function Row({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-sm text-left transition-colors ${
        active ? "bg-primary/15 text-primary" : "hover:bg-muted text-foreground/90"
      }`}
    >
      {children}
    </button>
  );
}

function Empty({ text, tall }: { text: string; tall?: boolean }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center text-muted-foreground ${tall ? "py-16" : "py-6"}`}>
      <BookOpen className="h-7 w-7 mb-2 opacity-40" />
      <p className="text-sm">{text}</p>
    </div>
  );
}
