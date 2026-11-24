import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { api, type Review } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function ReviewPanel({ projectId, shotId }: { projectId: string; shotId: string }) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr, lang } = useI18n();
  const { me } = useAuth();
  const role = me?.memberships.find(m => m.project_id === projectId)?.role ?? "";
  const canSubmit = ["admin", "director", "artist"].includes(role);
  const canDecide = ["admin", "director"].includes(role);
  const { data: reviews, isLoading, error, refetch } = useQuery({
    queryKey: ["reviews", shotId],
    queryFn: () => api.get<Review[]>(`${base}/reviews?target_type=shot&target_id=${shotId}`),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["reviews", shotId] });
    void qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
    void qc.invalidateQueries({ queryKey: ["board", projectId] });
    void qc.invalidateQueries({ queryKey: ["scene-shots", projectId] });
  };
  const create = useMutation({
    mutationFn: () => api.post(`${base}/reviews`, { target_type: "shot", target_id: shotId }),
    onSuccess: refresh,
  });
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      api.post(`${base}/reviews/${id}/decide`, { approve, note: approve ? "通过" : "打回" }),
    onSuccess: refresh,
  });

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">{lang === "zh" ? "审阅记录" : "Review history"}</h3>
        {canSubmit && <Button size="sm" variant="outline" disabled={create.isPending} onClick={() => create.mutate()}>
          {lang === "zh" ? "发起审阅" : "Start review"}
        </Button>}
      </div>
      {isLoading && <p className="text-sm text-muted-foreground" role="status">{tr("common.loading")}</p>}
      {reviews?.length === 0 && <p className="py-2 text-sm text-muted-foreground">{lang === "zh" ? "本镜头还没有审阅记录" : "No reviews for this shot yet"}</p>}
      {(error || create.error || decide.error) && <p role="alert" className="text-sm text-danger">
        {(error || create.error || decide.error)?.message}
        {error && <Button size="sm" variant="ghost" onClick={() => void refetch()}>{lang === "zh" ? "重试" : "Retry"}</Button>}
      </p>}
      {reviews?.map((r) => (
        <div key={r.id} className="flex items-center justify-between text-sm">
          <span>
            {tr("rev.roundN").replace("{n}", String(r.round_no))} ·{" "}
            <span
              className={
                r.status === "approved"
                  ? "text-success"
                  : r.status === "rejected"
                    ? "text-danger"
                    : "text-warning"
              }
            >
              {r.status === "pending" ? tr("rev.pending") : r.status === "approved" ? tr("rev.approved") : tr("rev.rejected")}
            </span>
          </span>
          {canDecide && r.status === "pending" && (
            <span className="flex gap-1">
              <Button size="sm" variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate({ id: r.id, approve: true })}>
                {tr("rev.approved")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-danger"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ id: r.id, approve: false })}
              >
                {tr("rev.rejected")}
              </Button>
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
