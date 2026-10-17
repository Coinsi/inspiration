import { StudioSelect } from "@/components/ui/studio-select";
import { SearchField } from "@/components/ui/search-field";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Button } from "./ui/button";

type Destination = {
  target_type: "shot" | "asset";
  target_id: string;
  name: string;
  path: string;
  current_generation_id: string | null;
};
type Usage = { state: string; uses: Destination[] };
export function AdoptMaterial({
  projectId,
  generationId,
  kind,
}: {
  projectId: string;
  generationId: string;
  kind: string;
}) {
  const base = `/projects/${projectId}`,
    qc = useQueryClient();
  const [open, setOpen] = useState(false),
    [target, setTarget] = useState(""),
    [search, setSearch] = useState("");
  const usage = useQuery({
    queryKey: ["generation-usage", projectId, generationId],
    queryFn: () => api.get<Usage>(`${base}/generations/${generationId}/usage`),
  });
  const targets = useQuery({
    queryKey: ["adoption-targets", projectId],
    queryFn: () => api.get<Destination[]>(`${base}/adoption-targets`),
    enabled: open,
  });
  const options =
    targets.data?.filter((t) => kind === "image" || t.target_type === "shot") ||
    [];
  const chosen = options.find(
    (t) => `${t.target_type}:${t.target_id}` === target,
  );
  const adopt = useMutation({
    mutationFn: () =>
      api.post(`${base}/generations/${generationId}/adopt`, {
        target_type: chosen!.target_type,
        target_id: chosen!.target_id,
        expected_generation_id: chosen!.current_generation_id,
      }),
    onSuccess: async () => {
      await Promise.all(
        [
          "generation-usage",
          "adoption-targets",
          "generations",
          "shots",
          "assets",
          "materials",
          "timeline",
        ].map((key) => qc.invalidateQueries({ queryKey: [key, projectId] })),
      );
      await qc.invalidateQueries({ queryKey: ["gens"] });
      setOpen(false);
      setTarget("");
    },
  });
  return (
    <section
      className="my-4 space-y-3 rounded-xl border p-4"
      aria-label="素材采用位置"
    >
      {usage.isPending ? (
        <p role="status">正在读取采用位置…</p>
      ) : usage.isError ? (
        <p role="alert">
          {usage.error.message}{" "}
          <button onClick={() => usage.refetch()}>重试</button>
        </p>
      ) : (
        <>
          <p className="text-sm font-medium">
            {usage.data?.uses.length ? "当前已采用" : "候选素材 · 尚未采用"}
          </p>
          {usage.data?.uses.map((t) => (
            <p
              className="text-sm text-muted-foreground"
              key={t.target_type + t.target_id}
            >
              {t.path} /{" "}
              <Link
                className="text-primary"
                to={
                  t.target_type === "asset"
                    ? `${base}/assets/${t.target_id}`
                    : `${base}/shots?shot=${t.target_id}`
                }
              >
                {t.name} ↗
              </Link>
            </p>
          ))}
        </>
      )}
      {!open ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            setOpen(true);
            adopt.reset();
          }}
        >
          采用到镜头或角色
        </Button>
      ) : (
        <div className="space-y-3">
          <SearchField
            className="w-full"
            label="查找采用位置"
            placeholder="搜索作品、镜头或角色…"
            value={search}
            onValueChange={(value) => setSearch(value)}
          />
          <StudioSelect
            aria-label="采用位置"
            className="w-full rounded-lg border bg-background p-2 text-sm"
            value={target}
            onValueChange={(selectedValue) => {
              setTarget(selectedValue);
              adopt.reset();
            }}
          >
            <option value="">选择具体位置</option>
            {options
              .filter((t) =>
                `${t.path} ${t.name}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((t) => (
                <option
                  key={t.target_type + t.target_id}
                  value={`${t.target_type}:${t.target_id}`}
                >
                  {t.path} / {t.name}
                  {t.current_generation_id ? "（已有采用版本）" : ""}
                </option>
              ))}
          </StudioSelect>
          {targets.isPending && <p role="status">正在读取位置…</p>}
          {targets.isError && (
            <p role="alert">
              {targets.error.message}{" "}
              <button onClick={() => targets.refetch()}>重试</button>
            </p>
          )}
          {targets.isSuccess && !options.length && (
            <p className="text-sm text-muted-foreground">
              请先在作品中添加镜头，或创建角色与场景。
            </p>
          )}
          {chosen && (
            <p className="text-xs leading-6 text-muted-foreground">
              {chosen.current_generation_id
                ? "将替换此位置当前的采用版本，旧版本仍可重新选择。"
                : "将把这份候选设为此位置的采用版本。"}{" "}
              已有剪辑保留原画面。
            </p>
          )}
          {adopt.error && (
            <p role="alert" className="text-sm">
              {adopt.error.message}{" "}
              <button
                onClick={() => {
                  setTarget("");
                  adopt.reset();
                  void targets.refetch();
                }}
              >
                刷新位置
              </button>
            </p>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!chosen || adopt.isPending}
              onClick={() => adopt.mutate()}
            >
              {adopt.isPending ? "正在采用…" : "确认采用"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={adopt.isPending}
              onClick={() => setOpen(false)}
            >
              取消
            </Button>
          </div>
        </div>
      )}
      {adopt.isSuccess && (
        <p role="status" className="text-sm text-primary">
          已采用，原素材和其他候选仍保留。
        </p>
      )}
    </section>
  );
}
