import "./cuts.css";
import { Film, Plus, X } from "lucide-react";
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
  const { t: tr, lang } = useI18n();
  const zh = lang === "zh";
  const [createOpen, setCreateOpen] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();

  const timelineQuery = useQuery({
    queryKey: ["timelines", projectId],
    queryFn: () => api.get<Timeline[]>(`${base}/timelines`),
  });
  const timelines = timelineQuery.data;
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
    mutationFn: () =>
      api.post(`${base}/timelines`, { name: tlName.trim(), kind: "main" }),
    onSuccess: () => {
      setTlName("");
      setCreateOpen(false);
      void qc.invalidateQueries({ queryKey: ["timelines", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
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
    <div className="cuts-page">
      <header className="cuts-page-header">
        <div>
          <h1>
            <Film className="h-5 w-5" />
            {tr("cuts.title")}
          </h1>
          <p>
            {zh
              ? "把镜头、声音与字幕，组织成完整的作品。"
              : "Bring shots, sound and captions together."}
          </p>
        </div>
        {!!timelines?.length && (
          <Button variant="outline" onClick={() => setCreateOpen(!createOpen)}>
            {createOpen ? (
              <X className="h-4 w-4" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            {zh ? "新建时间线" : "New timeline"}
          </Button>
        )}
      </header>
      {timelines?.length === 0 && !createOpen && (
        <section className="studio-empty !min-h-80">
          <Film size={36} />
          <h2 className="text-xl text-foreground">
            {zh ? "开始你的第一条成片" : "Start your first film"}
          </h2>
          <p>
            {zh
              ? "创建时间线，把已有镜头和素材编排成故事。"
              : "Create a timeline and arrange your shots."}
          </p>
          <Button onClick={() => setCreateOpen(true)}>
            {zh ? "新建时间线" : "New timeline"}
          </Button>
        </section>
      )}
      {createOpen && (
        <form
          className="cuts-create-timeline"
          onSubmit={(e) => {
            e.preventDefault();
            if (tlName.trim() && !createTl.isPending) createTl.mutate();
          }}
        >
          <label>
            {zh ? "时间线名称" : "Timeline name"}
            <Input
              aria-label={zh ? "时间线名称" : "Timeline name"}
              placeholder={tr("cuts.tlName")}
              value={tlName}
              maxLength={255}
              disabled={createTl.isPending}
              onChange={(e) => setTlName(e.target.value)}
            />
          </label>
          <Button disabled={!tlName.trim() || createTl.isPending}>
            {createTl.isPending
              ? zh
                ? "创建中…"
                : "Creating…"
              : tr("common.new")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={createTl.isPending}
            onClick={() => setCreateOpen(false)}
          >
            {zh ? "取消" : "Cancel"}
          </Button>
        </form>
      )}
      {timelineQuery.isError && (
        <div role="alert" className="cuts-load-error">
          {zh ? "时间线加载失败" : "Could not load timelines"}
          <Button variant="ghost" onClick={() => void timelineQuery.refetch()}>
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      )}
      {timelineQuery.isLoading && (
        <p role="status">{zh ? "正在读取时间线…" : "Loading timelines…"}</p>
      )}

      {/* 成片预览播放器 + 入剪时间线 */}
      {!!timelines?.length && (
        <TimelineStudio
          projectId={projectId!}
          timelines={timelines || []}
          shots={shots || []}
        />
      )}

      {(!!timelines?.length || !!baselines?.length) && (
        <details className="cuts-delivery-management">
          <summary>
            {zh ? "交付与版本对比" : "Delivery & version comparison"}
            <span>
              {zh
                ? "冻结定剪、保留基线与比较差异"
                : "Freeze cuts, preserve baselines and compare"}
            </span>
          </summary>
          <div className="cuts-delivery-grid">
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

            {/* 基线 */}
            <Panel title={tr("cuts.baselines")}>
              <div className="space-y-2">
                {baselines?.map((bl) => (
                  <div
                    key={bl.id}
                    className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm"
                  >
                    <span className="font-medium">{bl.name}</span>
                    <span className="truncate text-xs text-muted-foreground">
                      {bl.note}
                    </span>
                    <span className="ml-auto flex gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setA(bl.id)}
                      >
                        {tr("cuts.setA")}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setB(bl.id)}
                      >
                        {tr("cuts.setB")}
                      </Button>
                    </span>
                  </div>
                ))}
                <div className="flex items-center gap-2 border-t border-border pt-2">
                  <span className="font-code text-xs text-muted-foreground">
                    A: {a.slice(0, 8)} · B: {b.slice(0, 8)}
                  </span>
                  <Button
                    size="sm"
                    disabled={!a || !b}
                    onClick={() => compare.mutate()}
                  >
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
        </details>
      )}
    </div>
  );
}
