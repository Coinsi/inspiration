import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Eye,
  ImagePlus,
  Plus,
  Save,
  Upload,
} from "lucide-react";
import { api, apiUpload } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { ArticleBody } from "@/components/website/ArticleBody";
import { SiteMedia } from "@/components/website/SiteMedia";
import {
  articleDate,
  categories,
  type ArticleContent,
} from "@/components/website/BlogShared";
import type { SiteAsset } from "@/components/website/types";
import "./website-admin.css";
import "./blog-admin.css";

interface Post {
  id: string;
  slug: string;
  version: number;
  draft: ArticleContent;
  published: ArticleContent | null;
  published_at: string | null;
  archived: boolean;
}
interface Row {
  id: string;
  slug: string;
  title: string;
  category: string;
  version: number;
  live: boolean;
  has_changes: boolean;
  updated_at: string;
  archived: boolean;
}
const blank: ArticleContent = {
  title: "",
  excerpt: "",
  body: "",
  category: "创作指南",
  author: "Inspiration 编辑部",
  cover_media_id: null,
};

export default function BlogAdmin() {
  const { me, loading } = useAuth(),
    { postId } = useParams();
  if (loading) return <div className="website-denied">正在检查管理权限…</div>;
  if (!me?.user.is_platform_admin)
    return (
      <div className="website-denied">
        <h1>博客由平台管理员维护</h1>
        <Link to="/projects">返回工作台</Link>
      </div>
    );
  return postId ? <Editor key={postId} id={postId} /> : <PostList />;
}

function AdminHeader({ children }: { children?: React.ReactNode }) {
  return (
    <header className="website-admin-header">
      <div>
        <BookOpen size={20} />
        <strong>
          Inspiration <span>博客管理</span>
        </strong>
      </div>
      <nav>
        {children || (
          <>
            <Link to="/admin/website">官网管理</Link>
            <Link to="/blog">
              查看博客 <ArrowUpRight size={14} />
            </Link>
          </>
        )}
      </nav>
    </header>
  );
}

function PostList() {
  const [archived, setArchived] = useState(false),
    [offset, setOffset] = useState(0),
    [error, setError] = useState("");
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["admin-blog", archived, offset],
    queryFn: () =>
      api.get<{ items: Row[]; total: number }>(
        `/admin/blog?archived=${archived}&offset=${offset}`,
      ),
  });
  async function restore(row: Row) {
    try {
      await api.post(`/admin/blog/${row.id}/restore`, { version: row.version });
      await qc.invalidateQueries({ queryKey: ["admin-blog"] });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="website-admin">
      <AdminHeader />
      <main className="blog-admin-list">
        <header className="blog-admin-heading">
          <div>
            <p className="site-eyebrow">JOURNAL / EDITORIAL DESK</p>
            <h1>把值得分享的，写下来。</h1>
            <p>教程、产品进展和创作中的思考，在这里成为下一篇文章。</p>
          </div>
          <Link to="/admin/blog/new" className="site-button">
            <Plus size={16} />
            写新文章
          </Link>
        </header>
        <div className="blog-admin-filters">
          <button
            aria-pressed={!archived}
            onClick={() => {
              setArchived(false);
              setOffset(0);
            }}
          >
            文章
          </button>
          <button
            aria-pressed={archived}
            onClick={() => {
              setArchived(true);
              setOffset(0);
            }}
          >
            已归档
          </button>
          <span>{list.data?.total || 0} 篇</span>
        </div>
        {error && <p role="alert">{error}</p>}
        {list.isLoading ? (
          <p className="website-empty">正在加载文章…</p>
        ) : list.error ? (
          <div role="alert" className="website-empty">
            <p>{list.error.message}</p>
            <button onClick={() => void list.refetch()}>重试</button>
          </div>
        ) : !list.data?.items.length ? (
          <div className="blog-admin-empty">
            <BookOpen size={35} strokeWidth={1} />
            <h2>{archived ? "没有归档文章" : "从第一篇创作手记开始"}</h2>
            <p>保存为草稿不会出现在官网，确认后再发布。</p>
            {!archived && <Link to="/admin/blog/new">写一篇文章 →</Link>}
          </div>
        ) : (
          list.data.items.map((row) => (
            <article className="blog-admin-row" key={row.id}>
              <div className="blog-admin-row-icon">
                <BookOpen size={21} strokeWidth={1.2} />
              </div>
              <div>
                <span>
                  {row.category} · 更新于 {articleDate(row.updated_at)}
                </span>
                <Link to={`/admin/blog/${row.id}`}>
                  <h2>{row.title}</h2>
                </Link>
                <small>/blog/{row.slug}</small>
              </div>
              <span className={`blog-status ${row.live ? "live" : ""}`}>
                {row.archived
                  ? "已归档"
                  : row.live
                    ? row.has_changes
                      ? "已发布 · 草稿有修改"
                      : "已发布"
                    : "草稿"}
              </span>
              {row.archived ? (
                <Button variant="outline" onClick={() => void restore(row)}>
                  恢复为草稿
                </Button>
              ) : (
                <Link className="blog-edit-link" to={`/admin/blog/${row.id}`}>
                  编辑文章 →
                </Link>
              )}
            </article>
          ))
        )}
        <div className="website-pagination">
          <Button
            variant="ghost"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            上一页
          </Button>
          <Button
            variant="ghost"
            disabled={!list.data || offset + 20 >= list.data.total}
            onClick={() => setOffset(offset + 20)}
          >
            下一页
          </Button>
        </div>
      </main>
    </div>
  );
}

function Editor({ id }: { id: string }) {
  const navigate = useNavigate(),
    qc = useQueryClient(),
    confirm = useConfirm();
  const [post, setPost] = useState<Post | null>(null),
    [content, setContent] = useState<ArticleContent>(blank),
    [slug, setSlug] = useState("");
  const [preview, setPreview] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [picker, setPicker] = useState(false),
    [mediaOffset, setMediaOffset] = useState(0);
  const bodyRef = useRef<HTMLTextAreaElement>(null),
    fileRef = useRef<HTMLInputElement>(null);
  const query = useQuery({
    queryKey: ["admin-blog-post", id],
    queryFn: () => api.get<Post>(`/admin/blog/${id}`),
    enabled: id !== "new",
    refetchOnWindowFocus: false,
  });
  const media = useQuery({
    queryKey: ["blog-cover-media", mediaOffset],
    queryFn: () =>
      api.get<SiteAsset[]>(`/admin/website/media?offset=${mediaOffset}`),
    enabled: picker,
  });
  const dirty =
    JSON.stringify(content) !== JSON.stringify(post?.draft || blank) ||
    (!post && !!slug);
  const readonly = busy || !!post?.archived;
  function adopt(next: Post) {
    setPost(next);
    setContent(next.draft);
    setSlug(next.slug);
    qc.setQueryData(["admin-blog-post", next.id], next);
  }
  useEffect(() => {
    if (query.data && !post) adopt(query.data);
  }, [query.data]);
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  async function run(fn: () => Promise<void>, message: string) {
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
  async function leave(e: React.MouseEvent<HTMLAnchorElement>) {
    if (!dirty) return;
    e.preventDefault();
    const target = e.currentTarget.getAttribute("href") || "/admin/blog";
    if (
      await confirm({
        title: "离开文章编辑？",
        message: "未保存的修改会丢失，可以先保存草稿。",
        confirmText: "放弃修改并离开",
      })
    )
      navigate(target);
  }
  async function save() {
    await run(async () => {
      const next = post
        ? await api.put<Post>(`/admin/blog/${post.id}`, {
            version: post.version,
            content,
          })
        : await api.post<Post>("/admin/blog", {
            slug: slug.trim() || `note-${crypto.randomUUID().slice(0, 8)}`,
            content,
          });
      adopt(next);
      await qc.invalidateQueries({ queryKey: ["admin-blog"] });
      if (!post) navigate(`/admin/blog/${next.id}`, { replace: true });
    }, "草稿已保存，公开文章未改变");
  }
  async function change(action: string) {
    if (!post) return;
    const messages: Record<string, [string, string]> = {
      publish: ["发布这篇文章？", "已保存的正文和封面将公开展示在博客和官网。"],
      unpublish: ["将文章下线？", "访客将无法再读取文章，草稿会保留。"],
      archive: [
        "归档这篇文章？",
        "文章会从前台下线，之后可以从已归档列表恢复。",
      ],
      restore: ["恢复为草稿？", "恢复不会自动发布文章。"],
    };
    const [title, message] = messages[action];
    if (
      !(await confirm({
        title,
        message,
        confirmText: action === "publish" ? "确认发布" : "确认",
      }))
    )
      return;
    await run(
      async () => {
        adopt(
          await api.post<Post>(`/admin/blog/${post.id}/${action}`, {
            version: post.version,
          }),
        );
        await Promise.all([
          qc.invalidateQueries({ queryKey: ["admin-blog"] }),
          qc.invalidateQueries({ queryKey: ["blog"] }),
          qc.invalidateQueries({ queryKey: ["blog-home"] }),
          qc.invalidateQueries({ queryKey: ["blog-article"] }),
        ]);
      },
      action === "publish" ? "文章已发布，可在博客查看" : "文章状态已更新",
    );
  }
  async function reload() {
    if (
      dirty &&
      !(await confirm({
        title: "重新载入草稿？",
        message: "这会覆盖本窗口未保存的修改。",
        confirmText: "重新载入",
      }))
    )
      return;
    await run(
      async () => adopt(await api.get<Post>(`/admin/blog/${id}`)),
      "已载入最新草稿",
    );
  }
  function insert(before: string, after = "") {
    const area = bodyRef.current;
    if (!area) return;
    const start = area.selectionStart,
      end = area.selectionEnd;
    const chosen = content.body.slice(start, end) || "文字";
    setContent({
      ...content,
      body:
        content.body.slice(0, start) +
        before +
        chosen +
        after +
        content.body.slice(end),
    });
    requestAnimationFrame(() => {
      area.focus();
      area.setSelectionRange(
        start + before.length,
        start + before.length + chosen.length,
      );
    });
  }
  async function upload(file: File) {
    if (file.size > 80 * 1024 * 1024) {
      setError("封面图片不能超过80 MB");
      return;
    }
    await run(async () => {
      const form = new FormData();
      form.append("file", file);
      const asset = await apiUpload<SiteAsset>("/admin/website/media", form);
      if (!asset.mime.startsWith("image/"))
        throw Error("请选择静态图片作为封面");
      setContent({ ...content, cover_media_id: asset.id });
      setPicker(false);
      void qc.invalidateQueries({ queryKey: ["blog-cover-media"] });
    }, "封面已选择，保存并发布文章后才会公开");
  }
  if (id !== "new" && !post)
    return (
      <div className="website-admin">
        <AdminHeader />
        <div className="website-denied">
          <p>{query.error ? query.error.message : "正在载入文章…"}</p>
          {query.error && (
            <button onClick={() => void query.refetch()}>重试</button>
          )}
          <Link to="/admin/blog">返回文章列表</Link>
        </div>
      </div>
    );
  return (
    <div className="website-admin">
      <AdminHeader>
        <Link to="/admin/blog" onClick={(e) => void leave(e)}>
          <ArrowLeft size={14} />
          文章列表
        </Link>
        {post?.published && (
          <Link to={`/blog/${post.slug}`} onClick={(e) => void leave(e)}>
            查看线上文章 <ArrowUpRight size={14} />
          </Link>
        )}
      </AdminHeader>
      <div className="website-admin-bar">
        <div>
          <span className="website-state">
            {dirty
              ? "有未保存的修改"
              : post?.published
                ? "线上版本已保留"
                : "草稿"}
          </span>
          <small>内容保存后，需单独发布</small>
        </div>
        <div>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setPreview(!preview)}
          >
            <Eye size={15} />
            {preview ? "返回编辑" : "预览文章"}
          </Button>
          <Button
            variant="outline"
            disabled={readonly || !content.title.trim() || (!dirty && !!post)}
            onClick={() => void save()}
          >
            <Save size={15} />
            保存草稿
          </Button>
          <Button
            disabled={readonly || !post || dirty}
            onClick={() => void change("publish")}
          >
            发布文章
          </Button>
        </div>
      </div>
      {error && (
        <div className="website-feedback error" role="alert">
          {error}
          {post && (
            <button disabled={busy} onClick={() => void reload()}>
              重新载入草稿
            </button>
          )}
        </div>
      )}
      {notice && (
        <p className="website-feedback" role="status">
          {notice}
        </p>
      )}
      {preview ? (
        <div className="blog-admin-preview">
          <div className="journal-preview site-root">
            <ArticleBody article={content} preview />
          </div>
        </div>
      ) : (
        <main className="blog-editor-layout">
          <section className="blog-editor-paper">
            <div className="blog-editor-label">JOURNAL / DRAFT</div>
            <label className="sr-only" htmlFor="article-title">
              文章标题
            </label>
            <textarea
              id="article-title"
              className="blog-title-input"
              rows={2}
              placeholder="为这篇文章写一个标题…"
              maxLength={120}
              value={content.title}
              disabled={readonly}
              onChange={(e) =>
                setContent({ ...content, title: e.target.value })
              }
            />
            <label className="website-field">
              文章摘要
              <textarea
                aria-label="文章摘要"
                rows={3}
                value={content.excerpt}
                maxLength={300}
                placeholder="用两三句话，告诉读者为什么值得读下去。"
                disabled={readonly}
                onChange={(e) =>
                  setContent({ ...content, excerpt: e.target.value })
                }
              />
            </label>
            <div className="blog-format-toolbar" aria-label="正文格式">
              <button disabled={readonly} onClick={() => insert("\n## ", "\n")}>
                标题
              </button>
              <button disabled={readonly} onClick={() => insert("**", "**")}>
                加粗
              </button>
              <button disabled={readonly} onClick={() => insert("\n> ", "\n")}>
                引用
              </button>
              <button disabled={readonly} onClick={() => insert("\n- ", "\n")}>
                列表
              </button>
              <button
                disabled={readonly}
                onClick={() => insert("[", "](https://example.com)")}
              >
                链接
              </button>
              <span>{content.body.length.toLocaleString()} 字符</span>
            </div>
            <label className="sr-only" htmlFor="article-body">
              文章正文
            </label>
            <textarea
              ref={bodyRef}
              id="article-body"
              className="blog-body-input"
              value={content.body}
              maxLength={100000}
              disabled={readonly}
              onChange={(e) => setContent({ ...content, body: e.target.value })}
              placeholder={"## 从一个想法开始\n\n写下你想与创作者分享的内容…"}
            />
            <p className="blog-editor-tip">
              支持 Markdown
              标题、列表、引用、链接和表格。图片请通过右侧封面上传，正文不加载外链图片。
            </p>
          </section>
          <aside className="blog-editor-settings">
            <section>
              <h2>文章设置</h2>
              <label className="website-field">
                文章分类
                <select
                  value={content.category}
                  disabled={readonly}
                  onChange={(e) =>
                    setContent({ ...content, category: e.target.value })
                  }
                >
                  {categories.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
              <label className="website-field">
                作者署名
                <input
                  value={content.author}
                  maxLength={80}
                  disabled={readonly}
                  onChange={(e) =>
                    setContent({ ...content, author: e.target.value })
                  }
                />
              </label>
              <label className="website-field">
                文章地址
                <input
                  aria-label="文章地址"
                  value={slug}
                  maxLength={120}
                  disabled={readonly || !!post}
                  placeholder="留空则自动生成"
                  pattern="[a-z0-9]+(-[a-z0-9]+)*"
                  onChange={(e) => setSlug(e.target.value)}
                />
              </label>
              <p className="blog-editor-tip">
                使用小写英文、数字和短横线。首次保存后固定，避免已有链接失效。
              </p>
            </section>
            <section>
              <h2>文章封面</h2>
              {content.cover_media_id ? (
                <div className="blog-selected-cover">
                  <ArticleCover id={content.cover_media_id} />
                  <button
                    disabled={readonly}
                    onClick={() =>
                      setContent({ ...content, cover_media_id: null })
                    }
                  >
                    移除封面
                  </button>
                </div>
              ) : (
                <div className="blog-cover-placeholder">
                  <ImagePlus size={26} strokeWidth={1} />
                  <span>
                    没有封面也可以发布，
                    <br />
                    我们会使用统一的手记插画。
                  </span>
                </div>
              )}
              <Button
                variant="outline"
                disabled={readonly}
                onClick={() => {
                  setError("");
                  setPicker(true);
                }}
              >
                <ImagePlus size={15} />
                选择或上传封面
              </Button>
            </section>
            {post && (
              <section>
                <h2>发布状态</h2>
                <p className="blog-editor-tip">
                  {post.archived
                    ? "已归档，访客不可见。"
                    : post.published
                      ? `已发布于 ${articleDate(post.published_at)}`
                      : "尚未发布，只有平台管理员可见。"}
                </p>
                {post.archived ? (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void change("restore")}
                  >
                    恢复为草稿
                  </Button>
                ) : (
                  <div className="blog-state-actions">
                    {post.published && (
                      <button
                        disabled={busy || dirty}
                        onClick={() => void change("unpublish")}
                      >
                        下线文章
                      </button>
                    )}
                    <button
                      disabled={busy || dirty}
                      onClick={() => void change("archive")}
                    >
                      归档文章
                    </button>
                  </div>
                )}
              </section>
            )}
          </aside>
        </main>
      )}
      <Modal
        open={picker}
        onClose={() => {
          if (!busy) setPicker(false);
        }}
        title="选择文章封面"
        width={660}
      >
        <p className="website-editor-hint">
          选择官网素材库中的图片，或上传新的封面。发布前不会公开。
        </p>
        {error && <p role="alert">{error}</p>}
        <Button disabled={busy} onClick={() => fileRef.current?.click()}>
          <Upload size={15} />
          上传图片
        </Button>
        <input
          ref={fileRef}
          type="file"
          className="sr-only"
          accept="image/jpeg,image/png,image/webp"
          aria-label="上传文章封面"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) void upload(file);
          }}
        />
        <div className="website-picker">
          {media.data
            ?.filter((a) => a.mime.startsWith("image/"))
            .map((a) => (
              <button
                disabled={busy}
                key={a.id}
                onClick={() => {
                  setContent({ ...content, cover_media_id: a.id });
                  setPicker(false);
                }}
              >
                <ImagePlus size={20} />
                <span>
                  <strong>{a.name}</strong>
                  <small>{(a.bytes / 1024 / 1024).toFixed(2)} MB</small>
                </span>
              </button>
            ))}
        </div>
        {media.isLoading && <p>正在加载…</p>}
        {media.error && <p role="alert">{media.error.message}</p>}
        {media.data && !media.data.some((a) => a.mime.startsWith("image/")) && (
          <p className="website-empty">这一页没有图片，可上传或翻页查找。</p>
        )}
        <div className="website-pagination">
          <Button
            variant="ghost"
            disabled={!mediaOffset || busy}
            onClick={() => setMediaOffset(Math.max(0, mediaOffset - 30))}
          >
            上一页
          </Button>
          <Button
            variant="ghost"
            disabled={media.data?.length !== 30 || busy}
            onClick={() => setMediaOffset(mediaOffset + 30)}
          >
            下一页
          </Button>
        </div>
      </Modal>
    </div>
  );
}

function ArticleCover({ id }: { id: string }) {
  return <SiteMedia id={id} mime="image/jpeg" alt="文章封面预览" preview />;
}
