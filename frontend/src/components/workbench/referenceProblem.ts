import type { ReferenceRole } from "./referenceInput";

export type ReferenceCapabilities = {
  features: string[];
  max_reference_images: number;
  max_reference_videos?: number;
  reference_unavailable_reason?: string | null;
};

type ReferenceItem = { role: string; kind?: "image" | "video" | "audio" };

export type ReferenceSelection = {
  mode: string;
  capabilities?: ReferenceCapabilities;
  capabilitiesLoading?: boolean;
  capabilitiesError?: string | null;
  references: ReferenceItem[];
};

export function referenceProblem(
  caps: ReferenceCapabilities | undefined,
  references: ReferenceItem[],
): string {
  if (!caps || !references.length) return "";
  if (references.some(r => r.kind === "audio")) return "当前生成模型不支持音频参考。可切回讨论，或移除音频后生成。";
  const images = references.filter(r => r.kind !== "video");
  const videos = references.filter(r => r.kind === "video");
  if (videos.length && (!caps.features.includes("video_reference") || !caps.max_reference_videos))
    return caps.reference_unavailable_reason || "当前模型未启用参考视频，请更换支持视频参考的模型或移除视频。";
  if (videos.length > (caps.max_reference_videos || 0))
    return `当前模型最多支持 ${caps.max_reference_videos} 个参考视频，已选 ${videos.length} 个。`;
  if (images.length && caps.max_reference_images === 0)
    return caps.reference_unavailable_reason ||
      "当前模型未启用参考图输入，请检查模型的参考图上限及渠道能力，或移除参考图。";
  if (images.length > caps.max_reference_images)
    return `当前模型最多支持 ${caps.max_reference_images} 张参考图，已选 ${images.length} 张，请减少参考图。`;
  if (videos.some(r => r.role !== "reference")) return "参考视频不能作为首帧或尾帧。";
  const unsupported = references.find(
    (r) => r.role !== "reference" && !caps.features.includes(r.role),
  );
  if (unsupported)
    return `当前模型不支持${unsupported.role === "last_frame" ? "尾帧" : "首帧"}输入，请调整参考用途或更换模型。`;
  if (["first_frame", "last_frame"].some(role => images.filter(item => item.role === role).length > 1))
    return "首帧和尾帧各只能选一张，请调整参考用途或移除重复的帧。";
  if (images.some(r => r.role === "reference") && !caps.features.some(feature => feature === "reference" || feature === "img2img"))
    return caps.reference_unavailable_reason || "当前模型未启用普通参考图，请调整为支持的首尾帧用途，或更换模型。";
  return "";
}

function capabilityProblem(selection: ReferenceSelection): string {
  if (selection.mode === "text") return "";
  if (selection.capabilitiesLoading) return "正在读取当前模型的参考能力，请稍候…";
  if (selection.capabilitiesError) return "当前模型的参考能力读取失败，暂不能添加参考。请重试或更换模型。";
  if (!selection.capabilities) return "尚未取得当前模型的参考能力，请先选择可用模型。";
  return "";
}

/** Check a new item against every selected source, while allowing existing items to be removed. */
export function referencePickState(selection: ReferenceSelection, candidate: ReferenceItem): { role: ReferenceRole; problem: string } {
  const preferredRole = (selection.mode === "video" ? candidate.role : "reference") as ReferenceRole;
  if (selection.references.length >= 8) return { role: preferredRole, problem: "每次最多添加 8 项内容，请先移除已选内容。" };
  if (selection.mode === "text") return { role: "reference", problem: "" };
  const unavailable = capabilityProblem(selection);
  if (unavailable) return { role: preferredRole, problem: unavailable };
  const caps = selection.capabilities!;
  const roles: ReferenceRole[] = [preferredRole, "reference"];
  if (selection.mode === "video" && candidate.kind !== "video" && candidate.kind !== "audio") {
    const frames: ReferenceRole[] = ["first_frame", "last_frame"];
    // Prefer an unused frame slot when the model only accepts frame inputs.
    roles.push(...frames.filter(role => !selection.references.some(item => item.role === role)), ...frames);
  }
  const supportedRoles = roles.filter(value => !referenceProblem(caps, [{ ...candidate, role: value }]));
  const role = supportedRoles.find(value => value === "reference" || !selection.references.some(item => item.role === value));
  if (!role) return {
    role: preferredRole,
    problem: supportedRoles.length ? "首帧和尾帧各只能选一张，请先调整参考用途或移除已选帧。" : referenceProblem(caps, [{ ...candidate, role: preferredRole }]),
  };
  const sameKind = selection.references.filter(item => (item.kind || "image") === (candidate.kind || "image"));
  // Count existing inputs without rewriting their roles. Submission validates the entire
  // selection; old incompatible inputs remain removable/correctable in the composer.
  const limit = candidate.kind === "video" ? (caps.max_reference_videos || 0) : caps.max_reference_images;
  const unit = candidate.kind === "video" ? "个参考视频" : "张参考图";
  return { role, problem: sameKind.length >= limit ? `当前模型最多支持 ${limit} ${unit}，本次已添加 ${sameKind.length} 项，请先移除已选内容。` : "" };
}

export function referenceSelectionHint(selection: ReferenceSelection): string {
  if (selection.mode === "text") return "可添加图片、视频和音频，本次合计最多 8 项。";
  const unavailable = capabilityProblem(selection);
  if (unavailable) return unavailable;
  const caps = selection.capabilities!;
  const imageRoles = [
    ...(caps.features.some(feature => feature === "reference" || feature === "img2img") ? ["参考图"] : []),
    ...(selection.mode === "video" && caps.features.includes("first_frame") ? ["首帧"] : []),
    ...(selection.mode === "video" && caps.features.includes("last_frame") ? ["尾帧"] : []),
  ];
  const images = caps.max_reference_images > 0 && imageRoles.length ? `图片最多 ${caps.max_reference_images} 张（${imageRoles.join("、")}）` : "不支持图片参考";
  const videos = caps.features.includes("video_reference") && caps.max_reference_videos ? `视频最多 ${caps.max_reference_videos} 个` : "不支持视频参考";
  return `${images}；${videos}；不支持音频参考。本次合计最多 8 项。`;
}
