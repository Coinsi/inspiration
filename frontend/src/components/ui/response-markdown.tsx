import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./response-markdown.css";

/** One reading style for ordinary replies and prose inside interactive answers. */
export function ResponseMarkdown({ children }: { children: string }) {
  return <div className="response-prose">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      table: ({ children }) => <div className="response-table-scroll" tabIndex={0} role="region" aria-label="表格"><table>{children}</table></div>,
      img: ({ alt, src }) => <img src={src} alt={alt || "回答中的图片"} loading="lazy" />,
      a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    }}>{children}</ReactMarkdown>
  </div>;
}
