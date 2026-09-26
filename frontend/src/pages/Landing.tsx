import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { LandingView } from "@/components/website/LandingView";
import type { SiteContent } from "@/components/website/types";

export default function Landing() {
  const { me } = useAuth();
  const site = useQuery({
    queryKey: ["public-website"],
    queryFn: () =>
      api.get<{
        revision: number;
        content: SiteContent;
        media_types: Record<string, string>;
      }>("/website"),
    staleTime: 0,
  });
  useEffect(() => {
    const title = document.title;
    if (site.data)
      document.title = `${site.data.content.brand} · 让灵感成为作品`;
    return () => {
      document.title = title;
    };
  }, [site.data]);
  if (site.isLoading)
    return (
      <div
        className="site-root"
        style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}
      >
        Inspiration · 正在准备创作现场…
      </div>
    );
  if (site.error || !site.data)
    return (
      <div
        className="site-root"
        style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}
      >
        <div role="alert">
          <p>官网内容暂时未能加载。</p>
          <button onClick={() => void site.refetch()}>重新加载</button>
        </div>
      </div>
    );
  return (
    <LandingView
      content={site.data.content}
      mediaTypes={site.data.media_types}
      authenticated={!!me}
    />
  );
}
