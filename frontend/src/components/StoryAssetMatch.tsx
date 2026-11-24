import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

type Candidate = { id: string; name: string; code: string; summary?: string };
export type AssetMatch = {
  name: string;
  index?: number;
  kind?: string;
  manual?: boolean;
  saved_assets?: number;
  candidates: Candidate[];
};

export function assetMatch(error: Error): AssetMatch | null {
  const detail =
    error instanceof ApiError
      ? (error.detail as Partial<AssetMatch> | null)
      : null;
  return detail &&
    typeof detail.name === "string" &&
    Array.isArray(detail.candidates)
    ? {
        name: detail.name,
        candidates: detail.candidates,
        index: detail.index,
        kind: detail.kind,
        saved_assets: detail.saved_assets,
      }
    : null;
}

export function StoryAssetMatch({
  match,
  busy,
  choose,
  close,
  base,
}: {
  match: AssetMatch;
  busy: boolean;
  choose: (id: string | null) => void;
  close: () => void;
  base: string;
}) {
  const [text, setText] = useState("");
  const query = text.trim();
  const search = useQuery({
    queryKey: ["story-asset-search", base, match.kind, query],
    queryFn: () =>
      api.get<Candidate[]>(
        base +
          "/assets?" +
          new URLSearchParams({ type: match.kind!, q: query }),
      ),
    enabled: !!query && !!match.kind,
  });
  const candidates = query ? (search.data ?? []) : match.candidates;
  return (
    <Modal
      open
      title={`「${match.name}」对应哪项资产？`}
      onClose={close}
      width={490}
    >
      <div className="space-y-3">
        {!!match.saved_assets && (
          <p className="text-sm text-muted-foreground">
            已保存 {match.saved_assets} 项资产。请确认这条来源。
          </p>
        )}
        {match.kind && (
          <Input
            value={text}
            aria-label="搜索同类资产"
            placeholder="搜索同类资产名称"
            onChange={(e) => setText(e.target.value)}
          />
        )}
        {query && search.isSuccess && !candidates.length && (
          <p className="text-sm text-muted-foreground">未找到匹配的资产。</p>
        )}
        {search.error && <p role="alert">{search.error.message}</p>}
        {candidates.map((a) => (
          <button
            type="button"
            key={a.id}
            disabled={busy}
            className="block w-full rounded-lg border px-3 py-2 text-left hover:bg-muted"
            onClick={() => choose(a.id)}
          >
            <strong>{a.name}</strong>
            <small className="ml-2 text-muted-foreground">{a.code}</small>
            {a.summary && (
              <p className="text-sm text-muted-foreground">{a.summary}</p>
            )}
          </button>
        ))}
        <Button variant="outline" disabled={busy} onClick={() => choose(null)}>
          单独建为新资产
        </Button>
      </div>
    </Modal>
  );
}
