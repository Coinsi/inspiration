import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAssetPermissions } from "@/components/AssetActions";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type AffectedShot = {
  shot_id: string;
  title: string;
  revision: string;
  changes: {
    scene_title: string;
    scene_level: boolean;
    before: string | null;
    after: string | null;
  }[];
};

type Proposal = {
  revision: string;
  shot_revision: string;
  still_prompt: string;
  motion_prompt: string;
};

function RevisePrompts({
  path,
  shot,
  done,
}: {
  path: string;
  shot: AffectedShot;
  done: () => void;
}) {
  const zh = useI18n().lang === "zh";
  const toast = useToast();
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const url = path + "/" + shot.shot_id;
  const preview = useMutation({
    mutationFn: () =>
      api.post<Proposal>(url + "/preview", { revision: shot.revision }),
    onSuccess: setProposal,
    onError: (e) => toast.push(e.message, "error"),
  });
  const adopt = useMutation({
    mutationFn: () => api.post(url + "/adopt", proposal),
    onSuccess: done,
    onError: (e) => {
      toast.push(e.message, "error");
      setProposal(null);
      done();
    },
  });
  if (!proposal)
    return (
      <Button
        size="sm"
        variant="outline"
        disabled={preview.isPending}
        onClick={() => preview.mutate()}
      >
        {preview.isPending
          ? zh
            ? "正在拟稿…"
            : "Drafting…"
          : zh
            ? "用文字模型拟定新提示词"
            : "Draft with text model"}
      </Button>
    );
  return (
    <div className="w-full space-y-2">
      {(["still_prompt", "motion_prompt"] as const).map((field, i) => (
        <label key={field} className="block text-sm">
          {i
            ? zh
              ? "动态提示词"
              : "Motion prompt"
            : zh
              ? "画面提示词"
              : "Still prompt"}
          <textarea
            rows={3}
            maxLength={12000}
            className="mt-1 w-full rounded-md border bg-background p-2"
            value={proposal[field]}
            onChange={(e) =>
              setProposal({ ...proposal, [field]: e.target.value })
            }
          />
        </label>
      ))}
      <p className="text-xs text-muted-foreground">
        {zh
          ? "采用后更新这两段提示词。镜头另有手写最终提示词时，生成仍优先使用手写内容。"
          : "Adopting updates these two prompts. A manual final prompt still takes precedence."}
      </p>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={
            adopt.isPending ||
            !proposal.still_prompt.trim() ||
            !proposal.motion_prompt.trim()
          }
          onClick={() => adopt.mutate()}
        >
          {zh ? "采用并标为已核对" : "Adopt and mark reviewed"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={adopt.isPending}
          onClick={() => setProposal(null)}
        >
          {zh ? "取消" : "Cancel"}
        </Button>
      </div>
    </div>
  );
}

export function ScriptSourceReview({
  projectId,
  scriptId,
}: {
  projectId: string;
  scriptId: string;
}) {
  const [open, setOpen] = useState(false);
  const { canEdit } = useAssetPermissions(projectId);
  const { lang } = useI18n();
  const zh = lang === "zh";
  const cache = useQueryClient();
  const toast = useToast();
  const key = ["script-source-review", projectId, scriptId];
  const path = `/projects/${projectId}/scripts/${scriptId}/affected-shots`;
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.get<AffectedShot[]>(path),
  });
  const confirm = useMutation({
    mutationFn: (shot: AffectedShot) =>
      api.post(`${path}/${shot.shot_id}/confirm`, { revision: shot.revision }),
    onSuccess: () => cache.invalidateQueries({ queryKey: key }),
    onError: (error) => {
      toast.push(error.message, "error");
      void query.refetch();
    },
  });
  const rows = query.data || [];
  if (!rows.length && !open && !query.isError) return null;
  return (
    <>
      {query.isError ? (
        <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>
          {zh ? "重新检查镜头影响" : "Retry shot check"}
        </Button>
      ) : (
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          {zh ? `${rows.length} 个镜头待复核` : `Review ${rows.length} shots`}
        </Button>
      )}
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={zh ? "剧本改动影响的镜头" : "Shots affected by script edits"}
      >
        <div className="space-y-5">
          {!rows.length && (
            <p className="text-sm">
              {zh ? "所有镜头均已核对。" : "All shots reviewed."}
            </p>
          )}
          {rows.map((shot) => (
            <section
              key={shot.shot_id}
              className="space-y-3 border-b border-border pb-4"
            >
              <h3 className="font-semibold">{shot.title}</h3>
              {shot.changes.map((change, i) => (
                <div key={i} className="space-y-1 text-sm">
                  <p className="text-muted-foreground">
                    {change.scene_title}
                    {change.scene_level &&
                      (zh ? " · 仅记录了整场来源" : " · Scene-level source")}
                  </p>
                  <p className="whitespace-pre-wrap">
                    <strong>{zh ? "原稿　" : "Before　"}</strong>
                    {change.before || "—"}
                  </p>
                  <p className="whitespace-pre-wrap">
                    <strong>{zh ? "现稿　" : "Now　"}</strong>
                    {change.after ||
                      (zh ? "已从剧本移除" : "Removed from script")}
                  </p>
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-4">
                <Link
                  className="studio-link text-sm"
                  to={`/projects/${projectId}/shots?shot=${shot.shot_id}`}
                >
                  {zh ? "查看镜头与画面" : "Open shot and media"}
                </Link>
                {canEdit && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={confirm.isPending}
                    onClick={() => confirm.mutate(shot)}
                  >
                    {zh
                      ? "保留当前镜头，标为已核对"
                      : "Keep shot and mark reviewed"}
                  </Button>
                )}
                {canEdit && shot.changes.some((c) => c.after) && (
                  <RevisePrompts
                    key={shot.revision}
                    path={path}
                    shot={shot}
                    done={() => {
                      void cache.invalidateQueries({ queryKey: key });
                      for (const name of [
                        "shot",
                        "all-shots",
                        "scene-shots",
                        "compose-prompt",
                      ])
                        void cache.invalidateQueries({ queryKey: [name] });
                    }}
                  />
                )}
              </div>
            </section>
          ))}
        </div>
      </Modal>
    </>
  );
}
