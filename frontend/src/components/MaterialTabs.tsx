import { NavLink } from "react-router-dom";
import { useI18n } from "@/lib/i18n";
/** Shared material destinations; entity references and original videos keep their own data model. */
export function MaterialTabs({ projectId }: { projectId: string }) {
  const zh = useI18n().lang === "zh";
  return (
    <nav
      aria-label={zh ? "素材类型" : "Material types"}
      className="flex gap-1 border-b pb-3"
    >
      {[
        ["assets", zh ? "角色与场景" : "Characters & scenes"],
        ["library", zh ? "视频原片" : "Video footage"],
      ].map(([path, label]) => (
        <NavLink
          key={path}
          to={`/projects/${projectId}/${path}`}
          className={({ isActive }) =>
            `rounded-lg px-4 py-2 text-sm ${isActive ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:bg-elevated"}`
          }
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
