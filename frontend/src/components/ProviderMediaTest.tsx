import { Expand, FlaskConical, Sparkles } from "lucide-react";
import { Modal } from "./ui/modal";
import { ProviderReferenceField } from "./ProviderReferenceField";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  blobUrl,
  type Generation,
  type GenJob,
  type Project,
} from "@/lib/api";
import { MediaVideo } from "@/components/MediaVideo";
import { MediaImage } from "@/components/MediaImage";
import { ACTIVE_JOBS, JOB_LABEL } from "@/lib/task-center";
import { useAuth } from "@/lib/auth";
import { Button } from "./ui/button";
import { SelectField } from "./ui/select-field";
import type { Model } from "./ModelChannels";
import { useConversationAttachments } from "./workbench/useConversationAttachments";

type Props = {
  projectId?: string;
  personal?: boolean;
  models: Model[];
  purpose: string;
  disabled: boolean;
};

export function ProviderMediaTest({
  projectId,
  personal = false,
  models,
  purpose,
  disabled,
}: Props) {
  const [chosenProject, setChosenProject] = useState("");
  const [chosenModel, setChosenModel] = useState("");
  const { me } = useAuth();
  const writable = new Set(
    me?.memberships
      .filter((m) => ["admin", "director", "artist"].includes(m.role))
      .map((m) => m.project_id),
  );
  const pickProject = !projectId || (personal && !writable.has(projectId));
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
    enabled: pickProject && models.length > 0,
  });
  const availableProjects =
    projects.data?.filter((p) => writable.has(p.id)) ?? [];
  const target = pickProject
    ? availableProjects.find((p) => p.id === chosenProject)?.id
    : projectId;
  const model = models.find((m) => m.id === chosenModel) ?? models[0];
  return (
    <section className="provider-models provider-lab">
      <h3 className="provider-panel-title">
        <FlaskConical size={17} />
        测试{purpose === "image" ? "图片" : "视频"}生成
      </h3>
      {pickProject && model && (
        <label>
          测试项目
          <SelectField
            label="测试项目"
            value={chosenProject}
            options={[
              { value: "", label: "选择项目" },
              ...availableProjects.map((p) => ({
                value: p.id,
                label: p.name,
              })),
            ]}
            onChange={setChosenProject}
          />
        </label>
      )}
      {pickProject &&
        model &&
        projects.isSuccess &&
        !availableProjects.length && (
          <small>需要一个有生成权限的项目来保存测试结果。</small>
        )}
      {projects.error && pickProject && (
        <small role="alert">{projects.error.message}</small>
      )}
      {models.length > 1 && (
        <label>
          测试模型
          <SelectField
            label="测试模型"
            value={model.id}
            options={models.map((m) => ({
              value: m.id,
              label: m.name,
              description: m.model,
            }))}
            onChange={setChosenModel}
          />
        </label>
      )}
      <TestForm
        key={(target || "unsaved") + (model?.id ?? purpose)}
        projectId={target || ""}
        personal={personal}
        model={model}
        purpose={purpose}
        disabled={disabled || !target || !model}
      />
    </section>
  );
}

const parameterLabels: Record<string, string> = {
  duration: "时长（秒）",
  size: "画面尺寸",
  resolution: "清晰度",
  aspect_ratio: "画面比例",
  quality: "质量",
};

function TestForm({
  projectId,
  personal,
  model,
  purpose,
  disabled,
}: {
  projectId: string;
  personal: boolean;
  model?: Model;
  purpose: string;
  disabled: boolean;
}) {
  const { me } = useAuth();
  const qc = useQueryClient();
  const base = `/projects/${projectId}`;
  const [prompt, setPrompt] = useState("");
  const [params, setParams] = useState<Record<string, string | number>>({});
  const files = useConversationAttachments(
    projectId,
    `model-test.${me?.user.id}.${projectId}.${model?.id ?? purpose}`,
  );
  const framePair =
    purpose === "video" &&
    ["ark_video", "openrouter_video", "zhipu_video", "bfl_video", "minimax_h3_video"].includes(
      model?.protocol ?? "",
    );
  const ratioFromFrame = model?.protocol === "minimax_h3_video" && files.references.length > 0;
  const [expanded, setExpanded] = useState("");
  const [submitted, setSubmitted] = useState<GenJob>();
  const pending = useRef({ fingerprint: "", key: "" });
  const job = useQuery({
    queryKey: ["job", projectId, submitted?.id],
    queryFn: () => api.get<GenJob>(`${base}/jobs/${submitted!.id}`),
    enabled: !!submitted,
    initialData: submitted,
    refetchInterval: (q) =>
      q.state.data && ACTIVE_JOBS.has(q.state.data.status) ? 2000 : false,
  });
  const result = useQuery({
    queryKey: ["model-test-results", projectId, submitted?.id],
    queryFn: () =>
      api.get<Generation[]>(
        `${base}/generations?target_type=creation&target_id=${submitted!.target_id}`,
      ),
    enabled: job.data?.status === "succeeded",
  });
  useEffect(() => {
    if (!personal || !["succeeded", "failed"].includes(job.data?.status ?? ""))
      return;
    void qc.invalidateQueries({
      queryKey: ["personal-model-channels", me?.user.id],
    });
    void qc.invalidateQueries({ queryKey: ["providers", projectId] });
  }, [personal, job.data?.id, job.data?.status, me?.user.id, projectId, qc]);
  const submit = useMutation({
    mutationFn: async () => {
      if (!model || !projectId || disabled)
        throw new Error("请先完成模型配置。");
      const payload = {
        provider: model.provider_name,
        request_type: model.modality,
        prompt_override: prompt.trim(),
        provider_params: ratioFromFrame
          ? Object.fromEntries(Object.entries(params).filter(([key]) => key !== "aspect_ratio"))
          : params,
        count: 1,
        use_references: false,
        references:
          framePair && files.references.length === 2
            ? files.references.map((r, i) => ({
                ...r,
                role: i === 0 ? "first_frame" : "last_frame",
              }))
            : files.references,
      };
      const fingerprint = JSON.stringify(payload);
      if (pending.current.fingerprint !== fingerprint)
        pending.current = { fingerprint, key: crypto.randomUUID() };
      return api.post<GenJob>(`${base}/workbench/generate`, {
        ...payload,
        request_key: pending.current.key,
      });
    },
    onSuccess: (task) => {
      pending.current.fingerprint = "";
      setSubmitted(task);
    },
  });
  const busy = submit.isPending || ACTIVE_JOBS.has(job.data?.status ?? "");
  const error =
    submit.error?.message ||
    job.error?.message ||
    job.data?.error ||
    result.error?.message ||
    files.error;
  return (
    <>
      <label>
        描述想生成的内容
        <textarea
          disabled={!model || !projectId || busy}
          value={prompt}
          rows={3}
          maxLength={6000}
          placeholder={
            purpose === "video"
              ? "例如，镜头缓缓推近窗边的一盆绿植"
              : "例如，一盆窗边的绿植，柔和的晨光"
          }
          onChange={(e) => setPrompt(e.target.value)}
        />
      </label>
      <div className="provider-test-params">
        {!model &&
          (purpose === "video" ? ["画面比例", "时长（秒）"] : ["画面比例"]).map(
            (name) => (
              <label key={name}>
                {name}
                <SelectField
                  label={name}
                  value=""
                  disabled
                  options={[{ value: "", label: "模型默认" }]}
                  onChange={() => {}}
                />
              </label>
            ),
          )}
        {Object.entries(model?.parameters ?? {})
          .filter(([key, values]) => values.length && !(ratioFromFrame && key === "aspect_ratio"))
          .map(([key, values]) => (
            <label key={key}>
              {parameterLabels[key] ?? key}
              <SelectField
                label={parameterLabels[key] ?? key}
                value={String(params[key] ?? "")}
                options={[
                  { value: "", label: "模型默认" },
                  ...values.map((v) => ({
                    value: String(v),
                    label: String(v),
                  })),
                ]}
                onChange={(v) =>
                  setParams((old) => {
                    const next = { ...old };
                    const value = values.find((item) => String(item) === v);
                    if (value === undefined) delete next[key];
                    else next[key] = value;
                    return next;
                  })
                }
              />
            </label>
          ))}
      </div>
      {ratioFromFrame && <small>视频比例沿用参考图。</small>}
      {(!model ||
        model.max_reference_images > 0 ||
        !!model.max_reference_videos) && (
        <ProviderReferenceField
          files={files}
          framePair={framePair}
          projectId={projectId}
          images={model?.max_reference_images ?? 1}
          videos={model?.max_reference_videos}
          disabled={disabled || busy}
        />
      )}
      <div className="provider-test-footer">
        {model && (
          <div className="provider-run-model">
            本次模型 <strong>{model.name}</strong>
            {model.model !== model.name && <code>{model.model}</code>}
          </div>
        )}
        <small>
          {!model
            ? "保存连接并添加模型后即可试生成。"
            : !projectId
              ? "选择保存测试结果的项目。"
              : personal
                ? "本次生成使用你的个人密钥和供应商额度。"
                : "本次生成将使用供应商额度。"}
        </small>
        <Button
          disabled={disabled || busy || !prompt.trim() || !files.ready}
          onClick={() => submit.mutate()}
        >
          <Sparkles size={15} />
          {busy ? "生成中…" : "测试生成"}
        </Button>
      </div>
      {job.data && (
        <small role="status">
          {JOB_LABEL[job.data.status] ?? job.data.status}
        </small>
      )}
      {error && <p role="alert">{error}</p>}
      {result.data
        ?.filter((g) => g.output_blob_hash && g.job_id === submitted?.id)
        .map((g) => {
          const url = blobUrl(projectId, g.output_blob_hash!);
          return (
            <figure className="provider-result" key={g.id}>
              {g.output_type === "video" ? (
                <MediaVideo src={blobUrl(projectId, g.output_blob_hash!, "playback")}
                  label="测试生成的视频" presentation="studio"
                  poster={blobUrl(projectId, g.output_blob_hash!, "thumbnail")} />
              ) : (
                <button
                  type="button"
                  aria-label="放大测试图片"
                  onClick={() => setExpanded(url)}
                >
                  <MediaImage src={blobUrl(projectId, g.output_blob_hash!, "display")} alt="测试生成结果" />
                  <span>
                    <Expand size={16} />
                  </span>
                </button>
              )}
              <figcaption>测试结果 · {model?.name}</figcaption>
            </figure>
          );
        })}
      <Modal
        open={!!expanded}
        onClose={() => setExpanded("")}
        title="测试生成结果"
        width={960}
        bodyClassName="provider-result-expanded"
      >
        <img src={expanded || undefined} alt="放大的测试生成结果" />
      </Modal>
    </>
  );
}
