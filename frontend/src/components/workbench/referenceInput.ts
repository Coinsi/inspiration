export type ReferenceRole = "reference" | "first_frame" | "last_frame";
export type ReferenceSource = "generation" | "media_version" | "attachment";
export type ReferenceInput = (
  | { generation_id: string; media_version_id?: never; attachment_id?: never }
  | { media_version_id: string; generation_id?: never; attachment_id?: never }
  | { attachment_id: string; generation_id?: never; media_version_id?: never }
) & { role: ReferenceRole };

export function referenceInput(
  item: { id: string; source_type?: ReferenceSource; role: ReferenceRole },
  role: ReferenceRole = item.role,
): ReferenceInput {
  return item.source_type === "attachment" ? { attachment_id: item.id, role } : item.source_type === "media_version"
    ? { media_version_id: item.id, role }
    : { generation_id: item.id, role };
}

export function referenceDetailPath(reference: ReferenceInput): string {
  return reference.attachment_id ? `/attachments/${reference.attachment_id}` : reference.media_version_id
    ? `/workbench/references/${reference.media_version_id}?source_type=media_version`
    : `/workbench/references/${reference.generation_id}`;
}

/** Retry payloads can carry local files and reused attachments in separate fields. */
export function retryAttachmentReferences(input: {
  attachments?: { attachment_id: string; role: ReferenceRole }[];
  references?: ReferenceInput[];
  media?: { references: ReferenceInput[] } | null;
}): { attachment_id: string; role: ReferenceRole }[] {
  const candidates = [
    ...(input.attachments || []),
    ...(input.media?.references || input.references || []).flatMap(reference =>
      reference.attachment_id ? [{ attachment_id: reference.attachment_id, role: reference.role }] : [],
    ),
  ];
  const seen = new Set<string>();
  return candidates.filter(reference => {
    if (seen.has(reference.attachment_id)) return false;
    seen.add(reference.attachment_id);
    return true;
  });
}
