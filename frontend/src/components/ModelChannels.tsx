import { PersonalModelServices } from "./PersonalModelServices";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Server,
  Image,
  Film,
  MessageSquare,
  Pencil,
  RefreshCw,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import "./model-channels.css";
import { EditorFrame } from "./ModelEditorFrame";
import { CreativeModels, PriceFields, type ModelPrice } from "./CreativeModels";
import { ConnectModelService } from "./ConnectModelService";
import { DefaultModels } from "./DefaultModels";
import "./model-settings.css";

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
  price: ModelPrice;
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

export function ModelChannels({
  projectId,
  initialTab: _initialTab = "defaults",
}: {
  projectId: string;
  initialTab?: "defaults" | "services";
}) {
  const [tab, setTab] = useState<string>("personal");
  const [connecting, setConnecting] = useState(false);
  const [manualRequest, setManualRequest] = useState(false);
  const [notice, setNotice] = useState("");
  const qc = useQueryClient();
  return (
    <div className="model-config-workspace">
      <nav className="model-settings-nav" aria-label="模型设置分区">
        <button
          aria-pressed={tab === "personal"}
          onClick={() => setTab("personal")}
        >
          我的模型服务
        </button>
        <button
          aria-pressed={tab === "defaults"}
          onClick={() => setTab("defaults")}
        >
          项目默认模型
        </button>
        <button
          aria-pressed={tab === "services"}
          onClick={() => setTab("services")}
        >
          项目专用服务
        </button>
        <button
          aria-pressed={tab === "advanced"}
          onClick={() => setTab("advanced")}
        >
          高级路由
        </button>
      </nav>
      {notice && (
        <p role="status" className="model-settings-notice">
          {notice}
        </p>
      )}
      {tab === "personal" ? (
        <PersonalModelServices projectId={projectId} />
      ) : tab === "defaults" ? (
        <DefaultModels
          projectId={projectId}
          connect={() => setTab("personal")}
          services={() => setTab("services")}
        />
      ) : tab === "services" ? (
        <ChannelsPane
          projectId={projectId}
          connectService={() => setConnecting(true)}
          manualRequest={manualRequest}
          manualHandled={() => setManualRequest(false)}
        />
      ) : (
        <CreativeModels
          projectId={projectId}
          openChannels={() => setTab("services")}
          connectService={() => setConnecting(true)}
        />
      )}
      {connecting && (
        <ConnectModelService
          projectId={projectId}
          close={() => setConnecting(false)}
          custom={() => {
            setConnecting(false);
            setTab("services");
            setManualRequest(true);
          }}
          saved={async (message) => {
            await Promise.all(
              [
                "model-channels",
                "creative-models",
                "providers",
                "capabilities",
              ].map((key) =>
                qc.invalidateQueries({ queryKey: [key, projectId] }),
              ),
            );
            setNotice(message);
            setConnecting(false);
            setTab("defaults");
          }}
        />
      )}
    </div>
  );
}

function ChannelsPane({
  projectId,
  connectService,
  manualRequest,
  manualHandled,
}: {
  projectId: string;
  connectService: () => void;
  manualRequest: boolean;
  manualHandled: () => void;
}) {
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
  useEffect(() => {
    if (manualRequest) {
      setChannelEdit(null);
      manualHandled();
    }
  }, [manualRequest, manualHandled]);
  const [notice, setNotice] = useState("");
  const [directory, setDirectory] = useState<{
    channel: string;
    models: string[];
    truncated: boolean;
  } | null>(null);

  const refresh = async () => {
    for (const key of [
      "model-channels",
      "creative-models",
      "providers",
      "capabilities",
    ])
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
    <section>
      <header className="model-settings-heading">
        <div>
          <h2>服务连接</h2>
          <p>管理当前项目使用的模型服务。一份连接可以提供多个模型。</p>
        </div>
        <Button onClick={connectService}>
          <Plus size={15} />
          添加服务
        </Button>
      </header>
      {(notice || migrate.error || discover.error) && (
        <p role="status" className="model-settings-notice">
          {migrate.error?.message || discover.error?.message || notice}
        </p>
      )}
      {data.legacy_count > 0 && (
        <div className="model-legacy">
          <div>
            <strong>已有连接可以沿用</strong>
            <p>
              将 {data.legacy_count}{" "}
              套旧配置整理到此处，密钥和历史任务保持不变。
            </p>
          </div>
          <Button
            variant="outline"
            disabled={migrate.isPending}
            onClick={() => migrate.mutate()}
          >
            整理已有连接
          </Button>
        </div>
      )}
      <div className="service-list">
        {data.channels.map((channel) => (
          <article className="service-item" key={channel.id}>
            <header className="service-item-header">
              <Server size={18} />
              <div className="service-item-copy">
                <strong>{channel.name}</strong>
                <small>
                  {channel.models.length} 个模型 ·{" "}
                  {!channel.enabled
                    ? "已停用"
                    : channel.has_token ||
                        (channel.models.every((m) => m.protocol === "mock") &&
                          channel.models.length)
                      ? "已保存连接"
                      : "待填写密钥"}
                </small>
              </div>
              <div className="service-item-actions">
                <Button
                  variant="outline"
                  onClick={() => setChannelEdit(channel)}
                >
                  <Pencil size={13} />
                  编辑连接
                </Button>
                <Button
                  variant="ghost"
                  aria-expanded={selected === channel.id}
                  onClick={() =>
                    setSelected(selected === channel.id ? "" : channel.id)
                  }
                >
                  管理模型
                </Button>
              </div>
            </header>
            {selected !== channel.id && (
              <div className="service-model-chips">
                {channel.models.length ? (
                  channel.models
                    .slice(0, 8)
                    .map((m) => <span key={m.id}>{m.name}</span>)
                ) : (
                  <span>添加模型后即可用于创作</span>
                )}
                {channel.models.length > 8 && (
                  <span>+{channel.models.length - 8}</span>
                )}
              </div>
            )}
            {selected === channel.id && (
              <div className="service-expanded">
                <div className="service-tools">
                  <h3>此服务提供的模型</h3>
                  <div>
                    <Button
                      variant="outline"
                      disabled={
                        discover.isPending ||
                        !channel.has_token ||
                        !channel.endpoint
                      }
                      onClick={() => discover.mutate(channel.id)}
                    >
                      <RefreshCw size={13} />
                      {discover.isPending ? "读取中…" : "读取模型"}
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => setModelEdit({ channel, model: null })}
                    >
                      <Plus size={13} />
                      添加模型
                    </Button>
                  </div>
                </div>
                {directory?.channel === channel.id && (
                  <p className="model-settings-footnote">
                    已读取 {directory.models.length}{" "}
                    个模型。点击“添加模型”选择需要的模型与类型。
                  </p>
                )}
                {channel.models.map((model) => (
                  <div className="service-model-row" key={model.id}>
                    {model.modality === "image" ? (
                      <Image size={17} />
                    ) : model.modality === "video" ? (
                      <Film size={17} />
                    ) : (
                      <MessageSquare size={17} />
                    )}
                    <div>
                      <strong>{model.name}</strong>
                      <p>{model.model}</p>
                    </div>
                    <span>
                      {model.enabled ? kinds[model.modality] : "已停用"}
                    </span>
                    <button
                      onClick={() => setModelEdit({ channel, model })}
                      aria-label={`编辑模型 ${model.name}`}
                    >
                      编辑
                    </button>
                  </div>
                ))}
                {!channel.models.length && (
                  <p className="model-settings-footnote">
                    从服务读取模型编号，或手动添加你已开通的模型。
                  </p>
                )}
              </div>
            )}
          </article>
        ))}
      </div>
      {!data.channels.length && (
        <div className="model-settings-empty">
          <Server size={28} />
          <p>还没有连接模型服务</p>
          <Button onClick={connectService}>添加第一个服务</Button>
        </div>
      )}
      <p className="model-settings-footnote">
        连接对当前项目共享。密钥仅保存在服务端；修改连接不会改写已有作品。
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
            setNotice("连接已保存");
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
      title={channel ? "编辑服务连接" : "添加自定义服务"}
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
            服务名称
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
                channel?.has_token ? "已保存，留空保留" : "输入服务密钥"
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
            启用服务
          </label>
          <small>停用服务会暂停旗下模型的新调用，已有结果仍保留。</small>
          {save.error && (
            <p role="alert" className="text-danger">
              {save.error.message}
            </p>
          )}
          <Button type="submit" disabled={!name.trim()}>
            {save.isPending ? "保存中…" : "保存连接"}
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
  const [price, setPrice] = useState<ModelPrice>(
    model?.price || { mode: "unconfigured", amount: "0" },
  );
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
        ? protocol === "ark_image"
          ? ["size"]
          : ["size", "quality"]
        : protocol === "openai_video"
          ? ["duration", "size"]
          : protocol === "minimax_video"
            ? ["duration", "resolution"]
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
        price:
          modality === "text" ? { mode: "unconfigured", amount: "0" } : price,
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
                setPrice({ mode: "unconfigured", amount: "0" });
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
                setPrice({ mode: "unconfigured", amount: "0" });
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
              创建、查询和下载视频。每次一个视频；停止本地等待不保证上游停止计费。
            </p>
          )}
          {modality !== "text" && (
            <>
              <PriceFields
                value={price}
                onChange={setPrice}
                video={modality === "video"}
              />
              <label>
                最多参考图片数
                <Input
                  type="number"
                  min={0}
                  max={
                    (
                      {
                        openai_video: 1,
                        minimax_video: 1,
                        ark_video: 2,
                        ark_image: 14,
                      } as Record<string, number>
                    )[protocol] ?? 16
                  }
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
