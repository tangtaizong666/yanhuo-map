import type { User } from "./types";

export function isMerchantPath(value: string) {
  const path = value.split(/[?#]/, 1)[0];
  return path === "/merchant" || path.startsWith("/merchant/");
}

export function isOpsPath(value: string) {
  const path = value.split(/[?#]/, 1)[0];
  return path === "/ops" || path.startsWith("/ops/");
}

export function isOpsLoginPath(value: string) {
  const path = value.split(/[?#]/, 1)[0];
  return path === "/ops/login" || path.startsWith("/ops/login/");
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
  return path === "/login" ||
    path.startsWith("/login/") ||
    isOpsLoginPath(path)
    ? "/"
    : value;
}

export function loginDestination(user: User | null, returnTo: unknown) {
  const target = safeReturnTo(returnTo);
  const path = target.split(/[?#]/, 1)[0];
  if (user?.is_staff) {
    if (isOpsPath(target)) return target;
    if (user.is_merchant && isMerchantPath(target) && path !== "/merchant/apply")
      return target;
    return "/ops";
  }
  if (user?.is_merchant) {
    // An approved merchant has nothing left to apply for; open the workspace instead.
    if (path === "/merchant/apply") return "/merchant";
    return isMerchantPath(target) ? target : "/merchant";
  }
  if (path === "/merchant/apply") return target;
  if (user?.merchant_application_status && target === "/")
    return "/merchant/apply";
  return isMerchantPath(target) || isOpsPath(target) ? "/" : target;
}
