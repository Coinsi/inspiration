// 设定(故事圣经):AI 通读小说沉淀的世界观/体系/人物/地点等设定库。
// 一键全书提取(后台逐章增量),分类导航 + 设定卡片,可编辑/删除/转资产。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookMarked,
  Check,
  ImagePlus,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConfirm } from "@/components/ui/confirm";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import {
  api,
  SETTING_CATEGORIES,
  type Novel,
  type Setting,
  type SettingExtraction,
} from "@/lib/api";

const TO_ASSET_CATEGORIES = new Set(["character", "location", "item"]);

export default function StoryBible() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr } = useI18n();

  const [novelId, setNovelId] = usePersistentState<string | null>(`bible.novel.${projectId}`, null);
  const [category, setCategory] = useState<string>("");
  const [q, setQ] = useState("");
  const [chFilter, setChFilter] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editContent, setEditContent] = useState("");
  const [adding, setAdding] = useState(false);
  const [newCat, setNewCat] = useState("world");
  const [newName, setNewName] = useState("");
  // 提取范围(章节序号,空 = 全书)与并发数
  const [fromCh, setFromCh] = useState("");
  const [toCh, setToCh] = useState("");
  const [concurrency, setConcurrency] = usePersistentState<number>("bible.concurrency", 4);

  const { data: novels } = useQuery({ queryKey: ["novels", projectId], queryFn: () => api.get<Novel[]>(`${base}/novels`) });

  const { data: settings } = useQuery({
    queryKey: ["settings", projectId, novelId],
    queryFn: () => api.get<Setting[]>(`${base}/settings${novelId ? `?novel_id=${novelId}` : ""}`),
  });

  // 提取任务:运行中每 1.5s 轮询,同时刷新设定列表(边跑边看到设定长出来)
  const { data: job } = useQuery({
    queryKey: ["bible-job", novelId],
    queryFn: () => api.get<SettingExtraction | null>(`${base}/novels/${novelId}/extract-settings/latest`),
    enabled: !!novelId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
  });
  const running = job?.status === "running";
  useEffect(() => {
    // 提取进行中:跟随进度刷新设定列表(边跑边看到设定长出来);结束时再刷一次
    void qc.invalidateQueries({ queryKey: ["settings", projectId, novelId] });
  }, [job?.done_chapters, job?.status, qc, projectId, novelId]);

  const startExtract = useMutation({
    mutationFn: () =>
      api.post<SettingExtraction>(`${base}/novels/${novelId}/extract-settings`, {
        from_chapter: fromCh ? Number(fromCh) : null,
        to_chapter: toCh ? Number(toCh) : null,
        concurrency,
      }),
    onSuccess: () => {
      toast.push(tr("bible.started"), "success");
      void qc.invalidateQueries({ queryKey: ["bible-job", novelId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const cancelExtract = useMutation({
    mutationFn: () => api.post(`${base}/setting-extractions/${job!.id}/cancel`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["bible-job", novelId] }),
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["settings", projectId, novelId] });
  const saveEdit = useMutation({
    mutationFn: () => api.patch(`${base}/settings/${editingId}`, { name: editName, content: editContent }),
    onSuccess: () => {
      setEditingId(null);
      invalidate();
      toast.push(tr("toast.saved"), "success");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`${base}/settings/${id}`),
    onSuccess: invalidate,
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const toAsset = useMutation({
    mutationFn: (id: string) => api.post(`${base}/settings/${id}/to-asset`),
    onSuccess: () => {
      toast.push(tr("bible.assetCreated"), "success");
      void qc.invalidateQueries({ queryKey: ["assets", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const create = useMutation({
    mutationFn: () =>
      api.post<Setting>(`${base}/settings`, { novel_id: novelId, category: newCat, name: newName, content: "" }),
    onSuccess: (s) => {
      setAdding(false);
      setNewName("");
      invalidate();
      setEditingId(s.id);
      setEditName(s.name);
      setEditContent("");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  // 分类导航:预置 8 类 + 数据中出现的扩展分类
  const categories = useMemo(() => {
    const known = [...SETTING_CATEGORIES] as string[];
    const extra = [...new Set((settings ?? []).map((s) => s.category))].filter((c) => !known.includes(c));
    return [...known, ...extra];
  }, [settings]);
  const countOf = (c: string) => (settings ?? []).filter((s) => s.category === c).length;
  // 章节筛选:支持「5」或「5-8」区间
  const chMatch = (s: Setting): boolean => {
    const f = chFilter.trim();
    if (!f) return true;
    const m = f.match(/^(\d+)\s*[-~]\s*(\d+)$/);
    const [lo, hi] = m ? [Number(m[1]), Number(m[2])] : [Number(f), Number(f)];
    if (Number.isNaN(lo)) return true;
    return (s.source_chapters ?? []).some((c) => c >= lo && c <= hi);
  };
  const qMatch = (s: Setting): boolean => {
    const v = q.trim().toLowerCase();
    if (!v) return true;
    return s.name.toLowerCase().includes(v) || (s.content ?? "").toLowerCase().includes(v);
  };
  const shown = (settings ?? []).filter((s) => (!category || s.category === category) && chMatch(s) && qMatch(s));
  const catLabel = (c: string) => {
    const k = `bible.cat.${c}`;
    const v = tr(k);
    return v === k ? c : v; // 扩展分类无翻译时直接显示原文
  };

  return (
    <div className="flex min-h-full flex-col p-4 md:p-7">
      {/* 头部 */}
      <div className="mb-4 flex flex-wrap shrink-0 items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <BookMarked className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-xl font-semibold leading-tight">{tr("bible.title")}</h1>
            <p className="text-sm text-muted-foreground">{tr("bible.subtitle")}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
            value={novelId ?? ""}
            onChange={(e) => setNovelId(e.target.value || null)}
          >
            <option value="">{tr("bible.selectNovel")}</option>
            {novels?.map((n) => (
              <option key={n.id} value={n.id}>{n.title}</option>
            ))}
          </select>
          {running ? (
            <>
              <span className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                {tr("bible.extracting").replace("{d}", String(job!.done_chapters)).replace("{t}", String(job!.total_chapters))}
              </span>
              <Button size="sm" variant="outline" onClick={() => cancelExtract.mutate()}>
                {tr("common.cancel")}
              </Button>
            </>
          ) : (
            <>
              <div className="flex items-center gap-1 text-xs text-muted-foreground" title={tr("bible.rangeHint")}>
                <input
                  type="number"
                  min={1}
                  value={fromCh}
                  onChange={(e) => setFromCh(e.target.value)}
                  placeholder={tr("bible.fromCh")}
                  className="h-9 w-16 rounded-md border border-border bg-bg px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
                <span>~</span>
                <input
                  type="number"
                  min={1}
                  value={toCh}
                  onChange={(e) => setToCh(e.target.value)}
                  placeholder={tr("bible.toCh")}
                  className="h-9 w-16 rounded-md border border-border bg-bg px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
              </div>
              <select
                title={tr("bible.concurrencyHint")}
                className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
                value={concurrency}
                onChange={(e) => setConcurrency(Number(e.target.value))}
              >
                {[1, 2, 4, 8].map((n) => (
                  <option key={n} value={n}>{tr("bible.concurrency").replace("{n}", String(n))}</option>
                ))}
              </select>
              <Button size="sm" disabled={!novelId || startExtract.isPending} onClick={() => startExtract.mutate()}>
                <Sparkles className="mr-1 h-4 w-4" />
                {fromCh || toCh ? tr("bible.extractRange") : tr("bible.extract")}
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-12 gap-4">
        {/* 左:分类导航 */}
        <div className="lg:col-span-2 max-h-52 lg:max-h-none flex min-h-0 flex-col overflow-auto rounded-lg border border-border bg-card p-2">
          <CatRow active={!category} label={tr("bible.all")} count={settings?.length ?? 0} onClick={() => setCategory("")} />
          {categories.map((c) => (
            <CatRow key={c} active={category === c} label={catLabel(c)} count={countOf(c)} onClick={() => setCategory(c)} />
          ))}
        </div>

        {/* 右:设定卡片 */}
        <div className="lg:col-span-10 min-w-0 flex min-h-0 flex-col overflow-auto">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap min-w-0 items-center gap-2">
              <span className="shrink-0 text-sm text-muted-foreground">
                {category ? catLabel(category) : tr("bible.all")} · {shown.length}
              </span>
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={tr("bible.search")}
                className="h-8 w-48"
              />
              <Input
                value={chFilter}
                onChange={(e) => setChFilter(e.target.value)}
                placeholder={tr("bible.chFilter")}
                title={tr("bible.chFilterHint")}
                className="h-8 w-32"
              />
              {(q || chFilter) && (
                <button
                  onClick={() => { setQ(""); setChFilter(""); }}
                  title={tr("common.cancel")}
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-faint hover:bg-muted hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            {adding ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <select className="h-8 rounded-md border border-border bg-bg px-2 text-xs" value={newCat} onChange={(e) => setNewCat(e.target.value)}>
                  {categories.map((c) => (
                    <option key={c} value={c}>{catLabel(c)}</option>
                  ))}
                </select>
                <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={tr("bible.name")} className="h-8 w-44" />
                <Button size="sm" className="h-8" disabled={!newName.trim()} onClick={() => create.mutate()}>
                  <Check className="h-3.5 w-3.5" />
                </Button>
                <Button size="sm" variant="ghost" className="h-8" onClick={() => setAdding(false)}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            ) : (
              <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
                <Plus className="mr-1 h-4 w-4" /> {tr("bible.add")}
              </Button>
            )}
          </div>

          {shown.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center py-16 text-center text-muted-foreground">
              <BookMarked className="mb-3 h-10 w-10 opacity-40" />
              <p className="max-w-sm text-sm leading-6">{tr("bible.empty")}</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {shown.map((s) => (
                <div key={s.id} className="group flex flex-col rounded-xl border border-border bg-card p-3.5 transition-colors hover:border-primary/40">
                  {editingId === s.id ? (
                    <>
                      <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="mb-2 h-8 font-medium" />
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        rows={6}
                        className="mb-2 w-full resize-y rounded-md border border-border bg-bg px-2.5 py-1.5 text-[13px] leading-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      />
                      <div className="flex justify-end gap-1.5">
                        <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>{tr("common.cancel")}</Button>
                        <Button size="sm" disabled={saveEdit.isPending} onClick={() => saveEdit.mutate()}>{tr("common.save")}</Button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex flex-wrap min-w-0 items-center gap-2">
                          <Badge variant="primary">{catLabel(s.category)}</Badge>
                          <span className="truncate font-medium">{s.name}</span>
                        </div>
                        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100">
                          {TO_ASSET_CATEGORIES.has(s.category) && (
                            <button
                              onClick={() => toAsset.mutate(s.id)}
                              title={tr("bible.toAsset")}
                              className="grid h-7 w-7 place-items-center rounded-md text-faint hover:bg-primary/15 hover:text-primary"
                            >
                              <ImagePlus className="h-3.5 w-3.5" />
                            </button>
                          )}
                          <button
                            onClick={() => { setEditingId(s.id); setEditName(s.name); setEditContent(s.content); }}
                            title={tr("assets.edit")}
                            className="grid h-7 w-7 place-items-center rounded-md text-faint hover:bg-primary/15 hover:text-primary"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button
                            onClick={async () => {
                              if (await confirm({ title: tr("common.delete"), message: tr("bible.deleteConfirm").replace("{name}", s.name), confirmText: tr("common.delete"), danger: true })) {
                                del.mutate(s.id);
                              }
                            }}
                            title={tr("common.delete")}
                            className="grid h-7 w-7 place-items-center rounded-md text-faint hover:bg-danger/15 hover:text-danger"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                      <p className="mt-2 line-clamp-5 flex-1 whitespace-pre-wrap break-words text-[13px] leading-6 text-muted-foreground">
                        {s.content || tr("bible.noContent")}
                      </p>
                      {s.source_chapters?.length > 0 && (
                        <div className="mt-2 break-words text-[11px] leading-5 text-faint">
                          {tr("bible.sourceCh")} {formatChapterRanges(s.source_chapters)}
                          <span className="ml-1">({s.source_chapters.length})</span>
                        </div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// 章节列表 → 连续区间压缩:[1,2,3,4,8,11,12,14] → "1-4、8、11-12、14"
function formatChapterRanges(nums: number[]): string {
  const a = [...new Set(nums)].sort((x, y) => x - y);
  if (a.length === 0) return "";
  const parts: string[] = [];
  let start = a[0];
  let prev = a[0];
  for (let i = 1; i <= a.length; i++) {
    const v = a[i];
    if (v === prev + 1) {
      prev = v;
      continue;
    }
    parts.push(start === prev ? `${start}` : `${start}-${prev}`);
    if (v !== undefined) {
      start = v;
      prev = v;
    }
  }
  return parts.join("、");
}

function CatRow({ active, label, count, onClick }: { active: boolean; label: string; count: number; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-sm transition-colors ${
        active ? "bg-primary/15 font-medium text-primary" : "text-foreground/85 hover:bg-muted"
      }`}
    >
      <span className="truncate">{label}</span>
      <span className={`font-code text-[11px] ${active ? "text-primary" : "text-faint"}`}>{count}</span>
    </button>
  );
}
