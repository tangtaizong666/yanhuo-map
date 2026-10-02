import { ref } from "vue";
export const notices = ref<{ id: number; message: string; type: string }[]>([]);
let next = 0;
export function notify(
  message: string,
  type: "success" | "error" | "info" = "info",
) {
  const id = ++next;
  notices.value.push({ id, message, type });
  window.setTimeout(() => {
    notices.value = notices.value.filter((n) => n.id !== id);
  }, 4500);
}
