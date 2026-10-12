/** Never request a legacy merchant-selected third-party image in a browser. */
export function ownedImage(value: unknown): string {
  if (typeof value !== "string" || !/^\/(images|media)\//.test(value)) return "";
  try {
    let path = value;
    for (let index = 0; index < 3; index++) path = decodeURIComponent(path);
    if (/[\\\u0000-\u001f?#]/.test(path) || path.split("/").some(part => part === "." || part === "..")) return "";
    return value;
  } catch { return ""; }
}
