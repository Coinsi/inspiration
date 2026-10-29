import { validateAttachmentFile } from "./attachmentUpload";
import { referencePickState, type ReferenceSelection } from "./referenceProblem";
import type { ReferenceRole } from "./referenceInput";
import type { MediaType } from "@/lib/library";

/** Admit supported files individually and reserve their count/role within the same batch. */
export function preflightAttachmentFiles(files: FileList | File[], selection: ReferenceSelection) {
  const accepted: { file: File; kind: MediaType; role: ReferenceRole }[] = [];
  const errors: string[] = [];
  const references = [...selection.references];
  for (const file of Array.from(files)) {
    try {
      const kind = validateAttachmentFile(file);
      const state = referencePickState({ ...selection, references }, { kind, role: "reference" });
      if (state.problem) {
        errors.push(`${file.name}：${state.problem}`);
        continue;
      }
      accepted.push({ file, kind, role: state.role });
      references.push({ kind, role: state.role });
    } catch (error) {
      errors.push(`${file.name}：${error instanceof Error ? error.message : "无法读取此文件。"}`);
    }
  }
  return { accepted, errors };
}
