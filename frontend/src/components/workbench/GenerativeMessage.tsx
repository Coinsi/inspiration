import { Component, Suspense, lazy, useState, type ReactNode } from "react";
import { renderGenerativeUI, type GenerativeUIAction } from "@assistant-ui/generative-ui/react";
import "../assistant-ui/elements/generative-ui-official.css";
import "./generative-ui.css";
import { actionReply } from "./generative-actions";

export type UIBlock = { id: string; version: number; tree: Record<string, unknown> };

// Maps and diagrams are loaded only when a message actually contains generated UI.
const Content = lazy(async () => {
  const { workspaceGenerativeLibrary } = await import("./generative-library");
  return { default: ({ tree, dispatch }: { tree: unknown; dispatch: (action: GenerativeUIAction) => void }) =>
    <>{renderGenerativeUI(tree, workspaceGenerativeLibrary, { status: "done", dispatch })}</> };
});

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">这份交互内容暂时无法显示，请让助理重新整理。</p> : this.props.children; }
}

export function GenerativeMessage({ block, disabled, onReply }: {
  block: UIBlock; disabled: boolean; onReply: (text: string) => void;
}) {
  const [notice, setNotice] = useState("");
  if (block.version !== 1) return <p>当前版本暂不支持这份交互内容。</p>;
  return <section className="generative-ui" data-aui="root" aria-label="助理交互内容">
    <Boundary>
      <fieldset disabled={disabled} className="generative-ui-content">
        <Suspense fallback={<p role="status">正在加载交互内容…</p>}>
          <Content tree={block.tree} dispatch={action => {
            if (disabled) return;
            try {
              onReply(actionReply(action, block.tree));
              setNotice("已填入输入框，发送后继续。");
            } catch (error) { setNotice(error instanceof Error ? error.message : "操作未完成"); }
          }} />
        </Suspense>
      </fieldset>
    </Boundary>
    {notice && <p className="generative-ui-notice" role="status">{notice}</p>}
  </section>;
}
