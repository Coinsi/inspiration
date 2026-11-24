import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useI18n } from "@/lib/i18n";
import "@/pages/materials.css";

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
      className="materials-sections"
    >
      {[
        {
          path: "materials",
          label: zh ? "素材文件" : "Media files",
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
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
