import { AffectedMedia, type ImpactResult } from "@/components/AssetContinuity";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";

interface Result {
  brief: string;
  shots: { id: string; title: string }[];
  outputs: ImpactResult["outputs"];
  clips: ImpactResult["clips"];
  untracked_outputs: number;
}
export function SceneVisuals({
  base,
  sceneId,
  canEdit,
  onClose,
}: {
  base: string;
  sceneId: string;
  canEdit: boolean;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const path = `${base}/scenes/${sceneId}/visuals`;
  const query = useQuery({
    queryKey: ["scene-visuals", base, sceneId],
    queryFn: () => api.get<Result>(path),
  });
  const save = useMutation({
    mutationFn: () =>
      api.put(path, { brief: draft, previous: query.data?.brief }),
    onSuccess: async () => {
      await query.refetch();
      onClose();
    },
  });
  return (
      <Modal
        open
        onClose={onClose}
        title="本场光线与环境"
        width={570}
      >
        {query.isLoading && <p role="status">正在读取本场设定…</p>}
        {query.data && (
          <div className="space-y-3">
            <Textarea
              aria-label="本场光线与环境"
              rows={4}
              value={draft ?? query.data.brief}
              readOnly={!canEdit}
              placeholder="例如，临近黄昏，窗外的冷光落在人物右侧"
              onChange={(e) => setDraft(e.target.value)}
            />
            <details>
              <summary>沿用此设定的镜头 · {query.data.shots.length}</summary>
              <ul>
                {query.data.shots.map((s) => (
                  <li key={s.id}>{s.title}</li>
                ))}
              </ul>
            </details>
            <p className="text-sm text-muted-foreground">
              已记录关联的生成结果 {query.data.outputs.length} 个，剪辑片段{" "}
              {query.data.clips.length} 个。保存后可选择镜头重新生成。
            </p>
            <AffectedMedia
              base={base}
              outputs={query.data.outputs}
              clips={query.data.clips}
              untracked={query.data.untracked_outputs}
            />
            {canEdit && <Button
              disabled={
                draft === null || draft === query.data.brief || save.isPending
              }
              onClick={() => save.mutate()}
            >
              保存本场设定
            </Button>}
          </div>
        )}
        {(query.error || save.error) && (
          <p role="alert">{(query.error || save.error)?.message}</p>
        )}
      </Modal>
  );
}
