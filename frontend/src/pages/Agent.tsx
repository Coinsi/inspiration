import { ShotMedia } from "@/components/ShotMedia";
import { MediaImage } from "@/components/MediaImage";
import { ObjectPicker } from "@/components/ObjectPicker";
import SkillPicker from "@/components/SkillPicker";
import type { SkillUse } from "@/lib/skills";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Bot,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Loader2,
  Play,
  Plus,
  Send,
  ShieldCheck,
  Square,
  Wand2,
} from "lucide-react";
import { api, blobUrl, type Asset, type Shot } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";

type Target = { target_type: string; target_id: string };
type Step = {
  id: string;
  tool: string;
  arguments: Record<string, unknown>;
  message: string;
  status: string;
  result?: Record<string, unknown>;
  applied?: Record<string, unknown>;
  execution?: Record<string, unknown>;
  decision?: string;
  job_id?: string;
  note?: string;
};
type Run = {
  id: string;
  goal: string;
  engine: string;
  status: string;
  scope: Target[];
  turns: number;
  max_turns: number;
  steps?: Step[];
  result?: string;
  error?: string;
  created_at: string;
};
const status: Record<string, string> = {
  queued: "准备继续",
  thinking: "正在思考",
  running: "正在使用工具",
  waiting_review: "等待审阅",
  waiting_job: "等待生成",
  paused: "已暂停",
  canceled: "已取消",
  failed: "执行遇到问题",
  succeeded: "已结束",
};
const names: Record<string, string> = {
  "skill.load": "加载创作技能",
  "skill.read_file": "读取技能参考文件",
  "project.overview": "了解项目",
  "object.read": "读取创作对象",
  "object.propose_edit": "提出修改",
  "library.search": "查找素材片段",
  "canvas.read": "查看画布",
  "job.read": "查看任务结果",
  "generation.request": "准备生成",
};
const active = ["queued", "thinking", "running", "waiting_job"];
const input =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";

export default function Agent() {
  const { projectId } = useParams();
  const location = useLocation();
  // A second idea brought from browser history must open a fresh composer.
  return (
    <AgentWorkspace
      key={`${projectId}:${location.state?.creativeGoal ?? ""}`}
      projectId={projectId!}
    />
  );
}
export function AgentWorkspace({
  projectId,
  initialGoal,
  embedded = false,
}: {
  projectId: string;
  initialGoal?: string;
  embedded?: boolean;
}) {
  const location = useLocation();
  const incomingGoal =
    initialGoal ??
    (typeof location.state?.creativeGoal === "string"
      ? location.state.creativeGoal.slice(0, 6000)
      : "");
  const root = `/projects/${projectId}`,
    base = `${root}/agent`,
    qc = useQueryClient(),
    confirm = useConfirm();
  const [params, setParams] = useSearchParams(),
    [creating, setCreating] = useState(!!incomingGoal),
    [goal, setGoal] = useState(incomingGoal);
  const [engine, setEngine] = useState("cloud_llm"),
    [maxTurns, setMaxTurns] = useState(12),
    [scope, setScope] = useState<Target[]>([]),
    [scopeType, setScopeType] = useState("shot");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [offset, setOffset] = useState(0);
  const [selectedSkills, setSelectedSkills] = useState<SkillUse[]>([]);
  const requestKey = useRef<string | null>(null);
  const list = useQuery({
    queryKey: ["agent-runs", projectId, offset],
    queryFn: () => api.get<Run[]>(`${base}/runs?offset=${offset}`),
    refetchInterval: 3000,
  });
  const [embeddedRun, setEmbeddedRun] = useState<string | undefined>();
  const selected =
    (embedded ? embeddedRun : params.get("run")) || list.data?.[0]?.id;
  const selectRun = (id: string) =>
    embedded ? setEmbeddedRun(id) : setParams({ run: id });
  const detail = useQuery({
    queryKey: ["agent-run", projectId, selected],
    queryFn: () => api.get<Run>(`${base}/runs/${selected}`),
    enabled: !!selected && !creating,
    refetchInterval: 1500,
  });
  const assets = useQuery({
    queryKey: ["agent-assets", projectId],
    queryFn: () => api.get<Asset[]>(`${root}/assets`),
  });
  const shots = useQuery({
    queryKey: ["agent-shots", projectId],
    queryFn: () => api.get<Shot[]>(`${root}/shots`),
  });
  const scripts = useQuery({
    queryKey: ["agent-scripts", projectId],
    queryFn: () => api.get<{ id: string; title: string }[]>(`${root}/scripts`),
  });
  const objects =
    scopeType === "asset"
      ? assets.data?.map((a) => ({ id: a.id, name: a.name, context: a.code }))
      : scopeType === "script"
        ? scripts.data?.map((s) => ({
            id: s.id,
            name: s.title,
            context: "剧本",
          }))
        : shots.data?.map((s) => ({
            id: s.id,
            name: s.title || s.code,
            context: [s.code, s.scene_title, s.novel_title, s.chapter_title]
              .filter(Boolean)
              .join(" · "),
          }));
  function objectName(t: Target) {
    return t.target_type === "asset"
      ? assets.data?.find((a) => a.id === t.target_id)?.name
      : t.target_type === "script"
        ? scripts.data?.find((s) => s.id === t.target_id)?.title
        : shots.data
            ?.filter((s) => s.id === t.target_id)
            .map((s) => `${s.code} · ${s.title || "镜头"}`)[0];
  }
  async function perform(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["agent-runs", projectId] });
      await qc.invalidateQueries({ queryKey: ["agent-run", projectId] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    requestKey.current ??= crypto.randomUUID();
    const run = await api.post<Run>(`${base}/runs`, {
      goal,
      engine,
      max_turns: maxTurns,
      scope,
      skills: selectedSkills.map((s) => s.id),
      skill_versions: selectedSkills.map(({ id, revision }) => ({
        id,
        revision,
      })),
      request_key: requestKey.current,
    });
    requestKey.current = null;
    selectRun(run.id);
    setCreating(false);
    setOffset(0);
    setGoal("");
  }
  const run = detail.data;
  return (
    <div className={embedded ? "space-y-4" : "studio-page !max-w-none"}>
      {!embedded && (
        <header className="page-heading">
          <div>
            <div className="mb-2 text-xs tracking-widest text-muted-foreground">
              CREATIVE ASSISTANT
            </div>
            <h1 className="flex items-center gap-2">
              <Bot size={24} />
              创作助理
            </h1>
            <p>给出目标，让 AI 读取项目、使用工具，并根据实际结果继续创作。</p>
          </div>
          <Button variant="outline" onClick={() => setCreating(true)}>
            <Plus size={16} />
            新的创作任务
          </Button>
        </header>
      )}
      {embedded && (
        <p className="text-sm text-muted-foreground">
          在这里选定允许修改的对象，开始后会直接显示执行与审阅结果。
          <Link
            className="ml-2 text-primary"
            to={`${base}${embeddedRun ? `?run=${embeddedRun}` : ""}`}
          >
            查看完整任务记录 →
          </Link>
          {!creating && (
            <Button
              className="ml-2"
              size="sm"
              variant="outline"
              onClick={() => {
                setCreating(true);
                requestKey.current = null;
              }}
            >
              新的创作任务
            </Button>
          )}
        </p>
      )}
      {(error || list.error) && (
        <p
          role="alert"
          className="rounded-xl border border-danger/30 bg-danger/10 p-3 text-sm text-danger"
        >
          {error || list.error?.message}
        </p>
      )}
      <div
        className={
          embedded
            ? "min-w-0"
            : "grid min-w-0 gap-5 xl:grid-cols-[240px_minmax(0,1fr)]"
        }
      >
        {!embedded && (
          <aside className="min-w-0 rounded-xl border border-border bg-card p-3 xl:max-h-[calc(100dvh-225px)] xl:overflow-auto">
            <h2 className="px-2 py-2 text-xs text-muted-foreground">
              任务记录
            </h2>
            {list.isLoading ? (
              <p className="p-3 text-sm">载入中…</p>
            ) : !list.data?.length ? (
              <p className="p-3 text-sm text-muted-foreground">
                每次创作过程都会保留在这里。
              </p>
            ) : (
              <div className="flex gap-2 overflow-x-auto xl:block xl:space-y-2">
                {list.data?.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => {
                      selectRun(r.id);
                      setCreating(false);
                    }}
                    className={`w-52 shrink-0 rounded-lg border p-3 text-left xl:w-full ${selected === r.id && !creating ? "border-primary/40 bg-primary/10" : "border-transparent hover:bg-elevated"}`}
                  >
                    <div className="line-clamp-2 text-sm leading-6">
                      {r.goal}
                    </div>
                    <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span>{status[r.status]}</span>
                      <span>{r.turns} 轮</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
            <div className="mt-3 flex justify-between">
              <Button
                variant="ghost"
                size="sm"
                disabled={!offset}
                onClick={() => setOffset((x) => Math.max(0, x - 30))}
              >
                较新
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={list.data?.length !== 30}
                onClick={() => setOffset((x) => x + 30)}
              >
                较早
              </Button>
            </div>
          </aside>
        )}
        <section className="min-w-0">
          {creating || (!selected && !list.isLoading) ? (
            <div className="mx-auto max-w-3xl rounded-2xl border border-border bg-card p-5 md:p-8 space-y-5">
              <div className="flex gap-3">
                <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
                  <Wand2 size={24} />
                </div>
                <div>
                  <h2 className="text-lg font-semibold">这次想完成什么？</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    例如：检查海边这组镜头的描述，让光线和人物动作保持连贯。
                  </p>
                </div>
              </div>
              <label className="block space-y-2 text-sm">
                <span>创作目标</span>
                <textarea
                  aria-label="创作目标"
                  className={`${input} min-h-32 resize-y`}
                  value={goal}
                  maxLength={8000}
                  onChange={(e) => {
                    setGoal(e.target.value);
                    requestKey.current = null;
                  }}
                  placeholder="描述需要完成的内容、风格与约束…"
                />
              </label>
              <div className="rounded-xl border border-border p-4 space-y-3">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <ShieldCheck size={16} />
                  允许提出修改的对象
                </div>
                <p className="text-xs text-muted-foreground">
                  未选择对象时只分析项目。所有修改和生成仍会等待你审阅。
                </p>
                <div className="flex flex-wrap gap-2">
                  <select
                    aria-label="对象类型"
                    className={`${input} !w-auto`}
                    value={scopeType}
                    onChange={(e) => {
                      setScopeType(e.target.value);
                    }}
                  >
                    <option value="shot">镜头</option>
                    <option value="asset">角色与资产</option>
                    <option value="script">剧本</option>
                  </select>
                  <ObjectPicker
                    key={scopeType}
                    renderPreview={(id) => {
                      const shot = shots.data?.find((s) => s.id === id);
                      const asset = assets.data?.find((a) => a.id === id);
                      return scopeType === "shot" && shot ? (
                        <ShotMedia
                          projectId={projectId}
                          shot={shot}
                          thumbnail
                        />
                      ) : scopeType === "asset" && asset ? (
                        <MediaImage
                          src={
                            asset.representative_blob_hash
                              ? blobUrl(
                                  projectId,
                                  asset.representative_blob_hash,
                                )
                              : null
                          }
                          alt={asset.name}
                          className="[&_span]:sr-only [&_svg]:h-4 [&_svg]:w-4"
                        />
                      ) : (
                        <span className="grid place-items-center h-full text-xl">
                          T
                        </span>
                      );
                    }}
                    onRetry={() => {
                      void shots.refetch();
                      void assets.refetch();
                      void scripts.refetch();
                    }}
                    items={objects ?? []}
                    selected={scope
                      .filter((t) => t.target_type === scopeType)
                      .map((t) => t.target_id)}
                    disabled={busy || scope.length >= 12}
                    loading={
                      scopeType === "shot"
                        ? shots.isPending
                        : scopeType === "asset"
                          ? assets.isPending
                          : scripts.isPending
                    }
                    error={
                      scopeType === "shot"
                        ? shots.isError
                        : scopeType === "asset"
                          ? assets.isError
                          : scripts.isError
                    }
                    onSelect={(id) => {
                      setScope((s) =>
                        s.some((t) => t.target_id === id) || s.length >= 12
                          ? s
                          : [...s, { target_type: scopeType, target_id: id }],
                      );
                      requestKey.current = null;
                    }}
                  />
                </div>
                {(assets.error || shots.error || scripts.error) && (
                  <p role="alert" className="text-xs text-danger">
                    部分对象加载失败，可在选择窗口中重试。
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  {scope.map((t) => (
                    <button
                      key={t.target_id}
                      title="点击移出范围"
                      onClick={() => {
                        setScope((s) =>
                          s.filter((a) => a.target_id !== t.target_id),
                        );
                        requestKey.current = null;
                      }}
                      className="rounded-full bg-primary/10 px-3 py-1 text-xs text-primary"
                    >
                      {objectName(t) || "项目对象"} ×
                    </button>
                  ))}
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span>本次可用技能（最多3项）</span>
                  <Link className="text-primary text-xs" to={`${root}/skills`}>
                    管理技能
                  </Link>
                </div>
                <SkillPicker
                  projectId={projectId!}
                  value={selectedSkills}
                  max={3}
                  disabled={busy}
                  onChange={(value) => {
                    setSelectedSkills(value);
                    requestKey.current = null;
                  }}
                />
              </div>
              <details className="text-sm">
                <summary className="cursor-pointer text-muted-foreground">
                  高级运行选项
                </summary>
                <div className="flex flex-wrap gap-3 mt-3">
                  <label className="text-xs space-y-2 flex-1">
                    <span>运行方式</span>
                    <select
                      aria-label="运行方式"
                      className={input}
                      value={engine}
                      onChange={(e) => {
                        setEngine(e.target.value);
                        requestKey.current = null;
                      }}
                    >
                      <option value="cloud_llm">项目文本模型</option>
                      <option value="mock">离线机制测试</option>
                    </select>
                  </label>
                  <label className="text-xs space-y-2 w-32">
                    <span>最多执行轮数</span>
                    <input
                      aria-label="最多执行轮数"
                      className={input}
                      type="number"
                      min={1}
                      max={24}
                      value={maxTurns}
                      onChange={(e) => {
                        setMaxTurns(
                          Math.max(
                            1,
                            Math.min(24, Number(e.target.value) || 1),
                          ),
                        );
                        requestKey.current = null;
                      }}
                    />
                  </label>
                </div>
              </details>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">
                  使用已配置的模型，最多 {maxTurns} 轮；可随时暂停。
                </p>
                <Button
                  disabled={busy || !goal.trim()}
                  onClick={() => void perform(start)}
                >
                  {busy ? (
                    <Loader2 className="animate-spin" size={16} />
                  ) : (
                    <Send size={16} />
                  )}
                  开始创作
                </Button>
              </div>
            </div>
          ) : detail.error ? (
            <p role="alert" className="text-danger">
              {detail.error.message}
            </p>
          ) : !run ? (
            <p className="p-8 text-muted-foreground">正在载入创作过程…</p>
          ) : (
            <div className="mx-auto max-w-4xl space-y-5">
              <div className="rounded-2xl border border-border bg-card p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="whitespace-pre-wrap break-words text-lg font-medium">
                      {run.goal}
                    </h2>
                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1.5">
                        {active.includes(run.status) ? (
                          <Loader2 className="animate-spin" size={14} />
                        ) : (
                          <Clock3 size={14} />
                        )}{" "}
                        {status[run.status]}
                      </span>
                      <span>
                        {run.turns} / {run.max_turns} 轮
                      </span>
                      <span>
                        {run.engine === "mock"
                          ? "离线机制测试"
                          : "项目文本模型"}
                      </span>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {active.includes(run.status) && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          void perform(async () => {
                            await api.post(`${base}/runs/${run.id}/control`, {
                              action: "pause",
                            });
                          })
                        }
                      >
                        暂停
                      </Button>
                    )}
                    {run.engine !== "external" &&
                      ["paused", "failed"].includes(run.status) && (
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              if (
                                !(await confirm({
                                  title: "继续创作",
                                  message:
                                    run.turns >= run.max_turns
                                      ? "本次追加 6 轮执行，可能产生新的模型费用。"
                                      : "继续会再次调用模型；此前中断的请求可能已经计费。",
                                }))
                              )
                                return;
                              await api.post(`${base}/runs/${run.id}/control`, {
                                action: "resume",
                                additional_turns:
                                  run.turns >= run.max_turns ? 6 : 0,
                              });
                            })
                          }
                        >
                          <Play size={14} />
                          继续
                        </Button>
                      )}
                    {!["succeeded", "canceled", "failed"].includes(
                      run.status,
                    ) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          void perform(async () => {
                            await api.post(`${base}/runs/${run.id}/control`, {
                              action: "cancel",
                            });
                          })
                        }
                      >
                        <Square size={13} />
                        取消
                      </Button>
                    )}
                  </div>
                </div>
                {run.error && (
                  <p
                    role="alert"
                    className="mt-4 rounded-lg bg-danger/10 p-3 text-sm text-danger"
                  >
                    {run.error}
                  </p>
                )}
              </div>
              {run.steps?.map((step, index) => (
                <StepCard
                  key={step.id}
                  {...{ step, index, root, run, busy }}
                  decide={(action, note) =>
                    perform(async () => {
                      await api.post(
                        `${base}/runs/${run.id}/steps/${step.id}/decision`,
                        { action, note },
                      );
                    })
                  }
                />
              ))}
              {run.result && (
                <div className="rounded-2xl border border-success/30 bg-success/5 p-5">
                  <h3 className="mb-3 flex items-center gap-2 font-medium">
                    <CheckCircle2 size={18} className="text-success" />
                    本次结果
                  </h3>
                  <p className="whitespace-pre-wrap break-words text-sm leading-7">
                    {run.result}
                  </p>
                </div>
              )}
              {!run.steps?.length && active.includes(run.status) && (
                <div className="p-10 text-center text-sm text-muted-foreground">
                  正在读取目标和可用工具，执行过程将在这里逐步出现。
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
function content(value: unknown): string {
  if (value == null) return "（空）";
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value
      .map((v) =>
        typeof v === "object" && v && "text" in v ? String(v.text) : content(v),
      )
      .join("\n");
  if (typeof value === "object")
    return Object.entries(value as Record<string, unknown>)
      .map(
        ([k, v]) =>
          `${({ title: "标题", description: "描述", name: "名称", summary: "简介", blocks: "正文", fields: "内容", content: "内容" } as Record<string, string>)[k] || k}\n${content(v)}`,
      )
      .join("\n\n");
  return String(value);
}
function StepCard({
  step,
  index,
  root,
  run,
  busy,
  decide,
}: {
  step: Step;
  index: number;
  root: string;
  run: Run;
  busy: boolean;
  decide: (action: string, note: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const review =
    step.status === "review" &&
    run.status === "waiting_review" &&
    !step.decision;
  return (
    <article
      className={`rounded-xl border bg-card p-4 md:p-5 ${review ? "border-primary/50" : "border-border"}`}
    >
      <div className="flex items-center gap-3">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-elevated text-xs text-muted-foreground">
          {index + 1}
        </span>
        <h3 className="text-sm font-medium">{names[step.tool] || step.tool}</h3>
        <span className="ml-auto text-xs text-muted-foreground">
          {step.decision === "approve"
            ? "已批准"
            : step.decision === "reject"
              ? "已拒绝"
              : step.status === "succeeded"
                ? "已完成"
                : step.status === "failed"
                  ? "未完成"
                  : step.status === "review"
                    ? "请审阅"
                    : step.status === "waiting"
                      ? "生成中"
                      : "准备执行"}
        </span>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-6">
        {step.message}
      </p>
      {!!step.result?.error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {String(step.result.error)}
        </p>
      )}
      {step.tool === "object.propose_edit" && !!step.result?.before && (
        <>
          <p className="mt-3 text-sm font-medium">
            {String(step.result.summary || "修改提议")}
          </p>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="min-w-0 rounded-lg bg-elevated p-3">
              <h4 className="mb-2 text-xs text-muted-foreground">修改前</h4>
              <p className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-sm leading-6">
                {content(step.result.before)}
              </p>
            </div>
            <div className="min-w-0 rounded-lg border border-primary/20 bg-primary/5 p-3">
              <h4 className="mb-2 text-xs text-primary">修改后</h4>
              <p className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-sm leading-6">
                {content(step.result.after)}
              </p>
            </div>
          </div>
        </>
      )}
      {step.tool === "generation.request" &&
        step.result?.estimated_points != null && (
          <div className="mt-3 rounded-lg bg-primary/5 p-3 text-sm">
            <p>
              供应商：{String(step.result.provider)} ·{" "}
              {String(step.result.count)} 个候选 · 预计{" "}
              {String(step.result.estimated_points)} 点
            </p>
            <p className="mt-2 whitespace-pre-wrap">
              {String(step.result.prompt)}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              取消结果接收不一定能撤销已经发送到供应商的收费请求。
            </p>
          </div>
        )}
      {review && (
        <div className="mt-4 space-y-3 border-t border-border pt-4">
          <input
            aria-label={`审阅备注 ${index + 1}`}
            className={input}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="给助理的补充说明（可选）"
          />
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={busy}
              onClick={() => void decide("approve", note)}
            >
              <ShieldCheck size={16} />
              {step.tool === "generation.request"
                ? "确认生成并继续"
                : "应用修改并继续"}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void decide("reject", note)}
            >
              拒绝并继续
            </Button>
          </div>
        </div>
      )}
      {step.job_id && (
        <Link
          to={`${root}/tasks?job=${step.job_id}`}
          className="mt-3 inline-flex items-center text-sm text-primary"
        >
          查看生成任务与候选
          <ChevronRight size={14} />
        </Link>
      )}
      {step.result && !review && (
        <details className="mt-3 text-xs text-muted-foreground">
          <summary className="cursor-pointer">查看工具返回记录</summary>
          <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-elevated p-3">
            {JSON.stringify(
              step.applied || step.execution || step.result,
              null,
              2,
            )}
          </pre>
        </details>
      )}
    </article>
  );
}
