import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight, BookOpen, Clapperboard, Download as DownloadIcon, Home, Images, Laptop, Monitor, Moon, RefreshCw, Sun } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { DESKTOP_RELEASES_URL, getDesktopRelease, type DesktopRelease } from "@/lib/desktop-releases";
import { GitHubLink } from "@/components/website/GitHubLink";
import "@/components/website/portal.css";
import "@/components/website/download.css";

const platforms = [
  { id: "windows-x64", name: "Windows", architecture: "64 位 · x64", description: "适用于 Intel 或 AMD 处理器。", label: "下载 Windows 版", icon: Monitor },
  { id: "mac-arm64", name: "Mac", architecture: "Apple Silicon", description: "适用于搭载 Apple M 系列芯片的 Mac。", label: "下载 Apple Silicon 版", icon: Laptop },
  { id: "mac-x64", name: "Mac", architecture: "Intel", description: "适用于搭载 Intel 处理器的 Mac。", label: "下载 Intel 版", icon: Laptop },
] satisfies { id: keyof DesktopRelease["assets"]; name: string; architecture: string; description: string; label: string; icon: typeof Monitor }[];

function fileSize(bytes: number) {
  return bytes > 0 ? `${(bytes / 1024 / 1024).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} MB` : null;
}

export default function Download() {
  const { me } = useAuth();
  const { theme, toggle } = useTheme();
  const website = useQuery({
    queryKey: ["public-website"],
    queryFn: () => api.get<{ content: { brand: string; footer: string } }>("/website"),
  });
  const release = useQuery({
    queryKey: ["desktop-release"],
    queryFn: ({ signal }) => getDesktopRelease(signal),
    retry: false,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
  const brand = website.data?.content.brand || "Inspiration";
  const destination = me ? "/projects" : "/login?next=%2Fprojects";
  const releaseUrl = release.data?.releaseUrl || DESKTOP_RELEASES_URL;
  const missingAssets = release.data && platforms.some(({ id }) => !release.data?.assets[id]);
  useEffect(() => {
    const previous = document.title;
    document.title = `客户端下载 · ${brand}`;
    return () => { document.title = previous; };
  }, [brand]);

  return <div className="portal-home download-page">
    <a className="download-skip" href="#download-options">跳到下载选项</a>
    <header className="portal-nav">
      <Link className="portal-brand" to="/"><Clapperboard size={27} />{brand}</Link>
      <nav aria-label="全局导航">
        <Link to="/"><Home size={17} />首页</Link>
        <Link to={destination}><Images size={17} />项目</Link>
        <Link to="/blog"><BookOpen size={17} />博客</Link>
        <Link to="/download" aria-current="page"><DownloadIcon size={17} />下载</Link>
        <GitHubLink />
      </nav>
      <div className="portal-account">
        <button aria-label={theme === "dark" ? "切换浅色" : "切换深色"} onClick={toggle}>{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button>
        <Link to={me ? "/projects" : "/login"}>{me ? "进入工作台" : "注册 / 登录"}<ArrowRight size={15} /></Link>
      </div>
    </header>
    <main className="download-main portal-width">
      <header className="download-heading">
        <h1>下载 Inspiration 桌面端</h1>
        <p>把创作工作台放在桌面。登录同一账号，继续你的项目与创作。</p>
      </header>
      <section id="download-options" aria-labelledby="download-options-title" className="download-options" tabIndex={-1}>
        <div className="download-section-heading">
          <h2 id="download-options-title">选择你的电脑</h2>
          {release.data && <a className="download-text-link" href={releaseUrl} target="_blank" rel="noopener noreferrer">v{release.data.version} · 版本说明<ArrowUpRight size={15} /></a>}
        </div>
        <div className="download-release-status" role="status" aria-live="polite">
          {release.isPending ? <p>正在获取最新版本…</p> : release.isError ? <>
            <p>暂时无法获取安装包。请重试，或前往 GitHub 下载。</p>
            <button className="download-retry" onClick={() => void release.refetch()} disabled={release.isFetching}><RefreshCw size={14} />{release.isFetching ? "正在重试…" : "重试"}</button>
          </> : !release.data ? <p>暂未发布正式安装包。</p> : missingAssets ? <p>部分安装包暂未提供，可前往 GitHub 查看发布详情。</p> : <p>安装包来自 Inspiration 官方 GitHub 发布页。</p>}
          {!release.isPending && (!release.data || missingAssets) && <a className="download-text-link" href={releaseUrl} target="_blank" rel="noopener noreferrer">在 GitHub 查看安装包<ArrowUpRight size={15} /></a>}
        </div>
        <div className="download-platforms">
          {platforms.map(({ id, name, architecture, description, label, icon: Icon }) => {
            const asset = release.data?.assets[id];
            return <article className="download-platform" key={id} aria-labelledby={`${id}-title`}>
              <Icon className="download-platform-icon" size={30} strokeWidth={1.4} aria-hidden="true" />
              <h3 id={`${id}-title`}>{name}<span>{architecture}</span></h3>
              <p>{description}</p>
              <div className="download-platform-action">
                {asset ? <a className="download-button" href={asset.url} target="_blank" rel="noopener noreferrer"><DownloadIcon size={17} />{label}</a> : <button className="download-button" disabled>{release.isPending ? "获取安装包中…" : !release.data && !release.isError ? "尚未发布" : "暂不可下载"}</button>}
                <span className="download-file-meta">{asset ? [id === "windows-x64" ? "EXE 安装包" : "DMG 安装包", fileSize(asset.size)].filter(Boolean).join(" · ") : release.isPending ? "请稍候" : "可通过 GitHub 查看发布情况"}</span>
              </div>
            </article>;
          })}
        </div>
        <div className="download-fallback">
          <p>下载没有开始，或想查看历史版本？</p>
          <a className="download-text-link" href={DESKTOP_RELEASES_URL} target="_blank" rel="noopener noreferrer">前往 GitHub 下载<ArrowUpRight size={16} /></a>
        </div>
      </section>
      <section className="download-help" aria-labelledby="download-help-title">
        <h2 id="download-help-title">开始之前</h2>
        <div>
          <article><h3>Mac 应该选哪个版本？</h3><p>打开 Apple 菜单中的「关于本机」。显示 Apple M 系列芯片，选择 Apple Silicon；显示 Intel 处理器，选择 Intel。</p></article>
          <article><h3>也可以继续使用网页版</h3><p>桌面端需要联网使用。手机或暂时不方便安装时，直接在浏览器中进入创作工作台。</p><Link className="download-text-link" to={destination}>打开网页版<ArrowRight size={15} /></Link></article>
          <article className="download-install-note"><h3>安装时的系统提示</h3><p>当前 Windows 安装包未做代码签名，Mac 安装包未做公证，安装时系统可能显示安全提示。请确认安装包来自本页链接的官方 GitHub 发布页。</p></article>
        </div>
      </section>
    </main>
    <footer className="portal-footer portal-width">
      <Link className="portal-brand" to="/"><Clapperboard size={22} />{brand}</Link>
      <p>{website.data?.content.footer || "为认真创作的人，留一个让灵感生长的地方。"}</p>
      <Link to="/blog">博客</Link><Link to="/download">客户端下载</Link><GitHubLink />
      <small>© {new Date().getFullYear()} {brand}</small>
    </footer>
  </div>;
}
