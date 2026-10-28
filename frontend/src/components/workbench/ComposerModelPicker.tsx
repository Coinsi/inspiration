import { SearchField } from "@/components/ui/search-field";
import { verificationLabel } from "../PersonalModelServices";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronRight, Settings2 } from "lucide-react";
import type { ProviderConfig } from "@/lib/api";
import { ModelBrandIcon } from "./ModelBrandIcon";

function family(model: ProviderConfig) {
  if (model.provider_name === "mock") return "开发测试";
  const text =
    `${model.display_name || ""} ${model.config.model || ""} ${model.config.protocol || ""}`.toLowerCase();
  if (/seedance|seedream|doubao|ark_|即梦/.test(text)) return "火山引擎";
  if (/minimax|hailuo|海螺/.test(text)) return "MiniMax";
  if (/\bwan[\s\d.-]|通义万相/.test(text)) return "Wan";
  if (/gpt|sora|dall-e/.test(text)) return "OpenAI";
  if (/gemini|imagen|veo/.test(text)) return "Google";
  if (/claude/.test(text)) return "Anthropic";
  if (/deepseek/.test(text)) return "DeepSeek";
  return model.channel_name || "项目模型";
}
function badges(model: ProviderConfig) {
  const schema = (model.config.param_schema ||
    model.capabilities.param_schema ||
    {}) as Record<string, { enum?: unknown[] }>;
  const tags: string[] = [];
  if (schema.resolution?.enum?.length)
    tags.push(schema.resolution.enum.join(" / "));
  if (schema.size?.enum?.length)
    tags.push(schema.size.enum.slice(0, 3).join(" / "));
  if (schema.duration?.enum?.length)
    tags.push(`最长 ${Math.max(...schema.duration.enum.map(Number))} 秒`);
  if (Number(model.capabilities.max_reference_images) > 0)
    tags.push("支持参考图");
  return tags.slice(0, 3);
}
function price(model: ProviderConfig) {
  const value = model.config.price as
    { mode?: string; amount?: number } | undefined;
  if (
    !value ||
    !["per_request", "per_second"].includes(value.mode || "") ||
    typeof value.amount !== "number"
  )
    return null;
  return `${value.amount} 积分/${value.mode === "per_second" ? "秒" : "结果"}`;
}
export function ComposerModelPicker({
  models,
  value,
  onChange,
  manage,
  disabled,
  loading,
  error,
  retry,
}: {
  models: ProviderConfig[];
  value: string;
  onChange: (value: string) => void;
  manage?: () => void;
  disabled: boolean;
  loading: boolean;
  error?: string;
  retry: () => void;
}) {
  const [open, setOpen] = useState(false),
    [search, setSearch] = useState(""),
    [group, setGroup] = useState("");
  const [position, setPosition] = useState<CSSProperties>({
    visibility: "hidden",
  });
  const root = useRef<HTMLDivElement>(null),
    panel = useRef<HTMLDivElement>(null),
    trigger = useRef<HTMLButtonElement>(null);
  const selected = models.find((m) => m.provider_name === value);
  const brand = selected ? family(selected) : "";
  const selectedLabel = selected?.display_name || selected?.provider_name;
  const triggerLabel =
    selectedLabel &&
    brand &&
    !["开发测试", "项目模型"].includes(brand) &&
    !selectedLabel.toLowerCase().includes(brand.toLowerCase())
      ? `${brand} · ${selectedLabel}`
      : selectedLabel;
  const visible = models.filter((m) =>
    `${m.display_name || ""} ${m.channel_name || ""} ${m.config.model || ""} ${family(m)}`
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  );
  const groups = [...new Set(visible.map(family))];
  const active = groups.includes(group) ? group : groups[0];
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  useLayoutEffect(() => {
    if (!open) return;
    const positionPanel = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(880, window.innerWidth - 24);
      const above = rect.top - 20,
        below = window.innerHeight - rect.bottom - 20;
      const up = above >= 360 || above > below;
      const height = Math.max(120, Math.min(520, up ? above : below));
      setPosition({
        position: "fixed",
        width,
        maxHeight: height,
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        ...(up
          ? { bottom: window.innerHeight - rect.top + 10 }
          : { top: rect.bottom + 10 }),
      });
    };
    positionPanel();
    window.addEventListener("resize", positionPanel);
    window.addEventListener("scroll", positionPanel, true);
    return () => {
      window.removeEventListener("resize", positionPanel);
      window.removeEventListener("scroll", positionPanel, true);
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector("input")?.focus();
    const outside = (e: Event) => {
      if (
        !root.current?.contains(e.target as Node) &&
        !panel.current?.contains(e.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
    };
  }, [open]);
  return (
    <div className="composer-model-picker" ref={root}>
      <button
        ref={trigger}
        className="composer-control composer-model-trigger"
        type="button"
        aria-label="选择创作模型"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => {
          if (!open) {
            setSearch("");
            setGroup(selected ? family(selected) : "");
          }
          setOpen(!open);
        }}
      >
        <span className="composer-brand-mark">
          <ModelBrandIcon
            brand={selected ? family(selected) : "选择模型"}
            size={22}
          />
        </span>
        <span>
          {loading
            ? "读取模型…"
            : triggerLabel || (value ? "模型不可用，请重选" : "选择模型")}
        </span>
        <ChevronDown size={14} className={open ? "is-open" : ""} />
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            className="composer-model-menu"
            style={position}
            role="dialog"
            aria-label="选择创作模型"
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                close();
                return;
              }
              const isGroup = (e.target as HTMLElement).hasAttribute(
                "data-model-group",
              );
              if (e.key === "ArrowRight" && isGroup) {
                e.preventDefault();
                panel.current
                  ?.querySelector<HTMLButtonElement>(
                    '[role="option"]:not(:disabled)',
                  )
                  ?.focus();
                return;
              }
              if (
                e.key === "ArrowLeft" &&
                (e.target as HTMLElement).getAttribute("role") === "option"
              ) {
                e.preventDefault();
                panel.current
                  ?.querySelector<HTMLButtonElement>(
                    '[data-model-group][aria-pressed="true"]',
                  )
                  ?.focus();
                return;
              }
              if (
                !["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key) ||
                ((e.target as HTMLElement).tagName === "INPUT" &&
                  ["Home", "End"].includes(e.key))
              )
                return;
              const list = Array.from(
                panel.current!.querySelectorAll<HTMLButtonElement>(
                  isGroup
                    ? "[data-model-group]"
                    : '[role="option"]:not(:disabled)',
                ),
              );
              if (!list.length) return;
              e.preventDefault();
              const index = list.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              const next =
                e.key === "Home"
                  ? 0
                  : e.key === "End"
                    ? list.length - 1
                    : index < 0
                      ? e.key === "ArrowUp"
                        ? list.length - 1
                        : 0
                      : (index + (e.key === "ArrowUp" ? -1 : 1) + list.length) %
                        list.length;
              list[next]?.focus();
            }}
          >
            <SearchField
              className="composer-model-search"
              label="搜索模型"
              placeholder="搜索模型、系列或服务"
              value={search}
              onValueChange={(value) => setSearch(value)}
            />
            {error ? (
              <p className="composer-model-empty" role="alert">
                {error}
                <button type="button" onClick={retry}>
                  重新读取
                </button>
              </p>
            ) : loading ? (
              <p className="composer-model-empty" role="status">
                正在读取模型…
              </p>
            ) : !visible.length ? (
              <p className="composer-model-empty">
                {search
                  ? "没有匹配的模型，试试其他名称。"
                  : "还没有连接此类型的模型服务。"}
              </p>
            ) : (
              <div className="composer-model-columns">
                <nav className="composer-model-families" aria-label="模型系列">
                  {groups.map((name) => {
                    const items = visible.filter((m) => family(m) === name);
                    return (
                      <button
                        key={name}
                        type="button"
                        data-model-group
                        aria-pressed={name === active}
                        onClick={() => setGroup(name)}
                        onFocus={() => setGroup(name)}
                      >
                        <span className="composer-family-icon">
                          <ModelBrandIcon brand={name} size={30} />
                        </span>
                        <span>
                          <strong>{name}</strong>
                          <small>
                            {items.length} 个模型 ·{" "}
                            {name === "开发测试"
                              ? "本地验证"
                              : items.some((m) => m.config.scope === "personal")
                                ? "我的服务 / 项目服务"
                                : "项目服务"}
                          </small>
                        </span>
                        <ChevronRight size={16} />
                      </button>
                    );
                  })}
                </nav>
                <div
                  className="composer-model-list"
                  role="listbox"
                  aria-label={`${active}模型`}
                  key={active}
                >
                  {visible
                    .filter((m) => family(m) === active)
                    .map((model) => (
                      <button
                        key={model.provider_name}
                        type="button"
                        role="option"
                        aria-selected={model.provider_name === value}
                        disabled={!model.enabled}
                        onClick={() => {
                          onChange(model.provider_name);
                          close();
                        }}
                      >
                        <span className="composer-model-icon">
                          <ModelBrandIcon brand={family(model)} size={26} />
                        </span>
                        <span className="composer-model-copy">
                          <strong>
                            {model.display_name || model.provider_name}
                          </strong>
                          <small>
                            {!model.enabled
                              ? "连接不可用"
                              : model.provider_name === "mock"
                                ? "只验证流程，不调用云端模型"
                                : String(
                                    model.config.description ||
                                      (model.kind === "llm"
                                        ? "文字创作与故事构思"
                                        : model.config.modality === "video"
                                          ? "视频生成与镜头创作"
                                          : "图片生成与视觉创作"),
                                  )}
                          </small>
                          <span className="composer-model-origin">
                            {model.provider_name === "mock"
                              ? "本地运行"
                              : model.channel_name ||
                                (model.config.route_count
                                  ? `项目服务 · ${model.config.route_count} 条已配置线路`
                                  : "项目服务")}
                            {model.config.is_default ? (model.config.scope === "platform" ? " · 平台默认" : " · 项目默认") : ""}
                            {model.config.scope === "personal"
                              ? ` · ${verificationLabel(model.config.verification as { status: string })}`
                              : ""}
                          </span>
                          {!!badges(model).length && (
                            <span className="composer-model-badges">
                              {badges(model).map((tag) => (
                                <span key={tag}>{tag}</span>
                              ))}
                            </span>
                          )}
                        </span>
                        {price(model) && (
                          <span
                            className="composer-model-price"
                            title="项目配置的内部积分，实际费用以上游账单为准"
                          >
                            {price(model)}
                          </span>
                        )}
                        <span className="composer-model-check">
                          {model.provider_name === value && <Check size={18} />}
                        </span>
                      </button>
                    ))}
                </div>
              </div>
            )}
            <footer>
              {manage ? (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    manage();
                  }}
                >
                  <Settings2 size={15} />
                  连接或管理模型服务
                  <ChevronRight size={14} />
                </button>
              ) : (
                <small>需要更多模型时，请联系项目管理员。</small>
              )}
            </footer>
          </div>,
          document.body,
        )}
    </div>
  );
}
