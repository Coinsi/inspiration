import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Timeline } from "@/lib/api";
import { Button } from "./ui/button";
import { SelectField } from "./ui/select-field";
import { WorkPicker, type Work } from "./WorkPicker";

export function WorkEditing({
  projectId,
  workId,
}: {
  projectId: string;
  workId: string;
}) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [choosing, setChoosing] = useState(false);
  const [selected, setSelected] = useState("");
  const linked = useQuery({
    queryKey: ["work-timelines", projectId, workId],
    queryFn: () => api.get<Timeline[]>(`${base}/works/${workId}/timelines`),
  });
  const all = useQuery({
    queryKey: ["timelines", projectId],
    queryFn: () => api.get<Timeline[]>(`${base}/timelines`),
    enabled: choosing,
  });
  const path = (id: string) => `${base}/cuts?timeline=${id}&work=${workId}`;
  const connect = useMutation({
    mutationFn: (id?: string) =>
      api.post<Timeline>(`${base}/works/${workId}/timelines`, {
        timeline_id: id || null,
      }),
    onSuccess: async (t) => {
      await qc.invalidateQueries({
        queryKey: ["work-timelines", projectId, workId],
      });
      await qc.invalidateQueries({ queryKey: ["timelines", projectId] });
      navigate(path(t.id));
    },
  });
  return (
    <section className="space-y-4 rounded-2xl border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">工作剪辑</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            编辑中的时间线也属于作品，不必等到采用版本后再查看。
          </p>
        </div>
        <div className="flex gap-2">
          {!linked.isPending && !linked.error && !linked.data?.length && (
            <Button
              disabled={connect.isPending}
              onClick={() => connect.mutate(undefined)}
            >
              开始剪辑
            </Button>
          )}
          <Button variant="outline" onClick={() => setChoosing(!choosing)}>
            关联已有时间线
          </Button>
        </div>
      </div>
      {linked.isPending && <p role="status">读取关联剪辑…</p>}
      {linked.data?.map((t) => (
        <Link
          key={t.id}
          className="flex items-center justify-between rounded-xl border p-4 hover:bg-muted"
          to={path(t.id)}
        >
          <span className="truncate">{t.name}</span>
          <span className="shrink-0 text-sm text-primary">继续剪辑 →</span>
        </Link>
      ))}
      {choosing && (
        <div className="flex flex-wrap gap-2">
          <SelectField
            label="已有时间线"
            value={selected}
            onChange={setSelected}
            options={[
              { value: "", label: "选择要关联的时间线" },
              ...(all.data || [])
                .filter((t) => !linked.data?.some((l) => l.id === t.id))
                .map((t) => ({ value: t.id, label: t.name })),
            ]}
          />
          <Button
            disabled={!selected || connect.isPending}
            onClick={() => connect.mutate(selected)}
          >
            关联并打开
          </Button>
          <p className="w-full text-xs text-muted-foreground">
            关联的是同一条工作时间线；修改会影响其他引用它的作品，已采用快照不变。
          </p>
        </div>
      )}
      {(linked.error || all.error || connect.error) && (
        <p role="alert" className="text-sm text-danger">
          {(linked.error || all.error || connect.error)?.message}
          <Button
            variant="ghost"
            onClick={() => {
              void linked.refetch();
              if (choosing) void all.refetch();
            }}
          >
            重新读取
          </Button>
        </p>
      )}
    </section>
  );
}

export function TimelineWorks({
  projectId,
  timelineId,
}: {
  projectId: string;
  timelineId: string;
}) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const [choosing, setChoosing] = useState(false);
  const [selected, setSelected] = useState("");
  const linked = useQuery({
    queryKey: ["timeline-works", projectId, timelineId],
    queryFn: () =>
      api.get<Work[]>(`${base}/works/timeline-links?timeline_id=${timelineId}`),
    enabled: !!timelineId,
  });
  const connect = useMutation({
    mutationFn: () =>
      api.post(`${base}/works/${selected}/timelines`, {
        timeline_id: timelineId,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({
        queryKey: ["timeline-works", projectId, timelineId],
      });
      await qc.invalidateQueries({ queryKey: ["work-timelines", projectId] });
      setChoosing(false);
    },
  });
  if (!timelineId) return null;
  return (
    <div className="timeline-work-links flex flex-wrap items-center gap-3 text-sm">
      <span className="text-muted-foreground">关联作品</span>
      {linked.data?.map((w) => (
        <Link key={w.id} className="text-primary" to={`${base}/works/${w.id}`}>
          {w.title} · 返回作品 ↗
        </Link>
      ))}
      {linked.isPending && <span>读取中…</span>}
      {linked.data?.length === 0 && <span>尚未关联</span>}
      <Button size="sm" variant="ghost" onClick={() => setChoosing(!choosing)}>
        关联作品
      </Button>
      {choosing && (
        <>
          <WorkPicker
            projectId={projectId}
            value={selected}
            onChange={setSelected}
            disabled={connect.isPending}
          />
          <Button
            size="sm"
            disabled={!selected || connect.isPending}
            onClick={() => connect.mutate()}
          >
            保存关联
          </Button>
        </>
      )}
      {(linked.error || connect.error) && (
        <span role="alert" className="text-danger">
          {(linked.error || connect.error)?.message}
          <Button variant="ghost" onClick={() => linked.refetch()}>
            重新读取
          </Button>
        </span>
      )}
    </div>
  );
}
