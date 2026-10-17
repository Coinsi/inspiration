import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Script } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";

// Mount on open so a canceled name never carries into another script.
export function ScriptNameDialog({
  projectId,
  script,
  onClose,
  onCreated,
}: {
  projectId: string;
  script?: Pick<Script, "id" | "title">;
  onClose: () => void;
  onCreated?: (script: Script) => void;
}) {
  const [name, setName] = useState(script?.title ?? "");
  const submitting = useRef(false);
  const composing = useRef(false);
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: (title: string) =>
      script
        ? api.patch<Script>(`/projects/${projectId}/scripts/${script.id}`, {
            title,
          })
        : api.post<Script>(`/projects/${projectId}/scripts`, { title }),
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: ["scripts", projectId] });
      if (script) {
        qc.setQueryData(
          ["script", script.id],
          (old: Record<string, unknown> | undefined) =>
            old ? { ...old, title: result.title } : old,
        );
        toast.push("剧本已重命名", "success");
      }
      onClose();
      if (!script) onCreated?.(result);
    },
    onSettled: () => {
      submitting.current = false;
    },
  });
  const title = name.trim();
  const valid = title.length > 0 && title.length <= 255;
  return (
    <Modal
      open
      title={script ? "重命名剧本" : "新建剧本"}
      width={460}
      onClose={() => {
        if (!submitting.current) onClose();
      }}
    >
      <form
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (composing.current || submitting.current || !valid) return;
          if (script && title === script.title) {
            onClose();
            return;
          }
          submitting.current = true;
          save.mutate(title);
        }}
      >
        <div className="space-y-2">
          <label htmlFor="script-name" className="block text-sm font-medium">
            剧本名称
          </label>
          <Input
            id="script-name"
            autoFocus
            data-autofocus
            value={name}
            maxLength={255}
            placeholder="例如：雨夜来信"
            disabled={save.isPending}
            onChange={(e) => setName(e.target.value)}
            onCompositionStart={() => {
              composing.current = true;
            }}
            onCompositionEnd={() => {
              composing.current = false;
            }}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                (composing.current || e.nativeEvent.isComposing)
              )
                e.preventDefault();
            }}
          />
          <p className="text-xs text-muted-foreground">
            {script
              ? "修改名称不会影响正文、场次或镜头。"
              : "先给故事起个名字，之后也可以随时修改。"}
          </p>
        </div>
        {save.error && (
          <p role="alert" className="text-sm text-danger">
            {save.error.message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={save.isPending}
            onClick={onClose}
          >
            取消
          </Button>
          <Button type="submit" disabled={!valid || save.isPending}>
            {save.isPending ? "保存中…" : script ? "保存名称" : "创建剧本"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
