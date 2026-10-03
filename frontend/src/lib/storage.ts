export function readStorage(key: string, scope: "local" | "session" = "local") {
  try {
    return (scope === "local" ? localStorage : sessionStorage).getItem(key);
  } catch {
    return null;
  }
}
export function writeStorage(
  key: string,
  value: string,
  scope: "local" | "session" = "local",
) {
  try {
    (scope === "local" ? localStorage : sessionStorage).setItem(key, value);
    return true;
  } catch {
    return false;
  }
}
export function removeStorage(
  key: string,
  scope: "local" | "session" = "local",
) {
  try {
    (scope === "local" ? localStorage : sessionStorage).removeItem(key);
    return true;
  } catch {
    return false;
  }
}
