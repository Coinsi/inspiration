import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDown,
  ArrowRight,
  Check,
  ChevronRight,
  Clapperboard,
  FileText,
  Film,
  Folder,
  Layers,
  Menu,
  Pause,
  Play,
  Plus,
  Sparkles,
  Workflow,
  X,
} from "lucide-react";
import { SiteMedia } from "./SiteMedia";
import { JournalTeaser } from "./BlogShared";
import { GitHubLink } from "./GitHubLink";
import type { FeatureKey, SiteContent } from "./types";
import "./landing.css";

const views = [
  { key: "story", label: "故事与剧本", icon: FileText },
  { key: "canvas", label: "自由画布", icon: Workflow },
  { key: "library", label: "素材与角色", icon: Folder },
  { key: "edit", label: "剪辑成片", icon: Film },
] as const;
const scenes = [
  {
    name: "灯塔下的来信",
    heading: "01. 外景 · 海边灯塔 · 黄昏",
    text: "潮水退去，石阶上留下一道白色的盐痕。林舟停在旧灯塔前，手里是一封没有寄出的信。",
    who: "林舟",
    line: "如果灯还亮着，他就会找到回来的路。",
  },
  {
    name: "迟到的归人",
    heading: "02. 内景 · 值班室 · 夜",
    text: "门被风推开。桌上的航海日志翻过一页，一个人站在灯光之外。他没有说话，先把湿透的帽子放了下来。",
    who: "守塔人",
    line: "我以为，你已经忘记这个地方了。",
  },
  {
    name: "最后一次点灯",
    heading: "03. 外景 · 塔顶 · 黎明",
    text: "海雾散开之前，灯光最后一次扫过远方。林舟收起信，抬头看向正在变亮的天空。",
    who: "林舟",
    line: "有些路，不需要灯也能回去。",
  },
];

export function SceneArt({ variant = 0 }: { variant?: number }) {
  return (
    <svg
      className={`site-scene-art art-${variant}`}
      viewBox="0 0 640 400"
      role="img"
      aria-label="原创示意插画：海边灯塔"
    >
      <rect
        width="640"
        height="400"
        fill={variant === 1 ? "#c8d9cf" : "#dfe8db"}
      />
      <circle cx={variant === 1 ? 470 : 490} cy="103" r="47" fill="#f2e9be" />
      <path d="M0 210 Q150 190 320 219 T640 208 V400 H0Z" fill="#a2bab1" />
      <path d="M0 250 Q180 222 340 245 T640 234V400H0Z" fill="#77968c" />
      <path d="M0 390 Q150 210 380 304L640 360V400H0Z" fill="#3f6256" />
      <path d="M340 282L355 102H391L407 298Z" fill="#f1efe2" />
      <path d="M355 102L355 76H391V102Z" fill="#446454" />
      <path d="M347 76L373 52L400 76Z" fill="#385445" />
      <path d="M363 86H384V98H363Z" fill="#efda8a" />
      <path d="M367 230H382V269H367Z" fill="#587260" />
      <path d="M380 93L575 41L560 145Z" fill="#fcf3bd" opacity=".28" />
      <path
        d="M0 334Q200 265 348 310"
        fill="none"
        stroke="#c9c5a7"
        strokeWidth="8"
      />
      <path
        d="M85 270H180M425 273H562M33 228H127"
        stroke="#e8f0e8"
        opacity=".4"
        strokeWidth="2"
      />
    </svg>
  );
}

function MiniView({ kind }: { kind: FeatureKey }) {
  return (
    <div className={`site-mini mini-${kind}`} aria-hidden="true">
      <div className="mini-top">
        <i />
        <i />
        <i />
        <span>Inspiration / 最后一盏灯</span>
      </div>
      {kind === "story" ? (
        <div className="mini-paper">
          <small>01 / STORY</small>
          <h4>最后一盏灯</h4>
          <p>外景 · 海边灯塔 · 黄昏</p>
          <b />
          <b />
          <b className="short" />
          <em>「如果灯还亮着……」</em>
          <b />
          <b className="short" />
        </div>
      ) : kind === "canvas" ? (
        <div className="mini-nodes">
          <span>
            故事灵感
            <br />
            <small>一封未寄出的信</small>
          </span>
          <i />
          <span>
            <SceneArt />
            <small>镜头参考</small>
          </span>
          <i />
          <span>
            画面创作
            <br />
            <small>连接下一份灵感</small>
          </span>
        </div>
      ) : kind === "library" ? (
        <div className="mini-assets">
          {[0, 1, 2].map((i) => (
            <div key={i}>
              <SceneArt variant={i % 2} />
              <span>{["场景参考", "灯光与氛围", "镜头版本"][i]}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mini-timeline">
          <SceneArt />
          <div>
            <i />
            <i />
            <i />
          </div>
          <div className="audio-strip" />
        </div>
      )}
    </div>
  );
}

function ProductDemo({
  view,
  setView,
  target,
}: {
  view: FeatureKey;
  setView: (v: FeatureKey) => void;
  target: string;
}) {
  const [scene, setScene] = useState(0),
    [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setScene((v) => (v + 1) % 3), 2600);
    return () => clearInterval(timer);
  }, [playing]);
  const s = scenes[scene];
  return (
    <div className="site-demo" id="product-demo">
      <div className="demo-titlebar">
        <span className="demo-dots">
          <i />
          <i />
          <i />
        </span>
        <span>
          <Clapperboard size={14} /> 最后一盏灯 <ChevronRight size={12} />{" "}
          创作项目
        </span>
        <span className="demo-indicator">
          <i /> 交互演示
        </span>
      </div>
      <div
        className="demo-views"
        role="tablist"
        aria-label="创作流程演示"
        onKeyDown={(e) => {
          const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
          if (!keys.includes(e.key)) return;
          e.preventDefault();
          const i = views.findIndex((v) => v.key === view);
          const next =
            e.key === "Home"
              ? 0
              : e.key === "End"
                ? 3
                : (i + (e.key === "ArrowRight" ? 1 : 3)) % 4;
          setView(views[next].key);
          document.getElementById(`demo-tab-${views[next].key}`)?.focus();
        }}
      >
        {views.map(({ key, label, icon: Icon }) => (
          <button
            id={`demo-tab-${key}`}
            role="tab"
            aria-selected={view === key}
            tabIndex={view === key ? 0 : -1}
            aria-controls="demo-panel"
            key={key}
            onClick={() => setView(key)}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>
      <div
        className="demo-body"
        id="demo-panel"
        role="tabpanel"
        aria-labelledby={`demo-tab-${view}`}
      >
        <aside className="demo-sidebar">
          <strong>最后一盏灯</strong>
          <small>一个关于等待与归来的故事</small>
          <p>
            项目内容 <Plus size={12} />
          </p>
          {scenes.map((s, i) => (
            <button
              key={s.name}
              className={scene === i ? "active" : ""}
              onClick={() => {
                setScene(i);
                setPlaying(false);
              }}
            >
              <span>0{i + 1}</span>
              {s.name}
            </button>
          ))}
          <div className="demo-side-bottom">
            <Layers size={15} />
            <span>所有素材，围绕故事生长。</span>
          </div>
        </aside>
        <div className={`demo-main view-${view}`}>
          {view === "story" ? (
            <article className="demo-script">
              <small>INSPIRATION ORIGINAL · 创作示例</small>
              <h3>最后一盏灯</h3>
              <p className="script-subtitle">THE LAST LIGHT</p>
              <hr />
              <h4>{s.heading}</h4>
              <p>{s.text}</p>
              <div className="script-dialogue">
                <strong>{s.who}</strong>
                <em>（望向远处）</em>
                <p>{s.line}</p>
              </div>
              <p className="script-action">
                远处传来一声汽笛。光线缓慢掠过海面。
              </p>
              <span className="script-cut">CUT TO:</span>
              <span className="script-page">— {scene + 1} —</span>
            </article>
          ) : view === "canvas" ? (
            <div className="demo-canvas">
              <svg
                viewBox="0 0 700 450"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <path d="M170 140C330 140 290 285 430 285" />
                <path d="M170 340C300 340 280 285 430 285" />
              </svg>
              <div className="demo-node node-text">
                <small>
                  <FileText size={12} /> 故事灵感
                </small>
                <strong>{s.name}</strong>
                <p>{s.line}</p>
                <i />
              </div>
              <div className="demo-node node-reference">
                <small>场景参考</small>
                <SceneArt variant={scene % 2} />
                <i />
              </div>
              <div className="demo-node node-result">
                <small>
                  <Sparkles size={12} /> 镜头构想
                </small>
                <SceneArt variant={1} />
                <p>晨雾里的灯塔 · 远景</p>
                <span>参考已连接</span>
              </div>
              <div className="demo-canvas-tools">
                ＋ <span>100%</span> －
              </div>
            </div>
          ) : view === "library" ? (
            <div className="demo-library">
              <header>
                <h3>故事的素材</h3>
                <span>固定版本 · 清晰引用</span>
              </header>
              <div>
                {["海岸与灯塔", "清晨的海雾", "灯塔的光", "归途的颜色"].map(
                  (name, i) => (
                    <article key={name}>
                      <SceneArt variant={i % 2} />
                      <strong>{name}</strong>
                      <span>场景参考 / v0{i + 1}</span>
                    </article>
                  ),
                )}
              </div>
            </div>
          ) : (
            <div className="demo-edit">
              <div className="edit-monitor">
                <SceneArt variant={scene % 2} />
                <span>{s.name}</span>
              </div>
              <div className="edit-playback">
                <button
                  onClick={() => setPlaying(!playing)}
                  aria-label={playing ? "暂停演示" : "播放演示"}
                >
                  {playing ? <Pause size={15} /> : <Play size={15} />}
                </button>
                <span>00:0{scene * 3} / 00:09</span>
                <span>剪辑流程示意</span>
              </div>
              <div className="edit-tracks">
                <div className="edit-ruler">
                  00:00 <span>00:03</span>
                  <span>00:06</span>
                  <span>00:09</span>
                </div>
                <div className="edit-clips">
                  {scenes.map((s, i) => (
                    <button
                      className={scene === i ? "active" : ""}
                      key={s.name}
                      onClick={() => setScene(i)}
                    >
                      <SceneArt variant={i % 2} />
                      <span>{s.name}</span>
                    </button>
                  ))}
                </div>
                <div className="edit-audio">环境声 · 海浪与风</div>
                <div
                  className="edit-playhead"
                  style={{ left: `${scene * 30 + 5}%` }}
                />
              </div>
            </div>
          )}
        </div>
        <aside className="demo-assistant">
          <div>
            <Sparkles size={16} />
            <strong>创作助理</strong>
          </div>
          <p>让下一步，更清楚一点。</p>
          <div className="demo-message">
            {view === "story"
              ? "帮我梳理这一场的情绪变化。"
              : view === "canvas"
                ? "把灯塔参考接到下一个镜头。"
                : view === "library"
                  ? "为这个故事整理参考素材。"
                  : "让这三组镜头的节奏更连贯。"}
          </div>
          <div className="demo-answer">
            <span>
              <Check size={12} /> 示例建议
            </span>
            <p>
              从「等待」到「确认」，可以用一个缓慢推进的远景，保留人物与灯塔的距离。
            </p>
            <div>
              <FileText size={13} /> 关联场景：{s.name}
            </div>
          </div>
          <small>此处为流程示意，不会调用模型或修改项目。</small>
          <Link to={target}>
            在自己的项目里试试 <ArrowRight size={14} />
          </Link>
        </aside>
      </div>
      <div className="demo-caption">
        <span>同一个故事，四种创作视角。</span>
        <span>
          点击标签，探索工作流程 <ArrowRight size={13} />
        </span>
      </div>
    </div>
  );
}

export function LandingView({
  content: c,
  mediaTypes = {},
  preview = false,
  authenticated = false,
}: {
  content: SiteContent;
  mediaTypes?: Record<string, string>;
  preview?: boolean;
  authenticated?: boolean;
}) {
  const [view, setView] = useState<FeatureKey>("story"),
    [mobile, setMobile] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const [showDemo, setShowDemo] = useState(!c.hero_media_id);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => setShowDemo(!c.hero_media_id), [c.hero_media_id]);
  useEffect(() => {
    const observer = new IntersectionObserver(
      () => {
        const readingLine = window.innerHeight * 0.3;
        const current = Array.from(
          root.current?.querySelectorAll("main > section[id]") ?? [],
        ).find((section) => {
          const box = section.getBoundingClientRect();
          return box.top <= readingLine && box.bottom > readingLine;
        });
        setActiveSection(current?.id ?? "");
      },
      { rootMargin: "-15% 0px -65% 0px" },
    );
    root.current
      ?.querySelectorAll("main > section[id]")
      .forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [c]);
  useEffect(() => {
    const items = root.current?.querySelectorAll(".site-reveal");
    if (!items) return;
    const observer = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
            observer.unobserve(e.target);
          }
        }),
      { threshold: 0.08 },
    );
    items.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [c]);
  const target = authenticated ? "/projects" : "/register";
  const jump = (key: FeatureKey) => {
    setView(key);
    setShowDemo(true);
    requestAnimationFrame(() =>
      root.current?.querySelector("#product-demo")?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      }),
    );
  };
  const media = (id: string, alt: string) => (
    <SiteMedia id={id} mime={mediaTypes[id]} preview={preview} alt={alt} />
  );
  const available = c.sections.filter((s) => s.enabled).map((s) => s.key);
  return (
    <div
      className="site-root"
      ref={root}
      onKeyDown={(event) => {
        if (event.key === "Escape" && mobile) {
          setMobile(false);
          root.current
            ?.querySelector<HTMLButtonElement>(".site-menu-toggle")
            ?.focus();
        }
      }}
      onClick={(event) => {
        if (
          event.defaultPrevented ||
          event.ctrlKey ||
          event.metaKey ||
          event.shiftKey ||
          event.altKey
        )
          return;
        const anchor = (event.target as HTMLElement).closest("a[href^='#']");
        const id = anchor?.getAttribute("href")?.slice(1);
        const section = id ? root.current?.querySelector(`[id="${id}"]`) : null;
        if (section) {
          event.preventDefault();
          section.scrollIntoView({
            behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
              .matches
              ? "auto"
              : "smooth",
            block: "start",
          });
        }
      }}
    >
      <a href="#site-main" className="site-skip">
        跳至正文
      </a>
      <header className="site-nav">
        <div className="site-nav-inner">
          <Link to="/" className="site-brand">
            <span className="site-mark">
              <i />
              <i />
              <i />
            </span>
            {c.brand}
            <small>创作工作台</small>
          </Link>
          <nav aria-label="官网导航" className={mobile ? "open" : ""}>
            {available.includes("features") && (
              <a
                href="#features"
                aria-current={
                  activeSection === "features" ? "location" : undefined
                }
                onClick={() => setMobile(false)}
              >
                产品能力
              </a>
            )}
            {available.includes("workflow") && (
              <a
                href="#workflow"
                aria-current={
                  activeSection === "workflow" ? "location" : undefined
                }
                onClick={() => setMobile(false)}
              >
                创作流程
              </a>
            )}
            {available.includes("case") && (
              <a
                href="#case"
                aria-current={activeSection === "case" ? "location" : undefined}
                onClick={() => setMobile(false)}
              >
                创作示例
              </a>
            )}
            {available.includes("faq") && (
              <a
                href="#faq"
                aria-current={activeSection === "faq" ? "location" : undefined}
                onClick={() => setMobile(false)}
              >
                常见问题
              </a>
            )}
            <Link to="/blog">博客</Link>
            <GitHubLink onClick={() => setMobile(false)} />
          </nav>
          <div className="site-nav-actions">
            <Link to={authenticated ? "/projects" : "/login"}>
              {authenticated ? "工作台" : "登录"}
            </Link>
            <Link to={target} className="site-button small">
              {authenticated ? "继续创作" : "开始创作"}
              <ArrowRight size={14} />
            </Link>
            <button
              className="site-menu-toggle"
              aria-label={mobile ? "收起菜单" : "展开菜单"}
              aria-expanded={mobile}
              onClick={() => setMobile(!mobile)}
            >
              {mobile ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>
      </header>
      {c.announcement && (
        <a className="site-announcement" href="#demo">
          <span>INSPIRATION / NOTES</span>
          {c.announcement}
          <ArrowRight size={14} />
        </a>
      )}
      <main id="site-main">
        <section className="site-hero site-contained">
          <span className="site-coordinate">[ 01 — THE BEGINNING ]</span>
          <span className="site-edition" aria-hidden="true">
            IDEAS INTO FRAMES / 创作进行时
          </span>
          <div className="site-hero-glow" />
          <p className="site-eyebrow">{c.eyebrow}</p>
          <h1>{c.title}</h1>
          <p className="site-hero-copy">{c.subtitle}</p>
          <div className="site-hero-actions">
            <Link to={target} className="site-button">
              {c.cta}
              <ArrowRight size={17} />
            </Link>
            <a href="#demo" className="site-watch">
              <span>
                <Play size={12} fill="currentColor" />
              </span>
              探索工作台
            </a>
          </div>
          <div className="site-hero-foot">
            <span>
              <i /> 留住灵感，保持创作的主动权
            </span>
            <a href="#demo">
              向下探索 <ArrowDown size={14} />
            </a>
          </div>
        </section>
        <section className="site-showcase site-contained" id="demo">
          <div className="site-showcase-heading">
            <span>
              <i /> THE CREATIVE WORKSPACE
            </span>
            <p>从一个想法，看见作品的轮廓。</p>
            <span>文字 · 画面 · 镜头</span>
          </div>
          {c.hero_media_id ? (
            <div className="site-hero-media">
              {media(c.hero_media_id, "工作台产品演示")}
            </div>
          ) : null}
          {c.hero_media_id && (
            <button
              className="site-demo-toggle"
              aria-expanded={showDemo}
              aria-controls="product-demo"
              onClick={() => setShowDemo(!showDemo)}
            >
              {showDemo ? "收起交互工作台" : "继续探索交互工作台"}
              <ArrowDown size={14} />
            </button>
          )}
          {showDemo && (
            <ProductDemo view={view} setView={setView} target={target} />
          )}
          <div className="site-value-strip">
            <p>
              <strong>故事与画面相连</strong>
              <span>从文字到镜头，沿着同一个想法前进。</span>
            </p>
            <p>
              <strong>素材与创作相连</strong>
              <span>参考、版本与引用，都有清楚的来处。</span>
            </p>
            <p>
              <strong>工具与人相连</strong>
              <span>让 AI 协助执行，把决定留给创作者。</span>
            </p>
          </div>
        </section>
        {c.sections
          .filter((s) => s.enabled)
          .map((section) =>
            section.key === "workflow" ? (
              <section
                id="workflow"
                key={section.key}
                className="site-section site-contained site-reveal"
              >
                <div className="site-section-heading">
                  <div>
                    <p className="site-eyebrow">01 / THE CREATIVE FLOW</p>
                    <h2>{c.workflow_title}</h2>
                  </div>
                  <p>{c.workflow_description}</p>
                </div>
                <div className="site-steps">
                  {views.map(({ key, label, icon: Icon }, i) => (
                    <button
                      key={key}
                      aria-pressed={view === key}
                      onClick={() => jump(key)}
                    >
                      <small>0{i + 1} /</small>
                      <Icon size={24} strokeWidth={1.2} />
                      <h3>{label}</h3>
                      <p>
                        {
                          [
                            "先写下，一个值得讲述的故事。",
                            "让想法与参考，在画布上相遇。",
                            "为每个画面，找到合适的素材。",
                            "把独立的镜头，变成一段表达。",
                          ][i]
                        }
                      </p>
                      <span>
                        探索这一环 <ArrowRight size={14} />
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            ) : section.key === "features" ? (
              <section
                id="features"
                key={section.key}
                className="site-section site-contained site-reveal"
              >
                <div className="site-section-heading">
                  <div>
                    <p className="site-eyebrow">02 / BUILT FOR THE DETAILS</p>
                    <h2>{c.features_title}</h2>
                  </div>
                  <a href="#demo" className="site-text-link">
                    看看它们如何配合 <ArrowRight size={16} />
                  </a>
                </div>
                <div className="site-features">
                  {c.features.map((f, i) => (
                    <article key={f.key} className={`site-feature-${f.key}`}>
                      <div className="site-feature-preview">
                        {f.media_id ? (
                          media(f.media_id, f.title)
                        ) : (
                          <MiniView kind={f.key} />
                        )}
                      </div>
                      <small>
                        0{i + 1} / {views.find((v) => v.key === f.key)?.label}
                      </small>
                      <h3>{f.title}</h3>
                      <p>{f.description}</p>
                      <button
                        className="site-text-link"
                        onClick={() => jump(f.key)}
                      >
                        打开流程演示 <ArrowRight size={15} />
                      </button>
                    </article>
                  ))}
                </div>
              </section>
            ) : section.key === "case" ? (
              <section
                id="case"
                key={section.key}
                className="site-case site-contained site-reveal"
              >
                <div className="site-case-art">
                  {c.case_media_id ? (
                    media(c.case_media_id, c.case_title)
                  ) : (
                    <>
                      <SceneArt />
                      <div className="case-film-caption">
                        <span>INSPIRATION ORIGINAL</span>
                        <strong>最后一盏灯</strong>
                        <small>THE LAST LIGHT / 创作示例</small>
                      </div>
                    </>
                  )}
                </div>
                <div className="site-case-copy">
                  <p className="site-eyebrow">
                    03 / ONE STORY. MANY POSSIBILITIES.
                  </p>
                  <h2>{c.case_title}</h2>
                  <p>{c.case_description}</p>
                  <div className="case-tags">
                    <span>故事</span>
                    <i>↗</i>
                    <span>参考</span>
                    <i>↗</i>
                    <span>镜头</span>
                    <i>↗</i>
                    <span>作品</span>
                  </div>
                  <button
                    className="site-text-link"
                    onClick={() => jump("story")}
                  >
                    回到故事的第一幕 <ArrowRight size={16} />
                  </button>
                </div>
              </section>
            ) : (
              <section
                id="faq"
                key={section.key}
                className="site-faq site-contained site-reveal"
              >
                <div>
                  <p className="site-eyebrow">04 / A FEW THINGS TO KNOW</p>
                  <h2>
                    开始之前，
                    <br />
                    你可能想知道。
                  </h2>
                  <p>
                    把工具了解清楚，
                    <br />
                    再把注意力留给创作。
                  </p>
                </div>
                <div>
                  {c.faq.map((f, i) => (
                    <details key={i}>
                      <summary>
                        <span className="site-faq-number" aria-hidden="true">
                          0{i + 1}
                        </span>
                        <span>{f.question}</span>
                        <Plus size={17} />
                      </summary>
                      <p>{f.answer}</p>
                    </details>
                  ))}
                </div>
              </section>
            ),
          )}
        <JournalTeaser />
        <section className="site-closing site-contained site-reveal">
          <span className="site-eyebrow">YOUR STORY STARTS HERE</span>
          <h2>{c.closing_title}</h2>
          <Link className="site-button" to={target}>
            {c.cta}
            <ArrowRight size={17} />
          </Link>
          <span className="site-closing-note">一个想法，也值得认真开始。</span>
        </section>
      </main>
      <footer className="site-footer">
        <div className="site-contained">
          <div>
            <Link to="/" className="site-brand">
              <span className="site-mark">
                <i />
                <i />
                <i />
              </span>
              {c.brand}
            </Link>
            <p>{c.footer}</p>
          </div>
          <nav aria-label="页脚导航">
            <a href="#demo">探索工作台</a>
            <Link to="/blog">博客与创作手记</Link>
            <GitHubLink />
            <Link to="/login">账户登录</Link>
            <Link to="/register">创建账户</Link>
          </nav>
          <div className="site-footer-bottom">
            <span>
              © {new Date().getFullYear()} {c.brand}
            </span>
            <span>MADE FOR THE STORIES ONLY YOU CAN TELL.</span>
            <a href="#site-main">回到顶部 ↑</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
