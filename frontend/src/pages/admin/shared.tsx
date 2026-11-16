import { useState, type ReactNode } from "react";
import { Search, RefreshCw } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";

export const kinds: Record<string, string> = {
  generation: "媒体生成 / 渲染",
  production: "Agent 制作",
  upload: "素材上传",
  attachment: "对话附件",
  index: "素材索引",
  processing: "媒体处理",
  transcription: "音视频转写",
  archive: "工程导入 / 导出",
};
export const roles: Record<string, string> = {
  user: "普通用户",
  admin: "平台管理员",
  super: "超级管理员",
};
export const date = (v?: string | null) =>
  v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "暂无记录";
export const bytes = (n: number) =>
  n >= 1024 ** 3
    ? `${(n / 1024 ** 3).toFixed(1)} GB`
    : n >= 1024 ** 2
      ? `${(n / 1024 ** 2).toFixed(1)} MB`
      : `${(n / 1024).toFixed(1)} KB`;
export const amount = (v: number | string) =>
  Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
export const short = (s?: string | null) => (s ? s.slice(0, 8) : "未记录");
export function Heading({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <header className="admin-heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {children}
    </header>
  );
}
export function Notice({ children }: { children: ReactNode }) {
  return <p className="admin-note">{children}</p>;
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div role="alert" className="admin-error">
      <span>{error instanceof Error ? error.message : "加载失败，请重试"}</span>
      {retry && (
        <Button variant="outline" onClick={retry}>
          <RefreshCw size={15} />
          重试
        </Button>
      )}
    </div>
  );
}
export function Loading() {
  return (
    <div className="admin-skeleton" role="status" aria-label="正在加载管理信息">
      <i />
      <i />
      <i />
    </div>
  );
}
export function Empty({
  children = "没有符合条件的记录",
}: {
  children?: ReactNode;
}) {
  return <div className="admin-empty">{children}</div>;
}
export function Badge({ value }: { value: string }) {
  const labels: Record<string, string> = {
    active: "正常",
    disabled: "已禁用",
    pending: "等待执行",
    queued: "排队中",
    running: "进行中",
    submitted: "已提交",
    processing: "处理中",
    uploading: "上传中",
    probing: "解析中",
    waiting: "等待服务",
    failed: "失败",
    needs_attention: "需要处理",
    needs_review: "等待确认",
    needs_plan_approval: "等待方案确认",
    succeeded: "完成",
    completed: "完成",
    ready: "就绪",
    canceled: "已取消",
    cancelled: "已取消",
    paused: "已暂停",
    ok: "正常",
    unavailable: "不可用",
    not_configured: "未配置",
    uncertain: "受理状态不明",
    submission_unknown: "受理状态不明",
  };
  const tone = [
    "failed",
    "unavailable",
    "disabled",
    "needs_attention",
    "uncertain",
  ].includes(value)
    ? "warn"
    : ["ok", "ready", "succeeded", "completed", "active"].includes(value)
      ? "good"
      : "neutral";
  return (
    <span className={`admin-badge ${tone}`}>{labels[value] || value}</span>
  );
}
export function SearchBox({
  initial,
  placeholder,
  onSearch,
}: {
  initial: string;
  placeholder: string;
  onSearch: (v: string) => void;
}) {
  const [text, setText] = useState(initial);
  return (
    <form
      className="admin-search"
      onSubmit={(e) => {
        e.preventDefault();
        onSearch(text.trim());
      }}
    >
      <Search size={17} />
      <input
        aria-label={placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
      />
      <Button variant="ghost" type="submit">
        搜索
      </Button>
    </form>
  );
}
export function useFilters() {
  const [params, setParams] = useSearchParams();
  const parsed = Number(params.get("offset") || 0);
  const offset = Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "offset") next.delete("offset");
    setParams(next);
  };
  return { params, set, offset };
}
export function Pager({
  total,
  offset,
  onPage,
}: {
  total: number;
  offset: number;
  onPage: (n: number) => void;
}) {
  return (
    <footer className="admin-pager">
      <span>
        共 {total} 条
        {total > 0 && ` · ${offset + 1}–${Math.min(offset + 25, total)}`}
      </span>
      <div>
        <Button
          variant="outline"
          disabled={offset === 0}
          onClick={() => onPage(Math.max(0, offset - 25))}
        >
          上一页
        </Button>
        <Button
          variant="outline"
          disabled={offset + 25 >= total}
          onClick={() => onPage(offset + 25)}
        >
          下一页
        </Button>
      </div>
    </footer>
  );
}
export function Stats({
  items,
}: {
  items: { label: string; value: ReactNode; note?: string }[];
}) {
  return (
    <div className="admin-stats">
      {items.map((i) => (
        <div key={i.label}>
          <span>{i.label}</span>
          <strong>{i.value}</strong>
          {i.note && <small>{i.note}</small>}
        </div>
      ))}
    </div>
  );
}
