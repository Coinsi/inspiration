import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Moon, Sun, ArrowUp, Clapperboard } from "lucide-react";
import { GenerativeMessage } from "../components/workbench/GenerativeMessage";
import fixtures from "./generative-ui-fixtures.json";
import { scenes } from "./generative-ui-scenes";
import "../index.css";
import "./generative-preview.css";

function Preview() {
  const [theme, setTheme] = useState("dark");
  const [draft, setDraft] = useState("");
  const [selected, setSelected] = useState("character");
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
        <select aria-label="选择组件" value={scene ? "" : selected} onChange={e => setSelected(e.target.value)}>
          <option value="" disabled>组件目录</option><option value="all">全部 35 个组件</option>{fixtures.map(f => <option key={f._type}>{f._type}</option>)}
        </select>
      </nav>
      {scene ? <section className="gui-conversation" key={scene.id}>
        <div className="gui-user-message">{scene.prompt}</div>
        <div className="gui-assistant-label"><Clapperboard size={16}/> Inspiration</div>
        <p className="gui-assistant-intro">{scene.intro}</p>
        <GenerativeMessage block={{ id: scene.id, version: 1, tree: scene.tree }} disabled={false} onReply={setDraft}/>
      </section> : visible.map(tree => <article key={tree._type} className="gui-catalog-item">
        <h2>{tree._type}</h2><GenerativeMessage block={{ id: tree._type, version: 1, tree: tree as Record<string, unknown> }} disabled={false} onReply={setDraft}/>
      </article>)}
      <div className="gui-reply">
        <label htmlFor="preview-reply">继续创作</label>
        <textarea id="preview-reply" aria-label="带回对话的内容" placeholder="选择或调整上面的内容，继续你的故事…" value={draft} onChange={e => setDraft(e.target.value)}/>
        <div className="gui-reply-footer"><span>{draft ? "内容已带回，可继续编辑" : "你的选择会出现在这里"}</span><ArrowUp size={18} aria-hidden="true"/></div>
      </div>
    </div>
  </main>;
}
if (import.meta.env.DEV) {
  const root = import.meta.hot?.data.root ?? createRoot(document.getElementById("root")!);
  if (import.meta.hot) import.meta.hot.data.root = root;
  root.render(<Preview/>);
}

