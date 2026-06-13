import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n";
import { api, ApiError, type ProviderConfig, type Quota } from "@/lib/api";

function Note({ ok, msg }: { ok: boolean; msg: string }) {
  if (!msg) return null;
  return <p className={`text-sm ${ok ? "text-success" : "text-danger"}`}>{msg}</p>;
}

export default function Settings() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const { t: tr } = useI18n();

  const { data: providers } = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${base}/providers`),
  });
  const { data: quota } = useQuery({
    queryKey: ["quota", projectId],
    queryFn: () => api.get<Quota | null>(`${base}/quota`),
  });

  // ── 生成供应商 ──
  const [gName, setGName] = useState("jimeng");
  const [gEndpoint, setGEndpoint] = useState("");
  const [gToken, setGToken] = useState("");
  const [gModel, setGModel] = useState("gpt-image-1");
  const [gQuality, setGQuality] = useState("");
  const [gMsg, setGMsg] = useState({ ok: false, msg: "" });
  // 切到 GPT Image 时带出默认端点与已存模型/质量
  useEffect(() => {
    if (gName === "gpt_image") {
      const existing = providers?.find((p) => p.provider_name === "gpt_image");
      setGEndpoint((existing?.endpoint as string) ?? "https://api.openai.com/v1");
      setGModel(((existing?.config?.model as string) || "gpt-image-1"));
      setGQuality(((existing?.config?.quality as string) || ""));
    }
  }, [gName, providers]);
  const gConfig = () =>
    gName === "gpt_image" ? { model: gModel, ...(gQuality ? { quality: gQuality } : {}) } : {};
  const saveGen = useMutation({
    mutationFn: () =>
      api.put(`${base}/providers`, {
        provider_name: gName,
        kind: "generation",
        enabled: true,
        endpoint: gEndpoint || null,
        token: gToken || null,
        config: gConfig(),
      }),
    onSuccess: () => {
      setGToken("");
      setGMsg({ ok: true, msg: tr("set.savedEnc") });
      void qc.invalidateQueries({ queryKey: ["providers", projectId] });
    },
    onError: (e) => setGMsg({ ok: false, msg: e instanceof ApiError ? e.message : tr("set.saveFail") }),
  });
  const testGen = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; message: string }>(`${base}/providers/test`, {
        provider_name: gName,
        kind: "generation",
        endpoint: gEndpoint || null,
        token: gToken || null,
        config: gConfig(),
      }),
    onSuccess: (r) => setGMsg({ ok: r.ok, msg: r.message }),
    onError: (e) => setGMsg({ ok: false, msg: e instanceof ApiError ? e.message : tr("set.saveFail") }),
  });

  // ── AI 拆解 LLM ──
  const llm = providers?.find((p) => p.kind === "llm");
  const [lBase, setLBase] = useState("https://api.openai.com/v1");
  const [lModel, setLModel] = useState("gpt-4o-mini");
  const [lToken, setLToken] = useState("");
  const [lMsg, setLMsg] = useState({ ok: false, msg: "" });
  useEffect(() => {
    if (llm) {
      setLBase(llm.endpoint ?? "https://api.openai.com/v1");
      setLModel((llm.config?.model as string) ?? "gpt-4o-mini");
    }
  }, [llm]);
  const saveLlm = useMutation({
    mutationFn: () =>
      api.put(`${base}/providers`, {
        provider_name: "cloud_llm",
        kind: "llm",
        enabled: true,
        endpoint: lBase,
        token: lToken || null,
        config: { model: lModel },
      }),
    onSuccess: () => {
      setLToken("");
      setLMsg({ ok: true, msg: tr("set.llmSaved") });
      void qc.invalidateQueries({ queryKey: ["providers", projectId] });
    },
    onError: (e) => setLMsg({ ok: false, msg: e instanceof ApiError ? e.message : tr("set.saveFail") }),
  });
  const testLlm = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; message: string }>(`${base}/providers/test`, {
        provider_name: "cloud_llm",
        kind: "llm",
        endpoint: lBase,
        token: lToken || null,
        config: { model: lModel },
      }),
    onSuccess: (r) => setLMsg({ ok: r.ok, msg: r.message }),
    onError: (e) => setLMsg({ ok: false, msg: e instanceof ApiError ? e.message : tr("set.saveFail") }),
  });

  // ── 配额 ──
  const [limit, setLimit] = useState("");
  const [qMsg, setQMsg] = useState({ ok: false, msg: "" });
  useEffect(() => {
    if (quota) setLimit(String(quota.limit_cost));
  }, [quota]);
  const saveQuota = useMutation({
    mutationFn: () => api.put(`${base}/quota`, { scope: "project", limit_cost: Number(limit) }),
    onSuccess: () => {
      setQMsg({ ok: true, msg: tr("set.quotaSaved") });
      void qc.invalidateQueries({ queryKey: ["quota", projectId] });
    },
    onError: (e) => setQMsg({ ok: false, msg: e instanceof ApiError ? e.message : tr("set.quotaSaveFail") }),
  });

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-5">
      <p className="text-sm text-muted-foreground">{tr("set.hint")}</p>

      {/* 生成供应商 */}
      <Card>
        <CardHeader>
          <CardTitle>{tr("set.genProvider")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2 flex-wrap items-center">
            <select
              className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
              value={gName}
              onChange={(e) => setGName(e.target.value)}
            >
              <option value="jimeng">即梦 Jimeng</option>
              <option value="gpt_image">GPT Image{tr("set.gptImageNote")}</option>
              <option value="mock">{tr("set.mockOpt")}</option>
            </select>
            <Input
              placeholder={tr("set.endpointPh")}
              value={gEndpoint}
              onChange={(e) => setGEndpoint(e.target.value)}
              className="flex-1 min-w-[260px]"
            />
            {gName === "gpt_image" && (
              <>
                <Input
                  placeholder={tr("set.gptModelPh")}
                  value={gModel}
                  onChange={(e) => setGModel(e.target.value)}
                  className="w-44"
                />
                <select
                  className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
                  value={gQuality}
                  onChange={(e) => setGQuality(e.target.value)}
                  title={tr("set.gptQualityHint")}
                >
                  <option value="">{tr("set.gptQualityAuto")}</option>
                  <option value="low">{tr("set.gptQualityLow")}</option>
                  <option value="medium">{tr("set.gptQualityMedium")}</option>
                  <option value="high">{tr("set.gptQualityHigh")}</option>
                </select>
              </>
            )}
          </div>
          <Input
            type="password"
            placeholder="API Key / Token"
            value={gToken}
            onChange={(e) => setGToken(e.target.value)}
          />
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={() => saveGen.mutate()}>
              {tr("set.saveProvider")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => testGen.mutate()} disabled={testGen.isPending}>
              {testGen.isPending ? tr("set.testing") : tr("set.test")}
            </Button>
            <Note ok={gMsg.ok} msg={gMsg.msg} />
          </div>
          <div className="text-xs text-muted-foreground">
            {tr("set.configured")}
            {providers?.filter((p) => p.kind === "generation").map((p) => (
              <span key={p.id} className="ml-2">
                {p.provider_name}
                {p.enabled ? tr("set.enabled") : tr("set.disabled")}
              </span>
            )) || tr("set.none")}
          </div>
        </CardContent>
      </Card>

      {/* AI 拆解 LLM */}
      <Card>
        <CardHeader>
          <CardTitle>{tr("set.llm")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input
            placeholder={tr("set.baseUrlPh")}
            value={lBase}
            onChange={(e) => setLBase(e.target.value)}
          />
          <div className="flex gap-2">
            <Input placeholder={tr("set.modelPh")} value={lModel} onChange={(e) => setLModel(e.target.value)} />
            <Input
              type="password"
              placeholder={llm ? tr("set.apiKeyKeep") : "API Key"}
              value={lToken}
              onChange={(e) => setLToken(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={() => saveLlm.mutate()}>
              {tr("set.saveLlm")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => testLlm.mutate()} disabled={testLlm.isPending}>
              {testLlm.isPending ? tr("set.testing") : tr("set.test")}
            </Button>
            <Note ok={lMsg.ok} msg={lMsg.msg} />
          </div>
          <div className="text-xs text-muted-foreground">
            {llm
              ? tr("set.llmCurrent").replace("{ep}", llm.endpoint ?? "").replace("{model}", (llm.config?.model as string) ?? "")
              : tr("set.llmNone")}
          </div>
        </CardContent>
      </Card>

      {/* 配额 */}
      <Card>
        <CardHeader>
          <CardTitle>{tr("set.quota")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2 items-center">
            <Input
              type="number"
              placeholder={tr("set.quotaLimitPh")}
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              className="w-48"
            />
            <Button size="sm" onClick={() => saveQuota.mutate()}>
              {tr("set.saveQuota")}
            </Button>
            <Note ok={qMsg.ok} msg={qMsg.msg} />
          </div>
          {quota && (
            <div className="text-xs text-muted-foreground">
              {tr("set.quotaUsed").replace("{used}", String(quota.used_cost)).replace("{limit}", String(quota.limit_cost))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
