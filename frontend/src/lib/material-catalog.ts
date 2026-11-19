import { queryOptions } from "@tanstack/react-query";
import { api } from "./api";

export type Material = {
  adopted: boolean;
  is_test: boolean;
  id: string;
  source: "upload" | "generated" | "asset" | "attachment";
  name: string;
  description?: string;
  tags?: string[] | null;
  metadata_revision?: number;
  kind: "image" | "video" | "audio" | "entity";
  created_at: string;
  deleted_at: string | null;
  status: string;
  poster_hash: string | null;
  blob_hash: string | null;
  preview_hash: string | null;
  size_bytes: number;
  duration_ms: number | null;
  version_id: string | null;
};

export function materialCatalog(
  project: string,
  deleted = false,
  kind = "",
  source = "",
  query = "",
  page = 0,
) {
  const params = new URLSearchParams({
    deleted: String(deleted),
    offset: String(page * 24),
    query,
  });
  if (kind) params.set("kind", kind);
  if (source) params.set("source", source);
  return queryOptions({
    queryKey: ["materials", project, deleted, kind, source, query, page],
    queryFn: ({ signal }) =>
      api.get<{ items: Material[]; total: number }>(
        `/projects/${project}/library/materials?${params}`,
        { signal },
      ),
    staleTime: 30_000,
  });
}
