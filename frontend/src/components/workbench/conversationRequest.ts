export type PendingConversationRequest = { fingerprint: string; key: string };

/** A retry reuses the original request, even if polling has advanced its run revision. */
export function prepareConversationRequest<T extends { production: unknown }>(
  body: T,
  pending: PendingConversationRequest | null,
) {
  if (pending) {
    try {
      const previous = JSON.parse(pending.fingerprint) as T;
      if (JSON.stringify({ ...previous, production: null }) === JSON.stringify({ ...body, production: null }))
        return { request: pending, body: previous };
    } catch { /* A damaged local retry record must not prevent a new send. */ }
  }
  return { request: { fingerprint: JSON.stringify(body), key: crypto.randomUUID() }, body };
}
