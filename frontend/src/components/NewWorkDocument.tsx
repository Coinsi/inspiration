import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "./ui/button";
import { SelectField } from "./ui/select-field";
import { DocumentChangeReview } from "./WorkPackage";

export function NewWorkDocument({
  projectId,
  workId,
}: {
  projectId: string;
  workId: string;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("image.document");
  const [candidate, setCandidate] = useState("");
  const cache = useQueryClient();
  const path = `/projects/${projectId}/works/${workId}/engineering`;
  const create = useMutation({
    mutationFn: async () => {
      if (!name.trim()) throw new Error("请填写文档名称");
      const head = await api.get<{ revision: number }>(path);
      const canvas = {
        width: 1280,
        height: 720,
        color_space: "srgb",
        alpha: "opaque",
      };
      const payload =
        kind === "audio.document"
          ? { sample_rate: 48000, channels: 2, tracks: [] }
          : kind === "composition.document"
            ? {
                canvas,
                layers: [],
                duration: { numerator: 5, denominator: 1 },
                frame_rate: { numerator: 24, denominator: 1 },
              }
            : { canvas, layers: [] };
      return api.post<{ id: string }>(`${path}/changes`, {
        request_key: crypto.randomUUID(),
        base_revision: head.revision,
        commands: [
          {
            operation: "document.put",
            document_id: crypto.randomUUID(),
            expected_revision: 0,
            kind,
            schema_version: 1,
            title: name.trim(),
            inputs: {},
            payload,
          },
        ],
      });
    },
    onSuccess: (result) => {
      setCandidate(result.id);
      setOpen(false);
      setName("");
      void cache.invalidateQueries();
    },
  });
  return (
    <div className="space-y-3">
      <Button variant="outline" onClick={() => setOpen(!open)}>
        新建工程文档
      </Button>
      {open && (
        <form
          className="flex flex-wrap items-end gap-3 rounded-xl border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <label className="space-y-1 text-sm">
            <span>文档名称</span>
            <input
              aria-label="文档名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={255}
              className="block rounded-lg border bg-transparent px-3 py-2"
              placeholder="例如：片头合成"
              required
            />
          </label>
          <SelectField
            label="文档类型"
            value={kind}
            onChange={setKind}
            options={[
              { value: "image.document", label: "图像画布" },
              { value: "audio.document", label: "音频工程" },
              { value: "composition.document", label: "动态合成" },
            ]}
          />
          <Button type="submit" disabled={create.isPending || !name.trim()}>
            {create.isPending ? "正在创建…" : "创建文档候选"}
          </Button>
          <p className="w-full text-xs text-muted-foreground">
            新建空白可编辑结构，采用后成为作品输出；不会调用模型或生成媒体。
          </p>
        </form>
      )}
      {create.error && (
        <p role="alert" className="text-sm text-danger">
          {create.error.message}
        </p>
      )}
      {candidate && (
        <DocumentChangeReview
          key={candidate}
          projectId={projectId}
          workId={workId}
          changeId={candidate}
        />
      )}
    </div>
  );
}
