import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { api } from "@/lib/api";
import { billingDate, decimalAmount, parseMinor, points } from "@/lib/billing";
import { Empty, ErrorNotice, Loading } from "./shared";

interface Reservation {
  job_id: string;
  user_id: string;
  units: number;
  job_status: string;
  updated_at: string;
}

export function Reservations() {
  const [selected, select] = useState<Reservation | null>(null);
  const query = useQuery({
    queryKey: ["admin", "billing", "reservations"],
    queryFn: ({ signal }) =>
      api.get<Reservation[]>("/admin/billing/reservations", { signal }),
  });
  return (
    <section className="admin-panel">
      <div className="billing-toolbar">
        <p>核实已结束任务的上游费用后，结算实际积分，或释放无需扣费的预留。</p>
        <Button variant="outline" onClick={() => void query.refetch()}>
          刷新预留
        </Button>
      </div>
      {query.error && (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      )}
      {query.isLoading && <Loading />}
      {query.data?.length === 0 && <Empty>没有需要人工处理的积分预留</Empty>}
      {!!query.data?.length && (
        <div className="admin-table-scroll">
          <table className="billing-orders-table">
            <thead>
              <tr>
                <th>任务 / 用户</th>
                <th>预留积分</th>
                <th>任务状态</th>
                <th>更新时间</th>
                <th>
                  <span className="sr-only">操作</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((item) => (
                <tr key={item.job_id}>
                  <td>
                    <span className="billing-id">{item.job_id}</span>
                    <small className="billing-table-note">
                      用户 {item.user_id}
                    </small>
                  </td>
                  <td>{points(item.units)}</td>
                  <td>
                    {(
                      {
                        succeeded: "已完成",
                        failed: "失败",
                        cancelled: "已取消",
                        canceled: "已取消",
                      } as Record<string, string>
                    )[item.job_status] ?? item.job_status}
                  </td>
                  <td>{billingDate(item.updated_at)}</td>
                  <td>
                    <Button variant="ghost" onClick={() => select(item)}>
                      处理预留
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Modal
        open={!!selected}
        onClose={() => select(null)}
        title="处理积分预留"
        width={560}
      >
        {selected && (
          <ResolveReservation
            reservation={selected}
            close={() => select(null)}
          />
        )}
      </Modal>
    </section>
  );
}

function ResolveReservation({
  reservation,
  close,
}: {
  reservation: Reservation;
  close: () => void;
}) {
  const [action, setAction] = useState<"release" | "settle">("settle");
  const [amount, setAmount] = useState(decimalAmount(reservation.units));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const confirm = useConfirm();
  const qc = useQueryClient();
  async function submit() {
    if (busy || !reason.trim()) return;
    const units = parseMinor(amount);
    if (action === "settle" && (!units || units > reservation.units)) {
      setError(
        new Error(
          "实际扣除积分必须大于零且不超过预留；无需扣费时请选择释放预留。",
        ),
      );
      return;
    }
    if (
      !(await confirm({
        title: action === "settle" ? "确认结算积分？" : "确认释放预留？",
        message:
          action === "settle"
            ? `实际扣除 ${points(units!)} 积分，剩余预留将释放。请确认已核实上游费用。`
            : "全部预留积分将恢复可用。请确认该任务无需扣费。",
        confirmText: "确认处理",
        danger: true,
      }))
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await api.post(
        `/admin/billing/reservations/${reservation.job_id}/resolve`,
        {
          action,
          reason: reason.trim(),
          ...(action === "settle" ? { units } : {}),
        },
      );
      await qc.invalidateQueries({ queryKey: ["admin", "billing"] });
      await qc.invalidateQueries({ queryKey: ["billing"] });
      close();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="billing-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="billing-muted">
        当前预留 {points(reservation.units)} 积分。任务{" "}
        <span className="billing-id">{reservation.job_id}</span>
      </p>
      <div className="billing-methods" role="group" aria-label="预留处理方式">
        <button
          type="button"
          disabled={busy}
          aria-pressed={action === "settle"}
          className={action === "settle" ? "is-selected" : ""}
          onClick={() => setAction("settle")}
        >
          结算实际费用
        </button>
        <button
          type="button"
          disabled={busy}
          aria-pressed={action === "release"}
          className={action === "release" ? "is-selected" : ""}
          onClick={() => setAction("release")}
        >
          释放全部预留
        </button>
      </div>
      {action === "settle" && (
        <label>
          实际扣除积分
          <Input
            inputMode="decimal"
            value={amount}
            required
            disabled={busy}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
      )}
      <label>
        处理依据与原因
        <textarea
          required
          maxLength={500}
          disabled={busy}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {error != null && <ErrorNotice error={error} />}
      <div className="billing-actions">
        <Button disabled={busy || !reason.trim()}>
          {busy ? "处理中…" : "核对并处理"}
        </Button>
      </div>
    </form>
  );
}
