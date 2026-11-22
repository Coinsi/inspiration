import { api } from "@/lib/api";
import type { MembershipPlan } from "@/lib/billing";

function samplePlans(): MembershipPlan[] {
  const tiers = [
    { key: "creator", name: "创作会员", amount: 2000, credits: 60_000, description: "适合日常出图和视频创作。" },
    { key: "plus", name: "进阶会员", amount: 4000, credits: 150_000, description: "适合持续制作和更多生成任务。" },
  ];
  return [1, 12].flatMap((months) =>
    tiers.map((tier, index) => ({
      // Non-UUID IDs cannot be submitted as actual checkout products.
      id: `preview-${tier.key}-${months}m`,
      name: tier.name,
      description: tier.description,
      currency: "usd",
      amount_minor: months === 12 ? (tier.amount * 12 * 80) / 100 : tier.amount,
      original_amount_minor: null,
      monthly_plan_id: months === 12 ? `preview-${tier.key}-1m` : null,
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
