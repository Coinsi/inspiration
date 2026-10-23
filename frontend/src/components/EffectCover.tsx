import { useRef, useState } from "react";
import { ImagePlus, ImageOff, Upload, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import "./effect-cover.css";

export function EffectCover({ src, name, detail = false }: { src?: string; name: string; detail?: boolean }) {
  const [failed, setFailed] = useState<string | null>(null);
  const safe = !!src && (/^https?:\/\//i.test(src) || src.startsWith("data:image/jpeg;base64,"));
  return <div className={`effect-cover ${detail ? "effect-cover-detail" : ""}`}>
    {safe && failed !== src ? <img src={src} alt={`${name}的效果图`} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(src!)} /> :
      <div className="effect-cover-empty">{src ? <ImageOff size={28} /> : <ImagePlus size={28} />}<span>{src ? "效果图暂时无法显示" : "尚未添加效果图"}</span></div>}
  </div>;
}

export function EffectCoverEditor({ value, onChange, onBusyChange }: { value: string; onChange: (value: string) => void; onBusyChange?: (busy: boolean) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [processing, setProcessing] = useState(false);
  async function select(file?: File) {
    if (!file) return;
    setError("");
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
      setError("请选择 10 MB 以内的 JPG、PNG 或 WebP 图片。"); return;
    }
    setProcessing(true); onBusyChange?.(true);
    const url = URL.createObjectURL(file);
    try {
      const image = new Image(); image.src = url; await image.decode();
      const scale = Math.min(1, 1200 / Math.max(image.width, image.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.width * scale)); canvas.height = Math.max(1, Math.round(image.height * scale));
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      let data = canvas.toDataURL("image/jpeg", 0.82);
      if (data.length > 700000) data = canvas.toDataURL("image/jpeg", 0.55);
      if (data.length > 700000) throw new Error("图片细节过多，请换用尺寸较小的图片。");
      onChange(data);
    } catch (e) { setError(e instanceof Error ? e.message : "图片读取失败，请重新选择。"); }
    finally { URL.revokeObjectURL(url); setProcessing(false); onBusyChange?.(false); }
  }
  return <div className="effect-cover-editor">
    <div className="text-sm font-medium">效果封面</div>
    <p className="text-xs text-muted-foreground">上传实际产出或示例截图，让浏览者直观看到使用效果。建议使用横图。</p>
    {value && <EffectCover src={value} name="封面预览" detail />}
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" size="sm" disabled={processing} onClick={() => input.current?.click()}><Upload size={14} />{processing ? "正在处理图片…" : value ? "更换图片" : "上传效果图"}</Button>
      {value && <Button type="button" variant="ghost" size="sm" disabled={processing} onClick={() => onChange("")}><X size={14} />移除封面</Button>}
    </div>
    <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(e) => { void select(e.target.files?.[0]); e.target.value = ""; }} />
    <Input aria-label="效果图地址" type="url" placeholder="或粘贴 https:// 图片地址" disabled={processing} value={value.startsWith("data:") ? "" : value} onChange={(e) => { setError(""); onChange(e.target.value); }} />
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
  </div>;
}
