import { infiniteQueryOptions } from "@tanstack/react-query";
import { api } from "./api";
import type { MediaType } from "./library";

export type SourceFilter = MediaType | "all";
export type TimelineSource = {
  id: string;
  source_type: "generation" | "media_version" | "attachment";
  kind: MediaType;
  title: string;
  blob_hash: string;
  thumbnail_hash: string | null;
  duration_ms: number | null;
  ordinal: number | null;
};

export function timelineSources(
  project: string,
  filter: SourceFilter = "all",
  search = "",
) {
  return infiniteQueryOptions({
    queryKey: ["materials", project, "timeline-sources", filter, search],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({
        media_type: filter,
        query: search,
        offset: String(pageParam),
      });
      return api.get<{ items: TimelineSource[]; has_more: boolean }>(
        `/projects/${project}/timeline-sources?${params}`,
        { signal },
      );
    },
    getNextPageParam: (last, _pages, offset) =>
      last.has_more ? offset + last.items.length : undefined,
    staleTime: 5_000,
  });
}
