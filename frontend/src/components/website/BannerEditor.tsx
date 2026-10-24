import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import type { SiteBanner, SiteContent } from "./types";

export function BannerEditor({ content, edit, busy, mediaField }: {
  content: SiteContent; edit: (next: SiteContent) => void; busy: boolean;
  mediaField: (id: string | null, change: (id: string | null) => void, label: string) => ReactNode;
}) {
  const banners = content.banners ?? [];
  function update(id: string, change: Partial<SiteBanner>) {
    edit({ ...content, banners: banners.map(b => b.id === id ? { ...b, ...change } : b) });
  }
  return <section className="website-editor-card">
    <h2>首页活动轮播</h2>
    <p className="website-editor-hint">最多 12 张，建议上传 16:9 横图。图片原样保存；不填跳转链接时只展示。保存草稿后可预览，发布后才对访客生效。</p>
    <label className="website-field"><span><input type="checkbox" checked={content.banner_autoplay ?? true} disabled={busy}
      onChange={e => edit({ ...content, banner_autoplay: e.target.checked })} /> 自动轮播</span></label>
    <label className="website-field">切换间隔（秒）<input type="number" min={3} max={30} value={content.banner_interval ?? 6} disabled={busy}
      onChange={e => edit({ ...content, banner_interval: Math.min(30, Math.max(3, Number(e.target.value) || 6)) })} /></label>
    {banners.map((b, i) => <div key={b.id} className="website-feature-editor">
      <div className="website-order-row"><strong>轮播图 {i + 1}</strong>
        <label><input type="checkbox" checked={b.enabled} disabled={busy} onChange={e => update(b.id, { enabled: e.target.checked })} /> 展示</label>
        {[-1, 1].map(d => <button key={d} disabled={busy || i + d < 0 || i + d >= banners.length} aria-label={`${d < 0 ? "上移" : "下移"}轮播图${i + 1}`} onClick={() => {
          const next = [...banners]; [next[i], next[i + d]] = [next[i + d], next[i]]; edit({ ...content, banners: next });
        }}>{d < 0 ? <ArrowUp size={16} /> : <ArrowDown size={16} />}</button>)}
        <button disabled={busy} aria-label={`删除轮播图${i + 1}`} onClick={() => edit({ ...content, banners: banners.filter(x => x.id !== b.id) })}><Trash2 size={16} /></button>
      </div>
      <label className="website-field">标题<input maxLength={100} value={b.title} disabled={busy} onChange={e => update(b.id, { title: e.target.value })} /></label>
      <label className="website-field">说明<input maxLength={200} value={b.description} disabled={busy} onChange={e => update(b.id, { description: e.target.value })} /></label>
      <label className="website-field">跳转链接<input maxLength={1000} placeholder="/blog 或 https://…" value={b.href} disabled={busy} onChange={e => update(b.id, { href: e.target.value })} /></label>
      {mediaField(b.media_id, id => update(b.id, { media_id: id }), `轮播图${i + 1}图片 / 视频`)}
    </div>)}
    {!banners.length && <p className="website-editor-hint">暂无轮播图，首页将隐藏此区域。</p>}
    <button className="website-order-row" disabled={busy || banners.length >= 12} onClick={() => edit({ ...content, banners: [...banners, {
      id: crypto.randomUUID(), title: "新的创作活动", description: "", media_id: null, href: "", enabled: true,
    }] })}><Plus size={17} /> 添加轮播图（{banners.length}/12）</button>
  </section>;
}
