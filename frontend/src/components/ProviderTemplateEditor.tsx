import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  Clapperboard,
  Image as ImageIcon,
  MessageSquare,
} from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { SelectField } from "./ui/select-field";
import { ModelBrandIcon } from "./workbench/ModelBrandIcon";
import { EditorFrame } from "./ModelEditorFrame";
import type { Catalog } from "./ModelChannels";
import type { ProviderRoute, ProviderTemplate } from "./ProviderWorkspace";
import "./provider-template-editor.css";

const capabilities = [
  {
    id: "text",
    name: "文字与理解",
    icon: MessageSquare,
    protocol: "cloud_llm",
  },
  { id: "image", name: "图片生成", icon: ImageIcon, protocol: "gpt_image" },
  {
    id: "video",
    name: "视频生成",
    icon: Clapperboard,
    protocol: "openai_video",
  },
];
const brandNames: Record<string, string> = {
  openai: "OpenAI",
  claude: "Anthropic",
  gemini: "Google Gemini",
  deepseek: "DeepSeek",
  minimax: "MiniMax",
  wavespeed: "WaveSpeed",
  openrouter: "OpenRouter",
  byteplus: "BytePlus ModelArk",
  xai: "xAI",
  volcengine: "火山方舟",
  kling: "可灵",
  zhipu: "智谱",
  bailian: "阿里云百炼",
  qwen: "通义千问",
  heygen: "HeyGen",
  bfl: "Black Forest Labs",
  vidu: "Vidu",
  recraft: "Recraft",
  fal: "fal.ai",
  runway: "Runway",
};
const brandOptions = [
  { value: "", label: "名称首字" },
  ...Object.entries(brandNames).map(([value, label]) => ({
    value,
    label,
    icon: <ModelBrandIcon brand={value} size={17} />,
  })),
];
const directoryOptions = [
  { value: "openai", label: "OpenAI 兼容" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Google Gemini" },
  { value: "openrouter", label: "OpenRouter 分用途目录" },
  { value: "manual", label: "手动填写模型 ID" },
];

export function ProviderTemplateEditor({
  template,
  initialPurpose,
  protocols,
  close,
  saved,
}: {
  template: ProviderTemplate | null;
  initialPurpose: string;
  protocols: Catalog["protocols"];
  close: () => void;
  saved: () => Promise<void>;
}) {
  const [id, setId] = useState(template?.id || "");
  const [purpose, setPurpose] = useState(initialPurpose);
  const current = capabilities.find((c) => c.id === purpose) ?? capabilities[0];
  const [value, setValue] = useState({
    name: template?.name || "",
    icon: template?.icon || "",
    endpoint: template?.endpoint || "",
    key_url: template?.key_url || "",
    note: template?.note || "",
    discovery_protocol: template?.discovery_protocol || "openai",
    enabled: template?.enabled ?? true,
    routes: template?.routes ?? [
      {
        modality: current.id,
        protocol: current.protocol,
        sdk_provider: "openai",
      },
    ],
    revision: template?.revision || 0,
  });
  const [dirty, setDirty] = useState(false);
  const update = (patch: Partial<typeof value>) => {
    setValue((previous) => ({ ...previous, ...patch }));
    setDirty(true);
  };
  const route = value.routes.find((r) => r.modality === current.id);
  const setRoute = (next: ProviderRoute | null) => {
    if (!next) update({ routes: value.routes.filter((r) => r !== route) });
    else if (route)
      update({ routes: value.routes.map((r) => (r === route ? next : r)) });
    else update({ routes: [...value.routes, next] });
  };
  const protocolOptions = Object.entries(protocols)
    .filter(([, p]) => p.types.includes(current.id))
    .map(([key, p]) => ({ value: key, label: p.name }));
  const sdkProviders =
    route && protocols[route.protocol]?.sdk_providers?.[current.id];
  const save = useMutation({
    mutationFn: () => api.put(`/admin/model-provider-templates/${id}`, value),
    onSuccess: saved,
  });
  return (
    <EditorFrame
      title={template ? "编辑供应商模板" : "新增供应商模板"}
      close={close}
      dirty={dirty}
      busy={save.isPending}
    >
      <form
        className="provider-template-form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="template-scroll">
          <fieldset
            className="template-body"
            disabled={save.isPending}
            onInvalidCapture={(e) => {
              const guide = (e.target as HTMLElement).closest("details");
              if (guide) guide.open = true;
            }}
          >
            <section aria-labelledby="template-identity-heading">
              <h3 id="template-identity-heading">供应商信息</h3>
              <div className="template-fields">
                <label>
                  显示名称
                  <Input
                    required
                    maxLength={100}
                    value={value.name}
                    onChange={(e) => update({ name: e.target.value })}
                  />
                </label>
                <label>
                  供应商图标
                  <SelectField
                    label="供应商图标"
                    searchable
                    value={value.icon}
                    options={brandOptions}
                    onChange={(icon) => update({ icon })}
                  />
                </label>
                <label className="template-span">
                  模板标识
                  <Input
                    required
                    pattern="[a-z0-9][a-z0-9_\-]*"
                    maxLength={80}
                    readOnly={!!template}
                    value={id}
                    onChange={(e) => {
                      setId(e.target.value);
                      setDirty(true);
                    }}
                    placeholder="例如 my-provider"
                  />
                </label>
              </div>
            </section>
            <section aria-labelledby="template-connection-heading">
              <h3 id="template-connection-heading">模型连接</h3>
              <div className="template-fields">
                <label>
                  默认 Base URL
                  <Input
                    type="url"
                    value={value.endpoint}
                    onChange={(e) => update({ endpoint: e.target.value })}
                    placeholder="https://api.example.com/v1"
                  />
                </label>
                <label>
                  模型列表读取方式
                  <SelectField
                    label="模型列表读取方式"
                    value={value.discovery_protocol}
                    options={directoryOptions}
                    onChange={(discovery_protocol) =>
                      update({ discovery_protocol })
                    }
                  />
                </label>
              </div>
            </section>
            <section aria-labelledby="template-capability-heading">
              <h3 id="template-capability-heading">用途与协议</h3>
              <div
                className="template-capabilities"
                role="group"
                aria-label="配置用途"
              >
                {capabilities.map((cap) => (
                  <button
                    key={cap.id}
                    type="button"
                    aria-pressed={current.id === cap.id}
                    onClick={() => setPurpose(cap.id)}
                  >
                    <cap.icon size={15} aria-hidden="true" />
                    {cap.name}
                    {value.routes.some((r) => r.modality === cap.id) && (
                      <Check size={13} aria-label="已启用" />
                    )}
                  </button>
                ))}
              </div>
              <div className="template-route">
                <label className="template-toggle">
                  <input
                    type="checkbox"
                    checked={!!route}
                    onChange={(e) =>
                      setRoute(
                        e.target.checked
                          ? {
                              modality: current.id,
                              protocol: current.protocol,
                              sdk_provider: "openai",
                            }
                          : null,
                      )
                    }
                  />
                  启用{current.name}
                </label>
                {route && (
                  <div className="template-fields">
                    <label>
                      接入协议
                      <SelectField
                        label={current.name + "接入协议"}
                        value={route.protocol}
                        options={protocolOptions}
                        onChange={(protocol) => {
                          const supported =
                            protocols[protocol]?.sdk_providers?.[current.id];
                          const sdk_provider = supported?.includes(
                            route.sdk_provider,
                          )
                            ? route.sdk_provider
                            : (supported?.[0] ?? "openai");
                          setRoute({ ...route, protocol, sdk_provider });
                        }}
                      />
                    </label>
                    {sdkProviders && (
                      <label>
                        SDK 供应商
                        <SelectField
                          label={current.name + " SDK 供应商"}
                          searchable={sdkProviders.length > 7}
                          value={route.sdk_provider}
                          options={sdkProviders.map((key) => ({
                            value: key,
                            label: brandNames[key] ?? key,
                          }))}
                          onChange={(sdk_provider) =>
                            setRoute({ ...route, sdk_provider })
                          }
                        />
                      </label>
                    )}
                  </div>
                )}
              </div>
              {!value.routes.length && (
                <small role="status">请至少启用一种用途。</small>
              )}
            </section>
            <details className="template-guide">
              <summary>
                <ChevronRight size={15} aria-hidden="true" />
                密钥链接与接入说明
              </summary>
              <label>
                密钥获取链接
                <Input
                  type="url"
                  value={value.key_url}
                  onChange={(e) => update({ key_url: e.target.value })}
                  placeholder="https://供应商控制台地址"
                />
              </label>
              <label>
                接入说明
                <textarea
                  aria-label="接入说明"
                  rows={3}
                  maxLength={2000}
                  value={value.note}
                  onChange={(e) => update({ note: e.target.value })}
                />
              </label>
            </details>
          </fieldset>
        </div>
        <footer className="template-footer">
          <label className="template-toggle">
            <input
              type="checkbox"
              checked={value.enabled}
              disabled={save.isPending}
              onChange={(e) => update({ enabled: e.target.checked })}
            />
            在供应商目录中显示
          </label>
          <Button
            type="submit"
            disabled={save.isPending || !value.routes.length}
          >
            {save.isPending ? "保存中…" : "保存模板"}
          </Button>
          {save.error && <p role="alert">{save.error.message}</p>}
        </footer>
      </form>
    </EditorFrame>
  );
}
