import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";
import { useSession } from "./session";
import { readStorage, writeStorage, removeStorage } from "../lib/storage";
import type { CartItem, Product, Portion } from "../lib/types";
import { normalizePortions } from "../lib/portions";
function initial(key: string): Record<string, CartItem[]> {
  try {
    const stored = JSON.parse(readStorage(key) || "{}");
    if (!stored || typeof stored !== "object" || Array.isArray(stored))
      return {};
    return Object.fromEntries(
      Object.entries(stored)
        .filter(([id, rows]) => /^\d+$/.test(id) && Array.isArray(rows))
        .map(([id, rows]) => [
          id,
          (rows as CartItem[])
            .filter(
              (i) =>
                i?.product &&
                Number.isInteger(i.product.id) &&
                Number.isInteger(i.product.price_cents) &&
                i.product.price_cents > 0 &&
                typeof i.product.name === "string" &&
                Number.isInteger(i.quantity) &&
                i.quantity > 0 &&
                i.quantity <= 99,
            )
            .map((item) => ({
              ...item,
              portions: normalizePortions(item.portions, item.quantity),
            })),
        ]),
    );
  } catch {
    return {};
  }
}
export const useCart = defineStore("cart", () => {
  const session = useSession();
  const guestKey = "yanhuo-cart-v2:guest";
  const ownerKey = () =>
    session.user ? `yanhuo-cart-v2:user:${session.user.id}` : guestKey;
  // v1 had no owner. Archive it without displaying or silently assigning its notes.
  const legacy = readStorage("yanhuo-cart-v1");
  if (legacy && writeStorage("yanhuo-cart-legacy-v1", legacy))
    removeStorage("yanhuo-cart-v1");
  const memory = new Map<string, Record<string, CartItem[]>>();
  let activeKey = ownerKey();
  const carts = ref<Record<string, CartItem[]>>(initial(activeKey));
  const guest = ref<Record<string, CartItem[]>>(
    activeKey === guestKey ? carts.value : initial(guestKey),
  );
  const guestCount = computed(() =>
    session.user
      ? Object.values(guest.value)
          .flat()
          .reduce((n, row) => n + row.quantity, 0)
      : 0,
  );
  watch(
    carts,
    (value) => {
      memory.set(activeKey, value);
      if (activeKey === guestKey) guest.value = value;
      writeStorage(activeKey, JSON.stringify(value));
    },
    { deep: true, flush: "sync" },
  );
  watch(
    () => session.user?.id ?? null,
    () => {
      memory.set(activeKey, carts.value);
      activeKey = ownerKey();
      carts.value = memory.get(activeKey) || initial(activeKey);
      guest.value = memory.get(guestKey) || initial(guestKey);
    },
    { flush: "sync" },
  );
  function mergeGuest() {
    if (!session.user || !guestCount.value) return;
    const result = structuredClone(JSON.parse(JSON.stringify(carts.value)));
    for (const [id, rows] of Object.entries(guest.value)) {
      const current: CartItem[] = result[id] || (result[id] = []);
      for (const row of rows) {
        const found = current.find(
          (item) => item.product.id === row.product.id,
        );
        if (!found) current.push(JSON.parse(JSON.stringify(row)));
        else {
          const quantity = Math.min(99, found.quantity + row.quantity);
          found.portions = [
            ...normalizePortions(found.portions, found.quantity),
            ...normalizePortions(row.portions, row.quantity),
          ].slice(0, quantity);
          found.quantity = quantity;
        }
      }
    }
    carts.value = result;
    dismissGuest();
  }
  function dismissGuest() {
    guest.value = {};
    memory.set(guestKey, {});
    removeStorage(guestKey);
  }
  function items(id: number | string) {
    return carts.value[String(id)] || [];
  }
  function setQuantity(
    id: number | string,
    product: Product,
    quantity: number,
    portions?: Portion[],
  ) {
    const q = Math.max(0, Math.min(99, Math.floor(quantity)));
    const previous = items(id).find((i) => i.product.id === product.id);
    const rows = items(id).filter((i) => i.product.id !== product.id);
    if (q)
      rows.push({
        product: { ...product },
        quantity: q,
        portions: normalizePortions(portions ?? previous?.portions, q),
      });
    carts.value[String(id)] = rows;
  }
  function clear(id: number | string) {
    delete carts.value[String(id)];
  }
  function remove(id: number | string, pid: number) {
    carts.value[String(id)] = items(id).filter((i) => i.product.id !== pid);
  }
  function total(id: number | string) {
    return items(id).reduce(
      (n, i) => n + i.product.price_cents * i.quantity,
      0,
    );
  }
  function count(id: number | string) {
    return items(id).reduce((n, i) => n + i.quantity, 0);
  }
  return {
    carts,
    items,
    setQuantity,
    clear,
    remove,
    total,
    count,
    guestCount,
    mergeGuest,
    dismissGuest,
  };
});
