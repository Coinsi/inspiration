import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useI18n } from "@/lib/i18n";

export function MaterialTabs({ projectId }: { projectId: string }) {
  const zh = useI18n().lang === "zh";
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  const root = `/projects/${projectId}`;
  const trash =
    params.get("tab") === "trash" || pathname.endsWith("/assets/trash");
  const entities = !trash && pathname.includes("/assets");
  const returnTo = params.get("return");
  return (
    <nav
      aria-label={zh ? "素材导航" : "Materials"}
      className="flex flex-wrap gap-1 border-b pb-3"
    >
      {[
        {
          path: "materials",
          label: zh ? "全部素材" : "All materials",
          active: !trash && !entities,
        },
        {
          path: "assets",
          label: zh ? "角色与场景" : "Characters & scenes",
          active: entities,
        },
        {
          path: "materials?tab=trash",
          label: zh ? "回收站" : "Trash",
          active: trash,
        },
      ].map((item) => (
        <Link
          key={item.path}
          aria-current={item.active ? "page" : undefined}
          to={`${root}/${item.path}${returnTo?.startsWith(`${root}/`) ? `${item.path.includes("?") ? "&" : "?"}return=${encodeURIComponent(returnTo)}` : ""}`}
          className={`rounded-lg px-4 py-2 text-sm ${item.active ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:bg-elevated"}`}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
