import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Moon, Sun, ArrowUp, Clapperboard } from "lucide-react";
import { GenerativeMessage } from "../components/workbench/GenerativeMessage";
import { ConversationAnswer } from "../components/workbench/ConversationAnswer";
import { ConversationMediaCard } from "../components/workbench/ConversationMedia";
import { ConversationTaskState } from "../components/workbench/ConversationTaskState";
import { I18nProvider } from "../lib/i18n";
import fixtures from "./generative-ui-fixtures.json";
import { scenes } from "./generative-ui-scenes";
import "../index.css";
import "../components/workbench/conversations.css";
import "./generative-preview.css";

function Preview() {
  const [theme, setTheme] = useState("dark");
  const [draft, setDraft] = useState("");
  const [selected, setSelected] = useState("visual");
  const [state, setState] = useState("running");
  const input = useRef<HTMLTextAreaElement>(null);
  const reply = (text: string) => { setDraft(value => value ? `${value}\n\n${text}` : text); input.current?.focus(); };
  const scene = scenes.find(s => s.id === selected);
  const visible = selected === "all" ? fixtures : fixtures.filter(f => f._type === selected);
  return <main className={`${theme} gui-preview`}>
    <div className="gui-preview-shell">
      <header className="gui-preview-header">
        <div className="gui-preview-brand"><Clapperboard size={18}/><span>Inspiration <span className="gui-preview-sub">/ 创作对话</span></span></div>
        <button className="gui-theme" aria-label="切换明暗" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun size={18}/> : <Moon size={18}/>}</button>
      </header>
      <nav className="gui-preview-nav" aria-label="创作场景">
        {scenes.map(s => <button key={s.id} aria-pressed={selected === s.id} onClick={() => setSelected(s.id)}>{s.label}</button>)}
        <button aria-pressed={selected === "media"} onClick={() => setSelected("media")}>生成结果</button>
        <button aria-pressed={selected === "status"} onClick={() => setSelected("status")}>执行状态</button>
        <select aria-label="选择组件" value={scene ? "" : selected} onChange={e => setSelected(e.target.value)}>
          <option value="" disabled>组件目录</option><option value="all">全部 35 个组件</option>{fixtures.map(f => <option key={f._type}>{f._type}</option>)}
        </select>
      </nav>
      {scene ? <section className="gui-conversation" key={scene.id}>
        <div className="gui-user-message">{scene.prompt}</div>
        <div className="gui-assistant-label"><Clapperboard size={16}/> Inspiration</div>
        <ConversationAnswer content={scene.intro} blocks={[{ id: scene.id, version: 1, tree: scene.tree }]} disabled={false} onReply={reply}/>
      </section> : selected === "media" ? <ConversationAnswer content={"## 先看画面，再决定如何继续\n\n可以放大检查细节，也可以把图片带入下一轮，继续调整或制作视频。"} disabled={false} onReply={reply}>
        <div className="conversation-media-results" data-multiple="true">
          <ConversationMediaCard src="/images/studio-scenes.webp" output={{id:"image-preview", type:"image", blob_hash:"preview-image"}} index={0} multiple disabled={false} reuse={(_, mode) => reply(mode === "video" ? "用这个画面继续制作视频" : "继续调整这张图片")} />
          <ConversationMediaCard src="/media/login-background.9da7def5a8d8.mp4" poster="/media/login-poster.c26c902907b5.webp" output={{id:"video-preview", type:"video", blob_hash:"preview-video"}} index={1} multiple disabled={false} reuse={() => reply("用这个画面继续制作视频")} />
        </div>
      </ConversationAnswer> : selected === "status" ? <section>
        <div className="gui-preview-nav">{["running", "failed"].map(value => <button key={value} aria-pressed={state === value} onClick={() => setState(value)}>{value === "running" ? "正在执行" : "执行失败"}</button>)}</div>
        <ConversationTaskState status={state} kind="视频" createdAt={new Date(Date.now()-28000).toISOString()} progress="正在生成第二个镜头，完成后会保留在这段对话里。" error="视频服务暂时不可用，已保留你的描述和参考素材。" disabled={false} retrying={false} retry={() => setState("running")} edit={() => reply("重新调整雨夜镜头的动作和节奏")} />
      </section> : visible.map(tree => <article key={tree._type} className="gui-catalog-item">
        <h2>{tree._type}</h2><GenerativeMessage block={{ id: tree._type, version: 1, tree: tree as Record<string, unknown> }} disabled={false} onReply={setDraft}/>
      </article>)}
      <div className="gui-reply">
        <label htmlFor="preview-reply">继续创作</label>
        <textarea ref={input} id="preview-reply" aria-label="带回对话的内容" placeholder="继续聊聊你的想法…" value={draft} onChange={e => setDraft(e.target.value)}/>
        <div className="gui-reply-footer"><span>{draft ? "内容已带回，可继续编辑" : "你的选择会出现在这里"}</span><ArrowUp size={18} aria-hidden="true"/></div>
      </div>
    </div>
  </main>;
}
if (import.meta.env.DEV) {
  const root = import.meta.hot?.data.root ?? createRoot(document.getElementById("root")!);
  if (import.meta.hot) import.meta.hot.data.root = root;
  root.render(<I18nProvider><BrowserRouter><Preview/></BrowserRouter></I18nProvider>);
}

