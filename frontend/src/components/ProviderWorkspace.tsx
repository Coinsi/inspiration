import { ProviderMediaTest } from "./ProviderMediaTest";
import { ProviderModelPicker } from "./ProviderModelPicker";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Search,
  ExternalLink,
  Trash2,
  Image as ImageIcon,
  Clapperboard,
  MessageSquare,
  RefreshCw,
  SlidersHorizontal,
  ChevronRight,
} from "lucide-react";
import { api } from "@/lib/api";
import {
  directoryEntries,
  type ModelDirectoryResult,
  type ModelListItem,
} from "@/lib/model-connection";
import { modelConfigRoot, verificationLabel } from "@/lib/model-config-scope";
import { useAuth } from "@/lib/auth";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { useConfirm } from "./ui/confirm";
import { ModelBrandIcon } from "./workbench/ModelBrandIcon";
import type { CreationMode } from "./workbench/ConversationMedia";
import {
  ModelEditor,
  type Channel,
  type Catalog,
  type Model,
  type SuggestedModel,
} from "./ModelChannels";
import { ProviderTemplateEditor } from "./ProviderTemplateEditor";
import "./provider-workspace.css";

export type ProviderRoute = {
  modality: string;
  protocol: string;
  sdk_provider: string;
};
export type ProviderTemplate = {
  id: string;
  name: string;
  icon: string;
  endpoint: string;
  key_url: string;
  note: string;
  discovery_protocol?: string;
  enabled: boolean;
  revision: number;
  routes: ProviderRoute[];
};
const purposes = [
  { id: "text", name: "文字与理解", icon: MessageSquare },
  { id: "image", name: "图片生成", icon: ImageIcon },
  { id: "video", name: "视频生成", icon: Clapperboard },
];
type WorkspaceProps = {
  syncPurpose?: boolean;
  initialPurpose?: CreationMode;
} & (
  | { personal?: false; projectId: string }
  | { personal: true; projectId?: string }
);

export function ProviderWorkspace({
  projectId = "",
  personal = false,
  syncPurpose = true,
  initialPurpose = "text",
}: WorkspaceProps) {
  const confirm = useConfirm();
  const [dirty, setDirty] = useState(false);
  const [params, setParams] = useSearchParams();
  const [localPurpose, setLocalPurpose] = useState<string>(initialPurpose);
  const purpose = syncPurpose
    ? (purposes.find((p) => p.id === params.get("purpose"))?.id ?? "text")
    : localPurpose;
  async function navigate(next: string, nextPurpose?: string) {
    if (
      dirty &&
      !(await confirm({
        title: "放弃未保存的连接修改？",
        message: "切换服务后，这次尚未保存的地址和密钥会丢失。",
        confirmText: "放弃修改",
        cancelText: "继续编辑",
      }))
    )
      return;
    setDirty(false);
    setSelection(next);
    if (nextPurpose) {
      if (syncPurpose) {
        const updated = new URLSearchParams(params);
        updated.set("purpose", nextPurpose);
        setParams(updated, { replace: true, preventScrollReset: true });
      } else {
        setLocalPurpose(nextPurpose);
      }
      setSearch("");
    }
  }
  const platform = !personal && projectId === "platform";
  const base = personal
    ? "/me/model-channels"
    : `${modelConfigRoot(projectId)}/model-channels`;
  const { me } = useAuth();
  const qc = useQueryClient();
  const [search, setSearch] = useState(""),
    [selection, setSelection] = useState(""),
    [templateEdit, setTemplateEdit] = useState<
      ProviderTemplate | null | undefined
    >();
  const query = useQuery({
    queryKey: personal
      ? ["personal-model-channels", me?.user.id]
      : ["model-channels", projectId],
    queryFn: () => api.get<Catalog>(base),
    enabled: !personal || !!me,
  });
  const templates = useQuery({
    queryKey: ["provider-templates", platform],
    queryFn: () =>
      api.get<ProviderTemplate[]>(
        platform
          ? "/admin/model-provider-templates"
          : "/model-provider-templates",
      ),
  });
  const refresh = async () => {
    await Promise.all(
      (personal
        ? [
            "personal-model-channels",
            "personal-model-services",
            "providers",
            "capabilities",
          ]
        : ["model-channels", "creative-models", "providers", "capabilities"]
      ).map((key) => qc.invalidateQueries({ queryKey: [key] })),
    );
  };
  const channels = query.data?.channels || [];
  const directory = templates.data || [];
  const matches = (name: string) =>
    name.toLowerCase().includes(search.toLowerCase());
  function channelTemplate(channel: Channel) {
    return (
      directory.find((t) => t.id === channel.provider_template_id) ??
      directory.find(
        (t) =>
          !!t.endpoint &&
          t.endpoint.replace(/\/$/, "") === channel.endpoint.replace(/\/$/, ""),
      )
    );
  }

  const supportsPurpose = (channel: Channel) =>
    channel.models.some((model) => model.modality === purpose) ||
    channelTemplate(channel)?.routes.some(
      (route) => route.modality === purpose,
    );
  const applicableChannels = channels.filter(supportsPurpose);
  const visibleChannels = applicableChannels.filter((channel) =>
    matches(channel.name),
  );
  const otherCustomChannels = channels.filter(
    (channel) =>
      !channelTemplate(channel) &&
      !supportsPurpose(channel) &&
      matches(channel.name),
  );
  const available = directory.filter(
    (t) =>
      t.enabled &&
      t.routes.some((r) => r.modality === purpose) &&
      !applicableChannels.some((c) => channelTemplate(c)?.id === t.id),
  );
  const availableMatches = available.filter((t) => matches(t.name));
  const fallback = visibleChannels[0]
    ? "channel:" + visibleChannels[0].id
    : availableMatches[0]
      ? "template:" + availableMatches[0].id
      : "";
  const current = selection || fallback;
  const activeChannel = channels.find((c) => "channel:" + c.id === current);
  const activeTemplate = directory.find((t) => "template:" + t.id === current);
  const templateFor =
    activeTemplate ?? (activeChannel && channelTemplate(activeChannel));
  const purposeName = purposes.find((p) => p.id === purpose)!.name;
  const enabledModels =
    activeChannel?.models.filter((m) => m.enabled && m.modality === purpose)
      .length ?? 0;
  const modelLabel =
    purpose === "text" ? "文字" : purpose === "image" ? "图片" : "视频";
  const connectionState =
    activeChannel?.enabled === false
      ? "已停用"
      : !activeChannel?.has_token
        ? "待配置"
        : enabledModels
          ? "连接已保存 · " + enabledModels + " 个" + modelLabel + "模型"
          : "连接已保存 · 尚未添加" + modelLabel + "模型";
  const error = query.error || templates.error;
  return (
    <div className="provider-boundary">
      <section
        className={`provider-workspace ${selection ? "has-selection" : ""}`}
      >
        <nav className="provider-purposes" aria-label="模型用途">
          {purposes.map((p) => (
            <button
              key={p.id}
              aria-pressed={purpose === p.id}
              onClick={() => void navigate("", p.id)}
            >
              <p.icon size={16} />
              {p.name}
            </button>
          ))}
        </nav>
        <aside className="provider-directory">
          <header>
            <strong>服务商</strong>
            <span title="已保存连接 / 此用途可选服务">
              {applicableChannels.length} /{" "}
              {applicableChannels.length + available.length}
            </span>
          </header>
          <label className="provider-search">
            <Search size={16} />
            <Input
              aria-label="搜索服务商"
              placeholder="搜索服务商"
              value={search}
              onChange={(e) => {
                if (dirty && !selection) setSelection(current);
                setSearch(e.target.value);
              }}
            />
          </label>
          <Button variant="outline" onClick={() => void navigate("custom")}>
            <Plus size={16} />
            接入自己的服务
          </Button>
          <div className="provider-directory-list">
            {visibleChannels.map((c) => {
              const t = channelTemplate(c);
              return (
                <button
                  className="provider-choice"
                  aria-label={c.name}
                  aria-description={
                    c.enabled && c.has_token ? "已保存连接" : "待配置"
                  }
                  aria-pressed={current === `channel:${c.id}`}
                  key={c.id}
                  onClick={() => void navigate(`channel:${c.id}`)}
                >
                  <ModelBrandIcon brand={t?.icon || c.name} />
                  <span>{c.name}</span>
                  <i
                    className={c.enabled && c.has_token ? "configured" : ""}
                    title={c.enabled && c.has_token ? "已配置" : "待配置"}
                  />
                </button>
              );
            })}
            {otherCustomChannels.length > 0 && (
              <details key={purpose} className="provider-other-connections">
                <summary>复用其他自定义连接</summary>
                {otherCustomChannels.map((channel) => (
                  <button
                    key={channel.id}
                    className="provider-choice"
                    aria-pressed={current === `channel:${channel.id}`}
                    onClick={() => void navigate(`channel:${channel.id}`)}
                  >
                    <ModelBrandIcon brand={channel.name} />
                    <span>{channel.name}</span>
                  </button>
                ))}
              </details>
            )}
            {availableMatches.map((t) => (
              <button
                className="provider-choice"
                aria-label={t.name}
                aria-pressed={current === `template:${t.id}`}
                key={t.id}
                onClick={() => void navigate(`template:${t.id}`)}
              >
                <ModelBrandIcon brand={t.icon || t.name} />
                <span>{t.name}</span>
                <i title="待配置" />
              </button>
            ))}
            {!visibleChannels.length && !availableMatches.length && (
              <p className="provider-empty">没有匹配的服务商</p>
            )}
          </div>
          {platform && (
            <>
              <Button variant="ghost" onClick={() => setTemplateEdit(null)}>
                <Plus size={14} />
                新增供应商模板
              </Button>
              {directory
                .filter((t) => !t.enabled)
                .map((t) => (
                  <button
                    className="provider-choice"
                    key={t.id}
                    onClick={() => setTemplateEdit(t)}
                  >
                    {t.name} · 已隐藏
                  </button>
                ))}
            </>
          )}
        </aside>
        <main className="provider-detail">
          {error ? (
            <div role="alert">
              {error.message}
              <Button
                onClick={() => {
                  void query.refetch();
                  void templates.refetch();
                }}
              >
                重新读取
              </Button>
            </div>
          ) : query.isPending || templates.isPending ? (
            <p role="status">正在读取模型服务…</p>
          ) : !current ? (
            <p className="provider-empty">选择服务商或接入自己的服务。</p>
          ) : (
            <>
              <Button
                className="provider-change"
                variant="ghost"
                onClick={() => void navigate("")}
              >
                ← 更换服务商
              </Button>
              <header className="provider-detail-heading">
                <ModelBrandIcon
                  brand={templateFor?.icon || activeChannel?.name || "自定义"}
                  size={30}
                />
                <div>
                  <h2>
                    {activeChannel?.name ||
                      activeTemplate?.name ||
                      "接入自己的服务"}
                    <span className="provider-heading-purpose">
                      {" "}
                      · {purposeName}
                    </span>
                  </h2>
                  <p className="provider-connection-state">
                    <i
                      data-connected={
                        !!activeChannel?.has_token && activeChannel.enabled
                      }
                    />
                    {connectionState}
                  </p>
                </div>
                {platform && templateFor && (
                  <Button
                    variant="ghost"
                    onClick={() => setTemplateEdit(templateFor)}
                  >
                    编辑模板
                  </Button>
                )}
              </header>
              <ProviderConnection
                projectId={platform ? undefined : projectId || undefined}
                personal={personal}
                suggestions={
                  query.data?.suggestions?.[templateFor?.id ?? ""] ?? []
                }
                key={`${current}:${purpose}`}
                onDirty={setDirty}
                base={base}
                channel={activeChannel}
                template={templateFor}
                purpose={purpose}
                protocols={query.data?.protocols || {}}
                refresh={refresh}
                select={(id) => setSelection(id ? `channel:${id}` : "")}
              />
            </>
          )}
        </main>
        {templateEdit !== undefined && (
          <ProviderTemplateEditor
            template={templateEdit}
            initialPurpose={purpose}
            protocols={query.data?.protocols || {}}
            close={() => setTemplateEdit(undefined)}
            saved={async () => {
              await qc.invalidateQueries({ queryKey: ["provider-templates"] });
              setTemplateEdit(undefined);
            }}
          />
        )}
      </section>
    </div>
  );
}

function ProviderConnection({
  projectId,
  personal,
  suggestions,
  onDirty,
  base,
  channel,
  template,
  purpose,
  protocols,
  refresh,
  select,
}: {
  projectId?: string;
  personal: boolean;
  suggestions: SuggestedModel[];
  onDirty: (dirty: boolean) => void;
  base: string;
  channel?: Channel;
  template?: ProviderTemplate;
  purpose: string;
  protocols: Catalog["protocols"];
  refresh: () => Promise<void>;
  select: (id: string) => void;
}) {
  const confirm = useConfirm();
  const [name, setName] = useState(channel?.name || template?.name || ""),
    [endpoint, setEndpoint] = useState(
      channel?.endpoint || template?.endpoint || "",
    ),
    [token, setToken] = useState(""),
    [accessKey, setAccessKey] = useState("");
  const usesKeyPair = template?.routes.some((r) =>
    r.protocol.startsWith("kling_"),
  );
  const credential =
    token && usesKeyPair ? accessKey.trim() + ":" + token.trim() : token;
  const incompletePair =
    usesKeyPair && Boolean(accessKey.trim()) !== Boolean(token.trim());
  const [requestId] = useState(() => crypto.randomUUID());
  const [notice, setNotice] = useState(""),
    [codes, setCodes] = useState<ModelListItem[] | null>(null),
    [code, setCode] = useState("");
  const [listRevision, setListRevision] = useState(0);
  const [editing, setEditing] = useState<Model | null | undefined>();
  const dirty =
    name !== (channel?.name || template?.name || "") ||
    endpoint !== (channel?.endpoint || template?.endpoint || "") ||
    !!token ||
    !!accessKey;
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const defaults = template?.routes.find((r) => r.modality === purpose);
  const purposeName = purposes.find((p) => p.id === purpose)!.name;
  const configured =
    channel?.models.filter((model) => model.modality === purpose) ?? [];
  const matchingConfigured = configured.filter(
    (model) => model.model.trim() === code.trim(),
  );
  const configuredIds = new Set(configured.map((model) => model.model.trim()));
  const outputs = new Map(Object.entries(channel?.known_outputs ?? {}));
  const otherPurpose = (id: string) =>
    purposes.find((p) => p.id === outputs.get(id.trim()) && p.id !== purpose)
      ?.name;
  const selectedOtherPurpose = otherPurpose(code);
  const suggested = suggestions.filter(
    (m) => m.modality === purpose && m.protocol === defaults?.protocol,
  );
  const selectedSuggestion = suggested.find((m) => m.model === code.trim());
  const selectedDirectoryModel = codes?.find((model) => model.id === code.trim());
  const documentedCount = codes?.filter((model) => model.source === "documentation").length ?? 0;
  const save = useMutation({
    mutationFn: () =>
      channel
        ? api.put<{ id: string }>(`${base}/${channel.id}`, {
            name,
            endpoint,
            provider_template_id:
              channel.provider_template_id || template?.id || null,
            token: credential || null,
            enabled: channel.enabled,
            revision: channel.revision,
          })
        : api.post<{ id: string }>(base, {
            name,
            endpoint,
            provider_template_id: template?.id || null,
            token: credential || null,
            request_id: requestId,
          }),
    onSuccess: async (r) => {
      setToken("");
      setAccessKey("");
      setCodes(null);
      setCode("");
      discover.reset();
      await refresh();
      select(r.id);
      setNotice("连接已保存。读取模型，或填写模型 ID 后添加。");
    },
  });
  const check = useMutation({
    mutationFn: () =>
      api.post<{ message: string }>(
        `${base}/check${channel ? `?channel_id=${channel.id}` : ""}`,
        {
          name: name || "连接检测",
          provider_template_id:
            channel?.provider_template_id || template?.id || null,
          endpoint,
          token: credential || null,
        },
      ),
    onSuccess: (r) => setNotice(r.message),
  });
  const discover = useMutation({
    mutationFn: () =>
      api.post<ModelDirectoryResult>(`${base}/${channel!.id}/discover?purpose=${purpose}`),
    onMutate: () => {
      setCodes([]);
      setNotice("");
    },
    onSuccess: (r) => {
      const matching = directoryEntries(r).filter(
        (m) => !m.outputs?.length || m.outputs.includes(purpose),
      );
      setCodes(matching);
      if (matching.length) setListRevision((revision) => revision + 1);
    },
  });
  const remove = useMutation({
    mutationFn: () =>
      api.del(`${base}/${channel!.id}?revision=${channel!.revision}`),
    onSuccess: async () => {
      await refresh();
      select("");
    },
  });
  const error = save.error || check.error || remove.error;
  const busy =
    save.isPending || check.isPending || discover.isPending || remove.isPending;
  return (
    <>
      <div className="provider-panels">
        <div className="provider-connection">
          <h3 className="provider-panel-title">
            <SlidersHorizontal size={17} />
            连接配置
          </h3>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            <fieldset disabled={busy} className="model-form">
              {(!template || (channel && channel.name !== template.name)) && (
                <label>
                  服务名称
                  <Input
                    required
                    maxLength={100}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
              )}
              <label>
                Base URL
                <Input
                  required
                  type="url"
                  value={endpoint}
                  maxLength={512}
                  onChange={(e) => {
                    setEndpoint(e.target.value);
                    check.reset();
                    setNotice("");
                  }}
                  placeholder="https://你的服务地址/v1"
                />
              </label>
              {usesKeyPair && (
                <label>
                  Access Key
                  <Input
                    autoComplete="off"
                    value={accessKey}
                    onChange={(e) => setAccessKey(e.target.value)}
                    placeholder={
                      channel?.has_token
                        ? "已保存，留空保留"
                        : "输入 Access Key"
                    }
                  />
                </label>
              )}
              <label>
                {usesKeyPair ? "Secret Key" : "API Key"}
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={token}
                  onChange={(e) => {
                    setToken(e.target.value);
                    check.reset();
                    setNotice("");
                  }}
                  placeholder={
                    channel?.has_token ? "已保存，留空保留" : "输入服务密钥"
                  }
                />
              </label>
              {template?.key_url && (
                <a href={template.key_url} target="_blank" rel="noreferrer">
                  获取 API Key · {new URL(template.key_url).hostname}{" "}
                  <ExternalLink size={13} />
                </a>
              )}
              <div className="provider-actions">
                <Button
                  type="submit"
                  disabled={
                    !name.trim() ||
                    !endpoint.trim() ||
                    incompletePair ||
                    (personal && !token.trim() && !channel?.has_token)
                  }
                >
                  {save.isPending ? "保存中…" : "保存连接"}
                </Button>
                {template?.discovery_protocol !== "manual" && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={
                      incompletePair ||
                      !endpoint ||
                      (!token && !channel?.has_token)
                    }
                    onClick={() => check.mutate()}
                  >
                    {check.isPending ? "检测中…" : "测试连接"}
                  </Button>
                )}
              </div>
            </fieldset>
          </form>
          <section className="provider-model-setup" aria-label="添加模型">
            <div className="provider-model-entry">
              <ProviderModelPicker
                listRevision={listRevision}
                configuredIds={configuredIds}
                brand={template?.icon || channel?.name || "AI"}
                codes={
                  codes ?? suggested.map((m) => ({ id: m.model, name: m.name }))
                }
                value={code}
                onChange={setCode}
              />
              {template?.discovery_protocol !== "manual" && (
                <Button
                  type="button"
                  variant="outline"
                  aria-label="读取模型列表"
                  disabled={!channel || dirty || busy}
                  onClick={() => discover.mutate()}
                >
                  <RefreshCw size={14} />
                  {discover.isPending ? "读取中…" : "拉取"}
                </Button>
              )}
            </div>
            {(!channel || dirty) && <small>先保存连接，再添加模型。</small>}
            {discover.error && <small role="alert">{discover.error.message}</small>}
            {discover.isSuccess && codes && (
              <small role="status">
                {documentedCount ? (
                  <>
                    接口返回 {codes.length - documentedCount} 个{purposeName}模型。
                    另有 {documentedCount} 个来自
                    {discover.data.documentation_url ? (
                      <a href={discover.data.documentation_url} target="_blank" rel="noreferrer">官方文档</a>
                    ) : "官方文档"}
                    ，账号权限待验证。
                  </>
                ) : codes.length ? (
                  `已读取 ${codes.length} 个候选模型。`
                ) : (
                  "没有读到此用途的模型，可手动填写模型 ID。"
                )}
              </small>
            )}
            {template?.discovery_protocol === "manual" && (
              <small>
                {suggested.length
                  ? "可选择预置模型，或填写供应商提供的模型 ID。"
                  : "请填写供应商提供的模型 ID。"}
              </small>
            )}
            {selectedSuggestion?.lifecycle_note && (
              <small>{selectedSuggestion.lifecycle_note}</small>
            )}
            {selectedDirectoryModel?.note && (
              <small>{selectedDirectoryModel.note}</small>
            )}
            {selectedOtherPurpose && (
              <small role="status">
                此型号属于「{selectedOtherPurpose}」。
              </small>
            )}
            <Button
              disabled={
                !channel ||
                dirty ||
                !code.trim() ||
                busy ||
                matchingConfigured.length > 1 ||
                (!matchingConfigured.length && !!selectedOtherPurpose)
              }
              onClick={() => setEditing(matchingConfigured[0] ?? null)}
            >
              {matchingConfigured.length
                ? "编辑已配置模型"
                : `添加到${purposeName}`}
            </Button>
            {matchingConfigured.length > 1 && (
              <small>
                这里有多条历史配置，请在「已配置模型」中选择要编辑的一条。
              </small>
            )}
          </section>
          {template?.note && <p className="provider-note">{template.note}</p>}
          {channel && (
            <Button
              className="provider-remove"
              type="button"
              variant="ghost"
              onClick={async () => {
                if (
                  await confirm({
                    title: `删除 ${channel.name}？`,
                    message:
                      "移除此连接及旗下模型；已生成的作品和历史记录保留。",
                    confirmText: "删除服务",
                    danger: true,
                  })
                )
                  remove.mutate();
              }}
            >
              <Trash2 size={14} />
              删除连接
            </Button>
          )}
        </div>
        <div className="provider-test-column">
          {purpose !== "text" && (
            <ProviderMediaTest
              projectId={projectId}
              personal={personal}
              purpose={purpose}
              models={configured.filter(
                (m) => m.enabled && !otherPurpose(m.model),
              )}
              disabled={
                dirty || busy || !channel?.enabled || !channel.has_token
              }
            />
          )}
          {(purpose === "text" || configured.length > 0) && (
            <section className="provider-models">
              <h3>已配置模型</h3>
              {!configured.length && <p>此用途还没有添加模型。</p>}
              <div className="provider-model-list">
                {configured.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    aria-label={`编辑 ${m.name}`}
                    disabled={dirty || busy}
                    onClick={() => setEditing(m)}
                  >
                    <span className="provider-model-identity">
                      <strong>{m.name}</strong>
                      {m.name.trim() !== m.model.trim() && (
                        <small>{m.model}</small>
                      )}
                      {otherPurpose(m.model) && (
                        <small className="provider-model-warning">
                          属于「{otherPurpose(m.model)}」，请更正用途
                        </small>
                      )}
                      {m.verification?.status === "failed" && (
                        <small>{m.verification.message}</small>
                      )}
                    </span>
                    <span className="provider-model-state">
                      {!m.enabled
                        ? "已停用"
                        : personal && purpose !== "text"
                          ? verificationLabel(m.verification)
                          : ""}
                    </span>
                    <ChevronRight size={15} aria-hidden="true" />
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
      {(error || notice) && (
        <p className="provider-feedback" role={error ? "alert" : "status"}>
          {error?.message || notice}
        </p>
      )}
      {editing !== undefined && channel && (
        <ModelEditor
          base={base}
          channel={channel}
          model={editing}
          initialCode={code.trim()}
          suggestion={selectedSuggestion}
          personal={personal}
          defaults={
            defaults || {
              modality: purpose,
              protocol:
                purpose === "text"
                  ? "cloud_llm"
                  : purpose === "image"
                    ? "gpt_image"
                    : "openai_video",
              sdk_provider: "openai",
            }
          }
          protocols={protocols}
          routes={template?.routes}
          close={() => setEditing(undefined)}
          saved={async () => {
            await refresh();
            setEditing(undefined);
            setCode("");
            setNotice(
              personal
                ? "个人模型已保存，各项目的创作助手都可选择。"
                : "模型已保存，可在默认模型中选择。实际生成能力仍需验证。",
            );
          }}
        />
      )}
    </>
  );
}
