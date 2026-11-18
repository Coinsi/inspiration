export type ModelListItem = {
  id: string;
  name?: string;
  outputs?: string[];
  note?: string;
  source?: "documentation";
};

export type ModelDirectoryResult = {
  models: string[];
  details?: ModelListItem[];
  documented_models?: ModelListItem[];
  documentation_url?: string | null;
  truncated: boolean;
};

export function directoryEntries(result: ModelDirectoryResult): ModelListItem[] {
  const reported = result.details ?? result.models.map((id) => ({ id }));
  const documented = (result.documented_models ?? []).map(
    (item): ModelListItem => ({ ...item, source: "documentation" }),
  );
  return [...reported, ...documented];
}

// Infer transport from a recognized service host, never from a model's brand name.
// Unknown services start with the documented OpenAI-compatible convention.
export function connectionProtocol(
  endpoint: string,
  modality: string,
  model = "",
  preferred?: string,
) {
  let host = "";
  let officialMiniMaxVideo = false;
  try {
    const url = new URL(endpoint);
    host = url.hostname.toLowerCase();
    officialMiniMaxVideo =
      ["api.minimax.cn", "api.minimax.io", "api.minimaxi.com"].includes(host) &&
      url.protocol === "https:" && !url.port && /^\/(v[12]\/?)?$/.test(url.pathname);
  } catch { /* incomplete form */ }
  const nativeMiniMax =
    preferred === "minimax_video" || (officialMiniMaxVideo && (!preferred || preferred === "openai_video"));
  if (nativeMiniMax && modality === "video" && /^MiniMax-H3(?:-Highspeed|-Lite|-Max)?$/.test(model.trim()))
    return "minimax_h3_video";
  if (preferred) return preferred;
  if (host === "ark.cn-beijing.volces.com")
    return { text: "cloud_llm", image: "ark_image", video: "ark_video" }[modality] || "";
  if (["api.minimax.cn", "api.minimax.io", "api.minimaxi.com"].includes(host))
    return { text: "cloud_llm", image: "minimax_image", video: "minimax_video" }[modality] || "";
  return { text: "cloud_llm", image: "gpt_image", video: "openai_video" }[modality] || "";
}
