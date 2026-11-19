import { useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { AudioLines, Check } from "lucide-react";
import { blobUrl } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { mediaTime, mediaTypeName } from "@/lib/library";
import {
  timelineSources,
  type SourceFilter,
  type TimelineSource,
} from "@/lib/timeline-sources";
import { MediaImage } from "./MediaImage";
import { Button } from "./ui/button";
import { SearchField } from "./ui/search-field";

export function TimelineSourcePicker({
  project,
  filter,
  selected,
  onSelect,
  disabled,
}: {
  project: string;
  filter: SourceFilter;
  selected: TimelineSource | null;
  onSelect: (source: TimelineSource) => void;
  disabled: boolean;
}) {
  const zh = useI18n().lang === "zh";
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!input.trim()) {
      setQuery("");
      return;
    }
    const timer = window.setTimeout(() => setQuery(input.trim()), 200);
    return () => window.clearTimeout(timer);
  }, [input]);
  const catalog = useInfiniteQuery(timelineSources(project, filter, query));
  const items = catalog.data?.pages.flatMap((page) => page.items) ?? [];
  const typing = query !== input.trim();
  const labels = {
    media_version: zh ? "上传与复用" : "Upload",
    generation: zh ? "项目产物" : "Project output",
    attachment: zh ? "对话附件" : "Chat attachment",
  };
  return (
    <div
      className="min-w-0 space-y-2"
      role="group"
      aria-label={zh ? "素材库选择器" : "Media picker"}
    >
      <SearchField
        className="w-full"
        label={zh ? "搜索剪辑素材" : "Search editing media"}
        placeholder={
          zh ? "搜索名称、文件名或提示词" : "Search names, filenames or prompts"
        }
        maxLength={255}
        disabled={disabled}
        value={input}
        onValueChange={setInput}
      />
      <div
        className="cut-library-grid"
        aria-busy={catalog.isFetching || typing}
      >
        {items.map((source, index) => {
          const active =
            source.id === selected?.id &&
            source.source_type === selected.source_type;
          const version = source.ordinal ? ` · v${source.ordinal}` : "";
          return (
            <button
              type="button"
              key={source.source_type + source.id}
              aria-pressed={active}
              aria-label={source.title + version}
              title={source.title}
              disabled={disabled}
              onClick={() => onSelect(source)}
            >
              <span className="cut-library-poster">
                {source.kind === "audio" ? (
                  <AudioLines size={27} />
                ) : (
                  <MediaImage
                    src={blobUrl(
                      project,
                      source.thumbnail_hash || source.blob_hash,
                      "thumbnail",
                    )}
                    priority={index === 0}
                    deferUntilVisible
                    alt=""
                  />
                )}
                {active && (
                  <i aria-hidden="true">
                    <Check size={13} />
                  </i>
                )}
              </span>
              <span className="cut-library-title">{source.title}</span>
              <small>
                {mediaTypeName(source.kind, zh)} · {labels[source.source_type]}
                {version}
                {source.duration_ms
                  ? " · " + mediaTime(source.duration_ms)
                  : ""}
              </small>
            </button>
          );
        })}
      </div>
      {!items.length && !catalog.isError && (
        <p role="status" className="text-sm text-muted-foreground">
          {catalog.isPending || typing
            ? zh
              ? "正在查找素材…"
              : "Finding media…"
            : query
              ? zh
                ? "没有匹配的素材"
                : "No matching media"
              : zh
                ? "当前项目中暂时没有这类可剪辑素材"
                : "No editable media of this type in this project"}
        </p>
      )}
      {catalog.isError ? (
        <div role="alert" className="flex items-center gap-2 text-sm">
          {zh ? "素材列表读取失败" : "Could not load media"}
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              void (catalog.isFetchNextPageError
                ? catalog.fetchNextPage()
                : catalog.refetch())
            }
          >
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      ) : (
        catalog.hasNextPage && (
          <Button
            size="sm"
            variant="outline"
            disabled={catalog.isFetchingNextPage || typing}
            onClick={() => void catalog.fetchNextPage()}
          >
            {catalog.isFetchingNextPage
              ? zh
                ? "正在读取…"
                : "Loading…"
              : zh
                ? "加载更多素材"
                : "Load more media"}
          </Button>
        )
      )}
    </div>
  );
}
