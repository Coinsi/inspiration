import { modelConfigRoot } from "@/lib/model-config-scope";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ExternalLink,
  Eye,
  EyeOff,
  Film,
  Image,
  KeyRound,
  Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EditorFrame } from "./ModelEditorFrame";
import "./connect-model-service.css";

type PresetModel = {
  id: string;
  name: string;
  model: string;
  modality: string;
  max_reference_images: number;
  recommended?: boolean;
  lifecycle_note?: string;
};
type Preset = {
  id: string;
  name: string;
  description: string;
  endpoint: string;
  key_url: string;
  note: string;
  models: PresetModel[];
};

export function ConnectModelService({
  projectId,
  close,
  custom,
  saved,
}: {
  projectId: string;
  close: () => void;
  custom: () => void;
  saved: (message: string) => Promise<void>;
}) {
  const query = useQuery({
    queryKey: ["model-presets", projectId],
    queryFn: () =>
      api.get<Preset[]>(`${modelConfigRoot(projectId)}/model-channels/presets`),
  });
  const [preset, setPreset] = useState<Preset | null>(null);
  const [token, setToken] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [reveal, setReveal] = useState(false);
  const requestId = useRef(crypto.randomUUID());
  const connect = useMutation({
    mutationFn: () =>
      api.post<{ message: string }>(
        `${modelConfigRoot(projectId)}/model-channels/connect`,
        {
          preset_id: preset!.id,
          token: token.trim(),
          models: selected,
          request_id: requestId.current,
          name: name.trim() || preset!.name,
          endpoint: endpoint.trim() || preset!.endpoint,
        },
      ),
    onSuccess: async (result) => {
      setToken("");
      await saved(result.message);
    },
  });
  const choose = (item: Preset) => {
    if (preset?.id === item.id) return;
    setPreset(item);
    setSelected(
      item.models.filter((m) => m.recommended !== false).map((m) => m.id),
    );
    setName(item.name);
    setEndpoint(item.endpoint);
    setToken("");
    setReveal(false);
    setAdvanced(false);
    requestId.current = crypto.randomUUID();
    connect.reset();
  };
  useEffect(() => {
    if (!preset && query.data?.length) choose(query.data[0]);
  }, [query.data]);
  return (
    <EditorFrame
      title="添加模型服务"
      close={close}
      dirty={!!token}
      busy={connect.isPending}
    >
      <div className="connect-service-tabs" aria-label="服务类型">
        {query.data?.map((item) => (
          <button
            type="button"
            key={item.id}
            aria-pressed={preset?.id === item.id}
            disabled={connect.isPending}
            onClick={() => choose(item)}
          >
            {item.name}
          </button>
        ))}
        <button type="button" disabled={connect.isPending} onClick={custom}>
          自定义 / 兼容服务
        </button>
      </div>
      {query.error && (
        <div className="connect-picker" role="alert">
          <p>{query.error.message}</p>
          <Button variant="outline" onClick={() => void query.refetch()}>
            重新读取
          </Button>
        </div>
      )}
      {!preset && !query.error ? (
        <p className="connect-picker" role="status">
          正在读取服务模板…
        </p>
      ) : preset ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (token.trim() && selected.length) connect.mutate();
          }}
        >
          <fieldset
            className="model-form connect-form"
            disabled={connect.isPending}
          >
            <div className="connect-provider-heading">
              <div>
                <h3>{preset.name}</h3>
                <p>密钥只需填写一次，所选模型会一起启用。</p>
              </div>
              <a href={preset.key_url} target="_blank" rel="noreferrer">
                获取密钥 <ExternalLink size={13} />
              </a>
            </div>
            <label>
              服务密钥
              <div className="connect-key">
                <KeyRound size={16} />
                <Input
                  required
                  type={reveal ? "text" : "password"}
                  value={token}
                  autoComplete="off"
                  maxLength={8192}
                  placeholder="粘贴 API Key"
                  onChange={(event) => setToken(event.target.value)}
                />
                <button
                  type="button"
                  aria-label={reveal ? "隐藏密钥" : "显示密钥"}
                  onClick={() => setReveal(!reveal)}
                >
                  {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <small>{preset.note}</small>
            </label>
            <div className="connect-models">
              <div className="connect-label">
                启用模型 <span>已选 {selected.length} 个</span>
              </div>
              {preset.models.map((model) => (
                <label
                  key={model.id}
                  className={`connect-model ${selected.includes(model.id) ? "is-selected" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(model.id)}
                    onChange={(event) =>
                      setSelected((current) =>
                        event.target.checked
                          ? [...current, model.id]
                          : current.filter((id) => id !== model.id),
                      )
                    }
                  />
                  {model.modality === "image" ? (
                    <Image size={19} />
                  ) : (
                    <Film size={19} />
                  )}
                  <span>
                    <strong>{model.name}</strong>
                    <small>
                      {model.lifecycle_note ||
                        (model.modality === "image"
                          ? "图片创作 · 支持参考图"
                          : model.max_reference_images > 1
                            ? "视频创作 · 支持首尾帧"
                            : "视频创作 · 支持起始参考图")}
                    </small>
                  </span>
                  <Check size={15} className="connect-model-check" />
                </label>
              ))}
            </div>
            <button
              type="button"
              className="connect-advanced-toggle"
              aria-expanded={advanced}
              onClick={() => setAdvanced(!advanced)}
            >
              高级设置{" "}
              <ChevronDown
                size={15}
                style={{ transform: advanced ? "rotate(180deg)" : undefined }}
              />
            </button>
            {advanced && (
              <div className="connect-advanced">
                <label>
                  服务名称
                  <Input
                    value={name}
                    maxLength={100}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <label>
                  服务地址
                  <Input
                    type="url"
                    value={endpoint}
                    onChange={(event) => setEndpoint(event.target.value)}
                  />
                  <small>
                    仅在使用同协议的代理或中转服务时修改。密钥将发送到此地址。
                  </small>
                </label>
              </div>
            )}
            {connect.error && (
              <p role="alert" className="model-notice text-danger">
                {connect.error.message}
              </p>
            )}
          </fieldset>
          <div className="connect-save">
            <p>
              保存配置不会发起付费生成。实际可用性取决于密钥、模型权限和账户余额。
            </p>
            <Button
              type="submit"
              disabled={!token.trim() || !selected.length || connect.isPending}
            >
              {connect.isPending ? (
                <>
                  <Loader2 size={15} className="animate-spin" />
                  正在保存…
                </>
              ) : (
                <>
                  保存并启用{" "}
                  {selected.length ? `${selected.length} 个模型` : "模型"}
                  <ArrowRight size={15} />
                </>
              )}
            </Button>
          </div>
        </form>
      ) : null}
    </EditorFrame>
  );
}
