import { api } from "@/lib/api";
import type { MembershipPlan } from "@/lib/billing";

function samplePlans(): MembershipPlan[] {
  const tiers = [
    { key: "starter", name: "基础会员", amount: 1990, credits: 100_000 },
    { key: "plus", name: "进阶会员", amount: 3990, credits: 300_000 },
  ];
  return [1, 12].flatMap((months) =>
    tiers.map((tier, index) => ({
      // Non-UUID IDs cannot be submitted as actual checkout products.
      id: `preview-${tier.key}-${months}m`,
      name: tier.name,
      description: "",
      currency: "usd",
      amount_minor: tier.amount * months,
      original_amount_minor: null,
      credits: tier.credits * months,
      duration: months,
      duration_unit: "month" as const,
      sort_order: index,
      active: false,
      features: [],
    })),
  );
}

export async function loadPlanPreview(
  signal?: AbortSignal,
): Promise<{ plans: MembershipPlan[]; sample: boolean }> {
  // The existing endpoint enforces Super permissions. Never replace a failed
  // or unauthorized response with examples, and never write preview products.
  const plans = await api.get<MembershipPlan[]>("/admin/billing/plans", {
    signal,
  });
  if (!Array.isArray(plans)) throw new Error("会员套餐响应格式有误，请重试");
  return plans.length
    ? { plans, sample: false }
    : { plans: samplePlans(), sample: true };
}
