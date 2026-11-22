import { Check } from "lucide-react";
import {
  membershipDuration,
  money,
  points,
  type MembershipPlan,
} from "@/lib/billing";

export function BillingPlanCards({
  plans,
  selected,
  busy,
  zh,
  onSelect,
}: {
  plans: MembershipPlan[];
  selected?: string;
  busy: boolean;
  zh: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      className="billing-packs billing-plans"
      role="group"
      aria-label={zh ? "选择会员套餐" : "Choose a membership plan"}
    >
      {plans.map((plan) => (
        <button
          key={plan.id}
          className={`billing-pack ${selected === plan.id ? "is-selected" : ""}`}
          aria-pressed={selected === plan.id}
          disabled={busy}
          onClick={() => onSelect(plan.id)}
        >
          <div className="billing-row">
            <span>{plan.name}</span>
            {selected === plan.id && <Check size={17} />}
          </div>
          <strong>{money(plan.amount_minor, plan.currency, zh)}</strong>
          <div className="billing-plan-duration">
            {plan.original_amount_minor != null &&
              plan.original_amount_minor > plan.amount_minor && (
                <del aria-label={zh ? "原价" : "Original price"}>
                  {money(plan.original_amount_minor, plan.currency, zh)}
                </del>
              )}
            <span>{membershipDuration(plan, zh)}</span>
          </div>
          {plan.description && <p>{plan.description}</p>}
          <span className="billing-plan-allowance">
            {zh
              ? `期内 ${points(plan.credits, zh)} 积分额度`
              : `${points(plan.credits, zh)} points per term`}
          </span>
          {!!plan.features.length && (
            <ul className="billing-plan-features">
              {plan.features.map((feature, index) => (
                <li key={index}>{feature}</li>
              ))}
            </ul>
          )}
        </button>
      ))}
    </div>
  );
}
