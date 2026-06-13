import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n";
import { api, type Asset } from "@/lib/api";

export default function Trash() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr } = useI18n();
  const { data: assets } = useQuery({
    queryKey: ["trash", projectId],
    queryFn: () => api.get<Asset[]>(`${base}/assets/trash`),
  });
  const restore = useMutation({
    mutationFn: (id: string) => api.post(`${base}/assets/${id}/restore`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["trash", projectId] }),
  });

  return (
      <div className="max-w-4xl mx-auto p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">{tr("trash.title")}</h2>
          <Link to={`${base}/assets`} className="text-sm text-muted-foreground hover:text-foreground">
            {tr("trash.back")}
          </Link>
        </div>
        {assets?.length === 0 && <p className="text-muted-foreground text-sm">{tr("trash.empty")}</p>}
        {assets?.map((a) => (
          <Card key={a.id}>
            <CardContent className="flex items-center justify-between py-3">
              <span className="text-sm">
                {a.name} <span className="text-muted-foreground">· {a.code} · {tr(`asset.${a.type}`)}</span>
              </span>
              <Button size="sm" variant="outline" onClick={() => restore.mutate(a.id)}>
                {tr("common.restore")}
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
  );
}
