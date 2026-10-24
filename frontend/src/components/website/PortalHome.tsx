import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, BookOpen, Box, Clapperboard, FileText, Home, Images, Moon, Pause, Play, Plus, Sun, Users } from "lucide-react";
import { api } from "@/lib/api";
import { useTheme } from "@/lib/theme";
import { SiteMedia } from "./SiteMedia";
import { GitHubLink } from "./GitHubLink";
import type { SiteBanner, SiteContent } from "./types";
import type { BlogList } from "./BlogShared";
import "./portal.css";

export const defaultBanners: SiteBanner[] = [
  { id: "canvas", title: "让想法，在画布上相遇", description: "整理故事、角色与镜头，开启你的创作。", media_id: null, href: "/projects", enabled: true },
  { id: "journal", title: "每一个故事，都有新的开始", description: "创作指南、产品更新与灵感笔记。", media_id: null, href: "/blog", enabled: true },
  { id: "studio", title: "从第一幕，到最后一个镜头", description: "在同一个工作台里，让故事逐渐成形。", media_id: null, href: "/projects", enabled: true },
  { id: "ideas", title: "把灵感，留给下一部作品", description: "从素材到故事，发现新的创作可能。", media_id: null, href: "/projects", enabled: true },
];

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

function safeHref(value: string) {
  if (/[\u0000-\u001f\\]/.test(value)) return "";
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password ? value : ""; } catch { return ""; }
}

export function PortalHome({ content: c, mediaTypes, preview = false, authenticated = false }: {
  content: SiteContent; mediaTypes: Record<string, string>; preview?: boolean; authenticated?: boolean;
}) {
  const { theme, toggle } = useTheme();
  const reduced = useReducedMotion();
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [visible, setVisible] = useState(!document.hidden);
  const [active, setActive] = useState(0);
  const [pages, setPages] = useState(1);
  const track = useRef<HTMLDivElement>(null);
  const banners = (c.banners ?? defaultBanners).filter(b => b.enabled);
  const bannerKey = banners.map(b => b.id).join("|");
  const articles = useQuery({ queryKey: ["blog-home"], queryFn: () => api.get<BlogList>("/blog?limit=3") });
  const destination = (route = "workbench") => {
    const next = `/projects?launch=${encodeURIComponent(route)}`;
    return authenticated ? next : `/login?next=${encodeURIComponent(next)}`;
  };
  function go(index: number) {
    const rail = track.current;
    if (!rail || !banners.length) return;
    const next = (index + pages) % pages;
    const first = rail.children[0] as HTMLElement;
    const second = rail.children[1] as HTMLElement | undefined;
    const step = second ? second.offsetLeft - first.offsetLeft : first.offsetWidth;
    rail.scrollTo({ left: Math.min(next * step, rail.scrollWidth - rail.clientWidth), behavior: reduced ? "auto" : "smooth" });
  }
  useEffect(() => {
    setActive(0); track.current?.scrollTo({ left: 0 });
  }, [bannerKey]);
  useEffect(() => {
    const rail = track.current;
    if (!rail) return;
    const update = () => {
      const first = rail.children[0] as HTMLElement | undefined;
      const second = rail.children[1] as HTMLElement | undefined;
      const step = first && second ? second.offsetLeft - first.offsetLeft : rail.clientWidth;
      setPages(Math.max(1, Math.ceil((rail.scrollWidth - rail.clientWidth - 2) / step) + 1));
      setActive(0); rail.scrollTo({ left: 0 });
    };
    const observer = new ResizeObserver(update); observer.observe(rail); update();
    return () => observer.disconnect();
  }, [bannerKey]);
  useEffect(() => {
    const update = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const rotating = (c.banner_autoplay ?? true) && !paused && !hovered && !focused && !reduced && visible && !preview;
  useEffect(() => {
    if (!rotating || pages < 2) return;
    const timer = window.setInterval(() => {
      const rail = track.current;
      if (!rail) return;
      go(rail.scrollLeft + rail.clientWidth >= rail.scrollWidth - 4 ? 0 : active + 1);
    }, Math.max(3, c.banner_interval ?? 6) * 1000);
    return () => window.clearInterval(timer);
  }, [rotating, active, bannerKey, c.banner_interval, pages]);
  const media = (id: string, alt: string) => <SiteMedia id={id} mime={mediaTypes[id]} preview={preview} alt={alt} />;
  return <div className="portal-home" onClickCapture={preview ? e => {
    if ((e.target as HTMLElement).closest("a")) e.preventDefault();
  } : undefined}>
    <header className="portal-nav">
      <Link className="portal-brand" to="/"><Clapperboard size={27} />{c.brand}</Link>
      <nav aria-label="全局导航"><Link to="/" aria-current="page"><Home size={17} />首页</Link><Link to={destination()}><Images size={17} />项目</Link><Link to="/blog"><BookOpen size={17} />博客</Link><GitHubLink /></nav>
      <div className="portal-account"><button aria-label={theme === "dark" ? "切换浅色" : "切换深色"} onClick={toggle}>{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button><Link to={authenticated ? "/projects" : "/login"}>{authenticated ? "进入工作台" : "注册 / 登录"}<ArrowRight size={15} /></Link></div>
    </header>
    <main>
      <section className="portal-hero">
        <div className="portal-backdrop" aria-hidden="true"><SiteMedia id={c.hero_media_id ?? undefined} localSource={c.hero_media_id ? undefined : "/media/home-reference-updream.mp4"} poster={c.hero_media_id ? undefined : "/media/home-reference-updream.jpg"} mime={c.hero_media_id ? mediaTypes[c.hero_media_id] : "video/mp4"} preview={preview} alt="首页主视觉" background playing={!reduced && visible} /></div>
        <div className="portal-hero-copy">{c.announcement && <p className="portal-announcement">{c.announcement}</p>}<h1>{c.title}</h1><p>{c.subtitle}</p></div>
        <div className="portal-launch" aria-label="快速创作">
          <Link className="portal-launch-main" to={destination("canvas")}><span className="portal-canvas-art" aria-hidden="true"><i /><i /><Plus size={28} /></span><strong>新建画布</strong><span>让想法自由展开 <ArrowRight size={15} /></span></Link>
          {[{ name: "3D 预演台", route: "director", icon: Box }, { name: "创作剧本", route: "scripts", icon: FileText }, { name: "设计角色", route: "bible", icon: Users }, { name: "拆分分镜", route: "storyboard", icon: Clapperboard }].map(({ name, route, icon: Icon }) => <Link className="portal-launch-small" key={route} to={destination(route)}><span>{name}</span><Icon size={34} strokeWidth={1.1} /><ArrowRight className="portal-launch-arrow" size={16} /></Link>)}
        </div>
      </section>
      {!!banners.length && <section className="portal-promos portal-width" role="region" aria-roledescription="轮播图" aria-label="活动推荐"
        onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocusCapture={() => setFocused(true)} onBlurCapture={e => { if (!e.currentTarget.contains(e.relatedTarget)) setFocused(false); }}>
        <div className="portal-banner-track" ref={track} onScroll={() => {
          const rail = track.current; if (!rail) return;
          const children = Array.from(rail.children) as HTMLElement[];
          let closest = 0;
          children.forEach((el, i) => { if (Math.abs(el.offsetLeft - rail.offsetLeft - rail.scrollLeft) < Math.abs(children[closest].offsetLeft - rail.offsetLeft - rail.scrollLeft)) closest = i; });
          setActive(rail.scrollLeft + rail.clientWidth >= rail.scrollWidth - 4 ? pages - 1 : Math.min(closest, pages - 1));
        }}>
          {banners.map((b, i) => {
            const href = safeHref(b.href);
            const body = <><div className={`portal-banner-art portal-scene-${i % 3}`}>{b.media_id && media(b.media_id, b.title)}</div><div className="portal-banner-copy"><small>{c.brand}</small><h2>{b.title}</h2><p>{b.description}</p>{href && <ArrowRight size={20} />}</div></>;
            return <article key={b.id} className="portal-banner" aria-label={`${i + 1} / ${banners.length} ${b.title}`}>
              {href && !mediaTypes[b.media_id ?? ""]?.startsWith("video/") ? <a href={href} rel="noopener noreferrer">{body}</a> : <>{body}{href && <a className="portal-video-link" href={href}>了解更多 <ArrowRight size={14} /></a>}</>}
            </article>;
          })}
        </div>
        {pages > 1 && <div className="portal-carousel-controls"><button aria-label="上一组活动" onClick={() => go(active - 1)}><ArrowLeft size={16} /></button><div className="portal-dots">{banners.slice(0, pages).map((b, i) => <button key={b.id} aria-label={`查看第${i + 1}组活动`} aria-pressed={active === i} onClick={() => go(i)} />)}</div><button aria-label="下一组活动" onClick={() => go(active + 1)}><ArrowRight size={16} /></button>{!reduced && (c.banner_autoplay ?? true) && <button aria-label={paused ? "播放轮播" : "暂停轮播"} onClick={() => setPaused(v => !v)}>{paused ? <Play size={15} /> : <Pause size={15} />}</button>}</div>}
      </section>}
      {c.sections.some(s => s.key === "features" && s.enabled) && <section className="portal-capabilities portal-width">
        <div className="portal-section-heading"><h2>{c.features_title}</h2><Link to={destination()}>进入创作 <ArrowRight size={16} /></Link></div>
        <div className="portal-feature-grid">{c.features.map((f, i) => {
          const FeatureIcon = [FileText, Plus, Images, Clapperboard][i];
          const href = destination(({ story: "scripts", canvas: "canvas", library: "assets", edit: "cuts" })[f.key]);
          return <article key={f.key}>
            {f.media_id ? <div className="portal-feature-cover">{media(f.media_id, f.title)}</div> : <Link to={href} className="portal-feature-cover portal-feature-symbol"><FeatureIcon size={44} strokeWidth={1} /><span>{["剧本与故事", "自由画布", "角色与素材", "剪辑成片"][i]}</span></Link>}
            <Link to={href}><h3>{f.title} <ArrowRight size={15} /></h3></Link><p>{f.description}</p>
          </article>;
        })}</div>
      </section>}
      <section className="portal-journal portal-width"><div className="portal-section-heading"><h2>创作手记</h2><Link to="/blog">全部博客 <ArrowRight size={16} /></Link></div>
        {articles.data?.items.length ? <div className="portal-blog-grid">{articles.data.items.map(a => <Link key={a.slug} to={`/blog/${a.slug}`}><span>{a.category}</span><h3>{a.title}</h3><p>{a.excerpt}</p><span>阅读全文 <ArrowRight size={15} /></span></Link>)}</div> : <p className="portal-journal-empty">{articles.error ? "文章暂时未能加载。" : "创作指南、产品更新与灵感笔记。"}<Link to="/blog">前往博客 <ArrowRight size={15} /></Link></p>}
      </section>
    </main>
    <footer className="portal-footer portal-width"><Link className="portal-brand" to="/"><Clapperboard size={22} />{c.brand}</Link><p>{c.footer}</p><Link to="/blog">博客</Link><GitHubLink /><small>© {new Date().getFullYear()} {c.brand}</small></footer>
  </div>;
}
