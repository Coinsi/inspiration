import { TimelineStudio } from "@/components/TimelineStudio";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/ui/panel";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { api, type Baseline, type Shot, type Timeline } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function Cuts() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr } = useI18n();
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
      const cut = await api.post<{ id: string }>(`${base}/cuts`, {
        name: cutName,
        kind: cutKind,
        timeline_id: tlId,
      });
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
      <TimelineStudio projectId={projectId!} timelines={timelines || []} shots={shots || []} />

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        {/* 时间线 */}
        <Panel title={tr("cuts.timeline")}>
          <div className="flex gap-2">
            <Input
              placeholder={tr("cuts.tlName")}
              value={tlName}
              onChange={(e) => setTlName(e.target.value)}
            />
            <Button size="sm" onClick={() => createTl.mutate()} disabled={!tlName}>
              {tr("common.new")}
            </Button>
          </div>
          <div className="mt-3 space-y-1.5">
            {timelines?.map((t) => (
              <div key={t.id} className="rounded-md border border-border bg-card px-3 py-2 text-sm">
                {t.name}
              </div>
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
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <div className="flex gap-2">
              <Input
                placeholder={tr("cuts.cutName")}
                value={cutName}
                onChange={(e) => setCutName(e.target.value)}
              />
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
                if (
                  await confirm({
                    title: tr("cuts.finalize"),
                    message: tr("cuts.finalizeConfirm"),
                    confirmText: tr("cuts.finalize"),
                  })
                ) {
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
            <div
              key={bl.id}
              className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm"
            >
              <span className="font-medium">{bl.name}</span>
              <span className="truncate text-xs text-muted-foreground">{bl.note}</span>
              <span className="ml-auto flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => setA(bl.id)}>
                  {tr("cuts.setA")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setB(bl.id)}>
                  {tr("cuts.setB")}
                </Button>
              </span>
            </div>
          ))}
          <div className="flex items-center gap-2 border-t border-border pt-2">
            <span className="font-code text-xs text-muted-foreground">
              A: {a.slice(0, 8)} · B: {b.slice(0, 8)}
            </span>
            <Button size="sm" disabled={!a || !b} onClick={() => compare.mutate()}>
              {tr("cuts.compare")}
            </Button>
          </div>
          {diff != null && (
            <pre className="overflow-auto rounded-md bg-bg border border-border p-2 text-xs">
              {JSON.stringify(diff, null, 2)}
            </pre>
          )}
        </div>
      </Panel>
    </div>
  );
}
