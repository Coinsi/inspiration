import { cn } from "@/lib/utils";

const GRADIENTS = [
  "from-teal-500/40 to-cyan-500/40",
  "from-violet-500/40 to-fuchsia-500/40",
  "from-amber-500/40 to-orange-500/40",
  "from-rose-500/40 to-pink-500/40",
  "from-sky-500/40 to-blue-500/40",
];

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
  const grad = GRADIENTS[(name?.charCodeAt(0) ?? 0) % GRADIENTS.length];
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
            "h-full w-full bg-gradient-to-br flex items-center justify-center font-medium text-foreground/80",
            grad,
          )}
          style={{ fontSize: size * 0.4 }}
        >
          {initial}
        </div>
      )}
    </div>
  );
}
