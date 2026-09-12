// 剧本库:全项目剧本列表(一级菜单),点击进入剧本编辑器。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, FileText, Plus, ScrollText, Trash2 } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { api, type Script } from "@/lib/api";

export default function Scripts() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr, lang } = useI18n();

  const { data: scripts } = useQuery({
    queryKey: ["scripts", projectId],
    queryFn: () => api.get<Script[]>(`${base}/scripts`),
  });

  const fmtTime = (iso: string) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  };

  const create = useMutation({
    mutationFn: () => api.post<Script>(`${base}/scripts`, { title: tr("scripts.untitled") }),
    onSuccess: (s) => {
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
      navigate(`${base}/scripts/${s.id}`);
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`${base}/scripts/${id}`),
    onSuccess: () => {
      toast.push(tr("nar.scriptDeleted"), "success");
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  return (
    <div className="mx-auto max-w-[1200px] p-6">
      <div className="mb-5 flex flex-wrap gap-3 items-center justify-between">
        <div className="flex items-center gap-2">
          <FileText className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-xl font-semibold leading-tight">{tr("scripts.title")}</h1>
            <p className="text-sm text-muted-foreground">{tr("scripts.subtitle")}（{scripts?.length ?? 0}）</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => navigate(`${base}/narrative`)}>
            <ScrollText className="mr-1 h-4 w-4" /> {tr("scripts.fromChapter")}
          </Button>
          <Button size="sm" onClick={() => create.mutate()} disabled={create.isPending}>
            <Plus className="mr-1 h-4 w-4" /> {tr("scripts.new")}
          </Button>
        </div>
      </div>

      {scripts && scripts.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {scripts.map((s) => (
            <button
              key={s.id}
              onClick={() => navigate(`${base}/scripts/${s.id}`)}
              className="group flex flex-col rounded-xl border border-border bg-card p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/60 "
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-semibold">{s.title || tr("nar.untitled")}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-faint">
                    <span className="font-code">{s.code}</span>
                    <span>· {fmtTime(s.created_at)}</span>
                  </div>
                </div>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={async (e) => {
                    e.stopPropagation();
                    const ok = await confirm({
                      title: tr("nar.deleteScript"),
                      message: tr("nar.deleteScriptConfirm").replace("{name}", s.title || tr("nar.untitled")),
                      confirmText: tr("common.delete"),
                      danger: true,
                    });
                    if (ok) del.mutate(s.id);
                  }}
                  title={tr("nar.deleteScript")}
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-faint opacity-0 transition hover:bg-danger/15 hover:text-danger group-hover:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </span>
              </div>
              <div className="mt-3 flex items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-elevated/60 px-2.5 py-1 text-xs text-muted-foreground">
                  <Clapperboard className="h-3.5 w-3.5" /> {s.scene_count ?? 0} {tr("scripts.sceneUnit")}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-elevated/60 px-2.5 py-1 text-xs text-muted-foreground">
                  <FileText className="h-3.5 w-3.5" /> {s.block_count ?? 0} {tr("se.blocks")}
                </span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="py-20 text-center text-muted-foreground">
          <FileText className="mx-auto mb-3 h-10 w-10 opacity-50" />
          <p className="text-sm">{tr("scripts.empty")}</p>
        </div>
      )}
    </div>
  );
}
