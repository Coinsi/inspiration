import { useState, useRef, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Server,
  Image,
  Film,
  MessageSquare,
  Pencil,
  X,
  RefreshCw,
  KeyRound,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import "./model-channels.css";

type Model = {
  id: string;
  provider_name: string;
  name: string;
  model: string;
  protocol: string;
  modality: string;
  enabled: boolean;
  is_default: boolean;
  revision: number;
  max_reference_images: number;
  mask_editing: boolean;
  max_video_seconds: number;
  parameters: Record<string, (string | number)[]>;
};
type Channel = {
  id: string;
  name: string;
  endpoint: string;
  enabled: boolean;
  has_token: boolean;
  revision: number;
  models: Model[];
};
type Catalog = {
  channels: Channel[];
  legacy_count: number;
  protocols: Record<string, { name: string; types: string[] }>;
};
const kinds: Record<string, string> = {
  image: "图片",
  video: "视频",
  text: "文字",
};
const paramNames: Record<string, string> = {
  size: "画面尺寸",
  quality: "图片质量",
  duration: "时长（秒）",
  aspect_ratio: "画面比例",
  resolution: "清晰度",
};
const field = "h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm";

export function ModelChannels({ projectId }: { projectId: string }) {
  const base = `/projects/${projectId}/model-channels`,
    qc = useQueryClient();
  const query = useQuery({
    queryKey: ["model-channels", projectId],
    queryFn: () => api.get<Catalog>(base),
  });
  const [selected, setSelected] = useState("");
  const [channelEdit, setChannelEdit] = useState<Channel | null | undefined>();
  const [modelEdit, setModelEdit] = useState<{
    channel: Channel;
    model: Model | null;
  } | null>(null);
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const [directory, setDirectory] = useState<{
    channel: string;
    models: string[];
    truncated: boolean;
  } | null>(null);
  const active =
    query.data?.channels.find((c) => c.id === selected) ||
    query.data?.channels[0];
  const refresh = async () => {
    for (const key of ["model-channels", "providers", "capabilities"])
      await qc.invalidateQueries({ queryKey: [key, projectId] });
  };
  const discover = useMutation({
    mutationFn: (id: string) =>
      api.post<{ models: string[]; truncated: boolean; message: string }>(
        `${base}/${id}/discover`,
      ),
    onSuccess: (r, id) => {
      setDirectory({ channel: id, ...r });
      setNotice(r.message);
    },
  });
  const migrate = useMutation({
    mutationFn: () => api.post<{ imported: number }>(`${base}/import-legacy`),
    onSuccess: async (r) => {
      setNotice(`已迁移 ${r.imported} 套原配置，原有任务和模型标识保持可用`);
      await refresh();
    },
  });
  if (query.isPending)
    return (
      <p className="p-6" role="status">
        正在读取渠道与模型…
      </p>
    );
  if (query.error)
    return (
      <div className="p-6 space-y-3" role="alert">
        <p>{query.error.message}</p>
        <Button onClick={() => void query.refetch()}>重新读取</Button>
      </div>
    );
  const data = query.data!;
  return (
    <section className="model-center">
      <header className="model-center-heading">
        <div>
          <span className="model-eyebrow">创作设置 / MODEL CONNECTIONS</span>
          <h1>渠道与模型</h1>
          <p>统一连接服务，为图片、视频和文字创作选择合适的模型。</p>
        </div>
        <Button onClick={() => setChannelEdit(null)}>
          <Plus size={16} />
          添加渠道
        </Button>
      </header>
      {(notice || migrate.error || discover.error) && (
        <p
          className={`model-notice ${migrate.error || discover.error ? "text-danger" : ""}`}
          role="status"
        >
          {migrate.error?.message || discover.error?.message || notice}
        </p>
      )}
      {data.legacy_count > 0 && (
        <div className="model-legacy">
          <div>
            <strong>发现 {data.legacy_count} 套原有配置</strong>
            <p>迁移后可按渠道管理；已有生成记录、密钥和模型标识都会保留。</p>
          </div>
          <Button
            variant="outline"
            disabled={migrate.isPending}
            onClick={() => migrate.mutate()}
          >
            {migrate.isPending ? "迁移中…" : "迁移原配置"}
          </Button>
        </div>
      )}
      <div className="model-layout">
        <aside className="model-channel-list" aria-label="模型渠道">
          <div className="model-section-title">
            服务渠道 <span>{data.channels.length}</span>
          </div>
          {data.channels.map((c) => (
            <button
              key={c.id}
              className={`model-channel ${active?.id === c.id ? "is-selected" : ""}`}
              onClick={() => {
                setSelected(c.id);
                setFilter("");
              }}
            >
              <Server size={18} />
              <span>
                <strong>{c.name}</strong>
                <small>
                  {c.models.length} 个模型 · {c.enabled ? "已启用" : "已停用"}
                </small>
              </span>
              <i className={c.enabled ? "is-on" : ""} />
            </button>
          ))}
          {!data.channels.length && (
            <p className="model-empty-small">
              添加第一个渠道，或迁移原有配置。
            </p>
          )}
          <p className="model-channel-help">
            <KeyRound size={14} />
            密钥仅在服务端保存，编辑时留空即保留。
          </p>
        </aside>
        <div className="model-content">
          {active ? (
            <>
              <header className="model-active-heading">
                <div>
                  <h2>{active.name}</h2>
                  <p>{active.endpoint || "本地离线测试，无服务地址"}</p>
                  <small>
                    {active.has_token ? "已保存密钥" : "未设置密钥"} ·{" "}
                    {active.enabled
                      ? "渠道已启用"
                      : "渠道已停用，所有模型暂停使用"}
                  </small>
                </div>
                <Button
                  variant="outline"
                  onClick={() => setChannelEdit(active)}
                >
                  <Pencil size={14} />
                  编辑渠道
                </Button>
              </header>
              <div className="model-toolbar">
                <div className="model-filters">
                  {["", "image", "video", "text"].map((k) => (
                    <button
                      key={k}
                      className={filter === k ? "is-selected" : ""}
                      onClick={() => setFilter(k)}
                    >
                      {kinds[k] || "全部"}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2 flex-wrap">
                  <Button
                    variant="outline"
                    disabled={
                      discover.isPending ||
                      !active.has_token ||
                      !active.endpoint
                    }
                    onClick={() => discover.mutate(active.id)}
                  >
                    <RefreshCw size={14} />
                    {discover.isPending ? "读取中…" : "读取模型目录"}
                  </Button>
                  <Button
                    onClick={() =>
                      setModelEdit({ channel: active, model: null })
                    }
                  >
                    <Plus size={14} />
                    添加模型
                  </Button>
                </div>
              </div>
              {directory?.channel === active.id && (
                <div className="model-directory">
                  <strong>
                    目录已读取 · {directory.models.length} 个模型
                    {directory.truncated ? "（仅显示前1000个）" : ""}
                  </strong>
                  <p>
                    添加模型时可从编号列表选择，再确认模型类型与协议。读取目录不会发起生成。
                  </p>
                </div>
              )}
              <div className="model-cards">
                {active.models
                  .filter((m) => !filter || m.modality === filter)
                  .map((m) => (
                    <article
                      key={m.id}
                      className={`model-card ${!m.enabled || !active.enabled ? "is-disabled" : ""}`}
                    >
                      <div className="model-card-top">
                        <span className="model-type-icon">
                          {m.modality === "image" ? (
                            <Image size={20} />
                          ) : m.modality === "video" ? (
                            <Film size={20} />
                          ) : (
                            <MessageSquare size={20} />
                          )}
                        </span>
                        <span className="model-tag">{kinds[m.modality]}</span>
                        {m.is_default && (
                          <span className="model-tag is-default">默认</span>
                        )}
                        <button
                          aria-label={`编辑模型 ${m.name}`}
                          onClick={() =>
                            setModelEdit({ channel: active, model: m })
                          }
                        >
                          <Pencil size={15} />
                        </button>
                      </div>
                      <h3>{m.name}</h3>
                      <p className="model-id">
                        {m.model || "沿用原配置默认模型"}
                      </p>
                      <p>{data.protocols[m.protocol]?.name || m.protocol}</p>
                      <footer>
                        <span>
                          {m.enabled && active.enabled
                            ? "可供创作选择"
                            : "已停用"}
                        </span>
                        <span>
                          {m.max_reference_images
                            ? `最多 ${m.max_reference_images} 张参考图`
                            : ""}
                        </span>
                      </footer>
                    </article>
                  ))}
              </div>
              {!active.models.some((m) => !filter || m.modality === filter) && (
                <div className="model-empty">
                  <Plus size={28} />
                  <h3>在这个渠道添加模型</h3>
                  <p>
                    同一地址和密钥可以连接多个模型；每个模型独立配置类型与能力。
                  </p>
                </div>
              )}
            </>
          ) : (
            <div className="model-empty">
              <Server size={32} />
              <h2>连接你的创作服务</h2>
              <p>准备服务地址与 API Key，再添加需要使用的模型。</p>
              <Button onClick={() => setChannelEdit(null)}>
                添加第一个渠道
              </Button>
            </div>
          )}
        </div>
      </div>
      <p className="model-footnote">
        支持兼容协议不代表所有厂商原生接口都可直接使用。目录测试只验证连接；时长、画幅和参考图能力请以渠道说明为准。
      </p>
      {channelEdit !== undefined && (
        <ChannelEditor
          key={channelEdit?.id || "new"}
          base={base}
          channel={channelEdit}
          close={() => setChannelEdit(undefined)}
          saved={async (id) => {
            setSelected(id);
            await refresh();
            setChannelEdit(undefined);
            setNotice("渠道已保存");
          }}
        />
      )}
      {modelEdit && (
        <ModelEditor
          key={modelEdit.model?.id || `new-${modelEdit.channel.id}`}
          base={base}
          channel={modelEdit.channel}
          model={modelEdit.model}
          protocols={data.protocols}
          directory={
            directory?.channel === modelEdit.channel.id ? directory.models : []
          }
          close={() => setModelEdit(null)}
          saved={async () => {
            await refresh();
            setModelEdit(null);
            setNotice(
              "模型已保存，可在镜头与画布中选择；默认文字模型用于故事、助手和 Agent",
            );
          }}
        />
      )}
    </section>
  );
}

function EditorFrame({
  title,
  children,
  close,
  dirty,
  busy,
}: {
  title: string;
  children: React.ReactNode;
  close: () => void;
  dirty: boolean;
  busy: boolean;
}) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  const dismiss = () => {
    if (!busy && (!dirty || window.confirm("放弃尚未保存的修改？"))) close();
  };
  return (
    <div
      className="model-overlay"
      onKeyDown={(e) => {
        if (e.key === "Tab") {
          const elements = [
            ...(dialog.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"]',
            ) || []),
          ];
          const first = elements[0],
            last = elements[elements.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
        if (e.key === "Escape") {
          e.stopPropagation();
          dismiss();
        }
      }}
    >
      <section
        className="model-dialog"
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <button aria-label="关闭编辑" disabled={busy} onClick={dismiss}>
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

function ChannelEditor({
  base,
  channel,
  close,
  saved,
}: {
  base: string;
  channel: Channel | null;
  close: () => void;
  saved: (id: string) => Promise<void>;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(channel?.name || ""),
    [endpoint, setEndpoint] = useState(channel?.endpoint || ""),
    [token, setToken] = useState(""),
    [enabled, setEnabled] = useState(channel?.enabled ?? true),
    [clear, setClear] = useState(false),
    [dirty, setDirty] = useState(false);
  const [requestId] = useState(() => crypto.randomUUID());
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name,
        endpoint,
        token: token || null,
        clear_token: clear,
        enabled,
        revision: channel?.revision,
        request_id: requestId,
      };
      return channel
        ? api.put<{ id: string }>(`${base}/${channel.id}`, body)
        : api.post<{ id: string }>(base, body);
    },
    onSuccess: async (r) => {
      setToken("");
      await saved(r.id);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "VERSION_CONFLICT")
        void qc.invalidateQueries({ queryKey: ["model-channels"] });
    },
  });
  return (
    <EditorFrame
      title={channel ? "编辑渠道" : "添加渠道"}
      close={close}
      dirty={dirty}
      busy={save.isPending}
    >
      <form
        onChange={() => setDirty(true)}
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <fieldset disabled={save.isPending} className="model-form">
          <label>
            渠道名称
            <Input
              autoFocus
              required
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：创作服务 / 自建网关"
            />
          </label>
          <label>
            服务地址
            <Input
              type="url"
              maxLength={512}
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              placeholder="https://你的服务地址/v1"
            />
            <small>填写服务提供的基础地址；离线测试可以留空。</small>
          </label>
          <label>
            API Key
            <Input
              type="password"
              autoComplete="new-password"
              value={token}
              disabled={clear}
              onChange={(e) => setToken(e.target.value)}
              placeholder={
                channel?.has_token ? "已保存，留空保留" : "输入渠道密钥"
              }
            />
          </label>
          {channel?.has_token && (
            <label className="model-check">
              <input
                type="checkbox"
                checked={clear}
                onChange={(e) => {
                  setClear(e.target.checked);
                  setToken("");
                }}
              />
              清除已保存的密钥
            </label>
          )}
          <label className="model-check">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            启用渠道
          </label>
          <small>停用渠道会暂停旗下模型的新调用，已有结果仍保留。</small>
          {save.error && (
            <p role="alert" className="text-danger">
              {save.error.message}
            </p>
          )}
          <Button type="submit" disabled={!name.trim()}>
            {save.isPending ? "保存中…" : "保存渠道"}
          </Button>
        </fieldset>
      </form>
    </EditorFrame>
  );
}

function ModelEditor({
  base,
  channel,
  model,
  protocols,
  directory,
  close,
  saved,
}: {
  base: string;
  channel: Channel;
  model: Model | null;
  protocols: Catalog["protocols"];
  directory: string[];
  close: () => void;
  saved: () => Promise<void>;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(model?.name || ""),
    [code, setCode] = useState(model?.model || ""),
    [protocol, setProtocol] = useState(model?.protocol || "gpt_image"),
    [modality, setModality] = useState(model?.modality || "image"),
    [enabled, setEnabled] = useState(model?.enabled ?? true),
    [isDefault, setDefault] = useState(model?.is_default ?? false),
    [refs, setRefs] = useState(model?.max_reference_images ?? 0),
    [mask, setMask] = useState(model?.mask_editing ?? false),
    [seconds, setSeconds] = useState(model?.max_video_seconds ?? 12),
    [dirty, setDirty] = useState(false);
  const [parameters, setParameters] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(model?.parameters || {}).map(([k, v]) => [
        k,
        v.join(", "),
      ]),
    ),
  );
  const [requestId] = useState(() => crypto.randomUUID());
  const keys =
    modality === "text"
      ? []
      : modality === "image"
        ? ["size", "quality"]
        : protocol === "openai_video"
          ? ["duration", "size"]
          : ["duration", "aspect_ratio", "resolution"];
  const save = useMutation({
    mutationFn: () => {
      const params = Object.fromEntries(
        keys
          .filter((k) => parameters[k]?.trim())
          .map((k) => [
            k,
            parameters[k]
              .split(/[,，]/)
              .map((v) => (k === "duration" ? Number(v.trim()) : v.trim()))
              .filter((v) => v !== ""),
          ]),
      );
      const body = {
        name,
        model: code,
        protocol,
        modality,
        enabled,
        is_default: modality === "text" && isDefault,
        revision: model?.revision,
        request_id: requestId,
        max_reference_images: modality === "text" ? 0 : refs,
        mask_editing: protocol === "gpt_image" && mask,
        max_video_seconds: seconds,
        parameters: params,
      };
      return model
        ? api.put(`${base}/${channel.id}/models/${model.id}`, body)
        : api.post(`${base}/${channel.id}/models`, body);
    },
    onSuccess: saved,
    onError: (e) => {
      if (e instanceof ApiError && e.code === "VERSION_CONFLICT")
        void qc.invalidateQueries({ queryKey: ["model-channels"] });
    },
  });
  return (
    <EditorFrame
      title={model ? "编辑模型" : "添加模型"}
      close={close}
      dirty={dirty}
      busy={save.isPending}
    >
      <form
        onChange={() => setDirty(true)}
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <fieldset className="model-form" disabled={save.isPending}>
          <p className="text-sm text-muted-foreground">
            使用「{channel.name}」的地址和密钥
          </p>
          <div className="model-form-grid">
            <label>
              显示名称
              <Input
                autoFocus
                required
                maxLength={100}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="创作时看到的名称"
              />
            </label>
            <label>
              模型编号
              <Input
                required
                maxLength={128}
                list="channel-model-directory"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="服务提供的准确模型 ID"
              />
              <datalist id="channel-model-directory">
                {directory.map((id) => (
                  <option key={id} value={id} />
                ))}
              </datalist>
            </label>
          </div>
          <label>
            接口协议
            <select
              aria-label="接口协议"
              className={field}
              value={protocol}
              onChange={(e) => {
                const p = e.target.value;
                setProtocol(p);
                setModality(protocols[p].types[0]);
                setParameters({});
                setRefs(0);
                setMask(false);
                setDefault(false);
              }}
            >
              {Object.entries(protocols).map(([id, p]) => (
                <option key={id} value={id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            模型类型
            <select
              aria-label="模型类型"
              className={field}
              value={modality}
              onChange={(e) => {
                setModality(e.target.value);
                setParameters({});
              }}
            >
              {protocols[protocol].types.map((k) => (
                <option key={k} value={k}>
                  {kinds[k]}
                </option>
              ))}
            </select>
          </label>
          {protocol === "jimeng" && (
            <p className="model-notice">
              这是旧版自定义任务接口，不是火山即梦官方签名协议。仅适用于实现
              /submit 和 /status 的服务。
            </p>
          )}
          {protocol === "openai_video" && (
            <p className="model-notice">
              使用 /videos
              创建、查询和下载视频。每次一个视频；停止本地等待不保证上游停止计费。未配置价格，实际费用以上游账单为准。
            </p>
          )}
          {modality !== "text" && (
            <>
              <label>
                最多参考图片数
                <Input
                  type="number"
                  min={0}
                  max={protocol === "openai_video" ? 1 : 16}
                  value={refs}
                  onChange={(e) => {
                    setRefs(Number(e.target.value));
                    if (!Number(e.target.value)) setMask(false);
                  }}
                />
                <small>0 表示不使用参考图；只开启服务实际支持的能力。</small>
              </label>
              {protocol === "gpt_image" && refs > 0 && (
                <label className="model-check">
                  <input
                    type="checkbox"
                    checked={mask}
                    onChange={(e) => setMask(e.target.checked)}
                  />
                  支持蒙版局部重绘
                </label>
              )}
              {modality === "video" && (
                <label>
                  最大视频时长（秒）
                  <Input
                    type="number"
                    min={1}
                    max={120}
                    value={seconds}
                    onChange={(e) => setSeconds(Number(e.target.value))}
                  />
                </label>
              )}
              <div className="model-form-grid">
                {keys.map((k) => (
                  <label key={k}>
                    {paramNames[k]}
                    <Input
                      value={parameters[k] || ""}
                      onChange={(e) =>
                        setParameters((p) => ({ ...p, [k]: e.target.value }))
                      }
                      placeholder={
                        k === "duration"
                          ? "如：4, 8, 12"
                          : k === "size"
                            ? "如：1280x720, 720x1280"
                            : "多个选项用逗号分隔"
                      }
                    />
                  </label>
                ))}
              </div>
              <small>
                只填写渠道支持的选项；留空沿用上游默认，不会猜测模型能力。
              </small>
            </>
          )}
          <label className="model-check">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            启用模型
          </label>
          {modality === "text" && (
            <label className="model-check">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setDefault(e.target.checked)}
              />
              设为项目默认文字模型（故事、助手与 Agent）
            </label>
          )}
          {save.error && (
            <p role="alert" className="text-danger">
              {save.error.message}
            </p>
          )}
          <Button type="submit" disabled={!name.trim() || !code.trim()}>
            {save.isPending ? "保存中…" : "保存模型"}
          </Button>
        </fieldset>
      </form>
    </EditorFrame>
  );
}
