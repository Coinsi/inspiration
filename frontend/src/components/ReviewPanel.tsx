import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { api, type Review } from "@/lib/api";

export default function ReviewPanel({ projectId, shotId }: { projectId: string; shotId: string }) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr } = useI18n();
  const { data: reviews } = useQuery({
    queryKey: ["reviews", shotId],
    queryFn: () => api.get<Review[]>(`${base}/reviews?target_type=shot&target_id=${shotId}`),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["reviews", shotId] });
    void qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
    void qc.invalidateQueries({ queryKey: ["board", projectId] });
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
        <span className="text-xs text-muted-foreground">{tr("rev.review")}</span>
        <Button size="sm" variant="outline" onClick={() => create.mutate()}>
          {tr("rev.start")}
        </Button>
      </div>
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
          {r.status === "pending" && (
            <span className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => decide.mutate({ id: r.id, approve: true })}>
                {tr("rev.approved")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-danger"
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
