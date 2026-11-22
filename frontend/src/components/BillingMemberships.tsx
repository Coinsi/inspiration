import {
  billingDate,
  membershipLabel,
  points,
  type MembershipTerm,
} from "@/lib/billing";

export function BillingMemberships({
  terms,
  zh = true,
  admin = false,
}: {
  terms: MembershipTerm[];
  zh?: boolean;
  admin?: boolean;
}) {
  return (
    <div className="billing-memberships">
      {terms.map((term) => (
        <article className="billing-membership" key={term.order_id}>
          <div className="billing-row">
            <h3>{term.plan_name}</h3>
            <span className={`billing-status is-${term.status}`}>
              {membershipLabel(term.status, zh)}
            </span>
          </div>
          {admin && (
            <p className="billing-muted">
              {term.user_name || term.user_id}
              {term.user_email && <> · {term.user_email}</>}
            </p>
          )}
          <p className="billing-membership-dates">
            <span>
              {zh ? "生效" : "Starts"} {billingDate(term.starts_at, zh)}
            </span>
            <span>
              {zh ? "到期" : "Expires"} {billingDate(term.expires_at, zh)}
            </span>
          </p>
          <dl className="billing-membership-balances">
            <div>
              <dt>{zh ? "期内额度" : "Term allowance"}</dt>
              <dd>{points(term.credits, zh)}</dd>
            </div>
            <div>
              <dt>{zh ? "当前可用" : "Available now"}</dt>
              <dd>{points(term.available, zh)}</dd>
            </div>
            <div>
              <dt>{zh ? "预留额度" : "Reserved"}</dt>
              <dd>{points(term.reserved, zh)}</dd>
            </div>
          </dl>
          {term.status !== "active" && (
            <p className="billing-muted">
              {zh
                ? "仅生效中的会员额度可用于创作。"
                : "Only an active membership allowance can be used."}
            </p>
          )}
          {admin && <p className="billing-muted billing-id">{term.order_id}</p>}
        </article>
      ))}
    </div>
  );
}
