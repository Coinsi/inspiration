// 剧本编辑器:分块(typed blocks)编辑 + AI 侧栏改稿 + 派生场次/资产 + 导出 Fountain。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowLeftRight,
  Clapperboard,
  Download,
  FileText,
  List,
  Loader2,
  MessageCircle,
  Parentheses,
  Plus,
  Save,
  Sparkles,
  Trash2,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AssistPanel from "@/components/AssistPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
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

let nextKey = 1;
type EditBlock = ScriptBlock & { key: number };

const withKeys = (blocks: ScriptBlock[]): EditBlock[] => blocks.map((b) => ({ ...b, key: nextKey++ }));
const stripKeys = (blocks: EditBlock[]): ScriptBlock[] =>
  blocks.map(({ key: _key, ...b }) => b).filter((b) => b.text.trim());

export default function ScriptEditor() {
  const { projectId, scriptId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr } = useI18n();

  const { data: script } = useQuery({
    queryKey: ["script", scriptId],
    queryFn: () => api.get<ScriptDetail>(`${base}/scripts/${scriptId}`),
  });
  // 剧本数据(落库口径):派生场次/分镜 + 项目资产分类数
  const { data: dbStats } = useQuery({
    queryKey: ["script-stats", scriptId],
    queryFn: () => api.get<{ scenes: number; shots: number; assets: Record<string, number> }>(`${base}/scripts/${scriptId}/stats`),
  });
  const [assistOpen, setAssistOpen] = usePersistentState<boolean>("se.assistOpen", true);
  const [outlineOpen, setOutlineOpen] = usePersistentState<boolean>("se.outlineOpen", false);

  const [blocks, setBlocks] = useState<EditBlock[]>([]);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  // 仅在切换剧本或外部(AI 应用/回滚)刷新后重置本地编辑态
  useEffect(() => {
    if (script && loadedFor !== `${script.id}:${JSON.stringify(script.content_blocks)}`) {
      setBlocks(withKeys(script.content_blocks));
      setLoadedFor(`${script.id}:${JSON.stringify(script.content_blocks)}`);
    }
  }, [script, loadedFor]);

  const dirty = useMemo(
    () => !!script && JSON.stringify(stripKeys(blocks)) !== JSON.stringify(script.content_blocks.filter((b) => b.text.trim())),
    [blocks, script],
  );

  // 剧本数据:场景 / 角色 / 对白 / 字数(实时,纯前端统计)
  const stats = useMemo(() => {
    const scenes = blocks.filter((b) => b.block_type === "scene_heading");
    const chars = new Set(
      blocks.filter((b) => b.block_type === "character").map((b) => b.text.trim()).filter(Boolean),
    );
    const dialogues = blocks.filter((b) => b.block_type === "dialogue").length;
    const words = blocks.reduce((n, b) => n + b.text.replace(/\s/g, "").length, 0);
    return { scenes, characters: chars.size, dialogues, words };
  }, [blocks]);

  const save = useMutation({
    mutationFn: () => api.put<ScriptDetail>(`${base}/scripts/${scriptId}/blocks`, { blocks: stripKeys(blocks) }),
    onSuccess: () => {
      toast.push(tr("se.savedToast"), "success");
      void qc.invalidateQueries({ queryKey: ["script", scriptId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  const exportFountain = async () => {
    const res = await fetch(`/api/v1${base}/scripts/${scriptId}/fountain`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
    });
    const text = await res.text();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    a.download = `${script?.title ?? "script"}.fountain`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // ── 派生:场次/镜头 ──
  const [dec, setDec] = useState<DecomposeResult | null>(null);
  const decompose = useMutation({
    mutationFn: () => api.post<DecomposeResult>(`${base}/scripts/${scriptId}/decompose`),
    onSuccess: (d) => {
      setDec(d);
      if (d.scenes.length === 0) toast.push(tr("se.noScenes"), "error");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const applyScenes = useMutation({
    mutationFn: () =>
      api.post(`${base}/scripts/${scriptId}/apply-scenes`, {
        chapter_id: script?.source_chapter_id,
        scenes: dec?.scenes,
      }),
    onSuccess: () => {
      setDec(null);
      toast.push(tr("se.scenesApplied"), "success");
      void qc.invalidateQueries({ queryKey: ["script-stats", scriptId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  // ── 派生:实体→资产 ──
  const [drafts, setDrafts] = useState<(EntityDraft & { checked: boolean })[] | null>(null);
  const extract = useMutation({
    mutationFn: () => api.post<EntityDraft[]>(`${base}/extract-entities`, { script_id: scriptId }),
    onSuccess: (d) => {
      if (d.length === 0) toast.push(tr("se.noEntities"), "error");
      else setDrafts(d.map((e) => ({ ...e, checked: true })));
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const applyEntities = useMutation({
    mutationFn: () =>
      api.post(`${base}/apply-entities`, { entities: drafts?.filter((d) => d.checked).map(({ checked: _c, ...e }) => e) }),
    onSuccess: () => {
      setDrafts(null);
      toast.push(tr("se.entitiesApplied"), "success");
      void qc.invalidateQueries({ queryKey: ["assets", projectId] });
      void qc.invalidateQueries({ queryKey: ["script-stats", scriptId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  const update = (key: number, patch: Partial<ScriptBlock>) =>
    setBlocks((bs) => bs.map((b) => (b.key === key ? { ...b, ...patch } : b)));
  const removeBlock = (key: number) => setBlocks((bs) => bs.filter((b) => b.key !== key));
  const insertAfter = (key: number | null, block_type = "action") =>
    setBlocks((bs) => {
      const node: EditBlock = { key: nextKey++, block_type, text: "" };
      if (key === null) return [...bs, node];
      const i = bs.findIndex((b) => b.key === key);
      return [...bs.slice(0, i + 1), node, ...bs.slice(i + 1)];
    });
  // 工具栏插块:优先插在光标所在块之后,否则追加到末尾
  const [focusedKey, setFocusedKey] = useState<number | null>(null);
  const insertFromToolbar = (block_type: string) =>
    insertAfter(focusedKey ?? blocks[blocks.length - 1]?.key ?? null, block_type);
  const jumpTo = (key: number) =>
    document.getElementById(`blk-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" });

  if (!script) return <p className="p-6 text-muted-foreground">{tr("common.loading")}</p>;

  return (
    <div className="flex h-full flex-col p-6">
      {/* 头部 */}
      <div className="mb-4 flex shrink-0 items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <button
            onClick={() => navigate(`${base}/narrative`)}
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <FileText className="h-5 w-5 shrink-0 text-primary" />
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold leading-tight">{script.title}</h1>
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="font-code">{script.code}</span>
              <span>· {stats.scenes.length} {tr("scripts.sceneUnit")}</span>
              <span>· {stats.characters} {tr("se.statChars")}</span>
              <span>· {stats.dialogues} {tr("se.statDialogues")}</span>
              <span>· {stats.words} {tr("se.statWords")}</span>
              {dirty && <Badge variant="primary">{tr("se.dirty")}</Badge>}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            variant={outlineOpen ? "default" : "outline"}
            title={tr("se.outline")}
            onClick={() => setOutlineOpen(!outlineOpen)}
          >
            <List className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="outline" onClick={() => decompose.mutate()} disabled={decompose.isPending}>
            {decompose.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Clapperboard className="mr-1 h-4 w-4" />}
            {tr("se.decompose")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => extract.mutate()} disabled={extract.isPending}>
            {extract.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Users className="mr-1 h-4 w-4" />}
            {tr("se.extract")}
          </Button>
          <Button size="sm" variant="outline" onClick={exportFountain}>
            <Download className="mr-1 h-4 w-4" /> Fountain
          </Button>
          <Button size="sm" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            {save.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
            {tr("common.save")}
          </Button>
          {!assistOpen && (
            <Button size="sm" variant="outline" title={tr("assist.expand")} onClick={() => setAssistOpen(true)}>
              <Sparkles className="h-4 w-4 text-primary" />
            </Button>
          )}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-12 gap-4">
        {/* 左:分块编辑器 */}
        <div className={cn("flex min-h-0 flex-col rounded-lg border border-border bg-card", assistOpen ? "col-span-8" : "col-span-12")}>
          {/* 块类型快捷工具栏 */}
          <div className="flex shrink-0 items-center justify-center gap-1 border-b border-border px-3 py-1.5">
            {(
              [
                { bt: "scene_heading", icon: Clapperboard },
                { bt: "action", icon: Activity },
                { bt: "character", icon: UserRound },
                { bt: "dialogue", icon: MessageCircle },
                { bt: "parenthetical", icon: Parentheses },
                { bt: "transition", icon: ArrowLeftRight },
              ] as const
            ).map((t) => (
              <button
                key={t.bt}
                onClick={() => insertFromToolbar(t.bt)}
                title={`${tr("se.insertBlock")}:${tr(`se.bt.${t.bt}`)}`}
                className="flex flex-col items-center gap-0.5 rounded-md px-3 py-1 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
              >
                <t.icon className="h-4 w-4" />
                <span className="text-[10px]">{tr(`se.bt.${t.bt}`)}</span>
              </button>
            ))}
          </div>

          <div className="flex min-h-0 flex-1">
            {/* 大纲:场景头列表,点击跳转 */}
            {outlineOpen && (
              <div className="w-52 shrink-0 overflow-auto border-r border-border p-2">
                <div className="px-2 pb-1.5 text-[11px] uppercase tracking-wider text-faint">{tr("se.outline")}</div>
                {stats.scenes.length === 0 ? (
                  <p className="px-2 text-xs text-muted-foreground">{tr("se.noScenesYet")}</p>
                ) : (
                  stats.scenes.map((s, i) => (
                    <button
                      key={s.key}
                      onClick={() => jumpTo(s.key)}
                      className="flex w-full items-baseline gap-1.5 rounded-md px-2 py-1.5 text-left text-xs text-foreground/85 transition-colors hover:bg-muted"
                    >
                      <span className="font-code text-faint">{i + 1}</span>
                      <span className="min-w-0 flex-1 truncate">{s.text}</span>
                    </button>
                  ))
                )}
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-auto px-6 py-4">
              {blocks.length === 0 ? (
                <div className="flex flex-col items-center gap-3 py-16 text-center text-muted-foreground">
                  <FileText className="h-8 w-8 opacity-40" />
                  <p className="text-sm">{tr("se.empty")}</p>
                  <Button size="sm" variant="outline" onClick={() => insertAfter(null, "scene_heading")}>
                    <Plus className="mr-1 h-4 w-4" /> {tr("se.addBlock")}
                  </Button>
                </div>
              ) : (
                <div className="mx-auto max-w-[680px] space-y-0.5 pb-10">
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
                    onClick={() => insertAfter(blocks[blocks.length - 1]?.key ?? null)}
                    className="mt-2 flex w-full items-center justify-center gap-1 rounded-md border border-dashed border-border py-2 text-xs text-faint transition hover:border-primary/50 hover:text-primary"
                  >
                    <Plus className="h-3.5 w-3.5" /> {tr("se.addBlock")}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* 右:剧本数据 + AI 助手(可收起) */}
        {assistOpen && (
          <div className="col-span-4 flex min-h-0 flex-col gap-4">
            {/* 剧本数据(落库口径,对齐 Laper 右栏) */}
            <div className="shrink-0 rounded-lg border border-border bg-card p-3">
              <div className="mb-2 text-[11px] uppercase tracking-wider text-faint">{tr("se.dbStats")}</div>
              <div className="space-y-1.5 text-sm">
                <StatRow label={tr("se.dbScenes")} value={dbStats?.scenes ?? 0} />
                <StatRow label={tr("se.dbShots")} value={dbStats?.shots ?? 0} />
                {["character", "location", "prop"].map((t) => (
                  <StatRow key={t} label={tr(`asset.${t}`)} value={dbStats?.assets?.[t] ?? 0} />
                ))}
              </div>
            </div>
            <AssistPanel
              projectId={projectId!}
              targetType="script"
              targetId={scriptId!}
              className="min-h-0 flex-1"
              onCollapse={() => setAssistOpen(false)}
              onApplied={() => void qc.invalidateQueries({ queryKey: ["script", scriptId] })}
            />
          </div>
        )}
      </div>

      {/* 派生场次浮层 */}
      {dec && dec.scenes.length > 0 && (
        <Overlay title={`${tr("se.deriveScenes")} · ${dec.scenes.length}`} onClose={() => setDec(null)}>
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
          <div className="shrink-0 border-t border-border p-3">
            <Button
              className="w-full"
              disabled={applyScenes.isPending}
              onClick={async () => {
                if (await confirm({ title: tr("se.applyScenes"), message: tr("se.applyScenesConfirm"), confirmText: tr("se.applyScenes") })) {
                  applyScenes.mutate();
                }
              }}
            >
              {applyScenes.isPending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
              {tr("se.applyScenes")}
            </Button>
          </div>
        </Overlay>
      )}

      {/* 实体草稿浮层 */}
      {drafts && (
        <Overlay title={`${tr("se.entities")} · ${drafts.length}`} onClose={() => setDrafts(null)}>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-auto p-4">
            {drafts.map((d, i) => (
              <label key={i} className="flex cursor-pointer items-center gap-2.5 rounded-md border border-border px-2.5 py-2 transition hover:border-primary/40">
                <input
                  type="checkbox"
                  checked={d.checked}
                  onChange={(e) => setDrafts((ds) => ds!.map((x, j) => (j === i ? { ...x, checked: e.target.checked } : x)))}
                  className="accent-[var(--primary)]"
                />
                <Badge variant="primary">{tr(`asset.${d.type}`)}</Badge>
                <span className="min-w-0 flex-1 truncate text-sm">{d.name}</span>
                <span className="max-w-[40%] truncate text-xs text-muted-foreground">{d.summary}</span>
              </label>
            ))}
          </div>
          <div className="shrink-0 border-t border-border p-3">
            <Button
              className="w-full"
              disabled={applyEntities.isPending || !drafts.some((d) => d.checked)}
              onClick={() => applyEntities.mutate()}
            >
              {applyEntities.isPending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
              {tr("se.applyEntities")}
            </Button>
          </div>
        </Overlay>
      )}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-code">{value}</span>
    </div>
  );
}

// 块类型 → 专业剧本排版样式
const BLOCK_STYLE: Record<string, string> = {
  scene_heading: "font-semibold uppercase tracking-wide",
  action: "",
  character: "ml-[28%] font-medium uppercase",
  dialogue: "ml-[14%] mr-[14%]",
  parenthetical: "ml-[22%] italic text-muted-foreground",
  transition: "text-right uppercase text-muted-foreground",
};

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
    <div id={`blk-${block.key}`} onFocusCapture={onFocus} className="group/blk relative rounded-md transition hover:bg-elevated/50">
      {/* 悬浮工具条 */}
      <div className="absolute -left-1 top-1 z-10 flex -translate-x-full items-center gap-0.5 opacity-0 transition group-hover/blk:opacity-100">
        <select
          value={block.block_type}
          onChange={(e) => onChange({ block_type: e.target.value })}
          className="h-6 rounded border border-border bg-bg px-1 text-[10px] text-muted-foreground"
          title={tr("se.blockType")}
        >
          {SCRIPT_BLOCK_TYPES.map((bt) => (
            <option key={bt} value={bt}>{tr(`se.bt.${bt}`)}</option>
          ))}
        </select>
        <button onClick={onInsertAfter} title={tr("se.addBlock")} className="grid h-6 w-6 place-items-center rounded text-faint hover:bg-primary/15 hover:text-primary">
          <Plus className="h-3 w-3" />
        </button>
        <button onClick={onRemove} title={tr("common.delete")} className="grid h-6 w-6 place-items-center rounded text-faint hover:bg-danger/15 hover:text-danger">
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      <AutoTextarea
        value={block.text}
        onChange={(v) => onChange({ text: v })}
        className={cn(
          "w-full resize-none bg-transparent px-2 py-1 font-serif text-[15px] leading-7 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/40 rounded",
          BLOCK_STYLE[block.block_type] ?? "",
        )}
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
  return (
    <textarea
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      rows={1}
      ref={(el) => {
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

function Overlay({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const { t: tr } = useI18n();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={onClose}>
      <div
        className="flex max-h-[80vh] w-[560px] flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex h-11 shrink-0 items-center justify-between border-b border-border px-4">
          <span className="flex items-center gap-1.5 text-sm font-medium">
            <Sparkles className="h-4 w-4 text-primary" /> {title}
          </span>
          <button onClick={onClose} title={tr("common.cancel")} className="grid h-7 w-7 place-items-center rounded-md text-faint hover:bg-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

