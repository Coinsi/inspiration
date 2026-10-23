import { SelectField } from "@/components/ui/select-field";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  Play,
  ChevronRight,
  Coins,
  Clapperboard,
  BookOpen,
  Trash2,
  Undo2,
  Film,
  Image,
  MessageSquare,
  Plus,
  Sparkles,
  X,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import type { GeneratedMaterial } from "@/pages/GeneratedLibrary";
import ReactMarkdown from "react-markdown";
import {
  api,
  blobUrl,
  type ProviderConfig,
  type GenJob,
  type Generation,
} from "@/lib/api";
import { usePersistentState } from "@/lib/usePersistentState";
import { WorkPicker } from "@/components/WorkPicker";
import { useAuth } from "@/lib/auth";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { MediaImage } from "@/components/MediaImage";
import { MaterialVideoCover } from "@/components/MaterialVideoCover";

import { JobStatus, ACTIVE_JOBS, JOB_LABEL } from "@/components/JobStatus";
import { ComposerModelPicker } from "./ComposerModelPicker";
import { ComposerParameters } from "./ComposerParameters";
import { referenceProblem } from "./referenceProblem";
import { referenceInput } from "./referenceInput";
import {
  ComposerReferences,
  ReferencePreview,
  type ComposerReference,
} from "./ComposerReferences";
import "./composer.css";
const Services = lazy(() =>
  import("@/components/PersonalModelServices").then((m) => ({
    default: m.PersonalModelServices,
  })),
);
const Agent = lazy(() =>
  import("@/pages/Agent").then((m) => ({ default: m.AgentWorkspace })),
);
type Mode = "image" | "video" | "text";
type Caps = {
  max_reference_videos?: number;
  reference_unavailable_reason?: string | null;
  modalities: string[];
  features: string[];
  max_reference_images: number;
  param_schema: Record<string, { enum?: (string | number)[] }>;
};
type Creation = {
  id: string;
  title: string;
  request: { provider: string; request_type: Mode; prompt_override: string };
  jobs: GenJob[];
  outputs: Generation[];
};
const mock: ProviderConfig = {
  id: "mock",
  provider_name: "mock",
  kind: "generation",
  enabled: true,
  endpoint: null,
  config: {},
  capabilities: {},
  display_name: "离线测试",
};

export function WorkbenchComposer({ projectId }: { projectId: string }) {
  const { me } = useAuth(),
    qc = useQueryClient(),
    base = `/projects/${projectId}`;
  const [mode, setMode] = usePersistentState<Mode>(
    `workbench.mode.${me?.user.id}.${projectId}`,
    "image",
  );
  useEffect(() => {
    if (mode === "text") setMode("image");
  }, [mode, setMode]);
  const [prompt, setPrompt] = usePersistentState(
    `creative.goal.${projectId}`,
    "",
  );
  const [choices, setChoices] = usePersistentState<
    Partial<Record<Mode, string>>
  >(`workbench.models.${me?.user.id}.${projectId}`, {});
  const [parameters, setParameters] = usePersistentState<
    Record<string, Record<string, string | number>>
  >(`workbench.parameters.${me?.user.id}.${projectId}`, {});
  const [refs, setRefs] = usePersistentState<ComposerReference[]>(
    `workbench.references.${me?.user.id}.${projectId}`,
    [],
  );
  const [pending, setPending] = usePersistentState<{
    fingerprint: string;
    key: string;
  } | null>(`workbench.pending.${me?.user.id}.${projectId}`, null);
  const [agentId, setAgentId] = usePersistentState(
    `workbench.agent.${me?.user.id}.${projectId}`,
    "",
  );
  const [manage, setManage] = useState(false),
    [references, setReferences] = useState(false),
    [agentOpen, setAgentOpen] = useState(false),
    [offset, setOffset] = useState(0);
  const sending = useRef(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const [clearedPrompt, setClearedPrompt] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const importId = searchParams.get("reference") || searchParams.get("reuse");
  const importAsReference = !!searchParams.get("reference");
  const [importMessage, setImportMessage] = useState("");
  const imported = useQuery({
    queryKey: ["generated-material", projectId, importId],
    queryFn: () =>
      api.get<GeneratedMaterial>(`${base}/library/generated/${importId}`),
    enabled: !!importId,
    retry: false,
  });
  useEffect(() => {
    if (!importId || (!imported.data && !imported.error)) return;
    if (imported.error)
      setImportMessage(`素材读取失败：${imported.error.message}`);
    else if (imported.data) {
      const item = imported.data;
      if (importAsReference) {
        if (!item.reference_available)
          setImportMessage(
            "这项素材当前不能作为参考，可查看来源任务或复用提示词。",
          );
        else if (refs.length >= 8 && !refs.some((r) => r.id === item.id))
          setImportMessage(
            "最多添加 8 项参考素材，请先移除一项后再从素材库选择。",
          );
        else {
          setRefs((current) =>
            current.some((r) => r.id === item.id)
              ? current
              : [
                  ...current,
                  {
                    id: item.id,
                    title: item.title,
                    blob_hash: item.blob_hash,
                    kind: item.output_type,
                    thumbnail_hash: item.poster_hash,
                    role: "reference",
                  },
                ],
          );
          setMode(item.output_type);
          setImportMessage(
            "已添加素材库内容作为参考，可以继续描述你想创作的画面。",
          );
        }
      } else {
        setClearedPrompt(prompt);
        setPrompt(item.prompt);
        setMode(item.output_type);
        setImportMessage("已填入素材提示词，请选择当前可用的模型后创作。");
      }
    }
    const next = new URLSearchParams(searchParams);
    next.delete("reference");
    next.delete("reuse");
    next.set("view", "generate");
    setSearchParams(next, { replace: true });
  }, [
    importId,
    importAsReference,
    imported.data,
    imported.error,
    searchParams,
    setSearchParams,
    setRefs,
    setMode,
    setPrompt,
    prompt,
    refs,
  ]);
  const providers = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${base}/providers`),
  });
  const options = useMemo(
    () => [
      ...(providers.data || []).filter(
        (p) =>
          !p.config.hidden_from_catalog &&
          (mode === "text"
            ? p.kind === "llm"
            : p.kind === "generation" &&
              (p.config.modality === mode ||
                (!p.config.modality && mode === "image"))),
      ),
      mock,
    ],
    [providers.data, mode],
  );
  const provider =
    choices[mode] ??
    options.find((p) => p.enabled && p.config.is_default)?.provider_name ??
    options.find((p) => p.enabled && p.provider_name !== "mock")
      ?.provider_name ??
    "";
  const current = options.find((p) => p.provider_name === provider);
  const caps = useQuery({
    queryKey: ["capabilities", projectId, provider],
    queryFn: () => api.get<Caps>(`${base}/providers/${provider}/capabilities`),
    enabled: mode !== "text" && !!provider && !!current?.enabled,
  });
  const paramKey = `${mode}:${provider}`;
  const schema = mode === "text" ? {} : caps.data?.param_schema || {};
  const controls = Object.fromEntries(
    Object.entries(schema)
      .filter(
        ([key, spec]) =>
          spec.enum?.length &&
          !(
            mode === "image" &&
            ["duration", "resolution", "aspect_ratio"].includes(key) &&
            provider === "mock"
          ),
      )
      .map(([key, spec]) => [
        key,
        spec.enum!.includes(parameters[paramKey]?.[key])
          ? parameters[paramKey][key]
          : spec.enum![0],
      ]),
  );
  const activeRefs =
    mode === "text"
      ? []
      : refs.map((r) => ({
          ...r,
          role: mode === "image" ? ("reference" as const) : r.role,
        }));
  const refProblem = referenceProblem(caps.data, activeRefs);
  const payload = {
    work_id: searchParams.get("work") || null,
    provider,
    request_type: mode,
    prompt_override: prompt.trim(),
    provider_params: controls,
    count: 1,
    use_references: false,
    references: activeRefs.map((r) => referenceInput(r)),
  };
  const fingerprint = JSON.stringify(payload);
  const [quote, setQuote] = useState<{
    fingerprint: string;
    points: number;
    note: string;
  } | null>(null);
  const quoteMutation = useMutation({
    mutationFn: async () => {
      const result = await api.post<{
        points: number;
        detail: { message?: string };
      }>(`${base}/workbench/estimate`, {
        ...payload,
        request_key: crypto.randomUUID(),
      });
      setQuote({
        fingerprint,
        points: result.points,
        note:
          result.detail.message || "内部积分仅供参考，实际费用以上游账单为准。",
      });
    },
  });
  const creations = useQuery({
    queryKey: [
      "workbench-creations",
      projectId,
      searchParams.get("work"),
      offset,
    ],
    queryFn: () =>
      api.get<Creation[]>(
        `${base}/workbench/creations?offset=${offset}${searchParams.get("work") ? `&work_id=${encodeURIComponent(searchParams.get("work")!)}` : ""}`,
      ),
    refetchInterval: (q) =>
      q.state.data?.some((c) => c.jobs.some((j) => ACTIVE_JOBS.has(j.status)))
        ? 2000
        : false,
  });
  const agent = useQuery({
    queryKey: ["agent-run", projectId, agentId],
    queryFn: () =>
      api.get<{
        id: string;
        status: string;
        goal: string;
        result?: string;
        error?: string;
      }>(`${base}/agent/runs/${agentId}`),
    enabled: !!agentId,
    refetchInterval: (q) =>
      q.state.status === "error" ||
      (q.state.data &&
        [
          "succeeded",
          "failed",
          "canceled",
          "paused",
          "waiting_review",
        ].includes(q.state.data.status))
        ? false
        : 2000,
  });
  const submit = useMutation({
    mutationFn: async () => {
      if (sending.current) return;
      sending.current = true;
      const request =
        pending?.fingerprint === fingerprint
          ? pending
          : { fingerprint, key: crypto.randomUUID() };
      setPending(request);
      try {
        if (mode === "text") {
          const run = await api.post<{ id: string }>(`${base}/agent/runs`, {
            goal: prompt.trim(),
            engine: provider === "mock" ? "mock" : "cloud_llm",
            model_provider: provider === "mock" ? null : provider,
            scope: [],
            max_turns: 12,
            request_key: request.key,
          });
          setAgentId(run.id);
        } else {
          await api.post<GenJob>(`${base}/workbench/generate`, {
            ...payload,
            request_key: request.key,
          });
          setOffset(0);
          await qc.invalidateQueries({
            queryKey: ["workbench-creations", projectId],
          });
          await qc.invalidateQueries({ queryKey: ["jobs", projectId] });
        }
        setPending(null);
      } finally {
        sending.current = false;
      }
    },
  });
  const busy = submit.isPending;
  const ready =
    !!prompt.trim() &&
    !!current?.enabled &&
    !providers.isFetching &&
    !providers.error &&
    !refProblem &&
    (mode === "text" ||
      (!!caps.data?.modalities.includes(mode) &&
        !caps.isFetching &&
        !caps.error));
  const error =
    submit.error?.message ||
    quoteMutation.error?.message ||
    providers.error?.message ||
    (mode !== "text" ? caps.error?.message : "");
  const chooseMode = (next: Mode) => {
    setMode(next);
    submit.reset();
    quoteMutation.reset();
  };
  const reuseCreation = (creation: Creation) => {
    setPrompt(creation.request.prompt_override);
    setMode(creation.request.request_type);
    setChoices((v) => ({
      ...v,
      [creation.request.request_type]: creation.request.provider,
    }));
    promptRef.current?.focus();
  };
  const resultCreations = (creations.data || []).filter((c) =>
    c.outputs.some((g) => g.output_blob_hash),
  );
  const taskRecords = (creations.data || []).filter(
    (c) =>
      !c.outputs.some((g) => g.output_blob_hash) ||
      c.jobs[0]?.cost_raw?.warnings?.length,
  );
  const activeRecords = taskRecords.filter((c) =>
    ACTIVE_JOBS.has(c.jobs[0]?.status),
  );
  const pastRecords = taskRecords.filter(
    (c) => !ACTIVE_JOBS.has(c.jobs[0]?.status),
  );
  const taskRecord = (c: Creation) => (
    <details className="composer-task-record" key={c.id}>
      <summary>
        <ChevronRight size={14} />
        <span className="composer-task-title">{c.title}</span>
        <span className="composer-task-state">
          {c.jobs[0]?.cost_raw?.warnings?.length
            ? "参数有提示"
            : JOB_LABEL[c.jobs[0]?.status] || "等待任务"}
        </span>
      </summary>
      <div className="composer-task-detail">
        {c.jobs[0] && <JobStatus projectId={projectId} jobId={c.jobs[0].id} />}
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => reuseCreation(c)}
        >
          <Undo2 size={13} />
          复用提示词与模型
        </Button>
      </div>
    </details>
  );
  return (
    <div className="creative-launch workbench-composer">
      <div className="mb-4">
        <WorkPicker
          projectId={projectId}
          value={searchParams.get("work") || ""}
          disabled={submit.isPending}
          onChange={(id) => {
            setOffset(0);
            const next = new URLSearchParams(searchParams);
            id ? next.set("work", id) : next.delete("work");
            setSearchParams(next);
          }}
        />
      </div>
      {importMessage && (
        <div
          role="status"
          className="mb-4 flex items-center justify-between gap-3 rounded-xl border p-3 text-sm"
        >
          <span>{importMessage}</span>
          <button
            aria-label="关闭素材提示"
            onClick={() => setImportMessage("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      <form
        className="creative-composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready && !busy) submit.mutate();
        }}
      >
        <label className="sr-only" htmlFor="workbench-prompt">
          创作想法
        </label>
        <textarea
          ref={promptRef}
          id="workbench-prompt"
          value={prompt}
          maxLength={6000}
          disabled={busy}
          onChange={(e) => {
            setPrompt(e.target.value);
            setClearedPrompt(null);
          }}
          placeholder={
            mode === "text"
              ? "描述你的故事或需求，让创作助理一起完善…"
              : mode === "video"
                ? "描述镜头、动作和氛围，让画面动起来…"
                : "描述你脑海中的画面，也可以添加参考图片…"
          }
        />
        {mode !== "text" && (
          <div className="composer-references">
            {refs.map((r) => (
              <div key={r.id} className="composer-reference">
                <ReferencePreview projectId={projectId} item={r} />
                {r.kind === "video" && <span>参考视频</span>}
                <button
                  type="button"
                  aria-label={`移除参考 ${r.title}`}
                  disabled={busy}
                  onClick={() =>
                    setRefs((list) => list.filter((v) => v.id !== r.id))
                  }
                >
                  <X size={12} />
                </button>
                {mode === "video" && r.kind !== "video" && (
                  <SelectField
                    label={`参考角色 ${r.title}`}
                    className="composer-reference-role"
                    value={r.role || "reference"}
                    disabled={busy}
                    options={[
                      { value: "reference", label: "参考图" },
                      { value: "first_frame", label: "首帧" },
                      { value: "last_frame", label: "尾帧" },
                    ]}
                    onChange={(value) =>
                      setRefs((list) =>
                        list.map((v) =>
                          v.id === r.id
                            ? { ...v, role: value as ComposerReference["role"] }
                            : v,
                        ),
                      )
                    }
                  />
                )}
              </div>
            ))}
            <button
              type="button"
              className="composer-add-reference"
              disabled={busy}
              onClick={() => setReferences(true)}
            >
              <Plus size={15} />
              参考内容
            </button>
          </div>
        )}
        <div className="creative-composer-tools">
          <div className="composer-toolbar-options">
            <SelectField
              label="创作类型"
              className="composer-control composer-mode-select"
              value={mode}
              disabled={busy}
              onChange={(value) => chooseMode(value as Mode)}
              options={[
                {
                  value: "image",
                  label: "图片",
                  description: "用文字与参考图创作画面",
                  icon: <Image size={18} />,
                },
                {
                  value: "video",
                  label: "视频",
                  description: "生成动态镜头与视频片段",
                  icon: <Film size={18} />,
                },
              ]}
            />
            <ComposerModelPicker
              models={options}
              value={provider}
              onChange={(value) => {
                setChoices((c) => ({ ...c, [mode]: value }));
                submit.reset();
                quoteMutation.reset();
              }}
              manage={() => setManage(true)}
              disabled={busy}
              loading={providers.isPending}
              error={providers.error?.message}
              retry={() => void providers.refetch()}
            />
            <ComposerParameters
              key={paramKey}
              controls={controls}
              schema={schema}
              disabled={busy}
              onChange={(key, value) =>
                setParameters((all) => ({
                  ...all,
                  [paramKey]: { ...all[paramKey], [key]: value },
                }))
              }
            />
            <button
              type="button"
              className="composer-control composer-clear"
              disabled={busy || (!prompt && clearedPrompt === null)}
              title={
                clearedPrompt !== null && !prompt
                  ? "恢复刚清空的提示词"
                  : "清空提示词，保留参考内容"
              }
              onClick={() => {
                if (clearedPrompt !== null && !prompt) {
                  setPrompt(clearedPrompt);
                  setClearedPrompt(null);
                } else {
                  setClearedPrompt(prompt);
                  setPrompt("");
                }
              }}
            >
              {clearedPrompt !== null && !prompt ? (
                <Undo2 size={18} />
              ) : (
                <Trash2 size={18} />
              )}
              <span>{clearedPrompt !== null && !prompt ? "撤销" : "清空"}</span>
            </button>
          </div>
          <div className="composer-toolbar-submit">
            {mode !== "text" && (
              <button
                type="button"
                className="composer-estimate"
                disabled={!ready || busy || quoteMutation.isPending}
                onClick={() => quoteMutation.mutate()}
                title={
                  quote?.fingerprint === fingerprint ? quote.note : undefined
                }
              >
                <Coins size={18} />
                {quoteMutation.isPending
                  ? "预估中…"
                  : quote?.fingerprint === fingerprint
                    ? `预估 ${quote.points} 积分`
                    : "预估积分"}
              </button>
            )}
            <button
              className="creative-send"
              type="submit"
              disabled={!ready || busy}
              aria-label={busy ? "正在提交创作" : "开始创作"}
            >
              <ArrowUp size={18} />
              <span>{busy ? "提交中…" : "开始创作"}</span>
            </button>
          </div>
        </div>
        {(error || refProblem) && (
          <p role="alert" className="composer-error">
            {error || refProblem}
            {caps.error && (
              <button type="button" onClick={() => void caps.refetch()}>
                重新读取模型能力
              </button>
            )}
          </p>
        )}
      </form>
      {(mode === "text" || provider === "mock") && (
        <p className="composer-context-note">
          {mode === "text"
            ? "文字模式使用创作助理；涉及项目修改时仍需审阅。"
            : "离线测试仅产出占位素材。"}
        </p>
      )}
      <nav className="composer-starters" aria-label="开始创作">
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            chooseMode("video");
            document.getElementById("workbench-prompt")?.focus();
          }}
        >
          <span className="composer-starter-icon">
            <Clapperboard size={21} />
          </span>
          <span>
            <strong>生成第一个镜头</strong>
            <small>描述画面、镜头运动与光线</small>
          </span>
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (mode === "text") chooseMode("image");
            setReferences(true);
          }}
        >
          <span className="composer-starter-icon">
            <Image size={21} />
          </span>
          <span>
            <strong>从参考图开始</strong>
            <small>选取项目图片，延续画面风格</small>
          </span>
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            chooseMode("text");
            document.getElementById("workbench-prompt")?.focus();
          }}
        >
          <span className="composer-starter-icon">
            <BookOpen size={21} />
          </span>
          <span>
            <strong>续写故事</strong>
            <small>和创作助理讨论剧情与对白</small>
          </span>
        </button>
        <Link to={`${base}/skills`}>
          <span className="composer-starter-icon">
            <Sparkles size={21} />
          </span>
          <span>
            <strong>探索创作技能</strong>
            <small>挑选适合项目的专业创作能力</small>
          </span>
        </Link>
      </nav>
      {agentId && (
        <section className="composer-text-result" aria-label="文字创作结果">
          <header>
            <MessageSquare size={16} />
            <strong>文字创作</strong>
            <button type="button" onClick={() => setAgentOpen(true)}>
              查看助理详情
            </button>
          </header>
          <p>{agent.data?.goal}</p>
          {agent.error ? (
            <p role="alert">{agent.error.message}</p>
          ) : agent.data?.error ? (
            <p role="alert">{agent.data.error}</p>
          ) : agent.data?.result ? (
            <ReactMarkdown>{agent.data.result}</ReactMarkdown>
          ) : (
            <p role="status">
              {agent.data?.status === "waiting_review"
                ? "助理有待审阅的建议，请打开详情处理。"
                : agent.data?.status === "paused"
                  ? "创作已暂停，可在助理详情中继续。"
                  : agent.data?.status === "canceled"
                    ? "本次创作已取消。"
                    : agent.data?.status === "failed"
                      ? "本次创作未完成，请查看助理详情。"
                      : agent.data?.status === "succeeded"
                        ? "本次创作已完成，可查看助理详情。"
                        : "助理正在处理，可继续其他创作…"}
            </p>
          )}
        </section>
      )}
      {creations.data?.length || creations.error || offset > 0 ? (
        <section className="composer-results" aria-label="最近创作">
          <header>
            <h2>最近创作</h2>
            <div className="flex gap-4">
              <Link
                to={`${base}/materials?return=${encodeURIComponent(`${base}/workbench?${searchParams.toString()}`)}`}
              >
                全部素材 →
              </Link>
              <Link to={`${base}/tasks`}>全部任务 →</Link>
            </div>
          </header>
          {creations.error && (
            <p role="alert">
              {creations.error.message}
              <Button onClick={() => void creations.refetch()}>重试</Button>
            </p>
          )}
          {activeRecords.length > 0 && (
            <div className="composer-active-records" aria-label="正在生成">
              <h3>正在生成 · {activeRecords.length}</h3>
              {activeRecords.map((c) => (
                <div key={c.id}>
                  <p>{c.title}</p>
                  {c.jobs[0] && (
                    <JobStatus projectId={projectId} jobId={c.jobs[0].id} />
                  )}
                </div>
              ))}
            </div>
          )}
          {resultCreations.length > 0 && (
            <div className="composer-result-grid">
              {resultCreations.flatMap((c) =>
                c.outputs
                  .filter((g) => g.output_blob_hash)
                  .map((g) => (
                    <article key={g.id} className="composer-result-card">
                      <Link
                        className="composer-result-cover"
                        aria-label={`预览${g.output_type === "video" ? "视频" : "图片"}：${c.title}`}
                        to={`${base}/materials?generation=${g.id}&return=${encodeURIComponent(`${base}/workbench?${searchParams.toString()}`)}`}
                      >
                        {g.provider === "mock" ? (
                          <span className="composer-test-cover">
                            离线测试占位
                          </span>
                        ) : g.output_type === "video" ? (
                          <MaterialVideoCover
                            src={blobUrl(projectId, g.output_blob_hash!)}
                          />
                        ) : (
                          <MediaImage
                            src={blobUrl(projectId, g.output_blob_hash!)}
                            alt={c.title}
                          />
                        )}
                        <span className="composer-cover-type">
                          {g.output_type === "video" ? (
                            <>
                              <Play size={12} fill="currentColor" />
                              视频
                            </>
                          ) : (
                            <>
                              <Image size={12} />
                              图片
                            </>
                          )}
                        </span>
                        <span className="composer-cover-open">
                          查看素材 <ChevronRight size={14} />
                        </span>
                      </Link>
                      <div className="composer-result-caption">
                        <p className="composer-result-prompt" title={c.title}>
                          {c.title}
                        </p>
                        <div className="composer-result-meta">
                          <span>
                            {providers.data?.find(
                              (p) => p.provider_name === c.request.provider,
                            )?.display_name || c.request.provider}
                          </span>
                          <button
                            type="button"
                            disabled={busy}
                            aria-label={`复用提示词与模型：${c.title}`}
                            onClick={() => reuseCreation(c)}
                          >
                            <Undo2 size={13} />
                            复用
                          </button>
                        </div>
                      </div>
                    </article>
                  )),
              )}
            </div>
          )}
          {!resultCreations.length &&
            !activeRecords.length &&
            !creations.error && (
              <p className="composer-results-empty">
                这页还没有生成的作品。你可以查看任务记录，调整后继续创作。
              </p>
            )}
          {pastRecords.length > 0 && (
            <details className="composer-past-records">
              <summary>
                <ChevronRight size={14} />
                <span>生成记录</span>
                <span className="composer-record-count">
                  {pastRecords.length}
                </span>
                <span className="composer-record-hint">
                  查看未完成的任务与提示
                </span>
              </summary>
              <div>{pastRecords.map(taskRecord)}</div>
            </details>
          )}
          {(offset > 0 || creations.data?.length === 12) && (
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={!offset}
                onClick={() => setOffset((n) => Math.max(0, n - 12))}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                disabled={creations.data?.length !== 12}
                onClick={() => setOffset((n) => n + 12)}
              >
                下一页
              </Button>
            </div>
          )}
        </section>
      ) : null}
      <Modal
        open={manage}
        onClose={() => setManage(false)}
        title="模型服务"
        width={980}
      >
        <Suspense fallback={<p>正在读取服务…</p>}>
          {manage && <Services projectId={projectId} />}
        </Suspense>
      </Modal>
      <Modal
        open={references}
        onClose={() => setReferences(false)}
        title="选择参考内容"
      >
        <ComposerReferences
          projectId={projectId}
          includeVideos={mode === "video" && !!caps.data?.features.includes("video_reference")}
          selected={refs}
          pick={(item) => {
            setRefs((list) =>
              list.some((v) => v.id === item.id)
                ? list.filter((v) => v.id !== item.id)
                : list.length < 8
                  ? [...list, item]
                  : list,
            );
          }}
        />
      </Modal>
      {agentOpen && (
        <Modal
          open
          onClose={() => setAgentOpen(false)}
          title="创作助理"
          width={1100}
        >
          <Suspense fallback={<p>正在读取创作记录…</p>}>
            <Agent projectId={projectId} initialRunId={agentId} embedded />
          </Suspense>
        </Modal>
      )}
    </div>
  );
}
