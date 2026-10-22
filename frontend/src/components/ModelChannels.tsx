import { ModelDirectory, modelSuggestion } from "./ModelDirectory";
import { SelectField } from "./ui/select-field";
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
  Trash2,
} from "lucide-react";
import { useConfirm } from "./ui/confirm";
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
  max_reference_videos?: number;
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
  const confirm = useConfirm();
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
    code?: string;
  } | null>(null);
  useEffect(() => {
    if (manualRequest) {
      setChannelEdit(null);
      manualHandled();
    }
  }, [manualRequest, manualHandled]);
  const [notice, setNotice] = useState("");
  const [directoryOpen, setDirectoryOpen] = useState(false);
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
  const remove = useMutation({
    mutationFn: (channel: Channel) =>
      api.del(`${base}/${channel.id}?revision=${channel.revision}`),
    onSuccess: async () => {
      setSelected("");
      setDirectory(null);
      setNotice(
        "服务及其模型已删除，作品和历史记录保留。请重新检查项目默认模型。",
      );
      await refresh();
    },
  });
  async function confirmRemove(channel: Channel) {
    if (
      await confirm({
        title: `删除「${channel.name}」？`,
        message: `将移除本项目的服务连接、保存的密钥和 ${channel.models.length} 个模型。项目成员将无法继续使用它；已生成作品保留。默认模型若来自此服务，需要重新选择。`,
        confirmText: "删除服务",
        danger: true,
      })
    )
      remove.mutate(channel);
  }
  const discover = useMutation({
    mutationFn: (id: string) =>
      api.post<{ models: string[]; truncated: boolean; message: string }>(
        `${base}/${id}/discover`,
      ),
    onSuccess: (r, id) => {
      setDirectory({ channel: id, ...r });
      setDirectoryOpen(true);
      setNotice("");
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
      {(notice || migrate.error || discover.error || remove.error) && (
        <p role="status" className="model-settings-notice">
          {remove.error?.message ||
            migrate.error?.message ||
            discover.error?.message ||
            notice}
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
                  variant="ghost"
                  className="text-danger"
                  disabled={remove.isPending}
                  aria-label={`删除服务 ${channel.name}`}
                  onClick={() => void confirmRemove(channel)}
                >
                  <Trash2 size={14} />
                  删除
                </Button>

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
                      手动添加
                    </Button>
                  </div>
                </div>
                {directory?.channel === channel.id && (
                  <p className="model-settings-footnote">
                    已读取 {directory.models.length} 个模型。
                    <button
                      className="text-primary"
                      onClick={() => setDirectoryOpen(true)}
                    >
                      打开模型列表
                    </button>
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
                    从服务读取并选择模型，或手动填写已开通的模型编号。
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
      {directory && data.channels.some((c) => c.id === directory.channel) && (
        <ModelDirectory
          key={directory.channel}
          open={directoryOpen}
          name={data.channels.find((c) => c.id === directory.channel)!.name}
          models={directory.models}
          added={data.channels
            .find((c) => c.id === directory.channel)!
            .models.map((m) => m.model)}
          truncated={directory.truncated}
          close={() => setDirectoryOpen(false)}
          select={(code) => {
            setDirectoryOpen(false);
            setModelEdit({
              channel: data.channels.find((c) => c.id === directory.channel)!,
              model: null,
              code,
            });
          }}
          manual={() => {
            setDirectoryOpen(false);
            setModelEdit({
              channel: data.channels.find((c) => c.id === directory.channel)!,
              model: null,
            });
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
          initialCode={modelEdit.code}
          close={() => {
            setDirectoryOpen(!!modelEdit.code);
            setModelEdit(null);
          }}
          saved={async () => {
            await refresh();
            setDirectoryOpen(!!modelEdit.code);
            setModelEdit(null);
            setNotice(
              "模型已保存，可在创作时选择，或在项目默认模型中设为默认。",
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
  initialCode = "",
  close,
  saved,
}: {
  base: string;
  channel: Channel;
  model: Model | null;
  protocols: Catalog["protocols"];
  initialCode?: string;
  close: () => void;
  saved: () => Promise<void>;
}) {
  const qc = useQueryClient();
  const [price, setPrice] = useState<ModelPrice>(
    model?.price || { mode: "unconfigured", amount: "0" },
  );
  const [name, setName] = useState(model?.name || initialCode.slice(0, 100)),
    [code, setCode] = useState(model?.model || initialCode),
    [protocol, setProtocol] = useState(
      model?.protocol || modelSuggestion(initialCode).protocol,
    ),
    [modality, setModality] = useState(
      model?.modality || modelSuggestion(initialCode).modality,
    ),
    [enabled, setEnabled] = useState(model?.enabled ?? true),
    [isDefault, setDefault] = useState(model?.is_default ?? false),
    [refs, setRefs] = useState(model?.max_reference_images ?? 0),
    [videoRefs, setVideoRefs] = useState(model?.max_reference_videos ?? 0),
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
        max_reference_videos: modality === "video" && protocol === "video_v1" ? videoRefs : 0,
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
                value={code}
                onChange={(e) => {
                  const value = e.target.value;
                  setCode(value);
                  if (!model) {
                    if (!name || name === code.slice(0, 100))
                      setName(value.slice(0, 100));
                    const hint = modelSuggestion(value);
                    setModality(hint.modality);
                    setProtocol(hint.protocol);
                    setParameters({});
                    setRefs(0);
                    setMask(false);
                    setDefault(false);
                    setPrice({ mode: "unconfigured", amount: "0" });
                  }
                }}
                placeholder="服务提供的准确模型 ID"
              />
            </label>
          </div>
          <SelectField
            label="模型用途"
            value={modality}
            options={[
              { value: "", label: "请选择模型用途", disabled: true },
              ...Object.entries(kinds).map(([value, label]) => ({
                value,
                label,
              })),
            ]}
            onChange={(value) => {
              setModality(value);
              const hint = modelSuggestion(code);
              setProtocol(
                hint.modality === value
                  ? hint.protocol
                  : value === "text"
                    ? "cloud_llm"
                    : "",
              );
              setParameters({});
              setRefs(0);
              setMask(false);
              setDefault(false);
              setPrice({ mode: "unconfigured", amount: "0" });
              setDirty(true);
            }}
          />
          {modality && (
            <SelectField
              label="接入方式"
              value={protocol}
              options={[
                { value: "", label: "请选择接入方式", disabled: true },
                ...Object.entries(protocols)
                  .filter(([, p]) => p.types.includes(modality))
                  .map(([value, p]) => ({ value, label: p.name })),
              ]}
              onChange={(value) => {
                setProtocol(value);
                setParameters({});
                setRefs(0);
                setMask(false);
                setDirty(true);
              }}
            />
          )}
          {!model && modelSuggestion(code).modality && (
            <p className="text-xs text-muted-foreground">
              已按模型名称建议用途与接入方式，请以服务说明为准；可在上方调整。
            </p>
          )}
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
          {protocol === "video_v1" && (
            <p className="model-notice">
              使用 video.v1 JSON 格式创建视频。通过公网 HTTPS 网站提交时，
              系统会自动生成临时素材链接，无需单独填写域名。
              请按渠道能力设置图片和视频参考上限，可混合引用；不支持首尾帧角色。
              请按服务商文档填写时长选项，提示词中的秒数不能代替时长设置。
            </p>
          )}
          {modality && protocol && modality !== "text" && (
            <details className="model-optional-settings">
              <summary>高级选项 · 参考图、生成参数与费用</summary>
              <div className="model-form">
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
                          video_v1: 14,
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
                {protocol === "video_v1" && modality === "video" && (
                  <label>最多参考视频数
                    <Input type="number" min={0} max={10} value={videoRefs} onChange={e => setVideoRefs(Number(e.target.value))} />
                    <small>0 表示不接收视频；仅对渠道明确支持视频参考的模型启用，可与参考图片混用。</small>
                  </label>
                )}
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
              </div>
            </details>
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
          <Button
            type="submit"
            disabled={!name.trim() || !code.trim() || !modality || !protocol}
          >
            {save.isPending ? "保存中…" : "保存模型"}
          </Button>
        </fieldset>
      </form>
    </EditorFrame>
  );
}
