type CatalogEntry = {
  id: string;
  name: string;
  code: string;
  summary?: string | null;
  tags?: string[];
};

/** One set of labels for suggestions, selected references and the searchable catalog. */
export function assetChoices<T extends CatalogEntry>(assets: T[], zh = true) {
  const entries = assets.map((asset) => {
    const description = asset.summary?.trim() || asset.tags?.join(" · ") || "";
    const hint =
      description.length > 24 ? description.slice(0, 24) + "…" : description;
    return { asset, description, hint };
  });
  const byName = new Map<string, typeof entries>();
  for (const entry of entries) {
    const group = byName.get(entry.asset.name);
    if (group) group.push(entry);
    else byName.set(entry.asset.name, [entry]);
  }
  const ordinalLabels = new Map<string, string>();
  for (const group of byName.values()) {
    if (group.length < 2) continue;
    const hintCounts = new Map<string, number>();
    for (const { hint } of group)
      hintCounts.set(hint, (hintCounts.get(hint) ?? 0) + 1);
    // Only indistinguishable records need a number. Keep it stable across search and sorting.
    group.sort(
      (a, b) =>
        a.asset.code.localeCompare(b.asset.code) ||
        a.asset.id.localeCompare(b.asset.id),
    );
    group.forEach(({ asset, hint }, i) => {
      if (!hint || hintCounts.get(hint)! > 1) {
        ordinalLabels.set(
          asset.id,
          zh
            ? `${asset.name}（同名素材 ${i + 1}）`
            : `${asset.name} (match ${i + 1})`,
        );
      }
    });
  }
  return entries.map(({ asset, description, hint }) => {
    const label = ordinalLabels.get(asset.id) ?? asset.name;
    const selectedLabel =
      byName.get(asset.name)!.length > 1 && !ordinalLabels.has(asset.id)
        ? `${asset.name} · ${hint}`
        : label;
    return {
      asset,
      value: asset.id,
      label,
      selectedLabel,
      description:
        description.length > 72 ? description.slice(0, 72) + "…" : description,
      keywords: [asset.code, asset.summary, ...(asset.tags ?? [])].join(" "),
    };
  });
}
