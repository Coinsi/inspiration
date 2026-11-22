import { StudioSelect } from "@/components/ui/studio-select";
import { SearchField } from "@/components/ui/search-field";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ShieldCheck, UserPlus, Users, Crown, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Avatar } from "@/components/ui/avatar";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";
import { useAuth } from "@/lib/auth";
import { api, ApiError, type Member } from "@/lib/api";
import { isGeneratedUsername } from "@/lib/username";
import "./members.css";

const roles = [
  {
    id: "admin",
    zh: "管理员",
    en: "Administrator",
    desc: "管理项目、成员、模型配置和全部创作内容",
    eng: "Manage the project, members, providers and all creative content",
  },
  {
    id: "director",
    zh: "导演",
    en: "Director",
    desc: "统筹故事、资产、生成、剪辑及审核，不管理成员和模型配置",
    eng: "Manage creative work, generation, editing and reviews",
  },
  {
    id: "writer",
    zh: "编剧",
    en: "Writer",
    desc: "编辑故事剧本、技能与画布，使用创作助理",
    eng: "Edit stories, scripts, skills and canvases; use the assistant",
  },
  {
    id: "artist",
    zh: "美术",
    en: "Artist",
    desc: "编辑资产、镜头、画布和预演，执行生成并提交审核",
    eng: "Edit assets, shots, canvases and stages; generate and submit work",
  },
  {
    id: "producer",
    zh: "制片",
    en: "Producer",
    desc: "查看项目内容与进展；当前为只读权限",
    eng: "View project content and progress; currently read-only",
  },
  {
    id: "viewer",
    zh: "观察者",
    en: "Viewer",
    desc: "查看项目内容，不修改或生成作品",
    eng: "View project content without editing or generating",
  },
];

type LookupTarget =
  | { email: string; username?: never }
  | { username: string; email?: never };
interface MemberLookup {
  user_id: string;
  username: string;
  display_name: string;
  avatar_data: string | null;
  is_member: boolean;
}
interface MemberSearch {
  state: "idle" | "loading" | "found" | "missing" | "error";
  userId?: string;
  message?: string;
}
const completeEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function ProjectMembers() {
  const { projectId = "" } = useParams();
  return <ProjectMembersPanel key={projectId} projectId={projectId} />;
}

function ProjectMembersPanel({ projectId }: { projectId: string }) {
  const qc = useQueryClient(),
    { lang } = useI18n(),
    { me, refresh } = useAuth(),
    confirm = useConfirm();
  const zh = lang === "zh",
    base = `/projects/${projectId}/members`;
  const text = (chinese: string, english: string) => zh ? chinese : english;
  const members = useQuery({
    queryKey: ["members", projectId],
    queryFn: () => api.get<Member[]>(base),
  });
  const mine = members.data?.find((m) => m.user_id === me?.user.id),
    canManage = mine?.role === "admin";
  const [search, setSearch] = useState(""),
    [adding, setAdding] = useState(false),
    [identity, setIdentity] = useState(""),
    [role, setRole] = useState("artist");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [match, setMatch] = useState<{
    target: LookupTarget;
    user: MemberLookup;
  } | null>(null);
  const [finding, setFinding] = useState(false);
  const [listLookup, setListLookup] = useState<MemberSearch>({ state: "idle" });
  const addRequest = useRef<AbortController | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  const operationBusy = useRef(false);
  useEffect(() => {
    return () => {
      addRequest.current?.abort();
      listRequest.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!canManage) {
      addRequest.current?.abort();
      listRequest.current?.abort();
      setAdding(false);
      setMatch(null);
      setFinding(false);
      setListLookup({ state: "idle" });
    }
  }, [canManage]);
  const nameOf = (user: Pick<Member, "display_name" | "username">) => {
    const displayName = user.display_name?.trim();
    if (displayName && !isGeneratedUsername(displayName)) return displayName;
    if (user.username && !isGeneratedUsername(user.username)) return user.username;
    return text("未设置昵称", "Unnamed user");
  };
  const emailSearch = canManage && completeEmail.test(search.trim());
  function changeSearch(value: string) {
    listRequest.current?.abort();
    setSearch(value);
    setListLookup({ state: "idle" });
  }
  function changeIdentity(value: string) {
    addRequest.current?.abort();
    setIdentity(value);
    setMatch(null);
    setFinding(false);
    setError("");
  }
  function closeAdd() {
    if (busy) return;
    addRequest.current?.abort();
    setAdding(false);
    setMatch(null);
    setFinding(false);
  }
  async function findMemberByEmail() {
    if (!canManage || !emailSearch) return;
    const email = search.trim().toLowerCase();
    if (!completeEmail.test(email)) {
      setListLookup({
        state: "error",
        message: text(
          "请输入完整邮箱地址后查找。",
          "Enter a complete email address to search.",
        ),
      });
      return;
    }
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    setListLookup({ state: "loading" });
    try {
      const user = await api.post<MemberLookup>(
        `${base}/lookup`,
        { email, member_only: true },
        { signal: controller.signal },
      );
      if (!controller.signal.aborted && listRequest.current === controller) {
        setListLookup({ state: "found", userId: user.user_id });
      }
    } catch (e) {
      if (controller.signal.aborted || listRequest.current !== controller) return;
      const missing = e instanceof ApiError && e.code === "NOT_FOUND";
      setListLookup({
        state: missing ? "missing" : "error",
        message: missing
          ? text(
              "没有匹配的项目成员，请核对完整邮箱。",
              "No project member matches this email address.",
            )
          : text("查找失败，请重试。", "Search failed. Please try again."),
      });
    }
  }
  async function findAccount() {
    if (!canManage || busy || finding) return;
    const value = identity.trim().replace(/^@/, "");
    if (!value) return;
    const target: LookupTarget = value.includes("@")
      ? { email: value.toLowerCase() }
      : { username: value };
    if (target.email && !completeEmail.test(target.email)) {
      setError(text(
        "请输入完整邮箱地址，或使用用户名查找。",
        "Enter a complete email address or use a username.",
      ));
      return;
    }
    addRequest.current?.abort();
    const controller = new AbortController();
    addRequest.current = controller;
    setFinding(true);
    setError("");
    setMatch(null);
    try {
      const user = await api.post<MemberLookup>(`${base}/lookup`, target, {
        signal: controller.signal,
      });
      if (!controller.signal.aborted && addRequest.current === controller) {
        setMatch({ target, user });
      }
    } catch (e) {
      if (controller.signal.aborted || addRequest.current !== controller) return;
      setError(
        e instanceof ApiError && e.code === "NOT_FOUND"
          ? text(
              "未找到该账号，请确认对方已注册，并核对邮箱或用户名。",
              "No account found. Check the registered email address or exact username.",
            )
          : text("查找失败，请重试。", "Search failed. Please try again."),
      );
    } finally {
      if (!controller.signal.aborted && addRequest.current === controller) {
        setFinding(false);
      }
    }
  }
  const label = (id: string) => {
    const r = roles.find((r) => r.id === id);
    return r ? (zh ? r.zh : r.en) : id;
  };
  async function perform(action: () => Promise<unknown>, message: string) {
    if (operationBusy.current) return false;
    operationBusy.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await qc.invalidateQueries({ queryKey: ["members", projectId] });
      await refresh();
      setNotice(message);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      operationBusy.current = false;
      setBusy(false);
    }
  }
  const searchTerm = search.trim().replace(/^@/, "").toLowerCase();
  const filtered = (members.data || []).filter((m) => {
    if (emailSearch) {
      return listLookup.state === "found" && m.user_id === listLookup.userId;
    }
    return `${nameOf(m)} ${m.username ?? ""}`.toLowerCase().includes(searchTerm);
  });
  const searchPlaceholder = canManage
    ? text("昵称、用户名或完整邮箱", "Name, username or full email")
    : text("搜索昵称或用户名", "Search name or username");
  let searchMessage = listLookup.message;
  if (listLookup.state === "idle") {
    searchMessage = text(
      "输入完整邮箱并点击查找，仅匹配此项目的成员。",
      "Enter a full email and search for a member of this project.",
    );
  } else if (listLookup.state === "loading") {
    searchMessage = text("正在查找项目成员…", "Searching project members…");
  } else if (listLookup.state === "found") {
    searchMessage = text(
      "已按完整邮箱匹配项目成员。",
      "Project member matched by exact email.",
    );
  }
  return (
    <div className="members-page">
      <header className="members-heading">
        <div>
          <p className="members-eyebrow">PROJECT COLLABORATION</p>
          <h1>{zh ? "一起，把故事做出来" : "Create together"}</h1>
          <p>
            {zh
              ? "管理项目成员，让每个人清楚自己的工作范围。"
              : "Bring people into your project with clear responsibilities."}
          </p>
        </div>
        {canManage && (
          <Button
            onClick={() => {
              setAdding(true);
              setError("");
              changeIdentity("");
              setRole("artist");
            }}
          >
            <UserPlus size={16} />
            {zh ? "添加成员" : "Add member"}
          </Button>
        )}
      </header>
      <div className="members-summary">
        <span>
          <Users size={18} />
          <strong>{members.data?.length ?? "—"}</strong>
          {zh ? "位成员" : "members"}
        </span>
        <span>
          <ShieldCheck size={18} />
          {zh ? "你的角色" : "Your role"}
          <strong>
            {mine?.is_owner
              ? zh
                ? "所有者"
                : "Owner"
              : mine
                ? label(mine.role)
                : "—"}
          </strong>
        </span>
      </div>
      {error && !adding && (
        <p role="alert" className="members-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="members-notice">
          {notice}
        </p>
      )}
      <section className="members-panel">
        <div className="members-toolbar">
          <h2>{zh ? "项目成员" : "Project members"}</h2>
          <form
            className="members-search"
            onSubmit={(e) => {
              e.preventDefault();
              void findMemberByEmail();
            }}
          >
            <SearchField
              className="members-search-field"
              label={zh ? "搜索成员" : "Search members"}
              placeholder={searchPlaceholder}
              value={search}
              onValueChange={changeSearch}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={254}
            />
            {emailSearch && (
              <Button
                type="submit"
                variant="outline"
                disabled={listLookup.state === "loading"}
              >
                {listLookup.state === "loading"
                  ? text("查找中…", "Searching…")
                  : text("查找", "Search")}
              </Button>
            )}
          </form>
        </div>
        {emailSearch && (
          <p
            className={`members-search-status${listLookup.state === "error" ? " members-search-error" : ""}`}
            role={listLookup.state === "error" ? "alert" : "status"}
          >
            {searchMessage}
          </p>
        )}
        {members.isLoading ? (
          <p className="members-empty">
            {zh ? "正在加载成员…" : "Loading members…"}
          </p>
        ) : members.error ? (
          <div role="alert" className="members-empty">
            {members.error.message}
            <Button variant="outline" onClick={() => void members.refetch()}>
              {zh ? "重试" : "Retry"}
            </Button>
          </div>
        ) : (
          <>
            {!canManage && (
              <p className="members-readonly">
                {zh
                  ? "你可以查看成员；添加成员和调整角色由项目管理员负责。"
                  : "You can view members. Only administrators can manage access."}
              </p>
            )}
            <div className="members-list">
              {filtered.map((m) => {
                const own = m.user_id === me?.user.id,
                  protectedMember = m.is_owner || own;
                return (
                  <div className="members-row" key={m.id}>
                    <Avatar
                      src={m.avatar_data}
                      name={nameOf(m)}
                      size={42}
                    />
                    <div className="members-name">
                      <strong>
                        {nameOf(m)}
                        {own && <small>{zh ? "你" : "You"}</small>}
                      </strong>
                      {m.username && !isGeneratedUsername(m.username) && (
                        <span>@{m.username}</span>
                      )}
                    </div>
                    <div className="members-role">
                      {m.is_owner ? (
                        <span className="members-owner">
                          <Crown size={14} />
                          {zh ? "所有者" : "Owner"}
                        </span>
                      ) : canManage && !protectedMember ? (
                        <StudioSelect
                          aria-label={`${zh ? "调整角色" : "Role for"} ${nameOf(m)}`}
                          disabled={busy}
                          value={m.role}
                          onValueChange={async (selectedValue) => {
                            const next = selectedValue;
                            if (
                              !(await confirm({
                                title: zh
                                  ? "调整成员角色？"
                                  : "Change member role?",
                                message: zh
                                  ? `将 ${nameOf(m)} 的角色改为「${label(next)}」。新权限会立即生效。`
                                  : `Change ${nameOf(m)} to ${label(next)}? Access changes take effect immediately.`,
                                confirmText: zh ? "确认调整" : "Change role",
                              }))
                            )
                              return;
                            void perform(
                              () =>
                                api.patch(`${base}/${m.user_id}`, {
                                  role: next,
                                }),
                              zh ? "成员角色已更新" : "Role updated",
                            );
                          }}
                        >
                          {roles.map((r) => (
                            <option value={r.id} key={r.id}>
                              {zh ? r.zh : r.en}
                            </option>
                          ))}
                        </StudioSelect>
                      ) : (
                        <span>{label(m.role)}</span>
                      )}
                    </div>
                    {canManage && !protectedMember && (
                      <button
                        className="members-remove"
                        aria-label={`${zh ? "移除成员" : "Remove"} ${nameOf(m)}`}
                        disabled={busy}
                        onClick={async () => {
                          if (
                            !(await confirm({
                              title: zh ? "移除项目成员？" : "Remove member?",
                              message: zh
                                ? `${nameOf(m)} 将失去此项目的访问权限，已创作的内容会保留。`
                                : `${nameOf(m)} will lose access. Their existing work will remain.`,
                              danger: true,
                              confirmText: zh ? "移除成员" : "Remove",
                            }))
                          )
                            return;
                          void perform(
                            () => api.del(`${base}/${m.user_id}`),
                            zh ? "成员已移除" : "Member removed",
                          );
                        }}
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
                  </div>
                );
              })}
              {!filtered.length && (!emailSearch || listLookup.state === "found") && (
                <p className="members-empty">
                  {zh ? "没有匹配的成员" : "No matching members"}
                </p>
              )}
            </div>
          </>
        )}
      </section>
      <details className="members-guide">
        <summary>
          <ShieldCheck size={17} />
          {zh ? "各个角色可以做什么？" : "What can each role do?"}
        </summary>
        <div>
          {roles.map((r) => (
            <article key={r.id}>
              <h3>{zh ? r.zh : r.en}</h3>
              <p>{zh ? r.desc : r.eng}</p>
            </article>
          ))}
        </div>
        <p className="members-readonly">
          {zh
            ? "所有者保留完整管理权限，不能在成员列表中降权或移除。角色仅作用于当前项目。"
            : "The owner retains full access and cannot be removed or demoted here. Roles apply only to this project."}
        </p>
      </details>
      <Modal
        open={adding}
        onClose={closeAdd}
        title={zh ? "添加项目成员" : "Add project member"}
        width={500}
      >
        <form
          className="members-add-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!match) {
              void findAccount();
              return;
            }
            if (match.user.is_member || busy || !canManage) return;
            if (
              await perform(
                async () => {
                  try {
                    return await api.post(base, {
                      ...match.target,
                      expected_user_id: match.user.user_id,
                      role,
                    });
                  } catch (error) {
                    if (
                      error instanceof ApiError &&
                      error.code === "VERSION_CONFLICT"
                    ) {
                      setMatch(null);
                      throw new Error(text(
                        "成员信息已变化，请重新查找并确认后添加。",
                        "This account or its membership changed. Search again before adding.",
                      ));
                    }
                    throw error;
                  }
                },
                zh ? "成员已加入项目" : "Member added",
              )
            )
              setAdding(false);
          }}
        >
          <p>
            {zh
              ? "使用对方注册的邮箱或用户名查找，核对头像和昵称后再添加。"
              : "Find a registered email address or username, then check the avatar and display name before adding."}
          </p>
          <div className="members-identity-field">
            <label htmlFor="member-lookup-identity">
              {zh ? "注册邮箱或用户名" : "Registered email or username"}
            </label>
            <div className="members-lookup-input">
              <input
                id="member-lookup-identity"
                required
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                maxLength={254}
                value={identity}
                disabled={busy}
                placeholder={zh ? "输入完整邮箱或用户名" : "Full email address or username"}
                onChange={(e) => changeIdentity(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing && !match) {
                    e.preventDefault();
                    void findAccount();
                  }
                }}
              />
              <Button
                type="button"
                variant="outline"
                disabled={busy || finding || !identity.trim()}
                onClick={() => void findAccount()}
              >
                {finding
                  ? text("查找中…", "Searching…")
                  : text("查找用户", "Find user")}
              </Button>
            </div>
          </div>
          {finding && (
            <p role="status">{text("正在查找账号…", "Looking up account…")}</p>
          )}
          {match && (
            <div className="members-lookup-result" role="status">
              <Avatar
                src={match.user.avatar_data}
                name={nameOf(match.user)}
                size={44}
              />
              <div className="members-name">
                <strong>{nameOf(match.user)}</strong>
                {!isGeneratedUsername(match.user.username) && (
                  <span>@{match.user.username}</span>
                )}
                <span>
                  {match.user.is_member
                    ? text(
                        "已是项目成员，无需重复添加。",
                        "Already a project member. No need to add again.",
                      )
                    : text(
                        "请确认这是你要添加的人。",
                        "Confirm this is the person you want to add.",
                      )}
                </span>
              </div>
            </div>
          )}
          {match && !match.user.is_member && (
            <>
              <label>
                {zh ? "项目角色" : "Project role"}
                <StudioSelect
                  aria-label={zh ? "项目角色" : "Project role"}
                  value={role}
                  disabled={busy}
                  onValueChange={(selectedValue) => setRole(selectedValue)}
                >
                  {roles.map((r) => (
                    <option key={r.id} value={r.id}>
                      {zh ? r.zh : r.en}
                    </option>
                  ))}
                </StudioSelect>
              </label>
              <p className="members-role-description">
                {zh
                  ? roles.find((r) => r.id === role)?.desc
                  : roles.find((r) => r.id === role)?.eng}
              </p>
            </>
          )}
          {error && (
            <p role="alert" className="members-error">
              {error}
            </p>
          )}
          <footer>
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={closeAdd}
            >
              {zh ? "取消" : "Cancel"}
            </Button>
            <Button
              type="submit"
              disabled={busy || finding || !match || match.user.is_member}
            >
              {busy
                ? text("添加中…", "Adding…")
                : text("确认添加到项目", "Confirm and add")}
            </Button>
          </footer>
        </form>
      </Modal>
    </div>
  );
}
