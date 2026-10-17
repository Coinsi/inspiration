import { SelectField } from "./ui/select-field";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Script } from "@/lib/api";
import { Modal } from "./ui/modal";
import { Button } from "./ui/button";

export type Work = { id: string; title: string; script_id: string };
export function WorkPicker({
  projectId,
  value,
  onChange,
  disabled = false,
}: {
  projectId: string;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [script, setScript] = useState("");
  const works = useQuery({
    queryKey: ["works", projectId],
    queryFn: () => api.get<Work[]>(`${base}/works`),
  });
  const scripts = useQuery({
    queryKey: ["scripts", projectId],
    queryFn: () => api.get<Script[]>(`${base}/scripts`),
    enabled: open,
  });
  const chosen = works.data?.find((w) => w.id === value);
  const create = useMutation({
    mutationFn: () =>
      api.post<Work>(`${base}/works`, {
        title: title.trim(),
        script_id: script || null,
      }),
    onSuccess: async (w) => {
      await qc.invalidateQueries({ queryKey: ["works", projectId] });
      await qc.invalidateQueries({ queryKey: ["scripts", projectId] });
      onChange(w.id);
      setOpen(false);
      setTitle("");
      setScript("");
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label className="flex items-center gap-2 text-muted-foreground">
        作品
        <SelectField
          label="所属作品"
          className="max-w-64"
          value={value}
          disabled={disabled || works.isPending || works.isError}
          onChange={onChange}
          options={[
            { value: "", label: "自由探索 · 暂不关联作品" },
            ...(value && !chosen
              ? [{ value, label: "已关联作品（暂不可用）" }]
              : []),
            ...(works.data || []).map((w) => ({ value: w.id, label: w.title })),
          ]}
        />
      </label>
      <Button
        variant="ghost"
        size="sm"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        新建作品
      </Button>
      {chosen && (
        <Link
          className="text-primary"
          to={`${base}/scripts/${chosen.script_id}`}
        >
          查看作品内容 ↗
        </Link>
      )}
      {works.isError && (
        <p role="alert">
          无法读取作品 <button onClick={() => works.refetch()}>重试</button>
        </p>
      )}
      <Modal
        open={open}
        onClose={() => !create.isPending && setOpen(false)}
        title="新建作品"
        width={480}
      >
        <form
          className="space-y-4 p-5"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <p className="text-sm text-muted-foreground">
            同一作品可以关联多段对话。讨论和候选素材会保留，采用后才成为作品的画面。
          </p>
          <label className="block space-y-2">
            作品名称
            <input
              autoFocus
              required
              maxLength={120}
              className="w-full rounded-lg border bg-background p-3"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="block space-y-2">
            创作起点
            <SelectField
              label="创作起点"
              className="w-full"
              value={script}
              onChange={setScript}
              disabled={create.isPending || scripts.isPending}
              options={[
                { value: "", label: "从空白开始" },
                ...(scripts.data || [])
                  .filter((s) => !works.data?.some((w) => w.script_id === s.id))
                  .map((s) => ({
                    value: s.id,
                    label: `使用已有剧本：${s.title}`,
                  })),
              ]}
            />
          </label>
          {scripts.isError && (
            <p role="alert">
              已有剧本读取失败{" "}
              <button type="button" onClick={() => scripts.refetch()}>
                重试
              </button>
            </p>
          )}
          {create.error && <p role="alert">{create.error.message}</p>}
          <Button type="submit" disabled={!title.trim() || create.isPending}>
            {create.isPending ? "创建中…" : "创建作品"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
