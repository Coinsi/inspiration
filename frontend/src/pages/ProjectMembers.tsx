import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  Search,
  ShieldCheck,
  UserPlus,
  Users,
  Crown,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Avatar } from "@/components/ui/avatar";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";
import { useAuth } from "@/lib/auth";
import { api, type Member } from "@/lib/api";
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

export default function ProjectMembers() {
  const { projectId } = useParams(),
    qc = useQueryClient(),
    { lang } = useI18n(),
    { me, refresh } = useAuth(),
    confirm = useConfirm();
  const zh = lang === "zh",
    base = `/projects/${projectId}/members`;
  const members = useQuery({
    queryKey: ["members", projectId],
    queryFn: () => api.get<Member[]>(base),
  });
  const mine = members.data?.find((m) => m.user_id === me?.user.id),
    canManage = mine?.role === "admin";
  const [search, setSearch] = useState(""),
    [adding, setAdding] = useState(false),
    [username, setUsername] = useState(""),
    [role, setRole] = useState("artist");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const label = (id: string) => {
    const r = roles.find((r) => r.id === id);
    return r ? (zh ? r.zh : r.en) : id;
  };
  async function perform(action: () => Promise<unknown>, message: string) {
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
      setBusy(false);
    }
  }
  const filtered = (members.data || []).filter((m) =>
    `${m.display_name} ${m.username}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
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
              setUsername("");
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
          <label>
            <Search size={16} />
            <input
              aria-label={zh ? "搜索成员" : "Search members"}
              placeholder={zh ? "搜索昵称或用户名" : "Search name or username"}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
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
                      name={m.display_name || m.username}
                      size={42}
                    />
                    <div className="members-name">
                      <strong>
                        {m.display_name || m.username}
                        {own && <small>{zh ? "你" : "You"}</small>}
                      </strong>
                      <span>@{m.username}</span>
                    </div>
                    <div className="members-role">
                      {m.is_owner ? (
                        <span className="members-owner">
                          <Crown size={14} />
                          {zh ? "所有者" : "Owner"}
                        </span>
                      ) : canManage && !protectedMember ? (
                        <select
                          aria-label={`${zh ? "调整角色" : "Role for"} ${m.display_name || m.username}`}
                          disabled={busy}
                          value={m.role}
                          onChange={async (e) => {
                            const next = e.target.value;
                            if (
                              !(await confirm({
                                title: zh
                                  ? "调整成员角色？"
                                  : "Change member role?",
                                message: zh
                                  ? `将 ${m.display_name} 的角色改为「${label(next)}」。新权限会立即生效。`
                                  : `Change ${m.display_name} to ${label(next)}? Access changes take effect immediately.`,
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
                        </select>
                      ) : (
                        <span>{label(m.role)}</span>
                      )}
                    </div>
                    {canManage && !protectedMember && (
                      <button
                        className="members-remove"
                        aria-label={`${zh ? "移除成员" : "Remove"} ${m.display_name || m.username}`}
                        disabled={busy}
                        onClick={async () => {
                          if (
                            !(await confirm({
                              title: zh ? "移除项目成员？" : "Remove member?",
                              message: zh
                                ? `${m.display_name} 将失去此项目的访问权限，已创作的内容会保留。`
                                : `${m.display_name} will lose access. Their existing work will remain.`,
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
              {!filtered.length && (
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
        onClose={() => {
          if (!busy) setAdding(false);
        }}
        title={zh ? "添加项目成员" : "Add project member"}
        width={500}
      >
        <form
          className="members-add-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (
              await perform(
                () => api.post(base, { username: username.trim(), role }),
                zh ? "成员已加入项目" : "Member added",
              )
            )
              setAdding(false);
          }}
        >
          <p>
            {zh
              ? "输入对方已注册的用户名。添加后，对方即可在自己的项目列表中看到此项目。"
              : "Enter an existing username. The project will appear in their project list."}
          </p>
          <label>
            {zh ? "用户名" : "Username"}
            <input
              required
              autoComplete="off"
              minLength={3}
              maxLength={64}
              value={username}
              disabled={busy}
              placeholder={zh ? "例如：story_writer" : "e.g. story_writer"}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>
            {zh ? "项目角色" : "Project role"}
            <select
              aria-label={zh ? "项目角色" : "Project role"}
              value={role}
              disabled={busy}
              onChange={(e) => setRole(e.target.value)}
            >
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {zh ? r.zh : r.en}
                </option>
              ))}
            </select>
          </label>
          <p className="members-role-description">
            {zh
              ? roles.find((r) => r.id === role)?.desc
              : roles.find((r) => r.id === role)?.eng}
          </p>
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
              onClick={() => setAdding(false)}
            >
              {zh ? "取消" : "Cancel"}
            </Button>
            <Button type="submit" disabled={busy || !username.trim()}>
              {busy
                ? zh
                  ? "添加中…"
                  : "Adding…"
                : zh
                  ? "添加到项目"
                  : "Add to project"}
            </Button>
          </footer>
        </form>
      </Modal>
    </div>
  );
}
