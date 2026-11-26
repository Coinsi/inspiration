export function sceneLabel(scene: { ordinal: number; title?: string | null }, zh: boolean) {
  const ordinal = zh ? `第 ${scene.ordinal} 场` : `Scene ${scene.ordinal}`;
  let title = scene.title?.trim();
  if (title && zh) {
    title = title
      .replace(/^INT\.\s*\/\s*EXT\.\s*/i, "内外景 · ")
      .replace(/^INT\.\s*/i, "内景 · ")
      .replace(/^EXT\.\s*/i, "外景 · ");
  }
  return title ? ordinal + " · " + title : ordinal;
}
