import type { Portion } from "./types";
import { ref } from 'vue';
export interface CheckoutRequest {
  stall_id: number;
  items: {
    product_id: number;
    quantity: number;
    expected_price_cents: number;
    portions?: Portion[];
  }[];
  note: string;
  contact_phone: string;
  fulfillment_type?: "delivery";
  delivery_point_id?: number | null;
  recipient_name?: string;
  expected_delivery_fee_cents?: number;
  idempotency_key: string;
}

export interface CheckoutSubmission {
  version: 2;
  key: string;
  fingerprint: string;
  cart: string;
  cartPortions?: { productId: number; keys: string[] }[];
  note: string;
  phone: string;
  fulfillment: "pickup" | "delivery";
  pointId: number | null;
  recipient: string;
  body: CheckoutRequest;
  summary: string;
  totalCents: number;
}

// Retain the original write, scoped to this tab, account and stall. Reloading or
// editing a cart elsewhere must never silently replace an unresolved request.
const pending = new Map<string, CheckoutSubmission>();
const revision = ref(0);
export function saveSubmission(key: string, submission: CheckoutSubmission) {
  pending.set(key, submission);
  revision.value++;
  try {
    sessionStorage.setItem(key, JSON.stringify(submission));
  } catch {
    // In-memory recovery still survives in-app navigation when storage is denied.
  }
}
export function removeSubmission(key: string) {
  pending.delete(key);
  revision.value++;
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* optional browser persistence */
  }
}

export function listSubmissions(userId: number | undefined) {
  // Let screens react when an in-app retry resolves or a new write starts.
  void revision.value;
  if (!userId) return [];
  const prefix = `yanhuo-checkout-${userId}-`;
  const keys = new Set(pending.keys());
  try {
    for (let index = 0; index < sessionStorage.length; index++) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(prefix)) keys.add(key);
    }
  } catch { /* In-memory records still provide recovery with storage denied. */ }
  return [...keys].filter(key => key.startsWith(prefix)).flatMap(key => {
    const stallId = Number(key.slice(prefix.length));
    if (!Number.isInteger(stallId) || stallId < 1) return [];
    const record = readSubmission(key, stallId);
    return record ? [{ stallId, record }] : [];
  });
}

export function readSubmission(
  key: string,
  stallId: number,
): CheckoutSubmission | null {
  if (pending.has(key)) return pending.get(key)!;
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || "null");
    if (!saved || typeof saved.key !== "string" || !saved.cart) return null;
    // Older versions persisted enough information to reconstruct the exact
    // request; migrate that record instead of abandoning an existing order.
    if (!saved.body) {
      const cart = JSON.parse(saved.cart) as number[][];
      const fingerprint = JSON.parse(saved.fingerprint);
      saved.body = {
        stall_id: stallId,
        items: cart.map(([id, price, quantity]) => ({
          product_id: id,
          quantity,
          expected_price_cents: price,
        })),
        note: (saved.note || "").trim(),
        contact_phone: (saved.phone || "").trim(),
        ...(saved.fulfillment === "delivery"
          ? {
              fulfillment_type: "delivery",
              delivery_point_id: saved.pointId,
              recipient_name: (saved.recipient || "").trim(),
              expected_delivery_fee_cents: fingerprint[4][2],
            }
          : {}),
        idempotency_key: saved.key,
      };
      saved.version = 2;
      saved.summary = `原先提交的 ${cart.reduce((n, row) => n + row[2]!, 0)} 份餐点`;
      saved.totalCents =
        cart.reduce((n, row) => n + row[1]! * row[2]!, 0) +
        (saved.body.expected_delivery_fee_cents || 0);
    }
    if (
      saved.body.stall_id !== stallId ||
      saved.body.idempotency_key !== saved.key ||
      !Array.isArray(saved.body.items) ||
      !saved.body.items.length
    )
      return null;
    pending.set(key, saved);
    return saved;
  } catch {
    return null;
  }
}
