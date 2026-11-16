import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Badge,
  bytes,
  date,
  Empty,
  ErrorNotice,
  Heading,
  Loading,
  Notice,
  Stats,
  amount,
} from "./shared";
type Overview = {
  as_of: string;
  users: number;
  new_users_7d: number;
  active_users_7d: number;
  projects: number;
  active_tasks: number;
  attention_tasks: number;
  storage_bytes: number;
  usage_7d: {
    estimated: number;
    recorded_actual: number;
    unknown_jobs: number;
  };
  recent_attention: {
    title: string;
    project_name: string;
    user_name: string;
    id: string;
    kind: string;
    status: string;
    created_at: string;
  }[];
};
export default function AdminOverview() {
  const q = useQuery({
    queryKey: ["admin", "overview"],
    queryFn: () => api.get<Overview>("/admin/overview"),
  });
  return (
    <>
      <Heading
        title="平台概览"
        description="从待处理任务开始，了解平台的使用和运行情况。"
      >
        <Button
          variant="outline"
          disabled={q.isFetching}
          onClick={() => void q.refetch()}
        >
          <RefreshCw size={15} className={q.isFetching ? "animate-spin" : ""} />
          {q.isFetching ? "刷新中" : "刷新"}
        </Button>
      </Heading>
      {q.error ? (
        <ErrorNotice error={q.error} retry={() => void q.refetch()} />
      ) : !q.data ? (
        <Loading />
      ) : (
        <>
          <Stats
            items={[
              {
                label: "注册用户",
                value: q.data.users,
                note: `近 7 天新增 ${q.data.new_users_7d} 人`,
              },
              { label: "使用中的项目", value: q.data.projects },
              { label: "进行中的任务", value: q.data.active_tasks },
              {
                label: "需要关注",
                value: q.data.attention_tasks,
                note: "含历史失败及待处理任务",
              },
            ]}
          />
          <section className="admin-panel">
            <div className="admin-section-title">
              <div>
                <h2>需要关注的任务</h2>
                <p>查看问题，定位到具体处理环节。</p>
              </div>
              <Link to="/admin/tasks?state=attention">
                查看全部 <ArrowUpRight size={15} />
              </Link>
            </div>
            {q.data.recent_attention.length ? (
              <div className="admin-issue-list">
                {q.data.recent_attention.map((t) => (
                  <Link
                    key={`${t.kind}:${t.id}`}
                    to={`/admin/tasks?kind=${t.kind}&search=${t.id}&detail=${t.id}`}
                  >
                    <div>
                      <strong>{t.title}</strong>
                      <small>
                        {t.project_name} · {t.user_name} · {date(t.created_at)}
                      </small>
                    </div>
                    <Badge value={t.status} />
                    <ArrowUpRight size={17} />
                  </Link>
                ))}
              </div>
            ) : (
              <Empty>目前没有需要关注的任务</Empty>
            )}
          </section>
          <Stats
            items={[
              {
                label: "近 7 天有登录记录",
                value: q.data.active_users_7d,
                note: "仅统计开始记录后的成功登录",
              },
              {
                label: "已登记文件体积",
                value: bytes(q.data.storage_bytes),
                note: "文件去重后统计，不等于存储账单",
              },
              {
                label: "近 7 天已记录实际用量",
                value: amount(q.data.usage_7d.recorded_actual),
                note: `${q.data.usage_7d.unknown_jobs} 个任务尚无实际用量记录`,
              },
            ]}
          />
          <Notice>
            用量沿用现有内部计量单位，不代表人民币或供应商账单。数据更新于{" "}
            {date(q.data.as_of)}。
          </Notice>
        </>
      )}
    </>
  );
}
