import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Film, Play } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/ui/panel";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { api, PRODUCTION_STATUS_LABEL, type Baseline, type Shot, type Timeline } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function Cuts() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr, lang } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();

  const { data: timelines } = useQuery({
    queryKey: ["timelines", projectId],
    queryFn: () => api.get<Timeline[]>(`${base}/timelines`),
  });
  const { data: baselines } = useQuery({
    queryKey: ["baselines", projectId],
    queryFn: () => api.get<Baseline[]>(`${base}/baselines`),
  });
  const { data: shots } = useQuery({
    queryKey: ["all-shots", projectId],
    queryFn: () => api.get<Shot[]>(`${base}/shots`),
  });
  const inCut = (shots ?? []).filter((s) => s.production_status === "approved" || s.production_status === "in_cut");

  const [tlName, setTlName] = useState("");
  const createTl = useMutation({
    mutationFn: () => api.post(`${base}/timelines`, { name: tlName, kind: "main" }),
    onSuccess: () => {
      setTlName("");
      void qc.invalidateQueries({ queryKey: ["timelines", projectId] });
    },
  });

  const [cutName, setCutName] = useState("");
  const [cutKind, setCutKind] = useState("rough");
  const [tlId, setTlId] = useState("");
  const finalize = useMutation({
    mutationFn: async () => {
      const cut = await api.post<{ id: string }>(`${base}/cuts`, { name: cutName, kind: cutKind, timeline_id: tlId });
      return api.post(`${base}/cuts/${cut.id}/finalize`);
    },
    onSuccess: () => {
      setCutName("");
      void qc.invalidateQueries({ queryKey: ["baselines", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [diff, setDiff] = useState<unknown>(null);
  const compare = useMutation({
    mutationFn: () => api.get(`${base}/baselines/compare?a=${a}&b=${b}`),
    onSuccess: setDiff,
  });

  return (
    <div className="p-6 max-w-[1100px] mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-semibold">{tr("cuts.title")}</h1>
        <p className="text-sm text-muted-foreground">{tr("cuts.subtitle")}</p>
      </div>

      {/* 成片预览播放器 + 入剪时间线 */}
      <Panel title={tr("cuts.preview")} icon={<Film className="h-3.5 w-3.5" />} meta="3840×2160 · 4K">
        <div className="relative aspect-[21/9] w-full overflow-hidden rounded-lg border border-border">
          <div className="absolute inset-0 bg-[radial-gradient(520px_280px_at_50%_25%,hsl(210_60%_30%/0.5),transparent),linear-gradient(180deg,hsl(218_45%_12%),hsl(220_50%_6%))]" />
          <div className="absolute right-[24%] top-[16%] h-16 w-16 rounded-full bg-[radial-gradient(circle_at_40%_40%,hsl(40_40%_88%),hsl(35_30%_60%))] opacity-50 blur-[1px]" />
          <div className="absolute left-1/2 top-[38%] h-28 w-12 -translate-x-1/2 rounded-t-[40px] bg-gradient-to-b from-black/80 to-black/95" />
          <button className="absolute left-1/2 top-1/2 grid h-12 w-12 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-primary text-primary-foreground shadow-glow">
            <Play className="h-5 w-5 fill-current" />
          </button>
        </div>
        <div className="mt-2.5 flex items-center gap-2.5">
          <span className="font-code text-[11px] text-faint">00:00</span>
          <div className="relative h-1 flex-1 rounded bg-elevated">
            <span className="absolute inset-y-0 left-0 w-[14%] rounded bg-gradient-accent" />
          </div>
          <span className="font-code text-[11px] text-faint">01:20</span>
        </div>

        {/* 入剪镜头时间线 */}
        <div className="mt-4">
          <div className="mb-2 text-xs uppercase tracking-wider text-faint">{tr("cuts.shotsInCut")}（{inCut.length}）</div>
          {inCut.length > 0 ? (
            <div className="flex gap-2.5 overflow-x-auto pb-1">
              {inCut.map((s) => (
                <div key={s.id} className="w-28 flex-shrink-0">
                  <div className="relative h-16 overflow-hidden rounded-lg border border-border bg-gradient-to-br from-elevated to-card">
                    <div className="absolute inset-0 bg-[radial-gradient(60px_60px_at_50%_40%,hsl(var(--accent)/0.22),transparent)]" />
                    <span className="absolute left-1.5 top-1.5 font-code text-[9px] text-faint">{s.code}</span>
                    <Badge variant={s.production_status === "in_cut" ? "primary" : "success"} className="absolute bottom-1 left-1 px-1.5 py-0 text-[8.5px]">
                      {lang === "zh" ? PRODUCTION_STATUS_LABEL[s.production_status] : tr(`status.${s.production_status}`)}
                    </Badge>
                  </div>
                  <div className="mt-1 truncate text-center text-[10px] text-muted-foreground">{s.title ?? s.code}</div>
                </div>
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted-foreground">{tr("cuts.noShots")}</p>
          )}
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        {/* 时间线 */}
        <Panel title={tr("cuts.timeline")}>
          <div className="flex gap-2">
            <Input placeholder={tr("cuts.tlName")} value={tlName} onChange={(e) => setTlName(e.target.value)} />
            <Button size="sm" onClick={() => createTl.mutate()} disabled={!tlName}>
              {tr("common.new")}
            </Button>
          </div>
          <div className="mt-3 space-y-1.5">
            {timelines?.map((t) => (
              <div key={t.id} className="rounded-md border border-border bg-card px-3 py-2 text-sm">{t.name}</div>
            ))}
          </div>
        </Panel>

        {/* 定剪冻结基线 */}
        <Panel title={tr("cuts.baselineFreeze")}>
          <div className="space-y-2">
            <select
              className="h-9 w-full rounded-md border border-border bg-bg px-2 text-sm"
              value={tlId}
              onChange={(e) => setTlId(e.target.value)}
            >
              <option value="">{tr("cuts.selectTl")}</option>
              {timelines?.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            <div className="flex gap-2">
              <Input placeholder={tr("cuts.cutName")} value={cutName} onChange={(e) => setCutName(e.target.value)} />
              <select
                className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
                value={cutKind}
                onChange={(e) => setCutKind(e.target.value)}
              >
                <option value="rough">{tr("cuts.rough")}</option>
                <option value="fine">{tr("cuts.fine")}</option>
                <option value="final">{tr("cuts.final")}</option>
              </select>
            </div>
            <Button
              className="w-full"
              size="sm"
              disabled={!tlId || !cutName || finalize.isPending}
              onClick={async () => {
                if (await confirm({ title: tr("cuts.finalize"), message: tr("cuts.finalizeConfirm"), confirmText: tr("cuts.finalize") })) {
                  finalize.mutate();
                }
              }}
            >
              {tr("cuts.finalize")}
            </Button>
          </div>
        </Panel>
      </div>

      {/* 基线 */}
      <Panel title={tr("cuts.baselines")}>
        <div className="space-y-2">
          {baselines?.map((bl) => (
            <div key={bl.id} className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
              <span className="font-medium">{bl.name}</span>
              <span className="truncate text-xs text-muted-foreground">{bl.note}</span>
              <span className="ml-auto flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => setA(bl.id)}>{tr("cuts.setA")}</Button>
                <Button size="sm" variant="ghost" onClick={() => setB(bl.id)}>{tr("cuts.setB")}</Button>
              </span>
            </div>
          ))}
          <div className="flex items-center gap-2 border-t border-border pt-2">
            <span className="font-code text-xs text-muted-foreground">A: {a.slice(0, 8)} · B: {b.slice(0, 8)}</span>
            <Button size="sm" disabled={!a || !b} onClick={() => compare.mutate()}>{tr("cuts.compare")}</Button>
          </div>
          {diff != null && (
            <pre className="overflow-auto rounded-md bg-bg border border-border p-2 text-xs">{JSON.stringify(diff, null, 2)}</pre>
          )}
        </div>
      </Panel>
    </div>
  );
}
