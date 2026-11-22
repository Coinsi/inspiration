import { ArrowRight, Check } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  membershipPurchaseDuration,
  membershipPeriod,
  money,
  points,
  type MembershipPlan,
} from "@/lib/billing";

const periods = ["monthly", "yearly", "other"] as const;
const periodLabels = {
  monthly: ["月付", "Monthly"],
  yearly: ["年付", "Yearly"],
  other: ["其他周期", "Other terms"],
};

export function BillingPlanCards({
  plans,
  selected,
  busy,
  zh,
  preview = false,
  onSelect,
}: {
  plans: MembershipPlan[];
  selected?: string;
  busy: boolean;
  zh: boolean;
  preview?: boolean;
  onSelect: (id: string) => void;
}) {
  const [period, setPeriod] = useState<(typeof periods)[number]>("monthly");
  const available = periods.filter((item) =>
    plans.some((plan) => membershipPeriod(plan) === item),
  );
  const shown = available.includes(period) ? period : available[0];
  const action = preview
    ? (zh ? "查看套餐" : "View plan")
    : (zh ? "选择套餐" : "Choose plan");

  return (
    <div className="billing-plan-catalog">
      {available.length > 1 && (
        <div
          className="billing-period-switch"
          role="group"
          aria-label={zh ? "付款周期" : "Payment term"}
        >
          {available.map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={shown === item}
              onClick={() => setPeriod(item)}
            >
              {periodLabels[item][zh ? 0 : 1]}
            </button>
          ))}
        </div>
      )}
      <div
        className="billing-plan-grid"
        role="group"
        aria-label={zh ? "选择会员套餐" : "Choose a membership plan"}
      >
        {plans.filter((plan) => membershipPeriod(plan) === shown).map((plan) => {
          const chosen = selected === plan.id;
          const referencePrice = plan.original_amount_minor;
          return (
            <article key={plan.id} className={`billing-plan-card${chosen ? " is-selected" : ""}`}>
              <header className="billing-plan-title">
                <h3>{plan.name}</h3>
                {preview && !plan.active && <span>{zh ? "未上架" : "Not on sale"}</span>}
              </header>
              <div className="billing-plan-price">
                <strong>{money(plan.amount_minor, plan.currency, zh)}</strong>
                <span>/ {membershipPurchaseDuration(plan, zh)}</span>
                {referencePrice != null && referencePrice > plan.amount_minor && (
                  <del aria-label={zh ? "参考价" : "Reference price"}>
                    {money(referencePrice, plan.currency, zh)}
                  </del>
                )}
              </div>
              <div className="billing-plan-allowance">
                <span>{zh ? "本期生成积分" : "Generation points per term"}</span>
                <strong>{points(plan.credits, zh)}</strong>
              </div>
              {plan.description && <p className="billing-plan-description">{plan.description}</p>}
              {plan.features.length > 0 && (
                <ul className="billing-plan-features">
                  {plan.features.map((feature, index) => (
                    <li key={index}><Check size={15} aria-hidden="true" /><span>{feature}</span></li>
                  ))}
                </ul>
              )}
              <div className="billing-plan-action">
                <Button
                  type="button"
                  aria-label={zh ? `${action} ${plan.name}` : `${preview ? "View" : "Choose"} ${plan.name}`}
                  disabled={busy}
                  onClick={() => onSelect(plan.id)}
                >
                  {action}<ArrowRight size={16} aria-hidden="true" />
                </Button>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
