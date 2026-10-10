import type { ProviderConfig } from "./api";

export function defaultCreationProvider(
  providers: ProviderConfig[] | undefined,
  modality: string,
) {
  return (
    providers?.find(
      (p) =>
        p.enabled &&
        p.kind === "generation" &&
        !p.config.hidden_from_catalog &&
        p.config.is_default &&
        p.config.modality === modality,
    )?.provider_name || "mock"
  );
}

export function creationProviderLabel(
  providers: ProviderConfig[] | undefined,
  name: string,
) {
  if (name === "mock") return "离线测试";
  const model = providers?.find((p) => p.provider_name === name);
  return (
    model?.display_name ||
    (name.startsWith("creative_") ? "创作模型（配置不可用）" : name)
  );
}
