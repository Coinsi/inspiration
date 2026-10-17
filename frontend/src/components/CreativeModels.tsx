import { StudioSelect } from "@/components/ui/studio-select";
import { SearchField } from "@/components/ui/search-field";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Film,
  Image,
  MessageSquare,
  Plus,
  Pencil,
  Network,
  ArrowRight,
  FlaskConical,
} from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EditorFrame } from "./ModelEditorFrame";
import "./creative-models.css";

export type ModelPrice = {
  mode: "unconfigured" | "per_request" | "per_second";
  amount: string | number;
};
type Route = {
  profile_id: string;
  enabled: boolean;
  priority: number;
  weight: number;
  available?: boolean;
  reason?: string;
  channel_name?: string;
  model_name?: string;
};
type CreativeModel = {
  id: string;
  name: string;
  description: string;
  modality: string;
  enabled: boolean;
  is_default: boolean;
  revision: number;
  routes: Route[];
  available_routes: number;
  route_count: number;
  price_policy: string;
  price: ModelPrice;
};
type Profile = {
  id: string;
  name: string;
  model: string;
  modality: string;
  enabled: boolean;
  parameters: Record<string, (string | number)[]>;
};
type Channel = {
  id: string;
  name: string;
  enabled: boolean;
  models: Profile[];
};
const names: Record<string, string> = {
  image: "图片",
  video: "视频",
  text: "文字",
};
const emptyPrice: ModelPrice = { mode: "unconfigured", amount: "0" };

export function PriceFields({
  value,
  onChange,
  video,
}: {
  value: ModelPrice;
  onChange: (p: ModelPrice) => void;
  video: boolean;
}) {
  return (
    <div className="model-form-grid">
      <label>
        积分计价
        <StudioSelect
          className="creative-select"
          value={value.mode}
          onValueChange={(selectedValue) =>
            onChange({ ...value, mode: selectedValue as ModelPrice["mode"] })
          }
        >
          <option value="unconfigured">暂未配置</option>
          <option value="per_request">按结果数量</option>
          {video && <option value="per_second">按视频秒数</option>}
        </StudioSelect>
      </label>
      {value.mode !== "unconfigured" && (
        <label>
          {value.mode === "per_second" ? "每秒积分" : "每个结果积分"}
          <Input
            required
            type="number"
            min="0"
            max="1000000"
            step="0.0001"
            value={value.amount}
            onChange={(e) => onChange({ ...value, amount: e.target.value })}
          />
        </label>
      )}
      <small className="creative-price-note">
        用于项目内部配额。供应商实际费用以其账单为准；未配置不代表免费。
      </small>
    </div>
  );
}

export function CreativeModels({
  projectId,
  openChannels,
  connectService,
}: {
  projectId: string;
  openChannels: () => void;
  connectService: () => void;
}) {
  const root = `/projects/${projectId}`,
    qc = useQueryClient();
  const query = useQuery({
    queryKey: ["creative-models", projectId],
    queryFn: () => api.get<CreativeModel[]>(`${root}/creative-models`),
  });
  const channels = useQuery({
    queryKey: ["model-channels", projectId],
    queryFn: () => api.get<{ channels: Channel[] }>(`${root}/model-channels`),
  });
  const [edit, setEdit] = useState<CreativeModel | null | undefined>();
  const [simulation, setSimulation] = useState<CreativeModel | null>(null);
  const [filter, setFilter] = useState("all"),
    [search, setSearch] = useState(""),
    [notice, setNotice] = useState("");
  const refresh = async () => {
    for (const key of ["creative-models", "providers", "capabilities"])
      await qc.invalidateQueries({ queryKey: [key, projectId] });
  };
  const adopt = useMutation({
    mutationFn: () =>
      api.post<{ imported: number }>(`${root}/creative-models/import-profiles`),
    onSuccess: async (r) => {
      setNotice(
        `已整理 ${r.imported} 个模型；相同名称不会自动合并，可编辑模型绑定更多线路。`,
      );
      await refresh();
    },
  });
  const models = query.data || [],
    available = channels.data?.channels || [];
  const bound = new Set(
    models.flatMap((m) => m.routes.map((r) => r.profile_id)),
  );
  const unbound = available
    .flatMap((c) => c.models)
    .filter((m) => !bound.has(m.id));
  const visible = models.filter(
    (m) =>
      (filter === "all" || m.modality === filter) &&
      `${m.name} ${m.description}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <section className="model-center creative-center">
      <header className="model-center-heading">
        <div>
          <span className="model-eyebrow">创作设置 / MODEL LIBRARY</span>
          <h1>高级模型管理</h1>
          <p>
            为同一模型配置备用渠道、分配权重与内部积分。日常默认选择在“模型选择”中完成。
          </p>
        </div>
        <div className="model-heading-actions">
          <Button
            variant="outline"
            onClick={() => setEdit(null)}
            disabled={!available.some((c) => c.models.length)}
          >
            <Plus size={16} />
            自定义模型
          </Button>
          <Button onClick={connectService}>
            <Plus size={16} />
            连接模型服务
          </Button>
        </div>
      </header>
      {(query.error || channels.error) && (
        <div role="alert" className="model-notice">
          {query.error?.message || channels.error?.message}
          <Button
            variant="outline"
            onClick={() => {
              void query.refetch();
              void channels.refetch();
            }}
          >
            重新读取
          </Button>
        </div>
      )}
      {(notice || adopt.error) && (
        <p role="status" className="model-notice">
          {adopt.error?.message || notice}
        </p>
      )}
      {unbound.length > 0 && (
        <div className="model-legacy">
          <div>
            <strong>{unbound.length} 个渠道模型尚未加入创作目录</strong>
            <p>
              一键整理为独立创作模型，再按需要绑定备用渠道。已有配置和历史结果保留。
            </p>
          </div>
          <Button
            variant="outline"
            disabled={adopt.isPending}
            onClick={() => adopt.mutate()}
          >
            {adopt.isPending ? "整理中…" : "整理已有模型"}
          </Button>
        </div>
      )}
      <div className="creative-toolbar">
        <div className="creative-tabs" aria-label="模型类型">
          {["all", "image", "video", "text"].map((k) => (
            <button
              key={k}
              aria-pressed={filter === k}
              onClick={() => setFilter(k)}
            >
              {k === "all" ? "全部" : names[k]}
            </button>
          ))}
        </div>
        <SearchField
          className="w-full sm:w-72"
          label="搜索创作模型"
          placeholder="搜索模型名称或介绍"
          value={search}
          onValueChange={(value) => setSearch(value)}
        />
      </div>
      {query.isPending ? (
        <p role="status">正在读取模型…</p>
      ) : (
        <div className="creative-model-grid">
          {visible.map((m) => {
            const Icon =
              m.modality === "video"
                ? Film
                : m.modality === "text"
                  ? MessageSquare
                  : Image;
            return (
              <article className="creative-model-card" key={m.id}>
                <div className="creative-card-top">
                  <span className={`creative-model-icon is-${m.modality}`}>
                    <Icon size={22} />
                  </span>
                  <span
                    className={`creative-status ${m.enabled && m.available_routes ? "is-ready" : ""}`}
                  >
                    {!m.enabled
                      ? "已停用"
                      : m.available_routes
                        ? "已配置"
                        : "待配置渠道"}
                  </span>
                </div>
                <h2>
                  {m.name}
                  {m.is_default && <small>默认</small>}
                </h2>
                <p>
                  {m.description ||
                    `${names[m.modality]}创作模型，可绑定多个服务渠道。`}
                </p>
                <div className="creative-route-summary">
                  <Network size={15} />
                  <span>
                    {m.available_routes} / {m.route_count} 条线路可用
                  </span>
                </div>
                <div className="creative-card-channels">
                  {m.routes.slice(0, 3).map((r) => (
                    <span title={r.reason || r.model_name} key={r.profile_id}>
                      {r.channel_name}
                    </span>
                  ))}
                  {m.route_count > 3 && <span>+{m.route_count - 3}</span>}
                </div>
                <footer>
                  <span>
                    {m.price_policy === "channel"
                      ? "按实际渠道计积分"
                      : m.price.mode === "unconfigured"
                        ? "积分未配置"
                        : `${m.price.amount} 积分/${m.price.mode === "per_second" ? "秒" : "结果"}`}
                  </span>
                  <div>
                    {m.modality !== "text" && (
                      <button
                        aria-label={`检查 ${m.name} 的线路`}
                        onClick={() => setSimulation(m)}
                      >
                        <FlaskConical size={16} />
                      </button>
                    )}
                    <button
                      aria-label={`编辑 ${m.name}`}
                      onClick={() => setEdit(m)}
                    >
                      <Pencil size={16} />
                    </button>
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      )}
      {!query.isPending && !visible.length && (
        <div className="creative-empty">
          <Network size={36} />
          <h2>
            {models.length ? "没有找到匹配的模型" : "先连接服务，再开始创作"}
          </h2>
          <p>
            {models.length
              ? "试试其他名称或模型类型。"
              : "选择服务、填入密钥，勾选需要的模型；我们会自动完成配置与关联。"}
          </p>
          {!models.length && (
            <div className="model-heading-actions">
              <Button onClick={connectService}>
                选择模型服务
                <ArrowRight size={16} />
              </Button>
              <Button variant="outline" onClick={openChannels}>
                管理已有渠道
              </Button>
            </div>
          )}
        </div>
      )}
      <p className="creative-footnote">
        “已配置”表示存在可用配置，真实生成能力仍需验证。相同模型的不同渠道可配置不同优先级与分配权重。
      </p>
      {edit !== undefined && (
        <CreativeEditor
          root={root}
          model={edit}
          channels={available}
          close={() => setEdit(undefined)}
          saved={async () => {
            await refresh();
            setEdit(undefined);
          }}
        />
      )}
      {simulation && (
        <Simulation
          root={root}
          model={simulation}
          channels={available}
          close={() => setSimulation(null)}
        />
      )}
    </section>
  );
}

function CreativeEditor({
  root,
  model,
  channels,
  close,
  saved,
}: {
  root: string;
  model: CreativeModel | null;
  channels: Channel[];
  close: () => void;
  saved: () => Promise<void>;
}) {
  const [name, setName] = useState(model?.name || ""),
    [description, setDescription] = useState(model?.description || "");
  const [modality, setModality] = useState(model?.modality || "video"),
    [enabled, setEnabled] = useState(model?.enabled ?? true),
    [isDefault, setDefault] = useState(model?.is_default ?? false);
  const [routes, setRoutes] = useState<Route[]>(
    (model?.routes || []).map(({ profile_id, enabled, priority, weight }) => ({
      profile_id,
      enabled,
      priority,
      weight,
    })),
  );
  const [policy, setPolicy] = useState(model?.price_policy || "channel"),
    [price, setPrice] = useState<ModelPrice>(model?.price || emptyPrice);
  const [dirty, setDirty] = useState(false),
    [requestId] = useState(() => crypto.randomUUID());
  const options = channels.flatMap((c) =>
    c.models
      .filter((m) => m.modality === modality)
      .map((m) => ({ ...m, channel: c })),
  );
  const update = (id: string, values: Partial<Route>) => {
    setDirty(true);
    setRoutes((rs) =>
      rs.map((r) => (r.profile_id === id ? { ...r, ...values } : r)),
    );
  };
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name,
        description,
        modality,
        enabled,
        is_default: enabled && isDefault,
        routes,
        price_policy: policy,
        price: modality === "text" ? emptyPrice : price,
        revision: model?.revision,
        request_id: requestId,
      };
      return model
        ? api.put(`${root}/creative-models/${model.id}`, body)
        : api.post(`${root}/creative-models`, body);
    },
    onSuccess: saved,
  });
  return (
    <EditorFrame
      title={model ? "编辑创作模型" : "添加创作模型"}
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
          <div className="model-form-grid">
            <label>
              创作模型名称
              <Input
                autoFocus
                required
                maxLength={100}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：我的视频模型"
              />
            </label>
            <label>
              创作类型
              <StudioSelect
                className="creative-select"
                value={modality}
                disabled={!!model}
                onValueChange={(selectedValue) => {
                  setModality(selectedValue);
                  setRoutes([]);
                  setPrice(emptyPrice);
                }}
              >
                {Object.entries(names).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </StudioSelect>
            </label>
          </div>
          <label>
            模型介绍
            <Input
              maxLength={500}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="适合什么创作，有哪些特色"
            />
          </label>
          <div className="creative-editor-heading">
            <h3>供应商线路</h3>
            <span>{routes.length} 条已绑定</span>
          </div>
          <p className="creative-help">
            优先选择数值更高的优先级；同一优先级按权重分配。系统会先排除不支持本次输入和参数的线路。
          </p>
          <div className="creative-route-list">
            {options.map((p) => {
              const r = routes.find((r) => r.profile_id === p.id);
              return (
                <div
                  className={`creative-route ${r ? "is-bound" : ""}`}
                  key={p.id}
                >
                  <label className="creative-route-pick">
                    <input
                      type="checkbox"
                      checked={!!r}
                      onChange={(e) =>
                        setRoutes((rs) =>
                          e.target.checked
                            ? [
                                ...rs,
                                {
                                  profile_id: p.id,
                                  enabled: true,
                                  priority: 100,
                                  weight: 100,
                                },
                              ]
                            : rs.filter((r) => r.profile_id !== p.id),
                        )
                      }
                    />
                    <span>
                      <strong>
                        {p.channel.name} / {p.name}
                      </strong>
                      <small>
                        {p.model}
                        {!p.channel.enabled || !p.enabled ? " · 已停用" : ""}
                      </small>
                    </span>
                  </label>
                  {r && (
                    <div className="creative-route-options">
                      <label>
                        优先级
                        <Input
                          aria-label={`${p.channel.name} 优先级`}
                          required
                          type="number"
                          min={0}
                          max={10000}
                          value={r.priority}
                          onChange={(e) =>
                            update(p.id, { priority: Number(e.target.value) })
                          }
                        />
                      </label>
                      <label>
                        权重
                        <Input
                          aria-label={`${p.channel.name} 权重`}
                          required
                          type="number"
                          min={1}
                          max={10000}
                          value={r.weight}
                          onChange={(e) =>
                            update(p.id, { weight: Number(e.target.value) })
                          }
                        />
                      </label>
                      <label className="model-check">
                        <input
                          type="checkbox"
                          checked={r.enabled}
                          onChange={(e) =>
                            update(p.id, { enabled: e.target.checked })
                          }
                        />
                        启用线路
                      </label>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {!options.length && (
            <p className="model-notice">
              还没有此类型的渠道模型，请先在供应商渠道中添加。
            </p>
          )}
          {modality !== "text" && (
            <>
              <label>
                积分规则
                <StudioSelect
                  className="creative-select"
                  value={policy}
                  onValueChange={(selectedValue) => setPolicy(selectedValue)}
                >
                  <option value="channel">使用实际渠道的积分规则</option>
                  <option value="unified">为这个创作模型统一设置</option>
                </StudioSelect>
              </label>
              {policy === "unified" && (
                <PriceFields
                  value={price}
                  onChange={setPrice}
                  video={modality === "video"}
                />
              )}
            </>
          )}
          <div className="creative-switches">
            <label className="model-check">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              启用创作模型
            </label>
            <label className="model-check">
              <input
                type="checkbox"
                checked={isDefault}
                disabled={!enabled}
                onChange={(e) => setDefault(e.target.checked)}
              />
              设为此类型默认模型
            </label>
          </div>
          <small>保存会产生新版本；已提交任务保留原来的输入与线路配置。</small>
          {save.error && (
            <p role="alert" className="text-danger">
              {save.error.message}
            </p>
          )}
          <Button type="submit" disabled={!name.trim() || !routes.length}>
            {save.isPending ? "保存中…" : "保存创作模型"}
          </Button>
        </fieldset>
      </form>
    </EditorFrame>
  );
}

function Simulation({
  root,
  model,
  channels,
  close,
}: {
  root: string;
  model: CreativeModel;
  channels: Channel[];
  close: () => void;
}) {
  const [params, setParams] = useState<Record<string, string | number>>({});
  const fields: Record<string, (string | number)[]> = {};
  channels
    .flatMap((c) => c.models)
    .filter((p) => model.routes.some((r) => r.profile_id === p.id))
    .forEach((p) =>
      Object.entries(p.parameters).forEach(([k, vs]) => {
        fields[k] = [...new Set([...(fields[k] || []), ...vs])];
      }),
    );
  const probe = useMutation({
    mutationFn: () =>
      api.post<{
        candidates: {
          profile_id: string;
          channel_name: string;
          priority: number;
          weight: number;
          cost_points: number | null;
        }[];
        rejected: { reason: string }[];
        estimate: { points: number; detail: { message: string } };
        message: string;
      }>(`${root}/creative-models/${model.id}/simulate`, {
        request_type: model.modality,
        prompt: "",
        count: 1,
        provider_params: params,
      }),
  });
  const labels: Record<string, string> = {
    duration: "时长",
    size: "画面尺寸",
    aspect_ratio: "画幅比例",
    resolution: "清晰度",
    quality: "图片质量",
  };
  return (
    <EditorFrame
      title={`检查线路 · ${model.name}`}
      close={close}
      dirty={false}
      busy={probe.isPending}
    >
      <div className="model-form">
        <p className="creative-help">
          检查哪些线路支持这一组参数，不会向供应商发起生成，也不会扣费。
        </p>
        {Object.entries(fields).map(([k, vs]) => (
          <label key={k}>
            {labels[k] || k}
            <StudioSelect
              className="creative-select"
              value={String(params[k] ?? "")}
              onValueChange={(selectedValue) => {
                const value = vs.find((v) => String(v) === selectedValue);
                setParams((old) => {
                  const next = { ...old };
                  if (value === undefined) delete next[k];
                  else next[k] = value;
                  return next;
                });
                probe.reset();
              }}
            >
              <option value="">不指定</option>
              {vs.map((v) => (
                <option key={v} value={String(v)}>
                  {v}
                </option>
              ))}
            </StudioSelect>
          </label>
        ))}
        <Button onClick={() => probe.mutate()} disabled={probe.isPending}>
          {probe.isPending ? "检查中…" : "检查可用线路"}
        </Button>
        {probe.error && (
          <p role="alert" className="text-danger">
            {probe.error.message}
          </p>
        )}
        {probe.data && (
          <div className="creative-probe" role="status">
            <p>{probe.data.message}</p>
            {probe.data.candidates.map((c, i) => (
              <div key={c.profile_id}>
                <strong>
                  {i + 1}. {c.channel_name}
                </strong>
                <span>
                  优先级 {c.priority} · 权重 {c.weight} ·{" "}
                  {c.cost_points === null
                    ? "积分未配置"
                    : `${c.cost_points} 积分`}
                </span>
              </div>
            ))}
            <small>{probe.data.estimate.detail.message}</small>
            {probe.data.rejected.map((r, i) => (
              <p key={i}>排除线路：{r.reason}</p>
            ))}
          </div>
        )}
      </div>
    </EditorFrame>
  );
}
