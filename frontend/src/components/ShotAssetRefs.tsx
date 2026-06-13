import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { api, type Asset, type ShotAssetRef } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function ShotAssetRefs({
  projectId,
  shotId,
  onChange,
}: {
  projectId: string;
  shotId: string;
  onChange?: () => void;
}) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr } = useI18n();
  const [pick, setPick] = useState("");

  const { data: refs } = useQuery({
    queryKey: ["shot-refs", shotId],
    queryFn: () => api.get<ShotAssetRef[]>(`${base}/shots/${shotId}/asset-refs`),
  });
  const { data: assets } = useQuery({
    queryKey: ["assets-pick", projectId],
    queryFn: () => api.get<Asset[]>(`${base}/assets`),
  });

  const save = useMutation({
    mutationFn: (next: ShotAssetRef[]) =>
      api.put(`${base}/shots/${shotId}/asset-refs`, {
        refs: next.map((r) => ({
          asset_id: r.asset_id,
          role: r.role,
          ref_mode: r.ref_mode,
          pinned_version_id: r.pinned_version_id,
        })),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["shot-refs", shotId] });
      onChange?.();
    },
  });

  const assetName = (id: string) => assets?.find((a) => a.id === id)?.name ?? id.slice(0, 6);

  const add = () => {
    if (!pick || refs?.some((r) => r.asset_id === pick)) return;
    const a = assets?.find((x) => x.id === pick);
    const next = [
      ...(refs ?? []),
      { id: "", asset_id: pick, role: a?.type ?? "character", ref_mode: "floating", pinned_version_id: null, ordinal: 0 },
    ];
    save.mutate(next as ShotAssetRef[]);
    setPick("");
  };
  const remove = (assetId: string) => save.mutate((refs ?? []).filter((r) => r.asset_id !== assetId));

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {refs && refs.length > 0 ? (
          refs.map((r) => (
            <span key={r.asset_id} className="inline-flex items-center gap-1 rounded-full bg-primary/15 text-primary px-2 py-0.5 text-xs">
              <span className="opacity-70">{tr(`asset.${r.role}`)}</span>
              {assetName(r.asset_id)}
              <button onClick={() => remove(r.asset_id)} className="hover:text-foreground">×</button>
            </span>
          ))
        ) : (
          <span className="text-xs text-muted-foreground">{tr("sref.none")}</span>
        )}
      </div>
      <div className="flex gap-2">
        <select
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm flex-1"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
        >
          <option value="">{tr("sref.select")}</option>
          {assets
            ?.filter((a) => !refs?.some((r) => r.asset_id === a.id))
            .map((a) => (
              <option key={a.id} value={a.id}>
                [{tr(`asset.${a.type}`)}] {a.name}
              </option>
            ))}
        </select>
        <Button size="sm" variant="outline" onClick={add} disabled={!pick}>
          {tr("sref.add")}
        </Button>
      </div>
    </div>
  );
}
