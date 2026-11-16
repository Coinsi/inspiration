import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Badge,
  bytes,
  date,
  ErrorNotice,
  Heading,
  Loading,
  Notice,
  Stats,
} from "./shared";
type System = {
  storage_backend: string;
  blob_count: number;
  blob_bytes: number;
  uploads_incomplete: number;
  attachments_incomplete: number;
  services: { name: string; enabled: boolean; configured: boolean }[];
  retention: {
    gc_enabled: boolean;
    archive_days: number;
    attachment_draft_days: number;
  };
};
type Check = {
  status: string;
  checks: Record<string, string>;
  optional: Record<string, string>;
  background_workers: Record<string, string>;
};
const names: Record<string, string> = {
  database: "数据库",
  storage: "文件存储",
  queue: "任务队列",
  generation_worker: "生成工作进程",
  transcriber: "转写服务",
  indexer: "索引服务",
  media_processing: "媒体处理",
  library: "素材索引",
  transcription: "转写工作进程",
  agent: "Agent 工作进程",
  production: "制作运行层",
  canvas: "画布任务",
  engineering_delivery: "工程预览投递",
  engineering_archives: "工程归档",
};
export default function AdminSystem() {
  const [checkedAt, setCheckedAt] = useState("");
  const q = useQuery({
    queryKey: ["admin", "system"],
    queryFn: () => api.get<System>("/admin/system"),
  });
  const check = useMutation({
    mutationFn: () => api.post<Check>("/admin/system/check", {}),
    onSuccess: () => setCheckedAt(new Date().toISOString()),
  });
  return (
    <>
      <Heading
        title="系统状态"
        description="查看服务配置与存储概况。连接地址和密钥不会在此展示。"
      >
        <Button disabled={check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? "正在检查服务…" : "检查服务连接"}
        </Button>
      </Heading>
      {check.error && (
        <ErrorNotice error={check.error} retry={() => check.mutate()} />
      )}
      {check.data && (
        <section className="admin-panel">
          <div className="admin-section-title">
            <h2>连接检查结果</h2>
            <small>{date(checkedAt)}</small>
          </div>
          {[
            ["基础服务", check.data.checks],
            ["可选服务", check.data.optional],
            ["当前 API 进程内的后台任务", check.data.background_workers],
          ].map(([label, values]) => (
            <div className="admin-check-group" key={label as string}>
              <h3>{label as string}</h3>
              <div className="admin-service-list">
                {Object.entries(values).map(([name, value]) => (
                  <div key={name}>
                    <span>{names[name] || name}</span>
                    <Badge value={value as string} />
                  </div>
                ))}
              </div>
            </div>
          ))}
          <Notice>
            这是当前检查时刻的结果，不代表所有机器上的进程或真实模型生成质量。
          </Notice>
        </section>
      )}
      {q.error ? (
        <ErrorNotice error={q.error} retry={() => void q.refetch()} />
      ) : !q.data ? (
        <Loading />
      ) : (
        <>
          <Stats
            items={[
              {
                label: "已登记文件体积",
                value: bytes(q.data.blob_bytes),
                note: `${q.data.blob_count} 个去重文件 · ${q.data.storage_backend === "fs" ? "本地文件存储" : "对象存储"}`,
              },
              { label: "未完成素材上传", value: q.data.uploads_incomplete },
              { label: "未完成对话附件", value: q.data.attachments_incomplete },
            ]}
          />
          <section className="admin-panel">
            <div className="admin-section-title">
              <h2>服务配置</h2>
              <Link to="/admin/tasks?state=attention">查看异常任务 →</Link>
            </div>
            <div className="admin-service-list">
              {q.data.services.map((s) => (
                <div key={s.name}>
                  <strong>{s.name}</strong>
                  <span>{s.configured ? "已配置" : "未配置"}</span>
                  <Badge value={s.enabled ? "active" : "disabled"} />
                </div>
              ))}
            </div>
            <Notice>
              已配置不代表服务可用，请点击“检查服务连接”验证。连接与进程开关由部署配置管理。
            </Notice>
          </section>
          <section className="admin-panel">
            <div className="admin-section-title">
              <h2>文件保留策略</h2>
            </div>
            <dl className="admin-facts">
              <dt>物理文件回收</dt>
              <dd>{q.data.retention.gc_enabled ? "已开启" : "未开启"}</dd>
              <dt>工程归档暂存</dt>
              <dd>{q.data.retention.archive_days} 天</dd>
              <dt>未发送附件草稿</dt>
              <dd>{q.data.retention.attachment_draft_days} 天未活动后可清理</dd>
            </dl>
            <Notice>
              文件体积根据数据库登记记录统计，不是磁盘剩余空间或云存储账单。清理须遵守作品与历史版本的引用保护。
            </Notice>
          </section>
        </>
      )}
    </>
  );
}
