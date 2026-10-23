import type { GenerativeUILibrary } from "@assistant-ui/generative-ui/react";
import { styledGenerativeUILibrary } from "../assistant-ui/elements/generative-ui";
import { useState, type CSSProperties } from "react";
import { ImageOff, Maximize2 } from "lucide-react";
import { Lightbox } from "../ImageViewer";

function AnswerImage({ src, alt, size }: { src: string; alt: string; size?: string | number }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  return <figure className="answer-image" style={typeof size === "number" ? { maxWidth: size } : undefined}>
    <button type="button" className="answer-image-surface" disabled={failed} onClick={() => setOpen(true)} aria-label={`放大查看：${alt}`}>
      {!failed && <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />}
      {failed ? <span className="answer-image-fallback"><ImageOff size={24} />图片暂不可用</span> : <span className="answer-image-expand"><Maximize2 size={16} /></span>}
    </button>
    {open && <Lightbox src={src} alt={alt} filename={alt || "参考图片"} onClose={() => setOpen(false)} />}
  </figure>;
}

// Keep the complete official vocabulary and its schemas; adapt only presentation.
export const workspaceGenerativeLibrary: GenerativeUILibrary = { ...styledGenerativeUILibrary };
workspaceGenerativeLibrary.Image = {
  ...styledGenerativeUILibrary.Image,
  render: ({ src, alt, size }) => <AnswerImage key={src} src={src} alt={alt} size={size} />,
};
const table = styledGenerativeUILibrary.Table;
workspaceGenerativeLibrary.Table = {
  ...table,
  render: props => <div className="answer-table">
    {Array.isArray(props.columns) && props.columns.length > 2 && <p className="answer-table-hint">左右滑动查看完整表格</p>}
    <div className="answer-table-scroll" role="region" aria-label="表格，可横向滚动查看" tabIndex={0}
      style={{ "--table-min-width": `${Math.max(320, (Array.isArray(props.columns) ? props.columns.length : 2) * 160)}px` } as CSSProperties}>
      {table.render(props)}
    </div>
  </div>,
};
for (const name of ["Input", "Select", "DatePicker"]) {
  const entry = styledGenerativeUILibrary[name];
  workspaceGenerativeLibrary[name] = {
    ...entry,
    render: props => <label className="generative-ui-field">
      {typeof props.label === "string" && <span>{props.label}</span>}
      {entry.render(props)}
    </label>,
  };
}
const button = styledGenerativeUILibrary.Button;
workspaceGenerativeLibrary.Button = {
  ...button,
  render: props => button.render({ ...props, buttonStyle: props.buttonStyle ?? "primary" }),
};
