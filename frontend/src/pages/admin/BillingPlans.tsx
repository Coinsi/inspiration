import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api } from "@/lib/api";
import {
  billingCurrencies,
  currencyDecimals,
  decimalAmount,
  membershipDuration,
  money,
  parseMinor,
  points,
  type MembershipPlan,
  type MembershipTerm,
} from "@/lib/billing";
import { BillingMemberships } from "@/components/BillingMemberships";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { StudioSelect } from "@/components/ui/studio-select";
import { Empty, ErrorNotice, Loading } from "./shared";

export function BillingPlans() {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<MembershipPlan | "new" | null>(null);
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0);
  const plans = useQuery({
    queryKey: ["admin", "billing", "plans"],
    queryFn: ({ signal }) =>
      api.get<MembershipPlan[]>("/admin/billing/plans", { signal }),
  });
  const members = useQuery({
    queryKey: ["admin", "billing", "memberships", offset],
    queryFn: ({ signal }) =>
      api.get<MembershipTerm[]>(
        `/admin/billing/memberships?limit=50&offset=${offset}`,
        { signal },
      ),
    placeholderData: (previous) => previous,
  });
  async function saved() {
    await qc.invalidateQueries({ queryKey: ["admin", "billing"] });
    await qc.invalidateQueries({ queryKey: ["billing"] });
    setEdit(null);
  }
  return (
    <>
      <section className="admin-panel">
        <div className="billing-toolbar">
          <p>
            按次购买，不自动续费。1 个月按 30
            天计算。同套餐续购顺延，不同套餐独立生效。
          </p>
          <Button onClick={() => setEdit("new")}>
            <Plus size={16} />
            新建会员套餐
          </Button>
        </div>
        {plans.isLoading && <Loading />}
        {plans.error && (
          <ErrorNotice error={plans.error} retry={() => void plans.refetch()} />
        )}
        {plans.data?.length === 0 && <Empty>还没有会员套餐</Empty>}
        {!!plans.data?.length && (
          <div className="admin-table-scroll">
            <table className="billing-orders-table">
              <thead>
                <tr>
                  <th>会员套餐</th>
                  <th>售价 / 期限</th>
                  <th>期内额度</th>
                  <th>状态 / 排序</th>
                  <th>
                    <span className="sr-only">操作</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...plans.data]
                  .sort((a, b) => a.sort_order - b.sort_order)
                  .map((plan) => (
                    <tr key={plan.id}>
                      <td>
                        {plan.name}
                        <small className="billing-table-note">
                          {plan.description}
                        </small>
                      </td>
                      <td>
                        {money(plan.amount_minor, plan.currency)}
                        <small className="billing-table-note">
                          {membershipDuration(plan)}
                        </small>
                      </td>
                      <td>{points(plan.credits)} 积分</td>
                      <td>
                        {plan.active ? "在售" : "已下架"}
                        <small className="billing-table-note">
                          排序 {plan.sort_order}
                        </small>
                      </td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => setEdit(plan)}
                          aria-label={`编辑会员套餐 ${plan.name}`}
                        >
                          编辑
                        </Button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="billing-section">
        <div className="billing-section-heading">
          <h2>用户会员</h2>
        </div>
        {members.isLoading && <Loading />}
        {members.error && (
          <ErrorNotice
            error={members.error}
            retry={() => void members.refetch()}
          />
        )}
        {members.data?.length === 0 && <Empty>暂无会员记录</Empty>}
        {members.data && <BillingMemberships terms={members.data} admin />}
        {members.data && (offset > 0 || members.data.length >= 50) && (
          <div className="billing-pagination">
            <Button
              variant="outline"
              disabled={offset === 0 || members.isFetching}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              上一页
            </Button>
            <span>第 {Math.floor(offset / 50) + 1} 页</span>
            <Button
              variant="outline"
              disabled={members.data.length < 50 || members.isFetching}
              onClick={() => setOffset(offset + 50)}
            >
              下一页
            </Button>
          </div>
        )}
      </section>
      <Modal
        open={!!edit}
        onClose={() => {
          if (!busy) setEdit(null);
        }}
        title={edit === "new" ? "新建会员套餐" : "编辑会员套餐"}
        width={600}
      >
        {edit && (
          <PlanForm
            initial={edit === "new" ? null : edit}
            onSaved={saved}
            onBusy={setBusy}
          />
        )}
      </Modal>
    </>
  );
}

function PlanForm({
  initial,
  onSaved,
  onBusy,
}: {
  initial: MembershipPlan | null;
  onSaved: () => Promise<void>;
  onBusy: (busy: boolean) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [features, setFeatures] = useState(initial?.features.join("\n") ?? "");
  const [currency, setCurrency] = useState(initial?.currency ?? "cny");
  const [amount, setAmount] = useState(
    initial
      ? decimalAmount(initial.amount_minor, currencyDecimals(initial.currency))
      : "",
  );
  const [original, setOriginal] = useState(
    initial?.original_amount_minor != null
      ? decimalAmount(
          initial.original_amount_minor,
          currencyDecimals(initial.currency),
        )
      : "",
  );
  const [credits, setCredits] = useState(
    initial ? decimalAmount(initial.credits) : "",
  );
  const [duration, setDuration] = useState(String(initial?.duration ?? 1));
  const [unit, setUnit] = useState<MembershipPlan["duration_unit"]>(
    initial?.duration_unit ?? "month",
  );
  const [sort, setSort] = useState(String(initial?.sort_order ?? 0));
  const [active, setActive] = useState(initial?.active ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save() {
    if (busy) return;
    const price = parseMinor(amount, currencyDecimals(currency));
    const originalPrice = original.trim()
      ? parseMinor(original, currencyDecimals(currency))
      : null;
    const allowance = parseMinor(credits);
    const length = Number(duration);
    const days = length * { day: 1, week: 7, month: 30 }[unit];
    const rank = Number(sort);
    const benefits = features
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    let validation = "";
    if (!price || price > 1e9 || !allowance || allowance > 1e10)
      validation =
        "请输入有效售价和期内额度。日元仅支持整数，其他金额与积分最多两位小数。";
    else if (
      original.trim() &&
      (!originalPrice || originalPrice < price || originalPrice > 1e9)
    )
      validation = "原价应不低于售价，留空则不显示。";
    else if (
      !/^\d+$/.test(duration) ||
      !Number.isSafeInteger(length) ||
      days < 1 ||
      days > 3650
    )
      validation = "期限需为正整数，总时长不能超过 3650 天。";
    else if (
      !/^-?\d+$/.test(sort) ||
      !Number.isSafeInteger(rank) ||
      Math.abs(rank) > 10000
    )
      validation = "排序需为 -10000 到 10000 的整数。";
    else if (benefits.length > 12 || benefits.some((item) => item.length > 120))
      validation = "权益最多 12 条，每条不超过 120 个字符。";
    if (validation) {
      setError(new Error(validation));
      return;
    }
    setBusy(true);
    onBusy(true);
    setError(null);
    try {
      const body = {
        name: name.trim(),
        description: description.trim(),
        features: benefits,
        currency,
        amount_minor: price,
        original_amount_minor: originalPrice,
        credits: allowance,
        duration: length,
        duration_unit: unit,
        sort_order: rank,
        active,
      };
      if (initial) await api.patch(`/admin/billing/plans/${initial.id}`, body);
      else await api.post("/admin/billing/plans", body);
      await onSaved();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <form
      className="billing-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label>
        套餐名称
        <Input
          required
          maxLength={100}
          value={name}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        说明
        <textarea
          maxLength={500}
          value={description}
          disabled={busy}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label>
        权益列表
        <textarea
          value={features}
          disabled={busy}
          onChange={(e) => setFeatures(e.target.value)}
        />
        <small>每行一条，最多 12 条，每条最多 120 字。</small>
      </label>
      <div className="billing-form-grid">
        <label>
          币种
          <StudioSelect
            value={currency}
            disabled={busy}
            onValueChange={setCurrency}
          >
            {billingCurrencies.map((value) => (
              <option key={value} value={value}>
                {value.toUpperCase()}
              </option>
            ))}
          </StudioSelect>
        </label>
        <label>
          售价
          <Input
            required
            inputMode="decimal"
            value={amount}
            disabled={busy}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
      </div>
      <label>
        原价（可选）
        <Input
          inputMode="decimal"
          value={original}
          disabled={busy}
          onChange={(e) => setOriginal(e.target.value)}
        />
        <small>可选，填写时须大于或等于售价。</small>
      </label>
      <div className="billing-form-grid">
        <label>
          期限
          <Input
            required
            inputMode="numeric"
            value={duration}
            disabled={busy}
            onChange={(e) => setDuration(e.target.value)}
          />
        </label>
        <label>
          期限单位
          <StudioSelect
            value={unit}
            disabled={busy}
            onValueChange={(value) =>
              setUnit(value as MembershipPlan["duration_unit"])
            }
          >
            <option value="day">天</option>
            <option value="week">周</option>
            <option value="month">月（30 天）</option>
          </StudioSelect>
        </label>
      </div>
      <label>
        期内积分额度
        <Input
          required
          inputMode="decimal"
          value={credits}
          disabled={busy}
          onChange={(e) => setCredits(e.target.value)}
        />
        <small>只在该期内使用，到期失效，不计入永久积分。</small>
      </label>
      <label>
        排序
        <Input
          required
          inputMode="numeric"
          value={sort}
          disabled={busy}
          onChange={(e) => setSort(e.target.value)}
        />
        <small>数字越小越靠前。</small>
      </label>
      <label className="billing-toggle">
        <input
          type="checkbox"
          checked={active}
          disabled={busy}
          onChange={(e) => setActive(e.target.checked)}
        />
        <span>上架出售</span>
      </label>
      {error != null && <ErrorNotice error={error} />}
      <div className="billing-actions">
        <Button disabled={busy || !name.trim()}>
          {busy ? "保存中…" : "保存会员套餐"}
        </Button>
      </div>
    </form>
  );
}
