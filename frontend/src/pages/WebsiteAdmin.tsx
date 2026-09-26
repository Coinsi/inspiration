import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  ExternalLink,
  Eye,
  FileText,
  History,
  ImagePlus,
  LayoutTemplate,
  Loader2,
  Plus,
  Save,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { api, apiUpload } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { LandingView } from "@/components/website/LandingView";
import type {
  SiteAsset,
  SiteContent,
  SiteState,
} from "@/components/website/types";
import "./website-admin.css";
const sectionNames = {
  workflow: "创作流程",
  features: "产品能力",
  case: "创作示例",
  faq: "常见问题",
};
const groups = [
  { key: "hero", label: "首页与品牌", icon: LayoutTemplate },
  { key: "sections", label: "板块与内容", icon: FileText },
  { key: "media", label: "展示素材", icon: ImagePlus },
  { key: "history", label: "发布历史", icon: History },
] as const;
type Group = (typeof groups)[number]["key"];

export default function WebsiteAdmin() {
  const { me, loading } = useAuth();
  if (loading) return <p className="p-10">正在检查管理权限…</p>;
  if (!me?.user.is_platform_admin)
    return (
      <div className="website-denied">
        <ShieldCheck size={32} />
        <h1>这里是平台管理端</h1>
        <p>官网内容由平台管理员维护。项目管理员权限不能用于管理官网。</p>
        <Link to="/projects">返回工作台</Link>
      </div>
    );
  return <Editor />;
}

function Editor() {
  const qc = useQueryClient(),
    confirm = useConfirm(),
    navigate = useNavigate();
  const query = useQuery({
    queryKey: ["website-admin"],
    queryFn: () => api.get<SiteState>("/admin/website"),
    refetchOnWindowFocus: false,
  });
  const [state, setState] = useState<SiteState | null>(null),
    [content, setContent] = useState<SiteContent | null>(null),
    [dirty, setDirty] = useState(false);
  const [group, setGroup] = useState<Group>("hero"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [preview, setPreview] = useState(false),
    [note, setNote] = useState("");
  const [offset, setOffset] = useState(0),
    [historyOffset, setHistoryOffset] = useState(0),
    [choose, setChoose] = useState<((id: string | null) => void) | null>(null);
  const [types, setTypes] = useState<Record<string, string>>({});
  const uploadInput = useRef<HTMLInputElement>(null);
  const assets = useQuery({
    queryKey: ["website-media", offset],
    queryFn: () =>
      api.get<SiteAsset[]>(`/admin/website/media?offset=${offset}`),
    enabled: group === "media" || !!choose,
  });
  const history = useQuery({
    queryKey: ["website-history", historyOffset],
    queryFn: () =>
      api.get<
        { revision: number; note: string; author: string; created_at: string }[]
      >(`/admin/website/history?offset=${historyOffset}`),
    enabled: group === "history",
  });
  function adopt(next: SiteState) {
    setState(next);
    setContent(next.draft);
    setTypes((v) => ({ ...v, ...next.media_types }));
    setDirty(false);
    qc.setQueryData(["website-admin"], next);
  }
  useEffect(() => {
    if (query.data && !state) adopt(query.data);
  }, [query.data]);
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  useEffect(() => {
    if (assets.data)
      setTypes((v) => ({
        ...v,
        ...Object.fromEntries(assets.data.map((a) => [a.id, a.mime])),
      }));
  }, [assets.data]);
  function edit(next: SiteContent) {
    setContent(next);
    setDirty(true);
    setNotice("");
  }
  async function action(fn: () => Promise<void>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      setNotice(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function leave(event: React.MouseEvent<HTMLAnchorElement>) {
    if (!dirty) return;
    event.preventDefault();
    const href = event.currentTarget.getAttribute("href") || "/";
    if (
      await confirm({
        title: "离开内容管理？",
        message: "未保存的修改会丢失。可以先保存草稿。",
        confirmText: "放弃并离开",
        danger: true,
      })
    )
      navigate(href);
  }
  async function reload() {
    if (
      dirty &&
      !(await confirm({
        title: "重新载入服务器草稿？",
        message: "这会覆盖当前窗口尚未保存的修改。",
        confirmText: "重新载入",
      }))
    )
      return;
    void action(
      async () => adopt(await api.get<SiteState>("/admin/website")),
      "已载入最新草稿",
    );
  }
  async function upload(file: File) {
    if (file.size > 80 * 1024 * 1024) {
      setError("文件不能超过80 MB");
      return;
    }
    void action(async () => {
      const body = new FormData();
      body.append("file", file);
      const asset = await apiUpload<SiteAsset>("/admin/website/media", body);
      setTypes((v) => ({ ...v, [asset.id]: asset.mime }));
      await qc.invalidateQueries({ queryKey: ["website-media"] });
      if (choose) {
        choose(asset.id);
        setChoose(null);
      }
      setOffset(0);
    }, "素材已上传。只有引用它的内容发布后，访客才能访问。");
  }
  if (query.isLoading || !content || !state)
    return (
      <div className="website-denied">
        <p>{query.error ? query.error.message : "正在载入官网内容…"}</p>
        {query.error && (
          <button onClick={() => void query.refetch()}>重试</button>
        )}
      </div>
    );
  const c = content;
  function text(
    key: keyof SiteContent,
    label: string,
    max: number,
    multiline = false,
  ) {
    const props = {
      "aria-label": label,
      value: String(c[key] ?? ""),
      maxLength: max,
      disabled: busy,
      onChange: (
        e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
      ) => edit({ ...c, [key]: e.target.value }),
    };
    return (
      <label className="website-field" key={key}>
        {label}
        {multiline ? <textarea {...props} rows={3} /> : <input {...props} />}
      </label>
    );
  }
  function mediaField(
    id: string | null,
    onChange: (id: string | null) => void,
    label: string,
  ) {
    return (
      <div className="website-media-field">
        <div>
          <strong>{label}</strong>
          <small>{id ? "已选择展示素材" : "使用内置交互演示或原创插画"}</small>
        </div>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => {
            setChoose(() => onChange);
            setError("");
          }}
        >
          选择 / 上传
        </Button>
        {id && (
          <button
            aria-label={`移除${label}`}
            disabled={busy}
            onClick={() => onChange(null)}
          >
            <X size={16} />
          </button>
        )}
      </div>
    );
  }
  return (
    <div className={`website-admin ${preview ? "website-preview" : ""}`}>
      <header className="website-admin-header">
        <div>
          <ShieldCheck size={21} />
          <strong>
            Inspiration <span>平台管理</span>
          </strong>
        </div>
        <nav>
          <a href="/admin/blog" onClick={(e) => void leave(e)}>
            博客管理
          </a>
          <a href="/" onClick={(e) => void leave(e)}>
            <ExternalLink size={14} /> 查看官网
          </a>
          <a href="/projects" onClick={(e) => void leave(e)}>
            工作台 <ArrowLeft size={14} />
          </a>
        </nav>
      </header>
      <div className="website-admin-bar">
        <div>
          <span className={dirty ? "website-state dirty" : "website-state"}>
            <i />
            {dirty ? "有未保存的修改" : "草稿已保存"}
          </span>
          <small>
            线上版本 {state.published_revision || "初始内容"} · 草稿版本{" "}
            {state.version}
          </small>
        </div>
        <div>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setPreview(!preview)}
          >
            <Eye size={15} />
            {preview ? "返回编辑" : "预览草稿"}
          </Button>
          <Button
            variant="outline"
            disabled={!dirty || busy}
            onClick={() =>
              void action(
                async () =>
                  adopt(
                    await api.put<SiteState>("/admin/website/draft", {
                      version: state.version,
                      content: c,
                    }),
                  ),
                "草稿已保存，线上内容未改变",
              )
            }
          >
            <Save size={15} />
            保存草稿
          </Button>
          <Button
            disabled={dirty || busy}
            onClick={async () => {
              if (
                !(await confirm({
                  title: "发布到官网？",
                  message:
                    "这份已保存的草稿将替换当前官网。被引用的展示素材会公开，历史版本会保留。",
                  confirmText: "确认发布",
                }))
              )
                return;
              void action(async () => {
                adopt(
                  await api.post<SiteState>("/admin/website/publish", {
                    version: state.version,
                    note: note.trim() || "官网内容更新",
                  }),
                );
                await qc.invalidateQueries({ queryKey: ["website-history"] });
                await qc.invalidateQueries({ queryKey: ["public-website"] });
                setNote("");
              }, "官网已更新，访客现在可以看到新内容");
            }}
          >
            <Check size={15} />
            发布更新
          </Button>
        </div>
      </div>
      {error && (
        <div role="alert" className="website-feedback error">
          {error}
          <button onClick={() => void reload()} disabled={busy}>
            重新载入草稿
          </button>
        </div>
      )}
      {notice && (
        <p role="status" className="website-feedback">
          {notice}
        </p>
      )}
      {preview ? (
        <>
          <div className="website-preview-label">
            草稿预览 · {dirty ? "包含未保存修改" : "尚未发布的内容"} ·
            仅平台管理员可见
          </div>
          <div
            onClickCapture={(event) => {
              const link = (event.target as HTMLElement).closest("a");
              if (link?.getAttribute("href")?.startsWith("/")) {
                event.preventDefault();
                event.stopPropagation();
                setNotice("这是草稿预览。请使用顶部入口访问官网或工作台。");
              }
            }}
          >
            <LandingView content={c} mediaTypes={types} preview authenticated />
          </div>
        </>
      ) : (
        <div className="website-admin-layout">
          <aside>
            <p>官网内容管理</p>
            {groups.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                aria-current={group === key ? "page" : undefined}
                onClick={() => setGroup(key)}
              >
                <Icon size={17} />
                {label}
              </button>
            ))}
            <div className="website-admin-help">
              <strong>先编辑，再发布。</strong>
              <p>保存草稿不会影响官网。预览确认后，再发布给所有访客。</p>
              <button onClick={() => void reload()} disabled={busy}>
                载入最新草稿
              </button>
            </div>
          </aside>
          <main>
            <header className="website-editor-heading">
              <p>WEBSITE / CONTENT STUDIO</p>
              <h1>{groups.find((g) => g.key === group)?.label}</h1>
              <span>
                {group === "hero"
                  ? "把产品的第一印象，打磨成你想要的样子。"
                  : group === "sections"
                    ? "固定的精致布局，内容由你掌握。"
                    : group === "media"
                      ? "专门用于公开展示的素材，与私人项目隔离。"
                      : "每次发布都有记录，恢复后可预览并重新发布。"}
              </span>
            </header>
            {group === "hero" && (
              <>
                <section className="website-editor-card">
                  <h2>品牌与首屏</h2>
                  <div className="website-fields two">
                    {text("brand", "品牌名称", 40)}
                    {text("eyebrow", "首屏引导文字", 100)}
                  </div>
                  {text("announcement", "顶部公告（可留空）", 120)}
                  {text("title", "主标题（换行会保留）", 140, true)}
                  {text("subtitle", "产品介绍", 350, true)}
                  {text("cta", "开始创作按钮文字", 30)}
                  {mediaField(
                    c.hero_media_id,
                    (id) => edit({ ...c, hero_media_id: id }),
                    "首屏产品演示",
                  )}
                </section>
                <section className="website-editor-card">
                  <h2>结束语与页脚</h2>
                  {text("closing_title", "结束语", 120, true)}
                  {text("footer", "页脚介绍", 200, true)}
                  <label className="website-field">
                    本次发布说明
                    <input
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      maxLength={300}
                      placeholder="例如：更新画布介绍与产品演示"
                    />
                  </label>
                </section>
              </>
            )}
            {group === "sections" && (
              <>
                <section className="website-editor-card">
                  <h2>板块顺序与可见性</h2>
                  <p className="website-editor-hint">
                    首屏始终展示，下方板块可以调整顺序或隐藏。
                  </p>
                  {c.sections.map((s, i) => (
                    <div className="website-order-row" key={s.key}>
                      <span>0{i + 1}</span>
                      <strong>{sectionNames[s.key]}</strong>
                      <label>
                        <input
                          type="checkbox"
                          checked={s.enabled}
                          disabled={busy}
                          onChange={(e) =>
                            edit({
                              ...c,
                              sections: c.sections.map((x) =>
                                x.key === s.key
                                  ? { ...x, enabled: e.target.checked }
                                  : x,
                              ),
                            })
                          }
                        />
                        展示
                      </label>
                      {[-1, 1].map((d) => (
                        <button
                          key={d}
                          disabled={
                            busy || i + d < 0 || i + d >= c.sections.length
                          }
                          aria-label={`${d < 0 ? "上移" : "下移"}${sectionNames[s.key]}`}
                          onClick={() => {
                            const next = [...c.sections];
                            [next[i], next[i + d]] = [next[i + d], next[i]];
                            edit({ ...c, sections: next });
                          }}
                        >
                          {d < 0 ? (
                            <ArrowUp size={15} />
                          ) : (
                            <ArrowDown size={15} />
                          )}
                        </button>
                      ))}
                    </div>
                  ))}
                </section>
                <section className="website-editor-card">
                  <h2>创作流程</h2>
                  {text("workflow_title", "流程标题", 100)}
                  {text("workflow_description", "流程介绍", 350, true)}
                </section>
                <section className="website-editor-card">
                  <h2>产品能力</h2>
                  {text("features_title", "板块标题", 100)}
                  {c.features.map((f, i) => (
                    <div className="website-feature-editor" key={f.key}>
                      <small>
                        0{i + 1} / {f.key}
                      </small>
                      <label className="website-field">
                        功能标题
                        <input
                          value={f.title}
                          maxLength={80}
                          disabled={busy}
                          onChange={(e) =>
                            edit({
                              ...c,
                              features: c.features.map((x, n) =>
                                n === i ? { ...x, title: e.target.value } : x,
                              ),
                            })
                          }
                        />
                      </label>
                      <label className="website-field">
                        功能介绍
                        <textarea
                          rows={3}
                          value={f.description}
                          maxLength={350}
                          disabled={busy}
                          onChange={(e) =>
                            edit({
                              ...c,
                              features: c.features.map((x, n) =>
                                n === i
                                  ? { ...x, description: e.target.value }
                                  : x,
                              ),
                            })
                          }
                        />
                      </label>
                      {mediaField(
                        f.media_id,
                        (id) =>
                          edit({
                            ...c,
                            features: c.features.map((x, n) =>
                              n === i ? { ...x, media_id: id } : x,
                            ),
                          }),
                        `${f.title}配图`,
                      )}
                    </div>
                  ))}
                </section>
                <section className="website-editor-card">
                  <h2>创作示例</h2>
                  {text("case_title", "案例标题", 100)}
                  {text("case_description", "案例介绍", 700, true)}
                  {mediaField(
                    c.case_media_id,
                    (id) => edit({ ...c, case_media_id: id }),
                    "案例图片 / 视频",
                  )}
                </section>
                <section className="website-editor-card">
                  <h2>常见问题</h2>
                  {c.faq.map((f, i) => (
                    <div className="website-faq-editor" key={i}>
                      <label className="website-field">
                        问题 {i + 1}
                        <input
                          value={f.question}
                          disabled={busy}
                          maxLength={150}
                          onChange={(e) =>
                            edit({
                              ...c,
                              faq: c.faq.map((x, n) =>
                                n === i
                                  ? { ...x, question: e.target.value }
                                  : x,
                              ),
                            })
                          }
                        />
                      </label>
                      <label className="website-field">
                        回答
                        <textarea
                          value={f.answer}
                          disabled={busy}
                          rows={3}
                          maxLength={700}
                          onChange={(e) =>
                            edit({
                              ...c,
                              faq: c.faq.map((x, n) =>
                                n === i ? { ...x, answer: e.target.value } : x,
                              ),
                            })
                          }
                        />
                      </label>
                      <button
                        disabled={busy || c.faq.length <= 1}
                        onClick={() =>
                          edit({ ...c, faq: c.faq.filter((_, n) => n !== i) })
                        }
                      >
                        移除此问题
                      </button>
                    </div>
                  ))}
                  <Button
                    variant="outline"
                    disabled={busy || c.faq.length >= 12}
                    onClick={() =>
                      edit({
                        ...c,
                        faq: [
                          ...c.faq,
                          { question: "新的问题", answer: "请填写回答。" },
                        ],
                      })
                    }
                  >
                    <Plus size={15} />
                    添加问题
                  </Button>
                </section>
              </>
            )}
            {group === "media" && (
              <section className="website-editor-card">
                <h2>展示素材库</h2>
                <p className="website-editor-hint">
                  支持 JPG、PNG、WebP 和 MP4，最大80
                  MB。上传不等于公开，只有已发布内容引用的文件才能被访客读取。
                </p>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => uploadInput.current?.click()}
                >
                  {busy ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Upload size={16} />
                  )}
                  上传展示素材
                </Button>
                <div className="website-assets">
                  {assets.data?.map((a) => (
                    <div key={a.id}>
                      <ImagePlus size={20} />
                      <div>
                        <strong>{a.name}</strong>
                        <span>
                          {a.mime} · {(a.bytes / 1024 / 1024).toFixed(2)} MB
                        </span>
                      </div>
                      <small>
                        {Object.values(c).includes(a.id) ||
                        c.features.some((f) => f.media_id === a.id)
                          ? "草稿已引用"
                          : "可用于展示"}
                      </small>
                    </div>
                  ))}
                </div>
                {assets.isLoading && <p>正在加载…</p>}
                {assets.error && <p role="alert">{assets.error.message}</p>}
                {assets.data?.length === 0 && (
                  <p className="website-empty">
                    还没有展示素材。也可以继续使用内置的产品演示。
                  </p>
                )}
                <div className="website-pagination">
                  <Button
                    variant="ghost"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 30))}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={assets.data?.length !== 30}
                    onClick={() => setOffset(offset + 30)}
                  >
                    下一页
                  </Button>
                </div>
              </section>
            )}
            {group === "history" && (
              <section className="website-editor-card">
                <h2>发布记录</h2>
                <p className="website-editor-hint">
                  恢复历史版本只会替换草稿；确认并发布后，官网才会变化。
                </p>
                {history.isLoading && <p>正在加载…</p>}
                {history.error && <p role="alert">{history.error.message}</p>}
                {history.data?.map((r) => (
                  <div className="website-history-row" key={r.revision}>
                    <span>V{r.revision}</span>
                    <div>
                      <strong>{r.note}</strong>
                      <small>
                        {r.author} · {new Date(r.created_at).toLocaleString()}
                      </small>
                    </div>
                    {r.revision === state.published_revision && (
                      <small>当前线上</small>
                    )}
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={async () => {
                        if (
                          !(await confirm({
                            title: `将 V${r.revision} 恢复为草稿？`,
                            message:
                              "当前草稿及本窗口未保存的修改将被覆盖，线上内容暂不改变。",
                            confirmText: "恢复为草稿",
                          }))
                        )
                          return;
                        void action(
                          async () =>
                            adopt(
                              await api.post<SiteState>(
                                `/admin/website/history/${r.revision}/restore`,
                                { version: state.version },
                              ),
                            ),
                          "历史版本已恢复为草稿，可以预览后重新发布",
                        );
                      }}
                    >
                      恢复为草稿
                    </Button>
                  </div>
                ))}
                {history.data?.length === 0 && (
                  <p className="website-empty">
                    还没有发布记录。官网当前使用初始内容。
                  </p>
                )}
                <div className="website-pagination">
                  <Button
                    variant="ghost"
                    disabled={historyOffset === 0}
                    onClick={() =>
                      setHistoryOffset(Math.max(0, historyOffset - 20))
                    }
                  >
                    上一页
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={history.data?.length !== 20}
                    onClick={() => setHistoryOffset(historyOffset + 20)}
                  >
                    下一页
                  </Button>
                </div>
              </section>
            )}
          </main>
        </div>
      )}
      <input
        ref={uploadInput}
        className="sr-only"
        type="file"
        accept="image/jpeg,image/png,image/webp,video/mp4"
        aria-label="上传官网素材"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void upload(file);
        }}
      />
      <Modal
        open={!!choose}
        onClose={() => {
          if (!busy) setChoose(null);
        }}
        title="选择展示素材"
        width={650}
      >
        <p className="website-editor-hint">
          素材只用于官网展示。发布后将对所有访客公开。
        </p>
        {error && (
          <p role="alert" className="text-danger mb-4">
            {error}
          </p>
        )}
        <Button disabled={busy} onClick={() => uploadInput.current?.click()}>
          <Upload size={15} />
          上传新素材
        </Button>
        <div className="website-picker">
          {assets.data?.map((a) => (
            <button
              key={a.id}
              disabled={busy}
              onClick={() => {
                choose?.(a.id);
                setChoose(null);
              }}
            >
              <ImagePlus size={20} />
              <span>
                <strong>{a.name}</strong>
                <small>
                  {a.mime} · {(a.bytes / 1024 / 1024).toFixed(2)} MB
                </small>
              </span>
              <Plus size={15} />
            </button>
          ))}
        </div>
        {assets.isLoading && <p>正在加载素材…</p>}
        {assets.error && <p role="alert">{assets.error.message}</p>}
        {assets.data?.length === 0 && <p>暂无素材，请先上传。</p>}
        <div className="website-pagination">
          <Button
            variant="ghost"
            disabled={offset === 0 || busy}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            上一页
          </Button>
          <Button
            variant="ghost"
            disabled={assets.data?.length !== 30 || busy}
            onClick={() => setOffset(offset + 30)}
          >
            下一页
          </Button>
        </div>
      </Modal>
    </div>
  );
}
