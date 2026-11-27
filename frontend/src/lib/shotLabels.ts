import type { Shot } from "./api";

export function shotName(shot: Pick<Shot, "title" | "ordinal">, zh = true) {
  return (
    shot.title?.trim() || (zh ? `镜头 ${shot.ordinal}` : `Shot ${shot.ordinal}`)
  );
}

export function shotChoice(shot: Shot, zh = true) {
  const position = zh ? `第 ${shot.ordinal} 镜` : `Shot ${shot.ordinal}`;
  return {
    value: shot.id,
    label: shotName(shot, zh),
    selectedLabel: [shot.scene_title, position, shot.title]
      .filter(Boolean)
      .join(" · "),
    description: [
      shot.novel_title,
      shot.chapter_title,
      shot.scene_title,
      position,
    ]
      .filter(Boolean)
      .join(" · "),
    keywords: [shot.code, shot.description].join(" "),
  };
}

export function shotChoices(shots: Shot[], zh = true) {
  return shots.map((shot) => shotChoice(shot, zh));
}
