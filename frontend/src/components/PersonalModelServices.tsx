import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Catalog } from "./ModelChannels";
import type { CreationMode } from "./workbench/ConversationMedia";
import { ProviderWorkspace } from "./ProviderWorkspace";
import { Button } from "./ui/button";
import "./personal-model-services.css";

type Props = { projectId?: string; inProjectSettings?: boolean; initialPurpose?: CreationMode };

export function PersonalModelServices({
  projectId,
  inProjectSettings = false,
  initialPurpose,
}: Props) {
  const { me } = useAuth();
  const canImport =
    projectId &&
    me?.memberships.some(
      (m) => m.project_id === projectId && m.role === "admin",
    );
  return (
    <section className="personal-services" aria-label="我的模型服务">
      <header className="personal-scope">
        <KeyRound size={16} aria-hidden="true" />
        <p>
          个人连接仅你可用，所有项目共用。试生成使用你的密钥，结果保存在所选项目。
        </p>
      </header>
      {canImport && me && (
        <ProjectImports projectId={projectId} userId={me.user.id} />
      )}
      <ProviderWorkspace
        personal
        projectId={projectId}
        syncPurpose={inProjectSettings}
        initialPurpose={initialPurpose}
      />
    </section>
  );
}

function ProjectImports({
  projectId,
  userId,
}: {
  projectId: string;
  userId: string;
}) {
  const qc = useQueryClient();
  const [message, setMessage] = useState("");
  const project = useQuery({
    queryKey: ["model-channels", projectId],
    queryFn: () => api.get<Catalog>(`/projects/${projectId}/model-channels`),
  });
  const personal = useQuery({
    queryKey: ["personal-model-channels", userId],
    queryFn: () => api.get<Catalog>("/me/model-channels"),
  });
  const adopt = useMutation({
    mutationFn: (id: string) =>
      api.post(
        `/projects/${projectId}/model-channels/${id}/use-personally`,
        {},
      ),
    onSuccess: async () => {
      await Promise.all(
        [
          "personal-model-channels",
          "personal-model-services",
          "providers",
          "capabilities",
        ].map((key) => qc.invalidateQueries({ queryKey: [key] })),
      );
      setMessage("已存为个人连接，可在其他项目选用。");
    },
  });
  const eligible = personal.data
    ? (project.data?.channels.filter(
        (source) =>
          source.enabled &&
          source.has_token &&
          source.models.some((m) => m.enabled) &&
          !personal.data.channels.some(
            (mine) =>
              mine.preset_id === "custom-" + source.id.replace(/-/g, "") ||
              (mine.preset_id &&
                personal.data?.suggestions?.[mine.preset_id] &&
                mine.endpoint === source.endpoint),
          ),
      ) ?? [])
    : [];
  return (
    <>
      {eligible.length > 0 && (
        <details className="personal-imports">
          <summary>复用当前项目的连接</summary>
          <div>
            {eligible.map((channel) => (
              <Button
                key={channel.id}
                size="sm"
                variant="outline"
                disabled={adopt.isPending}
                onClick={() => {
                  setMessage("");
                  adopt.mutate(channel.id);
                }}
              >
                复用 {channel.name}
              </Button>
            ))}
          </div>
        </details>
      )}
      {adopt.error && (
        <p role="alert" className="provider-feedback">
          {adopt.error.message}
        </p>
      )}
      {message && (
        <p role="status" className="provider-feedback">
          {message}
        </p>
      )}
    </>
  );
}
