import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Languages, Moon, Plus, Sun } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError, type Project } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { useToast } from "@/components/ui/toast";

const COVERS = [
  "from-teal-600/50 to-cyan-700/40",
  "from-violet-600/50 to-fuchsia-700/40",
  "from-amber-600/50 to-orange-700/40",
  "from-rose-600/50 to-pink-700/40",
  "from-sky-600/50 to-blue-700/40",
  "from-emerald-600/50 to-green-700/40",
];

export default function Projects() {
  const qc = useQueryClient();
  const { me, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const { t: tr, lang, setLang } = useI18n();
  const toast = useToast();
  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });

  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<Project>("/projects", { code, name, settings: {} }),
    onSuccess: () => {
      setCode("");
      setName("");
      setOpen(false);
      toast.push(tr("toast.projectCreated"), "success");
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => toast.push(e instanceof ApiError ? e.message : tr("toast.createFailed"), "error"),
  });

  return (
    <div className="min-h-screen bg-transparent">
      <header className="h-14 border-b border-border bg-surface/60 backdrop-blur-xl flex items-center justify-between px-6">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-gradient-primary text-primary-foreground shadow-glow-sm">
            <Clapperboard className="h-[18px] w-[18px]" />
          </span>
          <span className="font-semibold tracking-tight">Inspiration</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">{me?.user.display_name}</span>
          <button onClick={() => setLang(lang === "zh" ? "en" : "zh")} className="h-8 w-8 rounded-md hover:bg-elevated flex items-center justify-center text-muted-foreground" title={tr("shell.language")}>
            <Languages className="h-4 w-4" />
          </button>
          <button onClick={toggle} className="h-8 w-8 rounded-md hover:bg-elevated flex items-center justify-center text-muted-foreground" title={tr("shell.theme")}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <Button variant="outline" size="sm" onClick={logout}>
            {tr("proj.logout")}
          </Button>
        </div>
      </header>

      <div className="max-w-6xl mx-auto p-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold">{tr("proj.title")}</h1>
            <p className="text-sm text-muted-foreground">{tr("proj.subtitle")}</p>
          </div>
          <Button onClick={() => setOpen((o) => !o)}>
            <Plus className="h-4 w-4 mr-1" /> {tr("proj.new")}
          </Button>
        </div>

        {open && (
          <div className="mb-6 flex gap-2 items-center rounded-lg border border-border bg-card p-3">
            <Input
              placeholder={tr("proj.code")}
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              className="w-48"
            />
            <Input placeholder={tr("proj.name")} value={name} onChange={(e) => setName(e.target.value)} />
            <Button onClick={() => create.mutate()} disabled={!code || !name || create.isPending}>
              {tr("common.create")}
            </Button>
          </div>
        )}

        {isLoading ? (
          <p className="text-muted-foreground text-sm">{tr("common.loading")}</p>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            {projects?.map((p, i) => (
              <Link
                key={p.id}
                to={`/projects/${p.id}/workbench`}
                className="group rounded-xl border border-border bg-card overflow-hidden hover:border-primary/60 transition-all hover:-translate-y-0.5 hover:shadow-glow-sm"
              >
                <div className={`h-28 bg-gradient-to-br ${COVERS[i % COVERS.length]} flex items-center justify-center`}>
                  <span className="text-3xl font-bold text-foreground/70">{p.name.slice(0, 1)}</span>
                </div>
                <div className="p-4">
                  <div className="font-medium">{p.name}</div>
                  <div className="text-xs text-muted-foreground font-code mt-0.5">{p.code}</div>
                </div>
              </Link>
            ))}
            {projects?.length === 0 && (
              <p className="text-muted-foreground text-sm">{tr("proj.empty")}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
