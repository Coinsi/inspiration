import { useRef, useState } from "react";
export type MentionOption = { id: string; alias: string; image: boolean };
export function CanvasMentionInput({
  value,
  disabled,
  options,
  onChange,
  onMention,
}: {
  value: string;
  disabled: boolean;
  options: MentionOption[];
  onChange: (text: string) => void;
  onMention: (source: MentionOption, text: string) => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [range, setRange] = useState<{
      start: number;
      end: number;
      query: string;
    } | null>(null),
    [index, setIndex] = useState(0);
  const candidates = range
    ? options
        .filter((o) =>
          o.alias.toLowerCase().includes(range.query.toLowerCase()),
        )
        .slice(0, 8)
    : [];
  function inspect(text: string, cursor: number) {
    const match = text.slice(0, cursor).match(/@([^@\n【】]{0,80})$/);
    setRange(
      match
        ? { start: cursor - match[0].length, end: cursor, query: match[1] }
        : null,
    );
    setIndex(0);
  }
  function choose(o: MentionOption) {
    if (!range) return;
    const token = `@【${o.alias}】 `;
    onMention(o, value.slice(0, range.start) + token + value.slice(range.end));
    setRange(null);
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.setSelectionRange(
        range.start + token.length,
        range.start + token.length,
      );
    });
  }
  return (
    <div className="canvas-mention-input">
      <textarea
        ref={input}
        aria-label="节点提示词"
        value={value}
        maxLength={10000}
        disabled={disabled}
        placeholder="描述镜头，输入 @ 引用画布中的角色、图片或文字…"
        onChange={(e) => {
          onChange(e.target.value);
          inspect(e.target.value, e.target.selectionStart);
        }}
        onClick={(e) => inspect(value, e.currentTarget.selectionStart)}
        onKeyDown={(e) => {
          if (!range) return;
          if (e.key === "Escape") {
            e.stopPropagation();
            setRange(null);
          } else if (
            candidates.length &&
            ["ArrowDown", "ArrowUp", "Enter"].includes(e.key)
          ) {
            e.preventDefault();
            if (e.key === "Enter")
              choose(candidates[index % candidates.length]);
            else
              setIndex(
                (index + (e.key === "ArrowDown" ? 1 : -1) + candidates.length) %
                  candidates.length,
              );
          }
        }}
      />
      {range && (
        <div
          className="canvas-mention-menu"
          role="listbox"
          aria-label="选择画布引用"
        >
          {candidates.map((o, i) => (
            <button
              key={o.id}
              role="option"
              aria-selected={i === index}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(o)}
            >
              <span>{o.alias}</span>
              <small>{o.image ? "图片参考" : "文字输入"}</small>
            </button>
          ))}
          {!candidates.length && <p>没有匹配项，可先把角色或素材放入画布</p>}
        </div>
      )}
    </div>
  );
}
