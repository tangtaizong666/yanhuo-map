import { ref } from "vue";
export const notices = ref<{ id: number; message: string; type: string }[]>([]);
let next = 0;
export function notify(
  message: string,
  type: "success" | "error" | "info" = "info",
) {
  const id = ++next;
  // Fast order handling needs the latest confirmation, not a stack covering
  // the next order. Errors and informational messages remain independent.
  if (type === "success")
    notices.value = notices.value.filter((notice) => notice.type !== "success");
  notices.value.push({ id, message, type });
  window.setTimeout(() => {
    notices.value = notices.value.filter((n) => n.id !== id);
  }, 4500);
}
