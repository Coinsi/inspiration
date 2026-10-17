import { StudioSelect } from "@/components/ui/studio-select";
import {
  NarrativeCover,
  NarrativeCoverEditor,
} from "@/components/NarrativeCover";
import { ScriptNameDialog } from "@/components/ScriptNameDialog";
import { SearchField } from "@/components/ui/search-field";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Clapperboard,
  FileText,
  Plus,
  ScrollText,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { api, type Script } from "@/lib/api";
import "./writing.css";

export default function Scripts() {
  const { projectId } = useParams();
  return <ScriptLibrary key={projectId} />;
}
function ScriptLibrary() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr, lang } = useI18n();
  const [query, setQuery] = useState("");
  const [nameDialog, setNameDialog] = useState<Script | "new" | null>(null);
  const [sort, setSort] = useState("newest");
  const scripts = useQuery({
    queryKey: ["scripts", projectId],
    queryFn: () => api.get<Script[]>(`${base}/scripts`),
  });
  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US");
  const del = useMutation({
    mutationFn: (id: string) => api.del(`${base}/scripts/${id}`),
    onSuccess: () => {
      toast.push(tr("nar.scriptDeleted"), "success");
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const visible = (scripts.data ?? [])
    .filter((s) =>
      `${s.title} ${s.code}`
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase()),
    )
    .sort((a, b) =>
      sort === "title"
        ? a.title.localeCompare(b.title, lang)
        : Date.parse(b.created_at) - Date.parse(a.created_at),
    );
  return (
    <div className="writing-library">
      {nameDialog && (
        <ScriptNameDialog
          projectId={projectId!}
          script={nameDialog === "new" ? undefined : nameDialog}
          onClose={() => setNameDialog(null)}
          onCreated={(s) => navigate(`${base}/scripts/${s.id}`)}
        />
      )}
      <header className="writing-library-header">
        <div>
          <span className="writing-eyebrow">STORY & SCREENPLAY</span>
          <h1>{tr("scripts.title")}</h1>
          <p>从一个故事，到每一场戏。让想法在这里成为剧本。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => navigate(`${base}/narrative`)}
          >
            <ScrollText size={16} />
            {tr("scripts.fromChapter")}
          </Button>
          <Button onClick={() => setNameDialog("new")}>
            <Plus size={16} />
            {tr("scripts.new")}
          </Button>
        </div>
      </header>
      <div className="writing-library-toolbar">
        <SearchField
          label="搜索剧本"
          placeholder="搜索剧本名称或编号"
          value={query}
          onValueChange={setQuery}
          className="w-full sm:w-72"
        />
        <StudioSelect
          aria-label="剧本排序"
          value={sort}
          onValueChange={(selectedValue) => setSort(selectedValue)}
        >
          <option value="newest">最近创建</option>
          <option value="title">按名称</option>
        </StudioSelect>
        <span className="writing-result-count">
          {scripts.data
            ? `${visible.length} / ${scripts.data.length} 部剧本`
            : ""}
        </span>
      </div>
      {scripts.isPending ? (
        <div className="writing-empty" role="status">
          正在读取剧本…
        </div>
      ) : scripts.isError ? (
        <div className="writing-empty" role="alert">
          <p>{scripts.error.message}</p>
          <Button variant="outline" onClick={() => void scripts.refetch()}>
            重新读取剧本
          </Button>
        </div>
      ) : visible.length ? (
        <div className="writing-script-grid narrative-cover-grid">
          {visible.map((s) => (
            <article className="writing-script-card" key={s.id}>
              <Link
                className="writing-script-link"
                to={`${base}/scripts/${s.id}`}
              >
                <NarrativeCover
                  projectId={projectId!}
                  kind="script"
                  title={s.title}
                  hash={s.cover_blob_hash}
                />
                <div className="narrative-card-title">
                  <h2 title={s.title}>{s.title || tr("nar.untitled")}</h2>
                </div>
                <div className="writing-script-meta">
                  <span>
                    <Clapperboard size={14} />
                    {s.scene_count ?? 0} 场戏
                  </span>
                  <span>{s.block_count ?? 0} 段正文</span>
                  <ArrowUpRight size={16} />
                </div>
                <span className="sr-only">打开剧本</span>
              </Link>
              <NarrativeCoverEditor
                projectId={projectId!}
                kind="script"
                id={s.id}
                title={s.title}
                hash={s.cover_blob_hash}
                actions={{
                  rename: () => setNameDialog(s),
                  removing: del.isPending,
                  remove: async () => {
                    if (
                      await confirm({
                        title: tr("nar.deleteScript"),
                        message: tr("nar.deleteScriptConfirm").replace(
                          "{name}",
                          s.title || tr("nar.untitled"),
                        ),
                        confirmText: tr("common.delete"),
                        danger: true,
                      })
                    )
                      del.mutate(s.id);
                  },
                }}
              />
              <footer>
                <span title="创建日期">{fmtTime(s.created_at)}</span>
              </footer>
            </article>
          ))}
        </div>
      ) : (
        <div className="writing-empty">
          <FileText size={36} strokeWidth={1} />
          <h2>{query ? "没有找到匹配的剧本" : "你的第一场戏，从这里开始"}</h2>
          <p>
            {query
              ? "换个名称试试，或清除搜索查看全部剧本。"
              : "新建一部剧本，或从已有小说章节开始改编。"}
          </p>
          <Button
            variant="outline"
            onClick={() => (query ? setQuery("") : setNameDialog("new"))}
          >
            {query ? "清除搜索" : tr("scripts.new")}
          </Button>
        </div>
      )}
    </div>
  );
}
