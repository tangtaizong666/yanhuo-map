import { api } from "./api";

export type EngagementKind =
  "share_click" | "share_open" | "qr_open" | "route_click" | "reorder";
export type EngagementSource =
  "share_link" | "stall_qr" | "stall_detail" | "map" | "home_recent";

// Interaction counts are observations, never proof of arrival or purchase.
export function trackEngagement(
  type: EngagementKind,
  stallId: number,
  source: EngagementSource,
) {
  void api("/events", {
    method: "POST",
    body: { type, stall_id: stallId, source },
  }).catch(() => {});
}

export function newRequestKey(prefix: string) {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

export async function copyText(value: string) {
  if (!navigator.clipboard?.writeText) return false;
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}
