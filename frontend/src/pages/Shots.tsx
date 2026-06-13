import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import GenerationPanel from "@/components/GenerationPanel";
import ReviewPanel from "@/components/ReviewPanel";
import ShotAssetRefs from "@/components/ShotAssetRefs";
import { Filter, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { api, NEXT_STATUS, type ComposeResult, type Novel, type NovelDetail, type Shot } from "@/lib/api";

const ORDER = ["to_design", "concept", "generating", "pending_review", "revising", "approved", "in_cut"];
const DOT: Record<string, string> = {
  to_design: "bg-muted-foreground",
  concept: "bg-info",
  generating: "bg-primary",
  pending_review: "bg-warning",
  revising: "bg-danger",
  approved: "bg-success",
  in_cut: "bg-foreground",
};

export default function Shots() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr, lang } = useI18n();
  const [open, setOpen] = useState<Shot | null>(null);
  const [mtab, setMtab] = useState("overview");
  const [compose, setCompose] = useState<ComposeResult | null>(null);
  const [novelId, setNovelId] = usePersistentState(`shots.novel.${projectId}`, "");
  const [chapterId, setChapterId] = usePersistentState(`shots.chapter.${projectId}`, "");
  const openShot = (s: Shot) => { setOpen(s); setMtab("overview"); setCompose(null); doCompose.mutate(s.id); };

  // 筛选数据源:小说列表 + 选中小说的章节
  const { data: novels } = useQuery({ queryKey: ["novels", projectId], queryFn: () => api.get<Novel[]>(`${base}/novels`) });
  const { data: novelDetail } = useQuery({
    queryKey: ["novel", projectId, novelId],
    queryFn: () => api.get<NovelDetail>(`${base}/novels/${novelId}`),
    enabled: !!novelId,
  });
  const chapters = novelDetail?.chapters ?? [];

  const { data: shots } = useQuery({
    queryKey: ["all-shots", projectId, novelId, chapterId],
    queryFn: () => {
      const p = new URLSearchParams();
      if (novelId) p.set("novel_id", novelId);
      if (chapterId) p.set("chapter_id", chapterId);
      const q = p.toString();
      return api.get<Shot[]>(`${base}/shots${q ? `?${q}` : ""}`);
    },
  });

  // 看板计数 / 总数从(可能已筛选的)镜头列表派生,确保与筛选一致
  const counts = ORDER.reduce<Record<string, number>>((a, st) => {
    a[st] = (shots ?? []).filter((s) => s.production_status === st).length;
    return a;
  }, {});
  const total = shots?.length ?? 0;

  const fmtDate = (iso?: string | null) => {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  };

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
    qc.invalidateQueries({ queryKey: ["board", projectId] });
  };
  const transition = useMutation({
    mutationFn: ({ id, to }: { id: string; to: string }) =>
      api.post<Shot>(`${base}/shots/${id}/transition`, { to }),
    onSuccess: (s) => { setOpen(s); refresh(); },
  });
  const doCompose = useMutation({
    mutationFn: (id: string) => api.get<ComposeResult>(`${base}/shots/${id}/compose-prompt`),
    onSuccess: setCompose,
  });

  const byStatus = (st: string) => (shots ?? []).filter((s) => s.production_status === st);

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-1">{tr("shots.title")}</h1>
      <p className="text-sm text-muted-foreground mb-4">{tr("shots.subtitle").replace("{n}", String(total))}</p>

      {/* 筛选:按小说 / 章节 */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="flex items-center gap-1 text-xs text-muted-foreground"><Filter className="h-3.5 w-3.5" /> {tr("shots.filter")}</span>
        <select
          value={novelId}
          onChange={(e) => { setNovelId(e.target.value); setChapterId(""); }}
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
        >
          <option value="">{tr("shots.allNovels")}</option>
          {novels?.map((n) => (
            <option key={n.id} value={n.id}>{n.title || n.code}</option>
          ))}
        </select>
        <select
          value={chapterId}
          onChange={(e) => setChapterId(e.target.value)}
          disabled={!novelId}
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm disabled:opacity-50"
        >
          <option value="">{tr("shots.allChapters")}</option>
          {chapters.map((c) => (
            <option key={c.id} value={c.id}>{c.title ?? tr("nar.chapterN").replace("{n}", String(c.ordinal))}</option>
          ))}
        </select>
        {(novelId || chapterId) && (
          <button onClick={() => { setNovelId(""); setChapterId(""); }} className="text-xs text-muted-foreground hover:text-primary">
            {tr("shots.clearFilter")}
          </button>
        )}
      </div>

      {/* KPI 指标条 */}
      <div className="grid grid-cols-7 gap-2 mb-6">
        {ORDER.map((st) => (
          <div key={st} className="glass rounded-xl border border-border p-3 shadow-panel backdrop-blur-md transition hover:border-primary/40">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={`h-2 w-2 rounded-full ${DOT[st]}`} /> {tr(`status.${st}`)}
            </div>
            <div className="text-2xl font-semibold mt-1 font-code">{counts[st] ?? 0}</div>
          </div>
        ))}
      </div>

      {/* 看板(左) + 详情(右) */}
      <div className="flex gap-4 items-start">
        <div className="flex-1 min-w-0 overflow-x-auto pb-4">
          <div className="flex gap-3">
            {ORDER.map((st) => {
              const items = byStatus(st);
              return (
                <div key={st} className="w-56 shrink-0">
                  <div className="flex items-center gap-1.5 mb-2 text-sm font-medium">
                    <span className={`h-2 w-2 rounded-full ${DOT[st]}`} />
                    {tr(`status.${st}`)}
                    <span className="text-muted-foreground">{items.length}</span>
                  </div>
                  <div className="space-y-2">
                    {items.map((s) => (
                      <button
                        key={s.id}
                        onClick={() => openShot(s)}
                        className={`block w-full text-left rounded-lg border bg-card p-3 transition-all hover:-translate-y-px ${
                          open?.id === s.id ? "border-primary ring-1 ring-primary shadow-glow-sm" : "border-border hover:border-primary/60"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[11px] font-code text-muted-foreground">{s.code}</span>
                          {s.created_at && <span className="font-code text-[10px] text-faint shrink-0">{fmtDate(s.created_at)}</span>}
                        </div>
                        <div className="text-sm mt-0.5 line-clamp-2">{s.title || s.description || "—"}</div>
                        {(s.novel_title || s.chapter_title) && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {s.novel_title && (
                              <span className="inline-flex max-w-full items-center gap-1 truncate rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                                📖 {s.novel_title}
                              </span>
                            )}
                            {(s.chapter_title || s.chapter_ordinal != null) && (
                              <span className="inline-flex max-w-full items-center gap-1 truncate rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                                🔖 {s.chapter_title ?? tr("nar.chapterN").replace("{n}", String(s.chapter_ordinal))}
                              </span>
                            )}
                          </div>
                        )}
                      </button>
                    ))}
                    {items.length === 0 && <div className="text-xs text-muted-foreground/60 px-1">—</div>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 右侧详情面板 */}
        {open && (
          <aside className="glass w-[420px] shrink-0 rounded-xl border border-border shadow-panel backdrop-blur-md sticky top-0 max-h-[calc(100vh-7rem)] flex flex-col overflow-hidden">
            <div className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0">
              <span className="flex items-center gap-2">
                <span className="font-code text-sm">{open.code}</span>
                <Badge variant="primary">{tr(`status.${open.production_status}`)}</Badge>
              </span>
              <button onClick={() => setOpen(null)} className="h-7 w-7 rounded-md hover:bg-elevated flex items-center justify-center text-muted-foreground" title={tr("shots.close")}>
                <X className="h-4 w-4" />
              </button>
            </div>

            {(open.created_at || open.novel_title || open.chapter_title) && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-2 text-[11px] shrink-0">
                {open.created_at && <span className="font-code text-faint">🕒 {fmtDate(open.created_at)}</span>}
                {open.novel_title && <span className="text-primary">📖 {open.novel_title}</span>}
                {(open.chapter_title || open.chapter_ordinal != null) && (
                  <span className="text-accent">🔖 {open.chapter_title ?? tr("nar.chapterN").replace("{n}", String(open.chapter_ordinal))}</span>
                )}
              </div>
            )}

            <div className="px-4 pt-2 shrink-0">
              <Tabs
                value={mtab}
                onChange={setMtab}
                tabs={[
                  { key: "overview", label: tr("shot.overview") },
                  { key: "gen", label: tr("shot.gen") },
                  { key: "review", label: tr("shot.review") },
                ]}
              />
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              {mtab === "overview" && (
                <div className="space-y-5">
                  <section>
                    <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1.5">{tr("shot.desc")}</div>
                    <p className="text-sm leading-6 whitespace-pre-wrap break-words text-foreground/90">{open.description || tr("shot.noDesc")}</p>
                  </section>

                  <section>
                    <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">{tr("shot.transition")}</div>
                    <div className="flex gap-2 flex-wrap items-center">
                      {(NEXT_STATUS[open.production_status] ?? []).map((to) => (
                        <Button key={to} size="sm" variant="outline" onClick={() => transition.mutate({ id: open.id, to })}>
                          → {tr(`status.${to}`)}
                        </Button>
                      ))}
                      {(NEXT_STATUS[open.production_status] ?? []).length === 0 && (
                        <span className="text-xs text-muted-foreground">{tr("shots.terminal")}</span>
                      )}
                    </div>
                  </section>

                  <section>
                    <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">{tr("shot.refs")}</div>
                    <ShotAssetRefs projectId={projectId!} shotId={open.id} onChange={() => doCompose.mutate(open.id)} />
                  </section>

                  <section>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs uppercase tracking-wider text-muted-foreground">{tr("shot.finalPrompt")}</span>
                      <Button size="sm" variant="ghost" onClick={() => doCompose.mutate(open.id)}>{tr("shot.recompute")}</Button>
                    </div>
                    <div className="p-3 rounded-md bg-bg border border-border text-sm leading-6 min-h-[60px] whitespace-pre-wrap break-words">
                      {compose ? (
                        <>
                          {compose.overridden && <div className="text-xs text-warning mb-1">{tr("shot.overridden")}</div>}
                          {compose.final_prompt || tr("shot.empty")}
                        </>
                      ) : (
                        <span className="text-muted-foreground">{tr("shot.computing")}</span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground mt-1.5">
                      {tr("shot.refsHint")}
                    </p>
                  </section>
                </div>
              )}

              {mtab === "gen" && <GenerationPanel projectId={projectId!} targetType="shot" targetId={open.id} />}
              {mtab === "review" && <ReviewPanel projectId={projectId!} shotId={open.id} />}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
