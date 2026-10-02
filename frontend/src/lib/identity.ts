import type { User } from "./types";

export function isMerchantPath(value: string) {
  const path = value.split(/[?#]/, 1)[0];
  return path === "/merchant" || path.startsWith("/merchant/");
}

export function safeReturnTo(value: unknown) {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  )
    return "/";
  const path = value.split(/[?#]/, 1)[0];
  return path === "/login" || path.startsWith("/login/") ? "/" : value;
}

export function loginDestination(user: User | null, returnTo: unknown) {
  const target = safeReturnTo(returnTo);
  if (user?.is_merchant || user?.is_staff)
    return isMerchantPath(target) ? target : "/merchant";
  if (target.split(/[?#]/, 1)[0] === "/merchant/apply") return target;
  if (user?.merchant_application_status && target === "/")
    return "/merchant/apply";
  return isMerchantPath(target) ? "/" : target;
}
