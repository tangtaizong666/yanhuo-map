import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { useSession } from "./session";

interface CheckoutDraft {
  note: string;
  phone: string;
  fulfillment: "pickup" | "delivery";
  pointId: number | null;
  recipient: string;
}

// These optional contact details only live in this browser tab's memory.
// Returning from a dish keeps the form, while changing accounts clears it.
export const useCheckoutDrafts = defineStore("checkoutDrafts", () => {
  const session = useSession();
  const drafts = ref<Record<string, CheckoutDraft>>({});
  watch(
    () => session.user?.id ?? null,
    () => {
      drafts.value = {};
    },
    { flush: "sync" },
  );
  function read(stallId: number): CheckoutDraft | undefined {
    if (!session.user) return undefined;
    return drafts.value[String(stallId)];
  }
  function update(stallId: number, patch: Partial<CheckoutDraft>) {
    if (!session.user) return;
    drafts.value[String(stallId)] = {
      note: "",
      phone: "",
      fulfillment: "pickup",
      pointId: null,
      recipient: "",
      ...read(stallId),
      ...patch,
    };
  }
  function clear(stallId: number) {
    delete drafts.value[String(stallId)];
  }
  return { read, update, clear };
});
