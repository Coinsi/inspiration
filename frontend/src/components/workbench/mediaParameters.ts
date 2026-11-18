import type { ReferenceCapabilities } from "./referenceProblem";

export type MediaCapabilities = ReferenceCapabilities & {
  modalities: string[];
  param_schema: Record<string, { enum?: (string | number)[] }>;
  aspect_ratio_from_image?: boolean;
};

/** Derive visible controls and the submitted parameters without changing saved preferences. */
export function composerParameters(
  caps: MediaCapabilities | undefined,
  saved: Record<string, string | number> | undefined,
  references: ReadonlyArray<{ kind?: string }>,
  hidden: readonly string[] = [],
) {
  const schema = caps?.param_schema ?? {};
  const ratioFromImage = !!caps?.aspect_ratio_from_image &&
    references.some(ref => (ref.kind ?? "image") === "image");
  const controls: Record<string, string | number> = {};
  for (const [name, spec] of Object.entries(schema)) {
    if (!spec.enum?.length || hidden.includes(name) || (ratioFromImage && name === "aspect_ratio"))
      continue;
    const value = saved?.[name];
    controls[name] = value !== undefined && spec.enum.includes(value) ? value : spec.enum[0];
  }
  return { schema, controls, ratioFromImage };
}
