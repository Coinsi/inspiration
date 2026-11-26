/** The gallery API returns newest first. Number from the oldest so a new result keeps prior labels. */
export function generationLabels(results: readonly { id: string }[], zh: boolean) {
  return new Map(
    results.map((result, index) => [result.id, (zh ? "方案 " : "Version ") + (results.length - index)]),
  );
}
