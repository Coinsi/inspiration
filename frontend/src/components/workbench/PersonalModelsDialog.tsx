import { lazy, Suspense } from "react";
import type { CreationMode } from "./ConversationMedia";
import { Modal } from "@/components/ui/modal";

const Services = lazy(() =>
  import("@/components/PersonalModelServices").then((module) => ({
    default: module.PersonalModelServices,
  })),
);

type Props = { projectId: string; purpose: CreationMode; onClose: () => void };

export function PersonalModelsDialog({ projectId, purpose, onClose }: Props) {
  return (
    <Modal open onClose={onClose} title="我的模型服务" width={1080}>
      <Suspense fallback={<p role="status">正在读取模型服务…</p>}>
        <Services projectId={projectId} initialPurpose={purpose} />
      </Suspense>
    </Modal>
  );
}
