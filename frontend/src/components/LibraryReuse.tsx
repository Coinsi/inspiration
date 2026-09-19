import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  LibraryVersionPicker,
  useLibraryVersion,
} from "@/components/LibraryVersionPicker";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

export function LibraryReuse({
  projectId,
  folderId,
  onClose,
  onDone,
}: {
  projectId: string;
  folderId?: string | null;
  onClose: () => void;
  onDone: (result: { media_id: string; version_id: string }) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [sourceProject, setSourceProject] = useState(""),
    [version, setVersion] = useState("");
  const projects = useQuery({
    queryKey: ["library-reuse-projects", projectId],
    queryFn: () =>
      api.get<{ id: string; name: string }[]>(
        `/projects/${projectId}/library/reuse-projects`,
      ),
  });
  const selected = useLibraryVersion(sourceProject, version);
  const reuse = useMutation({
    mutationFn: () =>
      api.post<{ media_id: string; version_id: string }>(
        `/projects/${projectId}/library/reuse`,
        {
          source_project_id: sourceProject,
          source_version_id: version,
          folder_id: folderId,
        },
      ),
    onSuccess: onDone,
  });
  return (
    <Modal
      open
      title={zh ? "从其他项目复用视频" : "Reuse video from another project"}
      onClose={() => {
        if (!reuse.isPending) onClose();
      }}
      width={620}
    >
      <div className="space-y-4">
        <p className="text-sm leading-6 text-muted-foreground">
          {zh
            ? "将固定版本加入当前项目，供本项目成员使用。原片与预览文件共用，无需重复上传；源视频更新不会替换本项目的版本。"
            : "Add a fixed version for this project's members. Original and preview files are shared without uploading again. Later source changes never replace this version."}
        </p>
        {projects.isPending ? (
          <p>{zh ? "读取可用项目…" : "Loading projects…"}</p>
        ) : projects.isError ? (
          <div role="alert">
            <p className="text-sm text-danger">{projects.error.message}</p>
            <Button variant="outline" onClick={() => void projects.refetch()}>
              {zh ? "重试" : "Retry"}
            </Button>
          </div>
        ) : !projects.data?.length ? (
          <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            {zh
              ? "暂无可复用的其他项目，需要同时拥有源项目与当前项目的素材编辑权限。"
              : "No other eligible projects. Asset editing permission is required in both projects."}
          </p>
        ) : (
          <label className="block space-y-2 text-xs">
            <span>{zh ? "来源项目" : "Source project"}</span>
            <select
              aria-label={zh ? "来源项目" : "Source project"}
              className="w-full rounded-lg border bg-background p-2 text-sm"
              value={sourceProject}
              disabled={reuse.isPending}
              onChange={(e) => {
                setSourceProject(e.target.value);
                setVersion("");
                reuse.reset();
              }}
            >
              <option value="">
                {zh ? "选择来源项目" : "Choose source project"}
              </option>
              {projects.data.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {sourceProject && (
          <LibraryVersionPicker
            key={sourceProject}
            projectId={sourceProject}
            value={version}
            label={zh ? "复用视频版本" : "Version to reuse"}
            disabled={reuse.isPending}
            onChange={(id) => {
              setVersion(id);
              reuse.reset();
            }}
          />
        )}
        <p className="text-xs leading-6 text-muted-foreground">
          {zh
            ? "复用保留来源版本；字幕、内容索引与人物判断由各项目分别管理。重复加入会打开已有素材。"
            : "Source provenance is retained. Transcripts, indexes and identity evidence stay project-specific. Adding again opens the existing item."}
        </p>
        {reuse.error && (
          <p role="alert" className="text-sm text-danger">
            {reuse.error.message}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" disabled={reuse.isPending} onClick={onClose}>
            {zh ? "取消" : "Cancel"}
          </Button>
          <Button
            disabled={
              !sourceProject ||
              !version ||
              selected.isError ||
              selected.data?.status !== "ready" ||
              reuse.isPending
            }
            onClick={() => reuse.mutate()}
          >
            {reuse.isPending
              ? zh
                ? "加入中…"
                : "Adding…"
              : zh
                ? "加入当前项目"
                : "Add to this project"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
