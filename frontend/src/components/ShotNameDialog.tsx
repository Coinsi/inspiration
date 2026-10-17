import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Shot } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";

// Mount on open so a canceled name never carries into another shot.
export function ShotNameDialog({
  projectId,
  shot,
  onClose,
  onSaved,
}: {
  projectId: string;
  shot: Pick<Shot, "id" | "title">;
  onClose: () => void;
  onSaved?: (shot: Shot) => void;
}) {
  const [name, setName] = useState(shot?.title ?? "");
  const submitting = useRef(false);
  const composing = useRef(false);
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: (title: string) =>
      api.patch<Shot>(`/projects/${projectId}/shots/${shot.id}`, { title }),
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
      void qc.invalidateQueries({ queryKey: ["scene-shots", projectId] });
      if (shot) {
        qc.setQueryData(
          ["shot", shot.id],
          (old: Record<string, unknown> | undefined) =>
            old ? { ...old, title: result.title } : old,
        );
        toast.push("镜头已重命名", "success");
      }
      onClose();
      onSaved?.(result);
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
      title="重命名镜头"
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
          if (shot && title === shot.title) {
            onClose();
            return;
          }
          submitting.current = true;
          save.mutate(title);
        }}
      >
        <div className="space-y-2">
          <label htmlFor="shot-name" className="block text-sm font-medium">
            镜头名称
          </label>
          <Input
            id="shot-name"
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
            只修改名称，已有画面、生成记录与素材引用都会保留。
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
            {save.isPending ? "保存中…" : "保存名称"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
