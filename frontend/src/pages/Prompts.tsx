import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { PromptLibrary, PROMPT_CATEGORIES } from "@/components/PromptLibrary";
import { api, ApiError, type Prompt, type Fragment } from "@/lib/api";

export default function Prompts() {
  const { projectId } = useParams();
  return <PromptWorkspace key={projectId} projectId={projectId!} />;
}
function PromptWorkspace({ projectId }: { projectId: string }) {
  const [editing, setEditing] = useState<Prompt | Fragment | "new" | null>(
      null,
    ),
    [kind, setKind] = useState("prompt");
  const [name, setName] = useState(""),
    [text, setText] = useState(""),
    [negative, setNegative] = useState(""),
    [category, setCategory] = useState("quality");
  const qc = useQueryClient(),
    toast = useToast(),
    confirm = useConfirm(),
    base = `/projects/${projectId}`;
  const [reloading, setReloading] = useState(false);
  const dirty =
    name !== (editing && editing !== "new" ? editing.name : "") ||
    text !==
      (editing && editing !== "new"
        ? "text" in editing
          ? editing.text
          : editing.positive
        : "") ||
    (editing &&
      editing !== "new" &&
      "text" in editing &&
      category !== editing.category) ||
    negative !==
      (editing && editing !== "new"
        ? "text" in editing
          ? ""
          : editing.negative
        : "");
  const save = useMutation({
    mutationFn: () =>
      editing && editing !== "new"
        ? "text" in editing
          ? api.patch(`${base}/prompt-fragments/${editing.id}`, {
              name: name.trim(),
              text,
              category,
              expected_updated_at: editing.updated_at,
            })
          : api.patch(`${base}/prompts/${editing.id}`, {
              name: name.trim(),
              positive: text,
              negative,
            })
        : kind === "prompt"
          ? api.post(`${base}/prompts`, {
              name: name.trim(),
              positive: text,
              negative,
            })
          : api.post(`${base}/prompt-fragments`, {
              name: name.trim(),
              text,
              category,
            }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["prompts", projectId] }),
        qc.invalidateQueries({ queryKey: ["fragments", projectId] }),
      ]);
      setEditing(null);
      toast.push("提示词已保存", "success");
    },
  });
  function open(p: Prompt | Fragment | "new") {
    save.reset();
    setEditing(p);
    setKind(p !== "new" && "text" in p ? "fragment" : "prompt");
    setCategory(p !== "new" && "text" in p ? p.category : "quality");
    setName(p === "new" ? "" : p.name);
    setText(p === "new" ? "" : "text" in p ? p.text : p.positive);
    setNegative(p === "new" || "text" in p ? "" : p.negative);
  }
  async function reloadFragment() {
    if (!editing || editing === "new" || !("text" in editing)) return;
    if (!(await confirm({ message: "读取最新片段会替换当前输入，继续吗？" })))
      return;
    setReloading(true);
    try {
      const latest = await api.get<Fragment[]>(`${base}/prompt-fragments`);
      const item = latest.find((f) => f.id === editing.id);
      if (!item)
        throw new Error("片段已被删除，当前输入仍然保留，可复制后另存。");
      qc.setQueryData(["fragments", projectId], latest);
      open(item);
    } catch (e) {
      toast.push((e as Error).message, "error");
    } finally {
      setReloading(false);
    }
  }
  const remove = useMutation({
    mutationFn: (f: Fragment) =>
      api.del(
        `${base}/prompt-fragments/${f.id}?expected_updated_at=${encodeURIComponent(f.updated_at)}`,
      ),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["fragments", projectId] });
      toast.push("片段已删除", "success");
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  async function close() {
    if (save.isPending || reloading) return;
    if (
      dirty &&
      !(await confirm({ message: "放弃这次尚未保存的提示词修改？" }))
    )
      return;
    setEditing(null);
  }
  return (
    <div className="max-w-6xl mx-auto p-5 md:p-8 space-y-7">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">提示词库</h1>
          <p className="text-sm text-muted-foreground mt-2">
            收藏好用的表达，让下一次创作更轻松。
          </p>
        </div>
        <Button onClick={() => open("new")}>
          <Plus size={16} />
          新建提示词
        </Button>
      </header>
      <PromptLibrary
        projectId={projectId}
        onEdit={open}
        onEditFragment={open}
        deleting={remove.isPending}
        onDeleteFragment={async (f) => {
          if (
            await confirm({
              message: `删除「${f.name}」？已有生成结果与已经插入的指令不受影响。`,
              danger: true,
            })
          )
            remove.mutate(f);
        }}
      />
      <Modal
        open={!!editing}
        onClose={() => void close()}
        title={editing === "new" ? "保存一条创作灵感" : "编辑提示词"}
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim() && text.trim() && !save.isPending && !reloading)
              save.mutate();
          }}
        >
          <fieldset
            disabled={save.isPending || reloading}
            className="space-y-4"
          >
            {editing === "new" && (
              <label className="block text-sm">
                保存为
                <select
                  aria-label="提示词类型"
                  className="block w-full mt-2 rounded-lg border bg-bg p-2"
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                >
                  <option value="prompt">完整提示词</option>
                  <option value="fragment">可组合的片段</option>
                </select>
              </label>
            )}
            {kind === "fragment" && (
              <label className="block text-sm">
                分类
                <select
                  aria-label="片段分类"
                  className="block w-full mt-2 rounded-lg border bg-bg p-2"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                >
                  {Object.entries(PROMPT_CATEGORIES).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="block text-sm">
              名称
              <Input
                aria-label="提示词名称"
                className="mt-2"
                maxLength={255}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：雨夜霓虹街景"
              />
            </label>
            <label className="block text-sm">
              内容
              <Textarea
                aria-label="提示词内容"
                className="mt-2"
                rows={7}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="记录画面、光线、镜头或风格描述…"
              />
            </label>
            {kind === "prompt" && (
              <details open={!!negative || undefined}>
                <summary className="text-sm text-muted-foreground cursor-pointer">
                  反向提示词（可选）
                </summary>
                <Textarea
                  aria-label="反向提示词"
                  className="mt-2"
                  rows={3}
                  value={negative}
                  onChange={(e) => setNegative(e.target.value)}
                />
              </details>
            )}
          </fieldset>
          {save.error && (
            <div role="alert" className="space-y-2 text-sm text-danger">
              <p>保存失败：{save.error.message}。当前输入已保留。</p>
              {save.error instanceof ApiError &&
                save.error.code === "VERSION_CONFLICT" &&
                editing &&
                editing !== "new" &&
                "text" in editing && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={reloading}
                    onClick={() => void reloadFragment()}
                  >
                    {reloading ? "正在读取…" : "重新读取最新片段"}
                  </Button>
                )}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={save.isPending || reloading}
              onClick={() => void close()}
            >
              取消
            </Button>
            <Button
              type="submit"
              disabled={
                !name.trim() || !text.trim() || save.isPending || reloading
              }
            >
              {save.isPending ? "正在保存…" : "保存提示词"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
