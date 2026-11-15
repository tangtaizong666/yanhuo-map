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
  if (!value) return false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      return false;
    }
  }
  if (
    typeof window === "undefined" ||
    window.isSecureContext !== false ||
    typeof document === "undefined"
  ) {
    return false;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  const previousFocus =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  try {
    textarea.focus({ preventScroll: true });
    textarea.select();
    textarea.setSelectionRange(0, value.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
    previousFocus?.focus({ preventScroll: true });
  }
}
