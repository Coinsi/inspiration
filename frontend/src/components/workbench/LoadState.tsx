import { type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/lib/i18n";

export function LoadState({
  loading,
  error,
  retry,
  children,
}: {
  loading: boolean;
  error: boolean;
  retry: () => void;
  children: ReactNode;
}) {
  const { lang } = useI18n();
  if (error)
    return (
      <div
        role="alert"
        className="flex min-h-32 flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-5 text-sm text-muted-foreground"
      >
        <p>
          {lang === "zh"
            ? "暂时无法加载，请重试。"
            : "Unable to load. Please try again."}
        </p>
        <button className="studio-link" onClick={retry}>
          <RefreshCw className="h-3.5 w-3.5" />
          {lang === "zh" ? "重新加载" : "Retry"}
        </button>
      </div>
    );
  if (loading)
    return (
      <div
        role="status"
        aria-label={lang === "zh" ? "加载中" : "Loading"}
        className="grid grid-cols-2 gap-4"
      >
        <Skeleton className="h-36 rounded-xl" />
        <Skeleton className="h-36 rounded-xl" />
      </div>
    );
  return children;
}
