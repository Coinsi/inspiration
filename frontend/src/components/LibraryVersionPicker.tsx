import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, blobUrl } from "@/lib/api";
import { mediaTime, type MediaVersion } from "@/lib/library";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { MediaImage } from "@/components/MediaImage";

type NamedVersion = MediaVersion & { name: string };
const field =
  "w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";
const pageSize = 24;

export function useLibraryVersion(projectId: string, id: string) {
  return useQuery({
    queryKey: ["library-version", projectId, id],
    queryFn: () =>
      api.get<NamedVersion>(`/projects/${projectId}/library/versions/${id}`),
    enabled: !!id,
  });
}

/** Selection is independent of the current search page and always pins a version. */
export function LibraryVersionPicker({
  projectId,
  value,
  onChange,
  label,
  disabled = false,
}: {
  projectId: string;
  value: string;
  onChange: (id: string) => void;
  label: string;
  disabled?: boolean;
}) {
  const zh = useI18n().lang === "zh";
  const [input, setInput] = useState(""),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(input.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [input]);
  const chosen = useLibraryVersion(projectId, value);
  const catalog = useQuery({
    queryKey: ["library-version-catalog", projectId, query, offset],
    queryFn: () =>
      api.get<{ items: NamedVersion[]; total: number }>(
        `/projects/${projectId}/library/version-catalog?query=${encodeURIComponent(query)}&offset=${offset}&limit=${pageSize}`,
      ),
  });
  useEffect(() => {
    if (catalog.data && offset > 0 && offset >= catalog.data.total)
      setOffset(
        Math.max(0, Math.ceil(catalog.data.total / pageSize) - 1) * pageSize,
      );
  }, [catalog.data, offset]);
  const waiting = catalog.isFetching || input.trim() !== query;
  const items = catalog.data?.items ?? [];
  const selected = chosen.data;
  const name = (v: NamedVersion) => `${v.name} · v${v.ordinal}`;
  return (
    <div
      className="min-w-0 space-y-2"
      role="group"
      aria-label={`${label}选择器`}
    >
      <input
        className={field}
        aria-label={`${label}：搜索`}
        placeholder={zh ? "搜索视频名称" : "Search video names"}
        type="search"
        maxLength={255}
        value={input}
        disabled={disabled}
        onChange={(e) => setInput(e.target.value)}
      />
      <select
        className={field}
        aria-label={label}
        value={value}
        disabled={disabled || waiting || catalog.isError}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">
          {zh ? "选择已处理的视频" : "Choose a ready video"}
        </option>
        {value && !items.some((v) => v.id === value) && (
          <option value={value}>
            {selected
              ? name(selected)
              : chosen.isError
                ? zh
                  ? "所选版本不可用"
                  : "Selected version unavailable"
                : zh
                  ? "读取所选版本…"
                  : "Loading selection…"}
          </option>
        )}
        {items.map((v) => (
          <option value={v.id} key={v.id}>
            {name(v)}
          </option>
        ))}
      </select>
      {catalog.isError ? (
        <div role="alert" className="text-xs text-danger">
          {zh ? "视频列表读取失败。" : "Could not load videos."}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void catalog.refetch()}
          >
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-1 text-xs text-muted-foreground">
          <span role="status">
            {waiting
              ? zh
                ? "查找中…"
                : "Searching…"
              : !catalog.data?.total
                ? query
                  ? zh
                    ? "没有匹配的视频"
                    : "No matching videos"
                  : zh
                    ? "暂无可用版本"
                    : "No ready versions"
                : `${offset + 1}–${Math.min(offset + pageSize, catalog.data.total)} / ${catalog.data.total} ${zh ? "个版本" : "versions"}`}
          </span>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              aria-label={`${label}：上一页`}
              disabled={disabled || waiting || !offset}
              onClick={() => setOffset((n) => Math.max(0, n - pageSize))}
            >
              {zh ? "上一页" : "Previous"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-label={`${label}：下一页`}
              disabled={
                disabled ||
                waiting ||
                offset + pageSize >= (catalog.data?.total ?? 0)
              }
              onClick={() => setOffset((n) => n + pageSize)}
            >
              {zh ? "下一页" : "Next"}
            </Button>
          </div>
        </div>
      )}
      {value && chosen.isError && (
        <div role="alert" className="text-xs text-danger">
          {zh
            ? "原片版本无法读取，请重试或重新选择。"
            : "Cannot read the selected source. Retry or choose another."}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void chosen.refetch()}
          >
            {zh ? "重读所选版本" : "Reload selected version"}
          </Button>
        </div>
      )}
      {selected && (
        <div className="flex min-w-0 items-center gap-3 rounded-lg bg-muted/50 p-2">
          <div className="h-12 w-20 shrink-0 overflow-hidden rounded">
            <MediaImage
              src={
                selected.poster_hash
                  ? blobUrl(projectId, selected.poster_hash)
                  : null
              }
              alt={selected.name}
            />
          </div>
          <div className="min-w-0 text-xs">
            <p className="truncate font-medium" title={name(selected)}>
              {name(selected)}
            </p>
            <p className="mt-1 text-muted-foreground">
              {mediaTime(selected.duration_ms ?? 0)} ·{" "}
              {zh ? "已固定原片版本" : "Source version pinned"}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
