// Infer transport from a recognized service host, never from a model's brand name.
// Unknown services start with the documented OpenAI-compatible convention.
export function connectionProtocol(endpoint: string, modality: string) {
  let host = "";
  try { host = new URL(endpoint).hostname.toLowerCase(); } catch { /* incomplete form */ }
  if (host === "ark.cn-beijing.volces.com")
    return { text: "cloud_llm", image: "ark_image", video: "ark_video" }[modality] || "";
  if (["api.minimax.io", "api.minimaxi.com"].includes(host) && modality === "video")
    return "minimax_video";
  return { text: "cloud_llm", image: "gpt_image", video: "openai_video" }[modality] || "";
}
