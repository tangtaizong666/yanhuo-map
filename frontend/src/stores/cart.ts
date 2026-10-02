import { defineStore } from "pinia";
import { ref, watch } from "vue";
import type { CartItem, Product, Portion } from "../lib/types";
import { normalizePortions } from "../lib/portions";
function initial(): Record<string, CartItem[]> {
  try {
    const stored = JSON.parse(localStorage.getItem("yanhuo-cart-v1") || "{}");
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
  const carts = ref<Record<string, CartItem[]>>(initial());
  watch(
    carts,
    (value) => {
      try {
        localStorage.setItem("yanhuo-cart-v1", JSON.stringify(value));
      } catch {}
    },
    { deep: true },
  );
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
  return { carts, items, setQuantity, clear, remove, total, count };
});
