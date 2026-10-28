type ReferenceCapabilities = {
  features: string[];
  max_reference_images: number;
  max_reference_videos?: number;
  reference_unavailable_reason?: string | null;
};

export function referenceProblem(
  caps: ReferenceCapabilities | undefined,
  references: { role: string; kind?: "image" | "video" | "audio" }[],
): string {
  if (!caps || !references.length) return "";
  if (references.some(r => r.kind === "audio")) return "当前生成模型不支持音频参考。可切回讨论，或移除音频后生成。";
  const images = references.filter(r => r.kind !== "video");
  const videos = references.filter(r => r.kind === "video");
  if (videos.length && (!caps.features.includes("video_reference") || !caps.max_reference_videos))
    return caps.reference_unavailable_reason || "当前模型未启用参考视频，请更换支持视频参考的模型或移除视频。";
  if (videos.length > (caps.max_reference_videos || 0))
    return `当前模型最多支持 ${caps.max_reference_videos} 个参考视频，已选 ${videos.length} 个。`;
  if (images.length && (!caps.features.includes("reference") || caps.max_reference_images === 0))
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
  return "";
}
