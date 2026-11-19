import { StudioSelect } from "@/components/ui/studio-select";
import { SearchField } from "@/components/ui/search-field";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, blobUrl } from "@/lib/api";
import {
  mediaTime,
  mediaTypeName,
  type MediaType,
  type MediaVersion,
} from "@/lib/library";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { AudioLines, Check } from "lucide-react";
import { MediaImage } from "@/components/MediaImage";

type NamedVersion = MediaVersion & { name: string };
const field =
  "w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";
const pageSize = 24;

export function useLibraryVersion(projectId: string, id: string, followProcessing = false) {
  return useQuery({
    queryKey: ["library-version", projectId, id],
    queryFn: () =>
      api.get<NamedVersion>(`/projects/${projectId}/library/versions/${id}`),
    enabled: !!id,
    refetchInterval: q => followProcessing && ["queued", "processing"].includes(q.state.data?.status ?? "") ? 2000 : false,
  });
}

/** Selection is independent of the current search page and always pins a version. */
export function LibraryVersionPicker({
  projectId,
  value,
  onChange,
  label,
  disabled = false,
  mediaType = "video",
  gallery = false,
}: {
  projectId: string;
  value: string;
  onChange: (id: string) => void;
  label: string;
  disabled?: boolean;
  mediaType?: MediaType | "all";
  gallery?: boolean;
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
  }, [input, mediaType]);
  const chosen = useLibraryVersion(projectId, value);
  const catalog = useQuery({
    queryKey: ["library-version-catalog", projectId, query, offset, mediaType],
    queryFn: () =>
      api.get<{ items: NamedVersion[]; total: number }>(
        `/projects/${projectId}/library/version-catalog?query=${encodeURIComponent(query)}&offset=${offset}&limit=${pageSize}${mediaType === "all" ? "" : `&media_type=${mediaType}`}`,
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
  const name = (v: NamedVersion) =>
    `${v.name} · v${v.ordinal}${mediaType === "all" ? ` · ${mediaTypeName(v.media_type || "video", zh)}` : ""}`;
  return (
    <div
      className="min-w-0 space-y-2"
      role="group"
      aria-label={`${label}选择器`}
    >
      <SearchField
        className="w-full"
        label={`${label}：搜索`}
        placeholder={zh ? "搜索素材名称" : "Search media names"}
        maxLength={255}
        value={input}
        disabled={disabled}
        onValueChange={(value) => setInput(value)}
      />
      {gallery ? (
        <div className="cut-library-grid" aria-busy={waiting}>
          {items.map((v) => {
            const poster = v.poster_hash || v.original_hash;
            return (
              <button
                type="button"
                key={v.id}
                aria-pressed={v.id === value}
                aria-label={name(v)}
                disabled={disabled}
                onClick={() => onChange(v.id)}
              >
                <span className="cut-library-poster">
                  {v.media_type === "audio" ? (
                    <AudioLines size={30} />
                  ) : (
                    <MediaImage
                      src={
                        poster
                          ? blobUrl(
                              projectId,
                              poster,
                              v.poster_hash ? undefined : "thumbnail",
                            )
                          : undefined
                      }
                      alt=""
                    />
                  )}
                  {v.id === value && (
                    <i aria-hidden="true">
                      <Check size={13} />
                    </i>
                  )}
                </span>
                <span className="cut-library-title">{v.name}</span>
                <small>
                  {mediaTypeName(v.media_type, zh)} · v{v.ordinal}
                  {v.duration_ms ? " · " + mediaTime(v.duration_ms) : ""}
                </small>
              </button>
            );
          })}
        </div>
      ) : (
        <StudioSelect
          className={field}
          aria-label={label}
          value={value}
          disabled={disabled || waiting || catalog.isError}
          onValueChange={(selectedValue) => onChange(selectedValue)}
        >
          <option value="">
            {zh ? "选择已处理的素材" : "Choose ready media"}
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
        </StudioSelect>
      )}
      {catalog.isError ? (
        <div role="alert" className="text-xs text-danger">
          {zh ? "素材列表读取失败。" : "Could not load media."}
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
                    ? "没有匹配的素材"
                    : "No matching media"
                  : zh
                    ? "暂无可用版本"
                    : "No ready versions"
                : `${offset + 1}–${Math.min(offset + pageSize, catalog.data.total)} / ${catalog.data.total} ${zh ? "个版本" : "versions"}`}
          </span>
          {(!gallery || (catalog.data?.total || 0) > pageSize) && (
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
          )}
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
      {selected && !gallery && (
        <div className="flex min-w-0 items-center gap-3 rounded-lg bg-muted/50 p-2">
          <div className="h-12 w-20 shrink-0 overflow-hidden rounded">
            {selected.media_type === "audio" ? (
              <div className="flex h-full items-center justify-center rounded bg-elevated text-muted-foreground">
                <AudioLines size={24} />
              </div>
            ) : (
              <MediaImage
                src={
                  selected.poster_hash ||
                  (selected.media_type === "image" ? selected.proxy_hash : null)
                    ? blobUrl(
                        projectId,
                        (selected.poster_hash || selected.proxy_hash)!,
                      )
                    : null
                }
                alt={selected.name}
              />
            )}
          </div>
          <div className="min-w-0 text-xs">
            <p className="truncate font-medium" title={name(selected)}>
              {name(selected)}
            </p>
            <p className="mt-1 text-muted-foreground">
              {mediaTypeName(selected.media_type || "video", zh)}
              {selected.duration_ms != null
                ? ` · ${mediaTime(selected.duration_ms)}`
                : ""}{" "}
              · {zh ? "已固定原片版本" : "Source version pinned"}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
