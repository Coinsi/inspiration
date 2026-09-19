import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Check, History, Plus, Search, Users, X } from "lucide-react";
import { api, blobUrl, type Asset } from "@/lib/api";
import { mediaTime } from "@/lib/library";
import {
  LibraryVersionPicker,
  useLibraryVersion,
} from "@/components/LibraryVersionPicker";
import { Button } from "@/components/ui/button";
import { MediaSegmentPlayer } from "@/components/MediaSegmentPlayer";
import { MediaImage } from "@/components/MediaImage";
import { useConfirm } from "@/components/ui/confirm";
type Identity = {
  id: string;
  name: string;
  aliases: string[];
  description: string;
  asset_id: string | null;
  revision: number;
};
type Evidence = {
  id: string;
  identity_id: string | null;
  version_id: string;
  start_ms: number;
  end_ms: number;
  kind: "visual" | "speech";
  status: "proposed" | "confirmed" | "rejected";
  observation: string;
  note: string;
  revision: number;
  name: string;
  ordinal: number;
  proxy_hash: string;
  poster_hash: string;
};
type Draft = Pick<
  Evidence,
  "identity_id" | "kind" | "status" | "observation" | "note"
>;
type Candidate = {
  id: string;
  version_id: string;
  name: string;
  start_ms: number;
  end_ms: number;
  thumbnail_hash: string;
  score: number;
};
const field =
  "w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";
const stateName = {
  proposed: "待核对",
  confirmed: "已确认",
  rejected: "已否定",
};
const blank: Draft = {
  identity_id: null,
  kind: "visual",
  status: "proposed",
  observation: "",
  note: "",
};
export default function EvidencePage() {
  const { projectId } = useParams();
  return <Workspace key={projectId} projectId={projectId!} />;
}
function Workspace({ projectId }: { projectId: string }) {
  const root = `/projects/${projectId}`,
    base = root + "/identity-evidence",
    qc = useQueryClient(),
    confirm = useConfirm(),
    [params] = useSearchParams();
  const [identityFilter, setIdentityFilter] = useState(""),
    [second, setSecond] = useState(""),
    [status, setStatus] = useState("confirmed"),
    [kind, setKind] = useState(""),
    [offset, setOffset] = useState(0),
    [identityOffset, setIdentityOffset] = useState(0),
    [identityQuery, setIdentityQuery] = useState("");
  const [identityDraft, setIdentityDraft] = useState<Partial<Identity> | null>(
      null,
    ),
    [editing, setEditing] = useState<Evidence | null>(null),
    [draft, setDraft] = useState<Draft>(blank),
    [form, setForm] = useState(!!params.get("version")),
    [version, setVersion] = useState(params.get("version") ?? ""),
    [start, setStart] = useState(
      String(Number(params.get("start") ?? 0) / 1000),
    ),
    [end, setEnd] = useState(String(Number(params.get("end") ?? 1000) / 1000));
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [selected, setSelected] = useState<Evidence | null>(null),
    [historyOpen, setHistoryOpen] = useState(false),
    [historyOffset, setHistoryOffset] = useState(0),
    [search, setSearch] = useState(""),
    [candidates, setCandidates] = useState<Candidate[]>([]),
    [searched, setSearched] = useState(false);
  const identities = useQuery({
    queryKey: ["identities", projectId, identityQuery, identityOffset],
    queryFn: () =>
      api.get<Identity[]>(
        `${base}/identities?q=${encodeURIComponent(identityQuery)}&offset=${identityOffset}`,
      ),
  });
  const selectedVersion = useLibraryVersion(projectId, version);
  const assets = useQuery({
    queryKey: ["assets", projectId],
    queryFn: () => api.get<Asset[]>(root + "/assets"),
  });
  const query = new URLSearchParams({ offset: String(offset) });
  if (identityFilter === "unknown") query.set("unknown", "true");
  else if (identityFilter) query.set("identity_id", identityFilter);
  if (status) query.set("status", status);
  if (kind) query.set("kind", kind);
  if (second) query.set("with_identity", second);
  const evidence = useQuery({
    queryKey: ["identity-evidence", projectId, query.toString()],
    queryFn: () =>
      api.get<{ total: number; items: Evidence[]; relation: string | null }>(
        `${base}?${query}`,
      ),
  });
  const history = useQuery({
    queryKey: ["evidence-history", selected?.id, historyOffset],
    queryFn: () =>
      api.get<{ revision: number; document: Evidence; created_at: string }[]>(
        `${base}/${selected!.id}/history?offset=${historyOffset}`,
      ),
    enabled: !!selected && historyOpen,
  });
  const v = selectedVersion.isError ? undefined : selectedVersion.data,
    startMs = Math.round(Number(start) * 1000),
    endMs = Math.round(Number(end) * 1000),
    valid =
      Number.isFinite(startMs) &&
      Number.isFinite(endMs) &&
      startMs >= 0 &&
      endMs - startMs >= 100 &&
      endMs <= (v?.duration_ms ?? 0);
  const identityName = (id: string | null) =>
    id
      ? (identities.data?.find((i) => i.id === id)?.name ??
        "角色身份（可搜索名称）")
      : "未知身份";
  async function perform(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
      await qc.invalidateQueries({
        queryKey: ["identity-evidence", projectId],
      });
      await qc.invalidateQueries({ queryKey: ["evidence-history"] });
      await qc.invalidateQueries({ queryKey: ["library-impact", projectId] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function setFilter(fn: () => void) {
    fn();
    setOffset(0);
    setSelected(null);
    setHistoryOpen(false);
  }
  function edit(e: Evidence) {
    setEditing(e);
    setDraft({
      identity_id: e.identity_id,
      kind: e.kind,
      status: e.status,
      observation: e.observation,
      note: e.note,
    });
    setVersion(e.version_id);
    setStart(String(e.start_ms / 1000));
    setEnd(String(e.end_ms / 1000));
    setForm(true);
  }
  async function decision(e: Evidence, next: Evidence["status"]) {
    const result = await api.put<Evidence>(`${base}/${e.id}`, {
      revision: e.revision,
      identity_id: e.identity_id,
      kind: e.kind,
      status: next,
      observation: e.observation,
      note: e.note,
    });
    if (selected?.id === e.id) setSelected({ ...e, ...result });
    setMessage(
      next === "confirmed"
        ? "已确认这条判断，现已进入角色证据检索。"
        : "已否定这条判断，旧记录保留在历史中。",
    );
  }
  return (
    <div className="studio-page space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="mb-2 text-[10px] tracking-[.2em] text-primary">
            CHARACTERS & EVIDENCE
          </p>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Users className="h-6 w-6" />
            身份与证据
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            用原片中的观察建立角色线索。先核对时间段，再确认身份；更正后，检索会使用最新判断，旧判断仍可追溯。
          </p>
        </div>
        <Link to={root + "/library"}>
          <Button variant="outline">返回视频素材</Button>
        </Link>
      </header>
      {error && (
        <div
          role="alert"
          className="rounded-xl border border-destructive/40 bg-destructive/10 p-3 text-sm"
        >
          {error}
        </div>
      )}
      {message && (
        <p role="status" className="text-sm text-primary">
          {message}
        </p>
      )}
      <div className="grid gap-5 xl:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="space-y-4 rounded-xl border border-border bg-card p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">角色身份</h2>
            <Button
              variant="ghost"
              aria-label="新建角色身份"
              disabled={busy}
              onClick={() =>
                setIdentityDraft({
                  name: "",
                  aliases: [],
                  description: "",
                  asset_id: null,
                })
              }
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <input
            aria-label="搜索角色身份"
            className={field}
            value={identityQuery}
            placeholder="按名称查找角色"
            onChange={(e) => {
              setIdentityQuery(e.target.value);
              setIdentityOffset(0);
            }}
          />
          <button
            className={`w-full rounded-lg px-3 py-2 text-left text-sm ${identityFilter === "" ? "bg-primary/15 text-primary" : "hover:bg-muted"}`}
            onClick={() =>
              setFilter(() => {
                setIdentityFilter("");
                setSecond("");
              })
            }
          >
            全部身份
          </button>
          <button
            className={`w-full rounded-lg px-3 py-2 text-left text-sm ${identityFilter === "unknown" ? "bg-primary/15 text-primary" : "hover:bg-muted"}`}
            onClick={() =>
              setFilter(() => {
                setIdentityFilter("unknown");
                setSecond("");
              })
            }
          >
            未知身份 · 待辨认
          </button>
          {identities.isError ? (
            <Button onClick={() => void identities.refetch()}>
              重新加载身份
            </Button>
          ) : (
            identities.data?.map((i) => (
              <div
                key={i.id}
                className={`rounded-lg border p-2 ${identityFilter === i.id ? "border-primary bg-primary/10" : "border-transparent"}`}
              >
                <button
                  className="w-full text-left"
                  onClick={() =>
                    setFilter(() => {
                      setIdentityFilter(i.id);
                      setSecond("");
                    })
                  }
                >
                  <strong className="block text-sm">{i.name}</strong>
                  <span className="text-xs text-muted-foreground">
                    {i.aliases.join(" / ") || "尚无别名"}
                  </span>
                </button>
                <button
                  className="mt-1 text-xs text-muted-foreground underline"
                  disabled={busy}
                  onClick={() => setIdentityDraft(i)}
                >
                  编辑身份资料
                </button>
              </div>
            ))
          )}
          <div className="flex gap-2">
            {identityOffset > 0 && (
              <Button
                variant="ghost"
                onClick={() => setIdentityOffset(identityOffset - 100)}
              >
                上一页
              </Button>
            )}
            {identities.data?.length === 100 && (
              <Button
                variant="ghost"
                onClick={() => setIdentityOffset(identityOffset + 100)}
              >
                下一页
              </Button>
            )}
          </div>
          <p className="border-t border-border pt-3 text-xs leading-6 text-muted-foreground">
            身份表示故事中的同一角色；衣着、发型和年龄等外观变化保存在每条观察中。人物被台词提及，不等于出现在画面里。
          </p>
        </aside>
        <section className="min-w-0 space-y-5">
          {identityDraft && (
            <section className="space-y-3 rounded-xl border border-primary/30 bg-card p-4">
              <h2 className="font-medium">
                {identityDraft.id ? "编辑角色身份" : "建立角色身份"}
              </h2>
              <fieldset disabled={busy} className="grid gap-3 md:grid-cols-2">
                <label className="space-y-1 text-xs">
                  角色姓名
                  <input
                    aria-label="角色姓名"
                    className={field}
                    value={identityDraft.name ?? ""}
                    maxLength={120}
                    onChange={(e) =>
                      setIdentityDraft({
                        ...identityDraft,
                        name: e.target.value,
                      })
                    }
                  />
                </label>
                <label className="space-y-1 text-xs">
                  别名（逗号分隔）
                  <input
                    aria-label="角色别名"
                    className={field}
                    value={(identityDraft.aliases ?? []).join(",")}
                    onChange={(e) =>
                      setIdentityDraft({
                        ...identityDraft,
                        aliases: e.target.value.split(/[,，]/).slice(0, 20),
                      })
                    }
                  />
                </label>
                <label className="space-y-1 text-xs md:col-span-2">
                  身份说明
                  <textarea
                    aria-label="身份说明"
                    className={field}
                    value={identityDraft.description ?? ""}
                    maxLength={4000}
                    onChange={(e) =>
                      setIdentityDraft({
                        ...identityDraft,
                        description: e.target.value,
                      })
                    }
                  />
                </label>
                <label className="space-y-1 text-xs">
                  关联创作角色资产
                  <select
                    aria-label="关联角色资产"
                    className={field}
                    value={identityDraft.asset_id ?? ""}
                    onChange={(e) =>
                      setIdentityDraft({
                        ...identityDraft,
                        asset_id: e.target.value || null,
                      })
                    }
                  >
                    <option value="">暂不关联</option>
                    {assets.data
                      ?.filter((a) => a.type === "character")
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                  </select>
                </label>
              </fieldset>
              <div className="flex gap-2">
                <Button
                  disabled={busy || !identityDraft.name?.trim()}
                  onClick={() =>
                    void perform(async () => {
                      const { id, revision, ...body } = identityDraft;
                      body.aliases = body.aliases
                        ?.map((s) => s.trim())
                        .filter(Boolean);
                      const result = id
                        ? await api.put<Identity>(`${base}/identities/${id}`, {
                            ...body,
                            revision,
                          })
                        : await api.post<Identity>(`${base}/identities`, body);
                      setIdentityDraft(null);
                      await qc.invalidateQueries({
                        queryKey: ["identities", projectId],
                      });
                      setIdentityFilter(result.id);
                      setOffset(0);
                      setMessage("角色身份已保存");
                    })
                  }
                >
                  保存身份
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setIdentityDraft(null)}
                >
                  取消
                </Button>
              </div>
            </section>
          )}
          <section className="rounded-xl border border-border bg-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-medium">相似画面线索</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  描述可见外观或场景，找出待核对片段。相似度不代表身份确认。
                </p>
              </div>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setEditing(null);
                  setDraft({
                    ...blank,
                    identity_id:
                      identityFilter && identityFilter !== "unknown"
                        ? identityFilter
                        : null,
                  });
                  setForm(true);
                }}
              >
                <Plus className="mr-1 h-4 w-4" />
                记录一条观察
              </Button>
            </div>
            <div className="mt-3 flex gap-2">
              <input
                aria-label="外观线索搜索"
                className={field}
                placeholder="例如：穿蓝色外套的人站在车站"
                value={search}
                maxLength={1000}
                disabled={busy}
                onChange={(e) => setSearch(e.target.value)}
              />
              <Button
                disabled={busy || !search.trim()}
                onClick={() =>
                  void perform(async () => {
                    const result = await api.post<{ items: Candidate[] }>(
                      base + "/suggestions",
                      { query: search },
                    );
                    setCandidates(result.items);
                    setSearched(true);
                  })
                }
              >
                <Search className="mr-1 h-4 w-4" />
                找线索
              </Button>
            </div>
            {searched && (
              <p className="mt-3 text-xs text-muted-foreground">
                找到 {candidates.length}{" "}
                个相似候选（最多显示8个），不代表全部出现片段。
              </p>
            )}
            {!!candidates.length && (
              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {candidates.map((c) => (
                  <button
                    key={c.id}
                    disabled={busy}
                    className="overflow-hidden rounded-lg border border-border text-left"
                    onClick={() => {
                      setEditing(null);
                      setDraft({
                        ...blank,
                        identity_id:
                          identityFilter && identityFilter !== "unknown"
                            ? identityFilter
                            : null,
                        note: `相似检索线索：${search}；尚未确认身份`,
                      });
                      setVersion(c.version_id);
                      setStart(String(c.start_ms / 1000));
                      setEnd(String(c.end_ms / 1000));
                      setForm(true);
                    }}
                  >
                    {c.thumbnail_hash && (
                      <MediaImage
                        src={blobUrl(projectId, c.thumbnail_hash)}
                        alt={c.name}
                        className="aspect-video w-full object-cover"
                      />
                    )}
                    <div className="space-y-1 p-2 text-xs">
                      <strong>{c.name}</strong>
                      <p>
                        {mediaTime(c.start_ms)} — {mediaTime(c.end_ms)}
                      </p>
                      <p className="text-primary">打开核对</p>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>
          {form && (
            <section className="space-y-4 rounded-xl border border-primary/40 bg-card p-4">
              <div className="flex items-center justify-between">
                <h2 className="font-medium">
                  {editing ? "纠正证据判断" : "核对并记录观察"}
                </h2>
                <Button
                  variant="ghost"
                  aria-label="关闭证据编辑"
                  disabled={busy}
                  onClick={() => {
                    setForm(false);
                    setEditing(null);
                  }}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <div className="space-y-3">
                  <div className="space-y-1 text-xs">
                    <p>原片版本</p>
                    <LibraryVersionPicker
                      projectId={projectId}
                      value={version}
                      label="证据视频版本"
                      disabled={busy || !!editing}
                      onChange={setVersion}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="space-y-1 text-xs">
                      开始秒数
                      <input
                        aria-label="证据开始秒数"
                        className={field}
                        type="number"
                        min={0}
                        step={0.1}
                        disabled={busy || !!editing}
                        value={start}
                        onChange={(e) => setStart(e.target.value)}
                      />
                    </label>
                    <label className="space-y-1 text-xs">
                      结束秒数
                      <input
                        aria-label="证据结束秒数"
                        className={field}
                        type="number"
                        min={0.1}
                        step={0.1}
                        disabled={busy || !!editing}
                        value={end}
                        onChange={(e) => setEnd(e.target.value)}
                      />
                    </label>
                  </div>
                  {v?.proxy_hash && valid && (
                    <MediaSegmentPlayer
                      key={`${v.id}:${startMs}:${endMs}`}
                      src={blobUrl(projectId, v.proxy_hash)}
                      start={startMs}
                      end={endMs}
                      label="核对证据片段"
                    />
                  )}
                  <p className="text-xs leading-6 text-muted-foreground">
                    {editing
                      ? "视频版本和原始片段固定不变。如时间选错，请否定本条并新建观察。"
                      : "缩短到能支持判断的时间段。不要用整集范围代替实际出现时间。"}
                  </p>
                </div>
                <fieldset disabled={busy} className="space-y-3">
                  <label className="block space-y-1 text-xs">
                    关联身份
                    <select
                      aria-label="证据关联身份"
                      className={field}
                      value={draft.identity_id ?? ""}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          identity_id: e.target.value || null,
                        })
                      }
                    >
                      <option value="">未知身份</option>
                      {identities.data?.map((i) => (
                        <option key={i.id} value={i.id}>
                          {i.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="space-y-1 text-xs">
                      证据类型
                      <select
                        aria-label="证据类型"
                        className={field}
                        value={draft.kind}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            kind: e.target.value as Draft["kind"],
                          })
                        }
                      >
                        <option value="visual">画面中出现</option>
                        <option value="speech">台词中提及</option>
                      </select>
                    </label>
                    <label className="space-y-1 text-xs">
                      核对状态
                      <select
                        aria-label="证据状态"
                        className={field}
                        value={draft.status}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            status: e.target.value as Draft["status"],
                          })
                        }
                      >
                        <option value="proposed">待核对</option>
                        <option value="confirmed">已确认</option>
                        <option value="rejected">已否定</option>
                      </select>
                    </label>
                  </div>
                  <label className="block space-y-1 text-xs">
                    实际观察
                    <textarea
                      aria-label="实际观察"
                      className={`${field} min-h-24`}
                      placeholder="可见人物的衣着、动作、位置；或实际听到的台词"
                      value={draft.observation}
                      maxLength={4000}
                      onChange={(e) =>
                        setDraft({ ...draft, observation: e.target.value })
                      }
                    />
                  </label>
                  <label className="block space-y-1 text-xs">
                    判断依据 / 更正原因
                    <textarea
                      aria-label="判断依据"
                      className={field}
                      value={draft.note}
                      maxLength={2000}
                      onChange={(e) =>
                        setDraft({ ...draft, note: e.target.value })
                      }
                    />
                  </label>
                  <Button
                    disabled={busy || !valid || !draft.observation.trim()}
                    onClick={() =>
                      void perform(async () => {
                        const result = editing
                          ? await api.put<Evidence>(`${base}/${editing.id}`, {
                              ...draft,
                              revision: editing.revision,
                            })
                          : await api.post<Evidence>(base, {
                              ...draft,
                              version_id: version,
                              start_ms: startMs,
                              end_ms: endMs,
                            });
                        if (editing && selected?.id === editing.id)
                          setSelected({ ...editing, ...result });
                        setForm(false);
                        setEditing(null);
                        setStatus(draft.status);
                        setOffset(0);
                        setMessage(
                          "观察与判断已保存，原片版本和时间范围已固定。",
                        );
                      })
                    }
                  >
                    {editing ? "保存更正" : "保存观察"}
                  </Button>
                </fieldset>
              </div>
            </section>
          )}
          <section className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="mr-auto font-medium">
                证据记录{" "}
                <span className="text-muted-foreground">
                  {evidence.data?.total ?? "—"}
                </span>
              </h2>
              <select
                aria-label="筛选证据状态"
                className={`${field} max-w-36`}
                value={status}
                onChange={(e) => setFilter(() => setStatus(e.target.value))}
              >
                <option value="">所有状态</option>
                <option value="confirmed">已确认</option>
                <option value="proposed">待核对</option>
                <option value="rejected">已否定</option>
              </select>
              <select
                aria-label="筛选证据类型"
                className={`${field} max-w-36`}
                value={kind}
                onChange={(e) => setFilter(() => setKind(e.target.value))}
              >
                <option value="">全部证据</option>
                <option value="visual">画面出现</option>
                <option value="speech">台词提及</option>
              </select>
              {identityFilter && identityFilter !== "unknown" && (
                <select
                  aria-label="重叠出现身份"
                  className={`${field} max-w-52`}
                  value={second}
                  onChange={(e) =>
                    setFilter(() => {
                      setSecond(e.target.value);
                      setStatus("confirmed");
                      setKind("visual");
                    })
                  }
                >
                  <option value="">查看单个身份</option>
                  {identities.data
                    ?.filter((i) => i.id !== identityFilter)
                    .map((i) => (
                      <option key={i.id} value={i.id}>
                        与 {i.name} 重叠出现
                      </option>
                    ))}
                </select>
              )}
            </div>
            {second && (
              <p className="rounded-lg border border-border bg-muted/40 p-3 text-xs leading-6">
                仅匹配同一原片版本中，两人已确认画面证据的时间交集。请播放核对是否实际同框；它不代表人物有互动、关系或处于同一场景。
              </p>
            )}
            {evidence.isLoading ? (
              <p className="text-sm text-muted-foreground">正在读取证据…</p>
            ) : evidence.isError ? (
              <Button onClick={() => void evidence.refetch()}>
                证据读取失败，重试
              </Button>
            ) : !evidence.data?.items.length ? (
              <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                当前筛选下还没有证据。记录实际观察，或切换到待核对。
              </div>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {evidence.data.items.map((e) => (
                  <article
                    key={e.id}
                    className={`overflow-hidden rounded-xl border bg-card ${selected?.id === e.id ? "border-primary" : "border-border"}`}
                  >
                    <button
                      className="flex w-full gap-3 p-3 text-left"
                      onClick={() => {
                        setSelected(e);
                        setHistoryOpen(false);
                        setHistoryOffset(0);
                      }}
                    >
                      {e.poster_hash && (
                        <MediaImage
                          src={blobUrl(projectId, e.poster_hash)}
                          alt={e.name}
                          className="h-20 w-28 shrink-0 rounded-lg object-cover"
                        />
                      )}
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap gap-2 text-xs">
                          <strong>{identityName(e.identity_id)}</strong>
                          <span
                            className={
                              e.status === "confirmed"
                                ? "text-primary"
                                : "text-muted-foreground"
                            }
                          >
                            {stateName[e.status]}
                          </span>
                          <span>
                            {e.kind === "visual" ? "画面出现" : "台词提及"}
                          </span>
                        </div>
                        <p className="truncate text-xs text-muted-foreground">
                          {e.name} · v{e.ordinal} · {mediaTime(e.start_ms)}—
                          {mediaTime(e.end_ms)}
                        </p>
                        <p className="line-clamp-2 text-sm">{e.observation}</p>
                      </div>
                    </button>
                    <div className="flex flex-wrap gap-2 border-t border-border px-3 py-2">
                      <Button
                        variant="ghost"
                        disabled={busy}
                        onClick={() => edit(e)}
                      >
                        纠正判断
                      </Button>
                      {e.status !== "confirmed" && (
                        <Button
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              if (
                                await confirm({
                                  title: "确认这条证据？",
                                  message:
                                    "请确保已核对原片中的身份、证据类型和时间范围。",
                                })
                              )
                                await decision(e, "confirmed");
                            })
                          }
                        >
                          <Check className="mr-1 h-3 w-3" />
                          确认
                        </Button>
                      )}
                      {e.status !== "rejected" && (
                        <Button
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              if (
                                await confirm({
                                  title: "否定这条判断？",
                                  message:
                                    "该条将退出已确认检索，历史仍可恢复。",
                                })
                              )
                                await decision(e, "rejected");
                            })
                          }
                        >
                          否定
                        </Button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
            {evidence.data && evidence.data.total > 50 && (
              <div className="flex items-center gap-3">
                <Button
                  variant="outline"
                  disabled={!offset}
                  onClick={() => setOffset(offset - 50)}
                >
                  上一页
                </Button>
                <span className="text-xs">
                  {offset + 1}—{Math.min(offset + 50, evidence.data.total)} /{" "}
                  {evidence.data.total}
                </span>
                <Button
                  variant="outline"
                  disabled={offset + 50 >= evidence.data.total}
                  onClick={() => setOffset(offset + 50)}
                >
                  下一页
                </Button>
              </div>
            )}
          </section>
          {selected && (
            <section className="space-y-3 rounded-xl border border-primary/30 bg-card p-4">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-medium">
                  {identityName(selected.identity_id)} · 原片证据
                </h2>
                <Button
                  variant="outline"
                  onClick={() => setHistoryOpen(!historyOpen)}
                >
                  <History className="mr-1 h-4 w-4" />
                  修正历史
                </Button>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                {selected.proxy_hash && (
                  <MediaSegmentPlayer
                    key={selected.id}
                    src={blobUrl(projectId, selected.proxy_hash)}
                    start={selected.start_ms}
                    end={selected.end_ms}
                    label="查看原片证据"
                  />
                )}
                <div className="space-y-3 text-sm">
                  <p>{selected.observation}</p>
                  <p className="text-muted-foreground">
                    {selected.note || "未填写判断依据"}
                  </p>
                  <p className="text-xs">
                    {stateName[selected.status]} · 修订 {selected.revision} ·{" "}
                    {selected.kind === "visual" ? "画面出现" : "台词提及"}
                  </p>
                </div>
              </div>
              {historyOpen && (
                <div className="space-y-2 border-t border-border pt-3">
                  {history.isError ? (
                    <Button onClick={() => void history.refetch()}>
                      重试历史
                    </Button>
                  ) : (
                    history.data?.map((h) => (
                      <div
                        key={h.revision}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/40 p-3 text-xs"
                      >
                        <div>
                          <strong>
                            修订 {h.revision} ·{" "}
                            {identityName(h.document.identity_id)} ·{" "}
                            {stateName[h.document.status]}
                          </strong>
                          <p className="mt-1">{h.document.observation}</p>
                          <p className="mt-1 text-muted-foreground">
                            {h.document.note}
                          </p>
                        </div>
                        <Button
                          variant="outline"
                          disabled={busy || h.revision === selected.revision}
                          onClick={() =>
                            void perform(async () => {
                              if (
                                !(await confirm({
                                  title: `恢复修订 ${h.revision}？`,
                                  message:
                                    "将使用这版身份与判断，当前判断会继续保留在历史中。",
                                }))
                              )
                                return;
                              const result = await api.post<Evidence>(
                                `${base}/${selected.id}/history/${h.revision}/restore`,
                                { revision: selected.revision },
                              );
                              setSelected({ ...selected, ...result });
                              setMessage("历史判断已恢复为新修订");
                            })
                          }
                        >
                          恢复
                        </Button>
                      </div>
                    ))
                  )}
                  <div className="flex gap-2">
                    {historyOffset > 0 && (
                      <Button
                        variant="ghost"
                        onClick={() => setHistoryOffset(historyOffset - 50)}
                      >
                        较新修订
                      </Button>
                    )}
                    {history.data?.length === 50 && (
                      <Button
                        variant="ghost"
                        onClick={() => setHistoryOffset(historyOffset + 50)}
                      >
                        更早修订
                      </Button>
                    )}
                  </div>
                </div>
              )}
            </section>
          )}
        </section>
      </div>
    </div>
  );
}
