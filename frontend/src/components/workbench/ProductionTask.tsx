import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  Clock3,
  Loader2,
  MessageSquare,
  Pause,
  Pencil,
  Play,
  RotateCcw,
  Settings2,
  Square,
  X,
} from "lucide-react";
import { api, ApiError, type ProviderConfig } from "@/lib/api";
import { SelectField } from "../ui/select-field";
import "./production-task.css";

type Review = {
  tool: string;
  arguments?: {
    summary?: string;
    target_type?: string;
    [key: string]: unknown;
  };
  result?: {
    summary?: string;
    before?: Record<string, unknown>;
    after?: Record<string, unknown>;
  };
};
type Task = {
  id: string;
  parent_id?: string | null;
  key: string;
  role: string;
  goal: string;
  status: string;
  error?: { message?: string; retryable?: boolean; recovery?: string };
  result?: { message?: string; review?: Review };
  state?: {
    question?: string;
    guidance_confirmation?: {
      guidance_id: string;
      task_ids: string[];
      expected_revision: number;
    };
    review_id?: string;
    review?: { review?: Review };
    waiting_candidate?: {
      change_id: string;
      work_id: string;
      message?: string;
    };
    [key: string]: unknown;
  };
};

export type ProductionRun = {
  id: string;
  work_id?: string | null;
  goal: string;
  engine: string;
  model_provider?: string | null;
  status: string;
  revision: number;
  event_sequence: number;
  policy: { budget_points?: number; max_parallel?: number; [key: string]: unknown };
  resources?: Record<string, { measured: number; reserved_or_unknown: number; limit: number; remaining: number }>;
  plan?: {
    summary?: string;
    tasks?: { key: string; goal: string; role?: string }[];
  };
  tasks: Task[];
  error?: { message?: string };
  result?: { message?: string };
};

export type ProductionControl = {
  request_key: string;
  expected_revision: number;
  action:
    | "pause"
    | "resume"
    | "cancel"
    | "retry"
    | "resume_receipt"
    | "steer"
    | "approve_plan"
    | "approve_tool"
    | "reject_tool"
    | "reply"
    | "update_limits";
  task_ids: string[];
  message: string;
  budget_points?: number;
  limits?: Record<string, number>;
  review_id?: string;
};

type ProductionRequest = ProductionControl & {
  endpoint?: "guidance" | "confirm_guidance" | "kernel";
  guidance_id?: string;
  engine?: string;
  provider?: string | null;
};

const resourceFields = [
  {key:"max_model_tokens",label:"文字模型用量上限（tokens）",value:4000000,min:0,max:100000000,factor:1},
  {key:"max_output_storage_bytes",label:"生成文件存储上限（GiB）",value:2,min:0,max:100,factor:1073741824},
  {key:"max_media_transfer_bytes",label:"媒体传输上限（GiB）",value:4,min:0,max:200,factor:1073741824},
  {key:"max_total_actions",label:"总执行步数上限",value:240,min:1,max:2000,factor:1},
  {key:"max_actions",label:"单个步骤的执行次数上限",value:40,min:1,max:200,factor:1},
  {key:"max_active_seconds",label:"累计执行时间上限（分钟）",value:120,min:1,max:1440,factor:60},
  {key:"max_preview_jobs",label:"预览渲染次数上限",value:12,min:0,max:100,factor:1},
];
function resourceNumber(value:number, unit:string) {
  if(unit === "model_tokens") return value.toLocaleString();
  if(value >= 1073741824) return `${(value/1073741824).toFixed(2)} GiB`;
  if(value >= 1048576) return `${(value/1048576).toFixed(1)} MiB`;
  if(value >= 1024) return `${(value/1024).toFixed(1)} KiB`;
  return `${value} B`;
}
function ResourceSettings({run,pending,onApply}:{run:ProductionRun;pending:boolean;onApply:(limits:Record<string,number>)=>void}) {
  const [open,setOpen] = useState(false);
  const [values,setValues] = useState<Record<string,string>>({});
  const policyKey = JSON.stringify(resourceFields.map(field=>run.policy[field.key]));
  useEffect(()=>{
    setValues(Object.fromEntries(resourceFields.map(field=>[field.key,String(typeof run.policy[field.key] === "number" ? (run.policy[field.key] as number)/field.factor : field.value)])));
  },[run.id,policyKey]);
  const allowed = ["paused","failed","needs_attention","needs_plan_approval"].includes(run.status);
  const valid = resourceFields.every(field=>{
    const value=Number(values[field.key]);
    return values[field.key]?.trim() && Number.isFinite(value) && value>=field.min && value<=field.max && Number.isInteger(value*field.factor);
  });
  return <div className="production-resources">
    <button type="button" className="production-text-button" aria-expanded={open} onClick={()=>setOpen(!open)}>
      {open?<ChevronUp size={14}/>:<ChevronDown size={14}/>}用量与资源设置
    </button>
    {open && <div className="production-resource-body">
      <div className="production-resource-summary">
        {Object.entries(run.resources||{}).map(([unit,state])=><div key={unit}>
          <strong>{{model_tokens:"文字模型",output_storage_bytes:"生成文件",media_transfer_bytes:"媒体传输"}[unit] || unit}</strong>
          <span>已测得 {resourceNumber(state.measured,unit)} / 上限 {resourceNumber(state.limit,unit)}</span>
          <small>预留或尚未计量 {resourceNumber(state.reserved_or_unknown,unit)}</small>
        </div>)}
      </div>
      <p>预留量会在实际用量返回后结算；尚未计量的部分仍占用额度。生成预算：{run.policy.budget_points || 0} 点。</p>
      {allowed ? <form onSubmit={event=>{event.preventDefault();if(valid&&!pending)onApply(Object.fromEntries(resourceFields.map(field=>[field.key,Number(values[field.key])*field.factor])));}}>
        <div className="production-resource-fields">{resourceFields.map(field=><label key={field.key}>{field.label}
          <input type="number" min={field.min} max={field.max} step={field.factor===1?1:"any"} value={values[field.key]||""} disabled={pending}
            onChange={event=>setValues({...values,[field.key]:event.target.value})}/>
        </label>)}</div>
        <small>仅调整本次制作的资源上限，付费生成预算与采用权限保持不变。暂停中的任务需要点击「继续」。</small>
        <button type="submit" disabled={pending||!valid}>保存资源上限</button>
      </form> : <small>{["succeeded","canceled"].includes(run.status)?"任务已结束，保留以上用量记录。":"暂停制作后可以调整资源上限。"}</small>}
    </div>}
  </div>;
}

const fieldLabels: Record<string, string> = {
  fields: "内容",
  title: "名称",
  name: "名称",
  description: "描述",
  summary: "简介",
  content: "正文",
  text: "文字",
  blocks: "剧本段落",
  content_blocks: "剧本段落",
  prompt: "画面描述",
  dialogue: "对白",
  action: "动作",
  notes: "备注",
  setting: "设定",
  visual_description: "画面描述",
  negative_prompt: "排除内容",
};

function differences(
  before: unknown,
  after: unknown,
  path: string[] = [],
): {
  path: string;
  before: unknown;
  after: unknown;
}[] {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (
    before !== null &&
    after !== null &&
    typeof before === "object" &&
    typeof after === "object"
  ) {
    const a = before as Record<string, unknown>;
    const b = after as Record<string, unknown>;
    return [...new Set([...Object.keys(a), ...Object.keys(b)])].flatMap((key) =>
      differences(a[key], b[key], [
        ...path,
        fieldLabels[key] ||
          (/^\d+$/.test(key) ? `第 ${Number(key) + 1} 项` : key),
      ]),
    );
  }
  return [{ path: path.join(" · ") || "内容", before, after }];
}

function readable(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未填写";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(readable).join("\n");
  if (typeof value === "object")
    return Object.entries(value)
      .map(([key, item]) => `${fieldLabels[key] || key}：${readable(item)}`)
      .join("\n");
  return String(value);
}

function ProposalReview({
  task,
  projectId,
  workId,
  pending,
  onDecide,
}: {
  task: Task;
  projectId: string;
  workId?: string | null;
  pending: boolean;
  onDecide: (action: "approve_tool" | "reject_tool", id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(6);
  // Task.result retains the complete review even when the context receipt was trimmed.
  const review = task.result?.review || task.state?.review?.review;
  const changes = useMemo(
    () => differences(review?.result?.before, review?.result?.after),
    [review],
  );
  if (review?.tool !== "object.propose_edit")
    return (
      <div className="production-review-link">
        <p>工程候选已保留，可以在作品中预览和审阅。</p>
        {workId && (
          <a
            href={`/projects/${projectId}/works/${workId}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            打开作品审阅 ↗
          </a>
        )}
      </div>
    );
  return (
    <div className="production-review">
      <button
        type="button"
        className="production-text-button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        {open ? "收起修改对比" : "查看这次修改"}
      </button>
      {open && (
        <>
          <p>
            {review.result?.summary ||
              review.arguments?.summary ||
              "请确认以下修改后再采用。"}
          </p>
          <div className="production-diff-list">
            {changes.slice(0, count).map((change, index) => (
              <div className="production-diff" key={`${change.path}-${index}`}>
                <strong>{change.path}</strong>
                <div>
                  <span>原内容</span>
                  <p>{readable(change.before)}</p>
                </div>
                <div>
                  <span>修改后</span>
                  <p>{readable(change.after)}</p>
                </div>
              </div>
            ))}
          </div>
          {changes.length > count && (
            <button
              type="button"
              className="production-text-button"
              onClick={() => setCount(count + 12)}
            >
              继续查看 {changes.length - count} 处修改
            </button>
          )}
          {!changes.length && <p>暂时无法展示修改差异，请先核对任务记录。</p>}
          <div className="production-review-actions">
            <button
              type="button"
              className="production-primary"
              disabled={pending || !task.state?.review_id || !changes.length}
              onClick={() =>
                task.state?.review_id &&
                onDecide("approve_tool", task.state.review_id)
              }
            >
              <Check size={14} />
              采用这次修改
            </button>
            <button
              type="button"
              disabled={pending || !task.state?.review_id}
              onClick={() =>
                task.state?.review_id &&
                onDecide("reject_tool", task.state.review_id)
              }
            >
              不采用
            </button>
          </div>
        </>
      )}
    </div>
  );
}

const labels: Record<string, string> = {
  queued: "准备中",
  running: "制作中",
  paused: "已暂停",
  succeeded: "已完成",
  failed: "需要处理",
  canceled: "已停止",
  needs_plan_approval: "等待确认方案",
  needs_attention: "需要你的意见",
  waiting_input: "等待回复",
  waiting_review: "等待审阅",
  waiting_candidate: "等待采用候选",
  waiting_children: "协同制作中",
  waiting_plan: "等待确认",
  waiting_job: "等待生成结果",
  waiting_transcription: "正在转写音轨",
  validating_job: "检查媒体文件",
  uncertain: "待核对执行结果",
  blocked: "等待前序步骤",
  superseded: "已调整",
};

function StatusIcon({ status }: { status: string }) {
  if (status === "succeeded") return <Check size={16} />;
  if (status === "running" || status === "validating_job")
    return <Loader2 size={16} className="production-spin" />;
  if (status === "paused") return <Pause size={16} />;
  if (["failed", "uncertain", "needs_attention"].includes(status))
    return <AlertCircle size={16} />;
  if (status.startsWith("waiting") || status === "queued")
    return <Clock3 size={16} />;
  return <Circle size={16} />;
}

export function ProductionTask({
  projectId,
  runId,
  onGuide,
  onUpdated,
}: {
  projectId: string;
  runId: string;
  /** Notification after a guidance control has been saved successfully. */
  onGuide?: (control: ProductionControl) => void;
  onUpdated?: (run: ProductionRun) => void;
}) {
  const qc = useQueryClient();
  const key = ["production-run", projectId, runId];
  const base = `/projects/${projectId}/production/runs/${runId}`;
  const [expanded, setExpanded] = useState(false);
  const [visibleCount, setVisibleCount] = useState(24);
  const [planExpanded, setPlanExpanded] = useState(false);
  const [form, setForm] = useState<{
    action: "steer" | "reply";
    task?: Task;
  } | null>(null);
  const [message, setMessage] = useState("");
  const [budget, setBudget] = useState("0");
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [nextProvider, setNextProvider] = useState("");
  const [modelNotice, setModelNotice] = useState("");
  const [resourceNotice,setResourceNotice] = useState("");
  const lastRequest = useRef<ProductionRequest | null>(null);
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.get<ProductionRun>(base),
    refetchInterval: (q) =>
      ["succeeded", "canceled"].includes(q.state.data?.status || "")
        ? false
        : 2500,
    retry: (count, error) =>
      !(
        error instanceof ApiError &&
        ["FORBIDDEN", "UNAUTHORIZED", "NOT_FOUND"].includes(error.code)
      ) && count < 2,
  });
  const run = query.data;
  const providers = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () =>
      api.get<ProviderConfig[]>(`/projects/${projectId}/providers`),
    enabled:
      run?.status === "paused" &&
      run.engine === "cloud_llm" &&
      modelSettingsOpen,
  });
  const textModels = (providers.data || []).filter(
    (model) =>
      model.kind === "llm" &&
      model.enabled &&
      !model.config.hidden_from_catalog,
  );
  useEffect(() => {
    setNextProvider(run?.model_provider || "");
  }, [runId, run?.model_provider]);
  useEffect(() => {
    setBudget(String(run?.policy.budget_points || 0));
  }, [runId, run?.policy.budget_points]);
  useEffect(() => {
    setForm(null);
    setMessage("");
    setExpanded(false);
    setPlanExpanded(false);
    setModelSettingsOpen(false);
    setModelNotice("");
    setResourceNotice("");
    lastRequest.current = null;
  }, [runId]);

  const update = useMutation({
    mutationFn: (control: ProductionRequest) => {
      if (control.endpoint === "kernel")
        return api.post<ProductionRun>(`${base}/kernel`, {
          request_key: control.request_key,
          expected_revision: control.expected_revision,
          engine: control.engine,
          provider: control.provider,
        });
      if (control.endpoint === "confirm_guidance")
        return api.post<ProductionRun>(
          `${base}/guidance/${control.guidance_id}/confirm`,
          {
            request_key: control.request_key,
            expected_revision: control.expected_revision,
          },
        );
      if (control.endpoint === "guidance")
        return api.post<ProductionRun>(`${base}/guidance`, {
          request_key: control.request_key,
          expected_revision: control.expected_revision,
          text: control.message,
          task_ids: control.task_ids,
          references: [],
        });
      return api.post<ProductionRun>(`${base}/control`, control);
    },
    onSuccess: (result, control) => {
      qc.setQueryData(key, result);
      lastRequest.current = null;
      if (control.action === "update_limits") {
        setResourceNotice("资源上限已更新，已有成果保留。暂停中的任务可点击「继续」。");
      }
      if (control.endpoint === "kernel") {
        setModelNotice(
          "模型配置已更新，已完成的步骤和成果保留。点击「继续」后使用新配置。",
        );
        setModelSettingsOpen(false);
      }
      if (control.action === "steer" || control.action === "reply") {
        setForm(null);
        setMessage("");
      }
      if (control.action === "steer" && control.endpoint !== "confirm_guidance")
        onGuide?.(control);
      onUpdated?.(result);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
        lastRequest.current = null;
        void query.refetch();
      }
    },
  });

  const tasks = useMemo(
    () => (run?.tasks || []).filter((t) => t.status !== "superseded"),
    [run?.tasks],
  );
  const actionable = tasks.filter((t) =>
    [
      "failed",
      "uncertain",
      "waiting_input",
      "waiting_review",
      "waiting_candidate",
    ].includes(t.status),
  );
  const ordered = [...tasks].sort((a, b) => {
    const rank = (t: Task) =>
      actionable.includes(t)
        ? 0
        : t.status === "running"
          ? 1
          : t.status === "succeeded"
            ? 3
            : 2;
    return rank(a) - rank(b);
  });
  const displayed = ordered.slice(0, expanded ? visibleCount : 5);
  const completed = tasks.filter((t) => t.status === "succeeded").length;
  const running = tasks.filter((t) => t.status === "running").length;
  const ended = run?.status === "succeeded" || run?.status === "canceled";
  const budgetValue = Number(budget);
  const validBudget =
    budget.trim() !== "" &&
    Number.isInteger(budgetValue) &&
    budgetValue >= 0 &&
    budgetValue <= 1000000;

  const send = (
    action: ProductionControl["action"],
    taskIds: string[] = [],
    text = "",
    reviewId?: string,
    guidanceId?: string,
  ) => {
    if (!run || update.isPending) return;
    const impactReply =
      action === "reply" &&
      tasks.some((task) => taskIds.includes(task.id) && task.role === "impact");
    const request: ProductionRequest = {
      request_key: crypto.randomUUID(),
      expected_revision: run.revision,
      action,
      task_ids: taskIds,
      message: text,
      ...(reviewId ? { review_id: reviewId } : {}),
      ...(action === "approve_plan" ? { budget_points: budgetValue } : {}),
      ...(guidanceId
        ? { endpoint: "confirm_guidance" as const, guidance_id: guidanceId }
        : impactReply
          ? { endpoint: "guidance" as const, task_ids: [] }
          : action === "steer"
            ? { endpoint: "guidance" as const }
            : {}),
    };
    lastRequest.current = request;
    update.mutate(request);
  };
  const openForm = (action: "steer" | "reply", task?: Task) => {
    setForm({ action, task });
    setMessage("");
    update.reset();
  };
  const applyModel = () => {
    if (
      !run ||
      update.isPending ||
      run.status !== "paused" ||
      run.engine !== "cloud_llm"
    )
      return;
    const request: ProductionRequest = {
      request_key: crypto.randomUUID(),
      expected_revision: run.revision,
      endpoint: "kernel",
      action: "resume",
      engine: run.engine,
      provider: nextProvider || null,
      task_ids: [],
      message: "",
    };
    lastRequest.current = request;
    update.mutate(request);
  };

  if (!run)
    return (
      <section className="production-task" aria-label="制作进度">
        {query.isError ? (
          <div className="production-notice" role="alert">
            <AlertCircle size={17} />
            <span>{query.error.message || "暂时无法读取制作进度"}</span>
            <button type="button" onClick={() => void query.refetch()}>
              <RotateCcw size={14} />
              重新加载
            </button>
          </div>
        ) : (
          <div className="production-heading" role="status">
            <Loader2 size={18} className="production-spin" />
            正在读取制作进度…
          </div>
        )}
      </section>
    );

  return (
    <section
      className="production-task"
      aria-label="制作进度"
      data-status={run.status}
      aria-busy={update.isPending}
    >
      <header className="production-heading">
        <span className="production-status-icon">
          <StatusIcon status={run.status} />
        </span>
        <div>
          <strong>{labels[run.status] || "制作任务"}</strong>
          <p>{run.goal}</p>
        </div>
        <div className="production-controls">
          {run.status === "paused" ? (
            <button
              type="button"
              disabled={update.isPending}
              onClick={() => send("resume")}
            >
              <Play size={14} />
              继续
            </button>
          ) : (
            !ended &&
            run.status !== "failed" && (
              <button
                type="button"
                disabled={update.isPending}
                onClick={() => send("pause")}
              >
                <Pause size={14} />
                暂停
              </button>
            )
          )}
          {!ended && (
            <button
              type="button"
              disabled={update.isPending}
              onClick={() => send("cancel")}
            >
              <Square size={13} />
              停止
            </button>
          )}
        </div>
      </header>

      {run.status === "paused" && (
        <div className="production-model-settings">
          {run.engine === "cloud_llm" ? (
            <>
              <button
                type="button"
                disabled={update.isPending}
                aria-expanded={modelSettingsOpen}
                onClick={() => setModelSettingsOpen(!modelSettingsOpen)}
              >
                <Settings2 size={14} />
                模型设置
              </button>
              {modelSettingsOpen && (
                <>
                  <SelectField
                    label="后续步骤使用的文字模型"
                    value={nextProvider}
                    disabled={update.isPending || providers.isPending}
                    onChange={setNextProvider}
                    options={[
                      { value: "", label: "使用项目默认文字模型" },
                      ...textModels.map((model) => ({
                        value: model.provider_name,
                        label:
                          model.display_name ||
                          (typeof model.config.model === "string"
                            ? model.config.model
                            : "") ||
                          model.provider_name,
                        description: model.channel_name || undefined,
                      })),
                      ...(nextProvider &&
                      !textModels.some(
                        (model) => model.provider_name === nextProvider,
                      )
                        ? [
                            {
                              value: nextProvider,
                              label: "原模型暂不可用，请重新选择",
                              disabled: true,
                            },
                          ]
                        : []),
                    ]}
                  />
                  <p>
                    可以应用刚保存的模型能力，或更换后续步骤使用的模型。已完成步骤和成果会保留；应用后仍保持暂停。
                  </p>
                  {providers.error && (
                    <p role="alert" className="production-error">
                      模型列表读取失败。
                      <button
                        type="button"
                        onClick={() => void providers.refetch()}
                      >
                        重新读取
                      </button>
                    </p>
                  )}
                  <button
                    type="button"
                    className="production-primary"
                    disabled={
                      update.isPending ||
                      providers.isPending ||
                      !!providers.error ||
                      (!!nextProvider &&
                        !textModels.some(
                          (model) => model.provider_name === nextProvider,
                        ))
                    }
                    onClick={applyModel}
                  >
                    应用最新模型配置
                  </button>
                </>
              )}
            </>
          ) : (
            <p>
              当前任务使用独立执行器，暂停期间会保留原配置和已完成的步骤，继续时不会自动更换。
            </p>
          )}
          {modelNotice && <p role="status">{modelNotice}</p>}
        </div>
      )}

      <ResourceSettings run={run} pending={update.isPending} onApply={limits=>{
        const request:ProductionRequest={request_key:crypto.randomUUID(),expected_revision:run.revision,action:"update_limits",task_ids:[],message:"",limits};
        lastRequest.current=request;
        update.mutate(request);
      }}/>

      {resourceNotice && <p className="production-resource-feedback" role="status">{resourceNotice}</p>}

      {run.status === "needs_plan_approval" && run.plan && (
        <div className="production-plan">
          <h4>这次的制作方案</h4>
          <p>{run.plan.summary}</p>
          <ol>
            {(run.plan.tasks || [])
              .slice(0, planExpanded ? undefined : 6)
              .map((step) => (
                <li key={step.key}>{step.goal}</li>
              ))}
          </ol>
          {(run.plan.tasks?.length || 0) > 6 && (
            <button
              className="production-text-button"
              type="button"
              onClick={() => setPlanExpanded(!planExpanded)}
            >
              {planExpanded
                ? "收起方案"
                : `查看完整方案（${run.plan.tasks?.length} 步）`}
            </button>
          )}
          <div className="production-approval">
            <label>
              本次生成预算上限（点）
              <input
                aria-label="本次生成预算上限"
                type="number"
                min="0"
                max="1000000"
                step="1"
                value={budget}
                disabled={update.isPending}
                onChange={(e) => setBudget(e.target.value)}
              />
            </label>
            <button
              className="production-primary"
              type="button"
              disabled={update.isPending || !validBudget}
              onClick={() => send("approve_plan")}
            >
              <Play size={15} />
              确认方案，开始制作
            </button>
          </div>
          <small>
            {budgetValue > 0
              ? "确认后在这份预算内连续执行；需要替换已采用内容时仍会交给你确认。"
              : "预算为 0 时，只整理信息和制作无需计费的候选，不调用付费生成。"}
          </small>
          {!validBudget && (
            <small className="production-error" role="alert">
              请输入 0 到 1,000,000 之间的整数。
            </small>
          )}
        </div>
      )}

      {tasks.length > 0 && (
        <>
          <div className="production-progress" aria-live="polite">
            <span>
              已完成 <b>{completed}</b> / {tasks.length} 步
            </span>
            {running > 0 && <span>{running} 个步骤正在处理</span>}
            {actionable.length > 0 && (
              <span className="production-attention">
                {actionable.length} 项需要关注
              </span>
            )}
            <button
              type="button"
              className="production-text-button"
              aria-expanded={expanded}
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              {expanded ? "收起步骤" : "查看步骤"}
            </button>
          </div>
          <ul className="production-steps">
            {displayed.map((task) => (
              <li key={task.id} data-status={task.status}>
                <span className="production-step-icon">
                  <StatusIcon status={task.status} />
                </span>
                <div className="production-step-main">
                  <span className="production-step-goal" title={task.goal}>
                    {task.goal}
                  </span>
                  <span className="production-step-status">
                    {labels[task.status] || task.status}
                  </span>
                  {task.state?.question && task.status === "waiting_input" && (
                    <p className="production-question">{task.state.question}</p>
                  )}
                  {task.status === "waiting_input" &&
                    task.state?.guidance_confirmation && (
                      <div className="production-review-link">
                        <p>
                          这次会调整{" "}
                          {task.state.guidance_confirmation.task_ids.length}{" "}
                          个步骤。已有成果和已采用版本会保留，新的修改先成为候选。
                        </p>
                        <button
                          type="button"
                          className="production-primary"
                          disabled={update.isPending}
                          onClick={() =>
                            send(
                              "steer",
                              [],
                              "",
                              undefined,
                              task.state!.guidance_confirmation!.guidance_id,
                            )
                          }
                        >
                          确认整体调整
                        </button>
                      </div>
                    )}
                  {task.error?.message && (
                    <p className="production-error">{task.error.message}</p>
                  )}
                  {task.status === "uncertain" && (
                    <p>执行结果尚未核实，暂不重复提交，避免重复生成或计费。</p>
                  )}
                  {task.status === "waiting_review" && (
                    <ProposalReview
                      task={task}
                      projectId={projectId}
                      workId={run.work_id}
                      pending={update.isPending}
                      onDecide={(action, id) => send(action, [task.id], "", id)}
                    />
                  )}
                  {task.status === "waiting_candidate" &&
                    task.state?.waiting_candidate && (
                      <div className="production-review-link">
                        <p>
                          {task.state.waiting_candidate.message ||
                            "候选已准备好，预览确认后继续后续步骤。"}
                        </p>
                        <a
                          href={`/projects/${projectId}/works/${task.state.waiting_candidate.work_id}?view=candidates&change=${task.state.waiting_candidate.change_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          预览并审阅候选 ↗
                        </a>
                      </div>
                    )}
                </div>
                <div className="production-step-actions">
                  {task.status === "failed" && task.error?.recovery === "resume_receipt" && run.status !== "canceled" && (
                    <button type="button" disabled={update.isPending}
                      onClick={() => send("resume_receipt", [task.id])}>
                      <RotateCcw size={14} />重新接收结果
                    </button>
                  )}
                  {task.status === "waiting_input" && (
                    <button
                      type="button"
                      disabled={update.isPending}
                      onClick={() => openForm("reply", task)}
                    >
                      <MessageSquare size={14} />
                      回复
                    </button>
                  )}
                  {task.status === "failed" &&
                    task.error?.retryable === true && (
                      <button
                        type="button"
                        disabled={update.isPending}
                        onClick={() => send("retry", [task.id])}
                      >
                        <RotateCcw size={14} />
                        重试
                      </button>
                    )}
                  {run.status !== "canceled" && (
                    <button
                      type="button"
                      disabled={update.isPending}
                      onClick={() => openForm("steer", task)}
                      aria-label={`调整：${task.goal}`}
                    >
                      <Pencil size={14} />
                      <span>调整</span>
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {ordered.length > displayed.length && (
            <button
              type="button"
              className="production-text-button production-more"
              onClick={() => {
                setExpanded(true);
                setVisibleCount((count) => (expanded ? count + 24 : count));
              }}
            >
              还有 {ordered.length - displayed.length} 个步骤，展开查看
            </button>
          )}
        </>
      )}

      {form && (
        <form
          className="production-guidance"
          onSubmit={(e) => {
            e.preventDefault();
            if (message.trim())
              send(
                form.action,
                form.task ? [form.task.id] : [],
                message.trim(),
              );
          }}
        >
          <div>
            <strong>
              {form.action === "reply"
                ? "回复这一步的问题"
                : form.task
                  ? "调整这一部分"
                  : "调整制作方向"}
            </strong>
            <button
              type="button"
              aria-label="收起指导输入"
              disabled={update.isPending}
              onClick={() => setForm(null)}
            >
              <X size={15} />
            </button>
          </div>
          {form.task && <p>{form.task.goal}</p>}
          <textarea
            autoFocus
            rows={3}
            maxLength={8000}
            aria-label={form.action === "reply" ? "回复内容" : "指导意见"}
            placeholder={
              form.task
                ? "说说哪里需要调整，以及哪些内容要保留…"
                : "说说接下来想怎么做…"
            }
            value={message}
            disabled={update.isPending}
            onChange={(e) => setMessage(e.target.value)}
          />
          <div className="production-guidance-footer">
            <small>
              {run.status === "paused"
                ? "意见会保存，等你点击继续后执行。"
                : "已完成的成果会保留，受影响的部分会重新规划。"}
            </small>
            <button
              className="production-primary"
              type="submit"
              disabled={update.isPending || !message.trim()}
            >
              {update.isPending
                ? "正在保存…"
                : form.action === "reply"
                  ? "发送回复"
                  : "提交指导"}
            </button>
          </div>
        </form>
      )}

      {update.isError && (
        <div className="production-notice production-error" role="alert">
          <AlertCircle size={16} />
          <span>{update.error.message}</span>
          {lastRequest.current && (
            <button
              type="button"
              disabled={update.isPending}
              onClick={() =>
                lastRequest.current && update.mutate(lastRequest.current)
              }
            >
              重试操作
            </button>
          )}
        </div>
      )}
      {query.isError && (
        <div className="production-notice" role="alert">
          <AlertCircle size={15} />
          <span>进度更新暂时中断，当前展示上一次读取的状态。</span>
          <button type="button" onClick={() => void query.refetch()}>
            重新连接
          </button>
        </div>
      )}
      {run.error?.message && (
        <p className="production-error" role="alert">
          {run.error.message}
        </p>
      )}
      <footer className="production-footer">
        <span>
          {run.status === "paused"
            ? "已停止派发后续步骤；已提交的生成结果仍会保留。"
            : run.status === "canceled"
              ? "已停止后续制作，已有成果保留；已提交的上游生成可能仍在执行，可在任务中心查看。"
              : run.status === "succeeded"
                ? "本轮执行已完成，候选仍可继续修改和审阅。"
                : "可以离开页面，回来后继续查看进展。"}
        </span>
        {run.status !== "canceled" && !form && (
          <button
            type="button"
            className="production-text-button"
            disabled={update.isPending}
            onClick={() => openForm("steer")}
          >
            <Pencil size={14} />
            补充指导
          </button>
        )}
      </footer>
    </section>
  );
}
