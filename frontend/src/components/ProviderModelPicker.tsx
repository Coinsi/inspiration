import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search } from "lucide-react";
import { Input } from "./ui/input";
import { ModelBrandIcon } from "./workbench/ModelBrandIcon";

export type ModelListItem = { id: string; name?: string; outputs?: string[] };
type Props = {
  codes: ModelListItem[];
  value: string;
  brand: string;
  listRevision: number;
  onChange: (value: string) => void;
};

function displayName(model: ModelListItem) {
  if (model.name) return model.name;
  const slug = model.id.split("/").pop() || model.id;
  // Format familiar names for scanning. The exact supplier ID remains selectable and visible.
  const family =
    /^(gpt|claude|gemini|deepseek|qwen|grok|flux|vidu|kling|seedance|seedream)-(.+)$/i.exec(
      slug,
    );
  if (!family) return model.id;
  const brand =
    { gpt: "GPT", flux: "FLUX", deepseek: "DeepSeek" }[
      family[1].toLowerCase()
    ] ?? family[1][0].toUpperCase() + family[1].slice(1);
  return (
    brand +
    " " +
    family[2]
      .split("-")
      .map((word) => word[0]?.toUpperCase() + word.slice(1))
      .join(" ")
  );
}

export function ProviderModelPicker({
  codes,
  value,
  brand,
  listRevision,
  onChange,
}: Props) {
  const id = useId();
  const field = useRef<HTMLInputElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [filter, setFilter] = useState("");
  const [position, setPosition] = useState<React.CSSProperties>({});
  useEffect(() => {
    if (listRevision > 0) {
      field.current?.focus();
      setFilter("");
      setActive(-1);
      setOpen(true);
    }
  }, [listRevision]);
  const matches = useMemo(() => {
    const term = filter.trim().toLowerCase();
    return codes.filter((model) =>
      (model.id + " " + (model.name ?? "")).toLowerCase().includes(term),
    );
  }, [codes, filter]);
  const exactMatch = matches.length === 1 && matches[0].id === filter.trim();
  const expanded = open && codes.length > 0 && !exactMatch;
  useLayoutEffect(() => {
    if (!expanded || !field.current || !menu.current) return;
    const popup = menu.current;
    popup.setAttribute("popover", "manual");
    popup.showPopover?.();
    const place = () => {
      const box = field.current!.getBoundingClientRect();
      const width = Math.min(Math.max(310, box.width), window.innerWidth - 24);
      const below = window.innerHeight - box.bottom - 14;
      const above = box.top - 14;
      const up = below < 245 && above > below;
      setPosition({
        width,
        left: Math.max(12, Math.min(box.left, window.innerWidth - width - 12)),
        top: up ? "auto" : box.bottom + 6,
        bottom: up ? window.innerHeight - box.top + 6 : "auto",
        maxHeight: Math.max(80, Math.min(330, up ? above : below)),
      });
    };
    place();
    const onScroll = (event: Event) => {
      if (!popup.contains(event.target as Node)) place();
    };
    window.addEventListener("resize", place);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [expanded]);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      const node = event.target as Node;
      if (
        !menu.current?.contains(node) &&
        !field.current?.parentElement?.contains(node)
      )
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [expanded]);
  function choose(code: string) {
    onChange(code);
    setOpen(false);
    setActive(-1);
  }
  function browse() {
    setFilter("");
    setActive(-1);
    setOpen(true);
  }
  return (
    <div className="provider-picker">
      <label htmlFor={id}>模型 ID</label>
      <div className="provider-picker-field">
        <Input
          ref={field}
          id={id}
          role="combobox"
          data-select-popup={expanded || undefined}
          aria-autocomplete="list"
          autoComplete="off"
          aria-expanded={expanded}
          aria-controls={expanded ? id + "-list" : undefined}
          aria-activedescendant={
            expanded && active >= 0 ? id + "-" + active : undefined
          }
          placeholder={codes.length ? "搜索或输入模型 ID" : "输入模型 ID"}
          value={value}
          onFocus={browse}
          onClick={() => {
            if (!open) browse();
          }}
          onBlur={() => setOpen(false)}
          onChange={(e) => {
            onChange(e.target.value);
            setFilter(e.target.value);
            setActive(-1);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Escape" || e.key === "Tab") setOpen(false);
            if (e.key === "Enter" && expanded) {
              e.preventDefault();
              choose(matches[active]?.id ?? value.trim());
            }
            if (
              (e.key === "ArrowDown" || e.key === "ArrowUp") &&
              matches.length
            ) {
              e.preventDefault();
              const next =
                (active + (e.key === "ArrowDown" ? 1 : -1) + matches.length) %
                matches.length;
              setActive(
                active === -1 && e.key === "ArrowUp"
                  ? matches.length - 1
                  : next,
              );
              setOpen(true);
            }
          }}
        />
        {codes.length > 0 && (
          <button
            type="button"
            tabIndex={-1}
            aria-label="展开模型列表"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              if (expanded) setOpen(false);
              else {
                field.current?.focus();
                browse();
              }
            }}
          >
            <ChevronDown size={15} />
          </button>
        )}
      </div>
      {expanded &&
        createPortal(
          <div
            ref={menu}
            className="provider-model-menu"
            style={position}
            onMouseDown={(e) => e.preventDefault()}
          >
            <div className="provider-menu-heading">
              <Search size={14} />
              <span>可选模型</span>
              <span>{matches.length}</span>
            </div>
            <div
              role="listbox"
              aria-label="可选模型"
              id={id + "-list"}
              className="provider-options"
            >
              {matches.map((model, index) => (
                <div
                  key={model.id}
                  id={id + "-" + index}
                  ref={(el) => {
                    if (index === active)
                      el?.scrollIntoView({ block: "nearest" });
                  }}
                  role="option"
                  aria-label={model.id}
                  aria-selected={model.id === value}
                  data-active={index === active}
                  onClick={() => choose(model.id)}
                >
                  <span className="provider-option-mark">
                    <ModelBrandIcon brand={brand} size={16} />
                  </span>
                  <span className="provider-option-text">
                    <strong>{displayName(model)}</strong>
                    {displayName(model) !== model.id && (
                      <small>{model.id}</small>
                    )}
                  </span>
                  {model.id === value && <Check size={15} />}
                </div>
              ))}
              {!matches.length && (
                <div className="provider-manual-id">
                  <p>没有匹配项，可直接添加输入的 ID。</p>
                  <button type="button" onClick={() => choose(value.trim())}>
                    使用此 ID <Check size={13} />
                  </button>
                </div>
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
