export type Vec = [number, number, number];
export type StageObject = {
  id: string;
  name: string;
  kind: "person" | "box" | "sphere" | "wall";
  position: Vec;
  rotation: number;
  scale: number;
  color: string;
  pose: "standing" | "sitting";
};
export type StageCamera = { position: Vec; target: Vec; fov: number };
export type CameraKeyframe = {
  id: string;
  at: number;
  camera: StageCamera;
  hold: number;
  ease: "smooth" | "linear";
};
export type StageDocument = {
  objects: StageObject[];
  camera: StageCamera;
  end_camera: StageCamera;
  camera_keyframes?: CameraKeyframe[];
  camera_hold?: number;
  camera_ease?: "smooth" | "linear";
  lighting: {
    position: Vec;
    intensity: number;
    ambient: number;
    color: string;
  };
  background: string;
  ground: string;
  aspect: "16:9" | "9:16" | "1:1";
  duration: number;
};
export type Stage = {
  id: string;
  name: string;
  revision: number;
  document: StageDocument;
};
export const aspectRatio = (d: StageDocument) =>
  d.aspect === "16:9" ? 16 / 9 : d.aspect === "9:16" ? 9 / 16 : 1;

export function cameraPath(d: StageDocument): CameraKeyframe[] {
  return [
    {
      id: "camera",
      at: 0,
      camera: d.camera,
      hold: d.camera_hold ?? 0,
      ease: d.camera_ease ?? "smooth",
    },
    ...(d.camera_keyframes ?? []),
    { id: "end_camera", at: 1, camera: d.end_camera, hold: 0, ease: "smooth" },
  ];
}

/** Shared by live preview, PNG capture and every frame of video export. */
export function sampleCamera(d: StageDocument, progress: number): StageCamera {
  const path = cameraPath(d),
    t = Math.min(1, Math.max(0, progress));
  const index = path.findIndex(
    (_, i) => i < path.length - 1 && t < path[i + 1].at,
  );
  if (index < 0) return structuredClone(d.end_camera);
  const a = path[index],
    b = path[index + 1];
  const fraction = Math.max(
    0,
    Math.min(1, (t - a.at - a.hold) / Math.max(0.00001, b.at - a.at - a.hold)),
  );
  const s =
    a.ease === "linear" ? fraction : fraction * fraction * (3 - 2 * fraction);
  const mix = (x: number, y: number) => x + (y - x) * s;
  return {
    position: a.camera.position.map((v, i) =>
      mix(v, b.camera.position[i]),
    ) as Vec,
    target: a.camera.target.map((v, i) => mix(v, b.camera.target[i])) as Vec,
    fov: mix(a.camera.fov, b.camera.fov),
  };
}

export function patchCamera(
  d: StageDocument,
  id: string,
  camera: StageCamera,
): StageDocument {
  if (id === "camera" || id === "end_camera") return { ...d, [id]: camera };
  return {
    ...d,
    camera_keyframes: (d.camera_keyframes ?? []).map((k) =>
      k.id === id ? { ...k, camera } : k,
    ),
  };
}

export function insertionProgress(
  d: StageDocument,
  progress: number,
): number | null {
  if ((d.camera_keyframes?.length ?? 0) >= 10) return null;
  const path = cameraPath(d);
  const gaps = path
    .slice(0, -1)
    .map((k, i) => ({
      start: k.at + k.hold + 0.01,
      end: path[i + 1].at - 0.01,
    }))
    .filter((g) => g.end >= g.start);
  if (gaps.some((g) => progress >= g.start && progress <= g.end))
    return progress;
  gaps.sort((a, b) => b.end - b.start - (a.end - a.start));
  return gaps.length ? (gaps[0].start + gaps[0].end) / 2 : null;
}
export function person(name: string, x = 0): StageObject {
  return {
    id: crypto.randomUUID(),
    name,
    kind: "person",
    position: [x, 0, 0],
    rotation: 0,
    scale: 1,
    color: "#c4a574",
    pose: "standing",
  };
}
export function sceneTemplate(
  d: StageDocument,
  kind: "dialogue" | "interior",
): StageDocument {
  const a = person("人物 A", -1.1),
    b = person("人物 B", 1.1);
  a.rotation = 35;
  b.rotation = -35;
  b.color = "#709eae";
  return {
    ...d,
    camera_keyframes: [],
    camera_hold: 0,
    camera_ease: "smooth",
    objects:
      kind === "dialogue"
        ? [a, b]
        : [
            a,
            b,
            {
              ...person("背景墙"),
              kind: "wall",
              position: [0, 0, -2],
              color: "#9caaa2",
            },
            {
              ...person("桌子"),
              kind: "box",
              position: [0, 0.35, 0],
              scale: 0.9,
              color: "#86765e",
            },
          ],
    camera: { position: [0, 2.2, 7], target: [0, 1, 0], fov: 45 },
    end_camera: { position: [-2.2, 1.8, 4.8], target: [0, 1.2, 0], fov: 42 },
  };
}
