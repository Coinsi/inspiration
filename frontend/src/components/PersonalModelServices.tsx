import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  KeyRound,
  Plus,
  Settings2,
  Trash2,
} from "lucide-react";
import { useConfirm } from "./ui/confirm";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { EditorFrame } from "./ModelEditorFrame";
import { ModelBrandIcon } from "./workbench/ModelBrandIcon";
import "./personal-model-services.css";
import {
  ModelInputCapabilitiesFields,
  textInputCapabilities,
  type ModelInputCapabilities,
} from "./ModelInputCapabilities";

type Verification = { status: string; message?: string };
type Service = {
  id: string;
  name: string;
  preset_id: string;
  endpoint: string;
  revision: number;
  enabled: boolean;
  catalog: { id: string; name: string; modality: string }[];
  models: {
    id: string;
    name: string;
    modality: string;
    verification: Verification;
    input_capabilities?: ModelInputCapabilities;
  }[];
};
type Preset = {
  id: string;
  name: string;
  description: string;
  key_url: string;
  endpoint: string;
  models: {
    id: string;
    name: string;
    modality: string;
    recommended?: boolean;
    lifecycle_note?: string;
  }[];
};
export function verificationLabel(value?: Verification) {
  return value?.status === "verified"
    ? "已验证生成"
    : value?.status === "failed"
      ? "上次调用失败"
      : "已配置 · 待验证生成";
}

export function PersonalModelServices({ projectId }: { projectId?: string }) {
  const { me } = useAuth();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const services = useQuery({
    queryKey: ["personal-model-services", me?.user.id],
    queryFn: () => api.get<Service[]>("/me/model-services"),
    refetchInterval: 15000,
  });
  const presets = useQuery({
    queryKey: ["personal-model-presets"],
    queryFn: () => api.get<Preset[]>("/me/model-services/presets"),
  });
  const canManage = me?.memberships.some(
    (m) => m.project_id === projectId && m.role === "admin",
  );
  const project = useQuery({
    queryKey: ["model-channels", projectId],
    enabled: !!projectId && !!canManage,
    queryFn: () =>
      api.get<{
        channels: {
          id: string;
          name: string;
          endpoint: string;
          has_token: boolean;
        }[];
      }>(`/projects/${projectId}/model-channels`),
  });
  const [editing, setEditing] = useState<Preset | null>(null),
    [selected, setSelected] = useState<string[]>([]);
  const [token, setToken] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [revision, setRevision] = useState<number | undefined>();
  const [inputEditing, setInputEditing] = useState<{
    service: Service;
    model: Service["models"][number];
  } | null>(null);
  const [inputValue, setInputValue] = useState<ModelInputCapabilities>(
    textInputCapabilities,
  );
  const [inputDirty, setInputDirty] = useState(false);
  const [inputError, setInputError] = useState("");
  async function saveInputs() {
    if (!inputEditing) return;
    setBusy(true);
    setInputError("");
    try {
      await api.patch(
        `/me/model-services/${inputEditing.service.id}/models/${encodeURIComponent(inputEditing.model.id)}/input-capabilities`,
        {
          revision: inputEditing.service.revision,
          input_capabilities: inputValue,
        },
      );
      setInputEditing(null);
      await refresh();
      setNotice("输入理解能力已保存；进行中的任务需暂停并确认新配置后继续。");
    } catch (error) {
      setInputError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function edit(preset: Preset) {
    const service = services.data?.find((s) => s.preset_id === preset.id);
    setRevision(service?.revision);
    setSelected(
      service
        ? service.models.map((m) => m.id)
        : preset.models.filter((m) => m.recommended !== false).map((m) => m.id),
    );
    setToken("");
    setError("");
    setEditing(preset);
  }
  async function refresh() {
    await Promise.all(
      ["personal-model-services", "providers", "capabilities"].map((key) =>
        qc.invalidateQueries({ queryKey: [key] }),
      ),
    );
  }
  async function save() {
    if (!editing) return;
    setBusy(true);
    setError("");
    try {
      await api.put(`/me/model-services/${editing.id}`, {
        preset_id: editing.id,
        models: selected,
        revision,
        ...(token.trim() ? { token: token.trim() } : {}),
      });
      setToken("");
      setEditing(null);
      await refresh();
      setNotice(
        "已保存到当前账号，所有项目均可选择。首次生成后会更新验证状态。",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(service: Service) {
    if (
      !(await confirm({
        title: `删除「${service.name}」？`,
        message:
          "将移除个人连接、保存的密钥及可选模型。已生成的作品和历史记录会保留；之后可以重新填写密钥连接。",
        confirmText: "删除服务",
        danger: true,
      }))
    )
      return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.del(
        `/me/model-services/${service.id}?revision=${service.revision}`,
      );
      await refresh();
      setNotice("服务已删除，已生成的作品保留。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function reuse(id: string) {
    setBusy(true);
    setError("");
    try {
      await api.post(
        `/projects/${projectId}/model-channels/${id}/use-personally`,
        {},
      );
      await refresh();
      setNotice("已复用到个人账号，无需重新填写密钥。原项目服务保留。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="personal-services">
      <header>
        <span className="personal-services-eyebrow">我的模型服务</span>
        <h2>连接一次，在每个项目创作</h2>
        <p>
          服务保存在当前账号，仅你可以使用。项目成员共享创作结果，不会获得你的密钥或个人服务调用权限。
        </p>
      </header>
      {(error || services.error || presets.error) && (
        <p role="alert" className="personal-services-error">
          {error || services.error?.message || presets.error?.message}
          <Button
            variant="ghost"
            onClick={() => {
              void services.refetch();
              void presets.refetch();
            }}
          >
            重新读取
          </Button>
        </p>
      )}
      {notice && (
        <p role="status" className="personal-services-notice">
          <Check size={16} />
          {notice}
        </p>
      )}
      {(services.isLoading || presets.isLoading) && (
        <p role="status">正在读取个人模型服务…</p>
      )}
      <div className="personal-services-grid">
        {[
          ...(presets.data || []),
          ...(services.data || [])
            .filter((s) => s.preset_id.startsWith("custom-"))
            .map((s) => ({
              id: s.preset_id,
              name: s.name,
              description: "从现有项目复用的个人模型服务",
              endpoint: s.endpoint,
              key_url: "",
              models: s.catalog,
            })),
        ].map((preset) => {
          const service = services.data?.find((s) => s.preset_id === preset.id);
          const legacy = project.data?.channels.find(
            (c) => c.endpoint === preset.endpoint && c.has_token,
          );
          return (
            <article key={preset.id} className="personal-service-card">
              <div className="personal-service-heading">
                <ModelBrandIcon
                  brand={
                    preset.id === "ark"
                      ? "火山引擎"
                      : preset.id === "minimax"
                        ? "MiniMax"
                        : preset.name
                  }
                  size={32}
                />
                <div>
                  <h3>{preset.name}</h3>
                  <span>
                    {service
                      ? `${service.models.length} 个已选模型 · 个人服务`
                      : "官方服务 · 填写 Key 即可连接"}
                  </span>
                </div>
              </div>
              <p>{preset.description}</p>
              {preset.id === "ark" &&
                service?.models.some(
                  (m) => m.verification.status === "failed",
                ) && (
                  <a
                    className="personal-service-help"
                    href="https://console.volcengine.com/ark/"
                    target="_blank"
                    rel="noreferrer"
                  >
                    打开方舟控制台，核对模型开通与 Key 权限 ↗
                  </a>
                )}
              {service && (
                <ul>
                  {service.models.map((model) => (
                    <li key={model.id}>
                      <div>
                        <strong>{model.name}</strong>
                        <span
                          className={`service-verification ${model.verification.status}`}
                        >
                          {verificationLabel(model.verification)}
                        </span>
                      </div>
                      {model.verification.message && (
                        <p>{model.verification.message}</p>
                      )}
                      {model.modality === "text" && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => {
                            setInputEditing({ service, model });
                            setInputValue(
                              model.input_capabilities || textInputCapabilities,
                            );
                            setInputDirty(false);
                            setInputError("");
                          }}
                        >
                          <Settings2 size={14} />
                          输入理解能力
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <footer>
                {service && (
                  <Button
                    variant="ghost"
                    className="text-danger"
                    disabled={busy}
                    aria-label={`删除服务 ${service.name}`}
                    onClick={() => void remove(service)}
                  >
                    <Trash2 size={15} />
                    删除服务
                  </Button>
                )}
                {!service && legacy ? (
                  <Button disabled={busy} onClick={() => void reuse(legacy.id)}>
                    <KeyRound size={15} />
                    复用当前项目配置
                  </Button>
                ) : null}
                <Button
                  variant={service ? "outline" : "default"}
                  disabled={busy}
                  onClick={() => edit(preset)}
                >
                  {service ? <Settings2 size={15} /> : <Plus size={15} />}{" "}
                  {service ? "管理模型与密钥" : "连接服务"}
                  <ChevronRight size={15} />
                </Button>
              </footer>
            </article>
          );
        })}
      </div>
      {!!project.data?.channels.filter(
        (c) =>
          c.has_token &&
          !presets.data?.some((p) => p.endpoint === c.endpoint) &&
          !services.data?.some(
            (s) => s.preset_id === "custom-" + c.id.replace(/-/g, ""),
          ),
      ).length && (
        <section className="personal-service-card">
          <h3>复用已有项目服务</h3>
          <p>
            把当前项目的文字、图片或视频服务保存到个人账号，其他项目也能直接使用。
          </p>
          <footer>
            {project.data?.channels
              .filter(
                (c) =>
                  c.has_token &&
                  !presets.data?.some((p) => p.endpoint === c.endpoint) &&
                  !services.data?.some(
                    (s) => s.preset_id === "custom-" + c.id.replace(/-/g, ""),
                  ),
              )
              .map((c) => (
                <Button
                  key={c.id}
                  disabled={busy}
                  variant="outline"
                  onClick={() => void reuse(c.id)}
                >
                  复用 {c.name}
                </Button>
              ))}
          </footer>
        </section>
      )}
      <p className="personal-services-footnote">
        模型目录表示已适配的能力，不代表账号已开通。验证状态来自真实生成；保存配置不会发起付费请求。
      </p>
      {inputEditing && (
        <EditorFrame
          title={`${inputEditing.model.name} · 输入能力`}
          close={() => setInputEditing(null)}
          dirty={inputDirty}
          busy={busy}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void saveInputs();
            }}
          >
            <fieldset disabled={busy} className="grid gap-5 pb-6">
              <ModelInputCapabilitiesFields
                value={inputValue}
                onChange={(value) => {
                  setInputValue(value);
                  setInputDirty(true);
                }}
              />
              {inputError && (
                <p role="alert" className="text-sm text-danger">
                  {inputError}
                </p>
              )}
              <Button type="submit" disabled={busy} className="mx-6">
                {busy ? "正在保存…" : "保存输入能力"}
              </Button>
            </fieldset>
          </form>
        </EditorFrame>
      )}
      {editing && (
        <EditorFrame
          title={`连接 ${editing.name}`}
          close={() => {
            if (!busy) {
              setEditing(null);
              setToken("");
            }
          }}
          busy={busy}
          dirty={
            !!token ||
            JSON.stringify(selected) !==
              JSON.stringify(
                services.data
                  ?.find((s) => s.preset_id === editing.id)
                  ?.models.map((m) => m.id) ||
                  editing.models
                    .filter((m) => m.recommended !== false)
                    .map((m) => m.id),
              )
          }
        >
          <form
            className="personal-service-editor"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <p>保存到个人账号 · 所有项目可用</p>
            <label>
              API Key{" "}
              {revision !== undefined && (
                <small>已保存，留空保留现有密钥</small>
              )}
              <Input
                type="password"
                autoComplete="new-password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={
                  revision !== undefined
                    ? "无需重复填写；更换密钥时输入"
                    : "粘贴服务 API Key"
                }
              />
            </label>
            <a
              hidden={!editing.key_url}
              href={editing.key_url}
              target="_blank"
              rel="noreferrer"
            >
              前往官方获取 API Key ↗
            </a>
            <fieldset disabled={busy}>
              <legend>选择创作模型</legend>
              {editing.models.map((model) => (
                <label className="personal-model-option" key={model.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(model.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, model.id]
                          : selected.filter((k) => k !== model.id),
                      )
                    }
                  />
                  <strong>
                    {model.name}
                    {model.lifecycle_note && (
                      <small className="personal-model-lifecycle">
                        {model.lifecycle_note}
                      </small>
                    )}
                  </strong>
                  <span>
                    {model.modality === "image"
                      ? "图片"
                      : model.modality === "text"
                        ? "文字"
                        : "视频"}
                  </span>
                </label>
              ))}
            </fieldset>
            <details>
              <summary>服务地址（已预置）</summary>
              <code>{editing.endpoint}</code>
            </details>
            {error && (
              <p role="alert" className="personal-services-error">
                {error}
              </p>
            )}
            <footer>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setEditing(null);
                  setToken("");
                }}
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={
                  busy ||
                  !selected.length ||
                  (revision === undefined && !token.trim())
                }
              >
                {busy ? "正在保存…" : "保存并使用"}
              </Button>
            </footer>
          </form>
        </EditorFrame>
      )}
    </div>
  );
}
