import { ModelBrandIcon } from "./workbench/ModelBrandIcon";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { EditorFrame } from "./ModelEditorFrame";
import { SelectField } from "./ui/select-field";
import type { Catalog } from "./ModelChannels";
import type { ProviderRoute, ProviderTemplate } from "./ProviderWorkspace";

export function ProviderTemplateEditor({
  template,
  protocols,
  close,
  saved,
}: {
  template: ProviderTemplate | null;
  protocols: Catalog["protocols"];
  close: () => void;
  saved: () => Promise<void>;
}) {
  const [id, setId] = useState(template?.id || "");
  const [value, setValue] = useState({
    name: template?.name || "",
    icon: template?.icon || "",
    endpoint: template?.endpoint || "",
    key_url: template?.key_url || "",
    note: template?.note || "",
    discovery_protocol: template?.discovery_protocol || "openai",
    enabled: template?.enabled ?? true,
    routes: template?.routes || [
      { modality: "text", protocol: "cloud_llm", sdk_provider: "openai" },
    ],
    revision: template?.revision || 0,
  });
  const [dirty, setDirty] = useState(false);
  const save = useMutation({
    mutationFn: () => api.put(`/admin/model-provider-templates/${id}`, value),
    onSuccess: saved,
  });
  const field = (
    key: "name" | "icon" | "endpoint" | "key_url" | "note",
    v: string,
  ) => {
    setValue({ ...value, [key]: v });
    setDirty(true);
  };
  const route = (modality: string, patch: Partial<ProviderRoute>) => {
    setValue({
      ...value,
      routes: value.routes.map((r) =>
        r.modality === modality ? { ...r, ...patch } : r,
      ),
    });
    setDirty(true);
  };
  return (
    <EditorFrame
      title={template ? "编辑供应商模板" : "新增供应商模板"}
      close={close}
      dirty={dirty}
      busy={save.isPending}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <fieldset className="model-form" disabled={save.isPending}>
          <p>模板只提供接入指引，不保存密钥，也不会覆盖已建立的连接。</p>
          <label>
            模板标识
            <Input
              required
              pattern="[a-z0-9][a-z0-9_-]*"
              maxLength={80}
              disabled={!!template}
              value={id}
              onChange={(e) => {
                setId(e.target.value);
                setDirty(true);
              }}
              placeholder="例如 my-provider"
            />
          </label>
          <label>
            显示名称
            <Input
              required
              value={value.name}
              maxLength={100}
              onChange={(e) => field("name", e.target.value)}
            />
          </label>
          <label>
            供应商图标
            <SelectField
              value={value.icon}
              onChange={(v) => field("icon", v)}
              options={[
                { value: "", label: "名称首字" },
                ...Object.entries({
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
                }).map(([value, label]) => ({
                  value,
                  label,
                  icon: <ModelBrandIcon brand={value} size={17} />,
                })),
              ]}
            />
          </label>
          <label>
            默认 Base URL
            <Input
              placeholder="留空可由使用者填写"
              type="url"
              value={value.endpoint}
              onChange={(e) => field("endpoint", e.target.value)}
            />
          </label>
          <label>
            密钥获取链接
            <Input
              type="url"
              value={value.key_url}
              onChange={(e) => field("key_url", e.target.value)}
              placeholder="https://供应商控制台地址"
            />
          </label>
          <label>
            接入说明
            <textarea
              value={value.note}
              maxLength={2000}
              onChange={(e) => field("note", e.target.value)}
            />
          </label>
          <label>
            模型列表读取方式
            <SelectField
              value={value.discovery_protocol}
              onChange={(v) => {
                setValue({ ...value, discovery_protocol: v });
                setDirty(true);
              }}
              options={[
                { value: "openai", label: "OpenAI 兼容" },
                { value: "anthropic", label: "Anthropic" },
                { value: "openrouter", label: "OpenRouter 分用途目录" },
                { value: "gemini", label: "Google Gemini" },
                { value: "manual", label: "不提供目录，手动填写" },
              ]}
            />
          </label>
          <h3>支持的用途与协议</h3>
          {["text", "image", "video"].map((modality, i) => {
            const r = value.routes.find((r) => r.modality === modality);
            return (
              <div className="provider-template-route" key={modality}>
                <label className="model-check">
                  <input
                    type="checkbox"
                    checked={!!r}
                    onChange={(e) => {
                      setValue({
                        ...value,
                        routes: e.target.checked
                          ? [
                              ...value.routes,
                              {
                                modality,
                                protocol:
                                  modality === "text"
                                    ? "cloud_llm"
                                    : modality === "image"
                                      ? "gpt_image"
                                      : "openai_video",
                                sdk_provider: "openai",
                              },
                            ]
                          : value.routes.filter((r) => r.modality !== modality),
                      });
                      setDirty(true);
                    }}
                  />
                  {["文字与理解", "图片生成", "视频生成"][i]}
                </label>
                {r && (
                  <>
                    <SelectField
                      label={`${modality}接入协议`}
                      value={r.protocol}
                      onChange={(v) =>
                        route(modality, { protocol: v, sdk_provider: "openai" })
                      }
                      options={Object.entries(protocols)
                        .filter(([, p]) => p.types.includes(modality))
                        .map(([k, p]) => ({ value: k, label: p.name }))}
                    />
                    {["cloud_llm", "litellm"].includes(r.protocol) && (
                      <SelectField
                        label={`${modality}SDK供应商`}
                        value={r.sdk_provider}
                        onChange={(v) => route(modality, { sdk_provider: v })}
                        options={(
                          protocols[r.protocol]?.sdk_providers?.[modality] || [
                            "openai",
                          ]
                        ).map((v) => ({ value: v, label: v }))}
                      />
                    )}
                  </>
                )}
              </div>
            );
          })}
          <label className="model-check">
            <input
              type="checkbox"
              checked={value.enabled}
              onChange={(e) => {
                setValue({ ...value, enabled: e.target.checked });
                setDirty(true);
              }}
            />
            在供应商目录中显示
          </label>
          <small>
            隐藏模板不会停用已有连接。新增协议仍需要实现适配器；这里不能添加任意执行代码。
          </small>
          {save.error && <p role="alert">{save.error.message}</p>}
          <Button disabled={!value.routes.length}>保存模板</Button>
        </fieldset>
      </form>
    </EditorFrame>
  );
}
