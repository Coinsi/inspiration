import type { QueryClient } from "@tanstack/react-query";
import type { AppliedScene } from "./api";
import type { Lang } from "./i18n";

export function sceneAdoptionMessage(scenes: AppliedScene[], failed: number, lang: Lang) {
  const added = scenes.reduce((total, scene) => total + scene.shots_added, 0);
  const filled = scenes.filter(scene => scene.shots_added > 0).length;
  const kept = scenes.filter(scene => scene.shots_kept > 0).length;
  const created = scenes.filter(scene => scene.scene_created).length;
  const zh = lang === "zh";
  const parts: string[] = [];
  const count = (value: number, noun: string) => `${value} ${noun}${value === 1 ? "" : "s"}`;
  if (added) {
    parts.push(zh
      ? `已将 ${added} 个镜头写入 ${filled} 个场次。`
      : `Added ${count(added, "shot")} across ${count(filled, "scene")}.`);
  } else if (created) {
    parts.push(zh ? `新建了 ${created} 个空场次。` : `Created ${count(created, "empty scene")}.`);
  } else {
    parts.push(zh ? "本次没有新增镜头。" : "No new shots.");
  }
  if (kept) parts.push(zh ? `${kept} 个场次保留了已有镜头。` : `Kept the existing shots in ${count(kept, "scene")}.`);
  if (failed) parts.push(zh ? `还有 ${failed} 场待重试。` : `Pending retry for ${count(failed, "scene")}.`);
  return parts.join(zh ? "" : " ");
}

export function refreshAppliedScenes(cache: QueryClient, projectId: string, scriptId: string) {
  const keys = [
    ["script-stats", scriptId],
    ["script-source-review", projectId, scriptId],
    ["scene-elements", projectId],
    ["gens", "shot"],
    ...["scenes-all", "scene-shots", "all-shots", "board", "material-shot-context", "assets", "asset-usages"]
      .map(key => [key, projectId]),
  ];
  return Promise.all(keys.map(queryKey => cache.invalidateQueries({ queryKey })));
}
