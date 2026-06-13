import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/lib/i18n";
import { api, type Fragment, type Prompt } from "@/lib/api";

export default function Prompts() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr } = useI18n();

  const { data: prompts } = useQuery({
    queryKey: ["prompts", projectId],
    queryFn: () => api.get<Prompt[]>(`${base}/prompts`),
  });
  const { data: fragments } = useQuery({
    queryKey: ["fragments", projectId],
    queryFn: () => api.get<Fragment[]>(`${base}/prompt-fragments`),
  });

  const [pName, setPName] = useState("");
  const [pPos, setPPos] = useState("");
  const createPrompt = useMutation({
    mutationFn: () => api.post(`${base}/prompts`, { name: pName, positive: pPos }),
    onSuccess: () => {
      setPName("");
      setPPos("");
      void qc.invalidateQueries({ queryKey: ["prompts", projectId] });
    },
  });

  const [fName, setFName] = useState("");
  const [fText, setFText] = useState("");
  const [fCat, setFCat] = useState("quality");
  const createFragment = useMutation({
    mutationFn: () => api.post(`${base}/prompt-fragments`, { name: fName, text: fText, category: fCat }),
    onSuccess: () => {
      setFName("");
      setFText("");
      void qc.invalidateQueries({ queryKey: ["fragments", projectId] });
    },
  });

  return (
    <div className="max-w-6xl mx-auto p-6 grid grid-cols-1 md:grid-cols-2 gap-5">
        <Card>
          <CardHeader>
            <CardTitle>{tr("prompts.title")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-2">
              <Input placeholder={tr("prompts.namePh")} value={pName} onChange={(e) => setPName(e.target.value)} />
              <Textarea
                placeholder={tr("prompts.posPh")}
                value={pPos}
                onChange={(e) => setPPos(e.target.value)}
                rows={5}
              />
              <Button size="sm" onClick={() => createPrompt.mutate()} disabled={!pName}>
                {tr("prompts.new")}
              </Button>
            </div>
            <div className="space-y-2 pt-2 border-t border-border">
              {prompts?.map((p) => (
                <div key={p.id} className="rounded-md border border-border bg-card p-2.5">
                  <div className="text-sm font-medium">
                    <span className="font-code text-xs text-muted-foreground">{p.code}</span> {p.name}
                  </div>
                  {p.positive && (
                    <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
                      {p.positive}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{tr("prompts.fragLib")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-2">
              <div className="flex gap-2">
                <select
                  className="h-9 rounded-md border border-border px-2 text-sm"
                  value={fCat}
                  onChange={(e) => setFCat(e.target.value)}
                >
                  {["quality", "camera", "lighting", "style", "custom"].map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
                <Input placeholder={tr("prompts.fragNamePh")} value={fName} onChange={(e) => setFName(e.target.value)} />
              </div>
              <Textarea
                placeholder={tr("prompts.fragTextPh")}
                value={fText}
                onChange={(e) => setFText(e.target.value)}
                rows={4}
              />
              <Button size="sm" onClick={() => createFragment.mutate()} disabled={!fName}>
                {tr("prompts.newFrag")}
              </Button>
            </div>
            <div className="space-y-2 pt-2 border-t border-border">
              {fragments?.map((f) => (
                <div key={f.id} className="rounded-md border border-border bg-card p-2.5 text-sm">
                  <div>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-muted mr-1">{f.category}</span>
                    {f.name}
                  </div>
                  {f.text && (
                    <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
                      {f.text}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
    </div>
  );
}
