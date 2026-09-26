export type FeatureKey = "story" | "canvas" | "library" | "edit";
export type SectionKey = "workflow" | "features" | "case" | "faq";
export interface SiteContent {
  brand: string;
  announcement: string;
  eyebrow: string;
  title: string;
  subtitle: string;
  cta: string;
  hero_media_id: string | null;
  workflow_title: string;
  workflow_description: string;
  features_title: string;
  features: {
    key: FeatureKey;
    title: string;
    description: string;
    media_id: string | null;
  }[];
  case_title: string;
  case_description: string;
  case_media_id: string | null;
  faq: { question: string; answer: string }[];
  closing_title: string;
  footer: string;
  sections: { key: SectionKey; enabled: boolean }[];
}
export interface SiteState {
  version: number;
  published_revision: number;
  draft: SiteContent;
  published: SiteContent;
  updated_at: string;
  media_types: Record<string, string>;
}
export interface SiteAsset {
  id: string;
  name: string;
  mime: string;
  bytes: number;
}
