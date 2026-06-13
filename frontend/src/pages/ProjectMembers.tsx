import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n";
import { api, ApiError, type Member } from "@/lib/api";

const ROLES = ["admin", "director", "writer", "artist", "producer", "viewer"];

export default function ProjectMembers() {
  const { projectId } = useParams();
  const qc = useQueryClient();
  const { t: tr } = useI18n();
  const { data: members } = useQuery({
    queryKey: ["members", projectId],
    queryFn: () => api.get<Member[]>(`/projects/${projectId}/members`),
  });

  const [userId, setUserId] = useState("");
  const [role, setRole] = useState("artist");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () => api.post<Member>(`/projects/${projectId}/members`, { user_id: userId, role }),
    onSuccess: () => {
      setUserId("");
      setError(null);
      void qc.invalidateQueries({ queryKey: ["members", projectId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Failed"),
  });

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{tr("mem.grant")}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2 items-center">
            <Input
              placeholder={tr("mem.userId")}
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
            />
            <select
              className="h-9 rounded-md border border-border px-2 text-sm"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <Button onClick={() => add.mutate()} disabled={!userId || add.isPending}>
              {tr("mem.grantBtn")}
            </Button>
          </div>
          {error && <p className="text-sm text-danger mt-2">{error}</p>}
        </CardContent>
      </Card>

      <div>
        <h2 className="font-semibold mb-3">{tr("mem.list")}</h2>
        <div className="space-y-2">
          {members?.map((m) => (
            <Card key={m.id}>
              <CardContent className="flex items-center justify-between py-3">
                <span className="text-sm font-mono">{m.user_id}</span>
                <span className="text-xs px-2 py-1 rounded bg-muted">{m.role}</span>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
