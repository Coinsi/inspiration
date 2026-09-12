import { cn } from "@/lib/utils";


export function Avatar({
  src,
  name,
  size = 40,
  className,
}: {
  src?: string | null;
  name?: string;
  size?: number;
  className?: string;
}) {
  const initial = (name || "?").slice(0, 1).toUpperCase();
  return (
    <div
      className={cn("rounded-full overflow-hidden border border-border shrink-0", className)}
      style={{ width: size, height: size }}
    >
      {src ? (
        <img src={src} alt={name} className="h-full w-full object-cover" />
      ) : (
        <div
          className={cn(
            "h-full w-full bg-elevated flex items-center justify-center font-medium text-foreground/80",
          )}
          style={{ fontSize: size * 0.4 }}
        >
          {initial}
        </div>
      )}
    </div>
  );
}
