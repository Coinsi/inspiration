export type ReferenceRole = "reference" | "first_frame" | "last_frame";
export type ReferenceSource = "generation" | "media_version";
export type ReferenceInput = (
  | { generation_id: string; media_version_id?: never }
  | { media_version_id: string; generation_id?: never }
) & { role: ReferenceRole };

export function referenceInput(
  item: { id: string; source_type?: ReferenceSource; role: ReferenceRole },
  role: ReferenceRole = item.role,
): ReferenceInput {
  return item.source_type === "media_version"
    ? { media_version_id: item.id, role }
    : { generation_id: item.id, role };
}

export function referenceDetailPath(reference: ReferenceInput): string {
  return reference.media_version_id
    ? `/workbench/references/${reference.media_version_id}?source_type=media_version`
    : `/workbench/references/${reference.generation_id}`;
}
