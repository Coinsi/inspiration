import { modelConfigRoot, modelConfigQueryKey } from "@/lib/model-config-scope";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Film,
  Image,
  MessageSquare,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { api, type ProviderConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";

type Model = {
  id: string;
  name: string;
  modality: string;
  enabled: boolean;
  is_default: boolean;
  available_routes: number;
  routes: {
    channel_name: string;
    profile_id: string;
    available: boolean;
    reason: string;
  }[];
};
type Profile = {
  id: string;
  name: string;
  modality: string;
  enabled: boolean;
  protocol: string;
  is_default: boolean;
};
type Channel = {
  id: string;
  name: string;
  endpoint: string;
  enabled: boolean;
  has_token: boolean;
  models: Profile[];
};
type Choice = {
  id: string;
  name: string;
  modality: string;
  selected: boolean;
  ready: boolean;
  source: "model" | "profile";
  service: string;
  unavailableReason: string;
};
const groups = [
  {
    type: "image",
    name: "默认图片模型",
    description: "图片生成、参考图创作与视觉探索",
    icon: Image,
  },
  {
    type: "video",
    name: "默认视频模型",
    description: "文生视频、图生视频与镜头创作",
    icon: Film,
  },
  {
    type: "text",
    name: "默认文字模型",
    description: "剧本、提示词与 AI 辅助创作",
    icon: MessageSquare,
  },
];

export function DefaultModels({
  projectId,
  connect,
  services,
}: {
  projectId: string;
  connect: () => void;
  services: () => void;
}) {
  const qc = useQueryClient();
  const [notice, setNotice] = useState("");
  const models = useQuery({
    queryKey: ["creative-models", projectId],
    queryFn: () => api.get<Model[]>(`${modelConfigRoot(projectId)}/creative-models`),
  });
  const shared = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`/projects/${projectId}/providers`),
    enabled: projectId !== "platform",
  });
  const channels = useQuery({
    queryKey: ["model-channels", projectId],
    queryFn: () =>
      api.get<{ channels: Channel[] }>(`${modelConfigRoot(projectId)}/model-channels`),
  });
  const choose = useMutation({
    mutationFn: (item: Choice) =>
      api.post(`${modelConfigRoot(projectId)}/creative-models/select-default`, {
        id: item.id,
        source: item.source,
      }),
    onSuccess: async (_, item) => {
      await Promise.all(
        ["creative-models", "model-channels", "providers", "capabilities"].map(
          (key) => qc.invalidateQueries({ queryKey: modelConfigQueryKey(key, projectId) }),
        ),
      );
      setNotice(
        `已将 ${item.name} 设为默认${groups.find((g) => g.type === item.modality)?.name.slice(2) || "模型"}`,
      );
    },
  });
  const catalog = models.data || [];
  const bound = new Set(
    catalog
      .filter((m) => m.enabled)
      .flatMap((m) =>
        m.routes.filter((r) => r.available).map((r) => r.profile_id),
      ),
  );
  const choices: Choice[] = [
    ...catalog.map((m) => ({
      id: m.id,
      name: m.name,
      modality: m.modality,
      selected: m.is_default,
      ready: m.enabled && m.available_routes > 0,
      source: "model" as const,
      unavailableReason: !m.enabled
        ? "模型已停用"
        : m.routes.find((r) => r.reason)?.reason || "暂无可用线路",
      service: [...new Set(m.routes.map((r) => r.channel_name))].join(" / "),
    })),
    ...(channels.data?.channels || []).flatMap((c) =>
      c.models
        .filter((m) => !bound.has(m.id))
        .map((m) => ({
          id: m.id,
          name: m.name,
          modality: m.modality,
          selected:
            m.is_default &&
            !catalog.some((a) => a.modality === m.modality && a.is_default),
          ready:
            c.enabled &&
            m.enabled &&
            (m.protocol === "mock" || !!(c.endpoint && c.has_token)),
          source: "profile" as const,
          unavailableReason: !c.enabled
            ? "服务已停用"
            : !m.enabled
              ? "模型已停用"
              : "服务尚未配置地址或密钥",
          service: c.name,
        })),
    ),
  ];
  const error = models.error || channels.error;
  return (
    <section className="default-models">
      <header className="model-settings-heading">
        <div>
          <h2>模型选择</h2>
          <p>为不同创作选择默认模型，也可以在创作时临时切换。</p>
        </div>
        <Button variant="outline" onClick={services}>
          管理服务
          <ArrowRight size={14} />
        </Button>
      </header>
      {(notice || choose.error) && (
        <p
          className="model-settings-notice"
          role={choose.error ? "alert" : "status"}
        >
          {choose.error?.message || notice}
        </p>
      )}
      {error ? (
        <div className="model-settings-empty" role="alert">
          <p>{error.message}</p>
          <Button
            variant="outline"
            onClick={() => {
              void models.refetch();
              void channels.refetch();
            }}
          >
            重新读取
          </Button>
        </div>
      ) : models.isPending || channels.isPending ? (
        <p role="status">正在读取模型…</p>
      ) : (
        groups.map((group) => {
          const items = choices.filter((m) => m.modality === group.type);
          const inherited = (shared.data || []).filter(m => m.enabled && m.config.scope === "platform" && !m.config.hidden_from_catalog && (m.config.modality || (m.kind === "llm" ? "text" : "image")) === group.type);
          const platformDefault = inherited.find(m => m.config.is_default) || inherited[0];
          const Icon = group.icon;
          return (
            <section
              className="default-model-group"
              key={group.type}
              aria-label={group.name}
            >
              <header>
                <span className="default-group-icon">
                  <Icon size={18} />
                </span>
                <div>
                  <h3>{group.name}</h3>
                  <p>{group.description}</p>
                </div>
                <small>{items.filter((m) => m.ready).length} 个已配置</small>
              </header>
              {items.length ? (
                <div
                  className="default-model-options"
                  role="radiogroup"
                  aria-label={group.name}
                >
                  {items.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      role="radio"
                      aria-checked={item.selected}
                      tabIndex={
                        (
                          items.find((m) => m.selected && m.ready) ||
                          items.find((m) => m.ready)
                        )?.id === item.id
                          ? 0
                          : -1
                      }
                      disabled={!item.ready || choose.isPending}
                      onKeyDown={(event) => {
                        if (
                          ![
                            "ArrowLeft",
                            "ArrowRight",
                            "ArrowUp",
                            "ArrowDown",
                            "Home",
                            "End",
                          ].includes(event.key)
                        )
                          return;
                        event.preventDefault();
                        const options = Array.from(
                          event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                            "button:not(:disabled)",
                          ),
                        );
                        const current = options.indexOf(event.currentTarget);
                        const next =
                          event.key === "Home"
                            ? 0
                            : event.key === "End"
                              ? options.length - 1
                              : (current +
                                  (["ArrowLeft", "ArrowUp"].includes(event.key)
                                    ? -1
                                    : 1) +
                                  options.length) %
                                options.length;
                        options[next]?.focus();
                        options[next]?.click();
                      }}
                      title={item.ready ? item.service : item.unavailableReason}
                      className={`default-model-option ${item.selected ? "is-selected" : ""}`}
                      onClick={() => choose.mutate(item)}
                    >
                      <span className="default-model-avatar">
                        <Icon size={18} />
                      </span>
                      <span className="default-model-copy">
                        <strong>{item.name}</strong>
                        <small>{item.service || "项目模型"}</small>
                        {!item.ready && <em>{item.unavailableReason}</em>}
                      </span>
                      <span className="default-model-check">
                        {choose.isPending &&
                        choose.variables?.id === item.id ? (
                          <Loader2 size={15} className="animate-spin" />
                        ) : item.selected ? (
                          <Check size={15} />
                        ) : null}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="default-model-empty">
                  <p>{platformDefault ? `使用平台模型：${platformDefault.display_name || platformDefault.provider_name}` : `还没有连接${group.name.slice(2, 4)}模型`}</p>
                  <button onClick={connect}>
                    {platformDefault ? "配置项目专用模型" : "添加模型服务"}
                    <ArrowRight size={13} />
                  </button>
                </div>
              )}
            </section>
          );
        })
      )}
      <p className="model-settings-footnote">
        {projectId === "platform" ? "全局模型供所有用户使用。项目已有默认配置时优先使用项目配置；已有任务保留原模型。平台承担这些渠道的调用费用。" : "默认选择对当前项目生效，已有任务和画布节点保留原来的模型。模型是否能生成，仍取决于服务权限和余额。"}
      </p>
    </section>
  );
}
