import { defineStore } from "pinia";
import { computed, onScopeDispose, ref, watch } from "vue";
import { useSession } from "./session";
import { readStorage, writeStorage, removeStorage } from "../lib/storage";
import type { CartItem, Product, Portion } from "../lib/types";
import { normalizePortions } from "../lib/portions";
import { remainingOrderQuantity } from "../lib/orderQuantity";
function newPortionKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
function portionSignature(portion: Portion) {
  return JSON.stringify([Object.entries(portion.options).sort(([a], [b]) => a.localeCompare(b)), portion.note]);
}
function portionKeys(item: CartItem) {
  const used = new Set<string>();
  return Array.from({ length: item.quantity }, (_, index) => {
    const previous = item.portionKeys?.[index];
    const key = typeof previous === 'string' && previous && !used.has(previous) ? previous : newPortionKey();
    used.add(key);
    return key;
  });
}
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
              portionKeys: portionKeys(item),
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
  // A failed write does not mean the old readable value came from another tab.
  // Track what was actually observed, including before the first attempted write.
  const observedStorage = new Map<string, string | null>([[activeKey, readStorage(activeKey)]]);
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
      const encoded = JSON.stringify(value);
      if (writeStorage(activeKey, encoded)) observedStorage.set(activeKey, encoded);
    },
    { deep: true, flush: "sync", immediate: true },
  );
  watch(
    () => session.user?.id ?? null,
    () => {
      memory.set(activeKey, carts.value);
      activeKey = ownerKey();
      const stored = readStorage(activeKey);
      const cached = memory.get(activeKey);
      const sameStoredValue = observedStorage.get(activeKey) === stored;
      // A denied read cannot invalidate the account's in-memory draft or its
      // last observed disk value; reads may recover while writes still fail.
      if (stored !== null || !cached) observedStorage.set(activeKey, stored);
      carts.value = cached && (stored === null || sameStoredValue) ? cached : initial(activeKey);
      guest.value = memory.get(guestKey) || initial(guestKey);
    },
    { flush: "sync" },
  );
  function syncStoredCart() {
    const stored = readStorage(activeKey);
    if (stored !== null && stored !== observedStorage.get(activeKey)) {
      observedStorage.set(activeKey, stored);
      carts.value = initial(activeKey);
    }
  }
  // Another tab can add food while this tab is waiting for an order response.
  // Read the latest persisted draft before consuming the submitted portions.
  function storageChanged(event: StorageEvent) {
    if (event.key === activeKey) syncStoredCart();
    if (event.key === guestKey && activeKey !== guestKey) guest.value = initial(guestKey);
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('storage', storageChanged);
    onScopeDispose(() => window.removeEventListener('storage', storageChanged));
  }
  function mergeGuest() {
    if (!session.user || !guestCount.value) return;
    const result = structuredClone(JSON.parse(JSON.stringify(carts.value)));
    const leftovers: Record<string, CartItem[]> = {};
    for (const [id, rows] of Object.entries(guest.value)) {
      const current: CartItem[] = result[id] || (result[id] = []);
      for (const row of rows) {
        const take = Math.min(row.quantity, remainingOrderQuantity(current));
        const found = current.find(
          (item) => item.product.id === row.product.id,
        );
        if (take && !found)
          current.push({
            ...JSON.parse(JSON.stringify(row)),
            quantity: take,
            portions: normalizePortions(row.portions, row.quantity).slice(
              0,
              take,
            ),
            portionKeys: portionKeys(row).slice(0, take),
          });
        else if (take && found) {
          found.portions = [
            ...normalizePortions(found.portions, found.quantity),
            ...normalizePortions(row.portions, row.quantity).slice(0, take),
          ];
          found.portionKeys = [...portionKeys(found), ...portionKeys(row).slice(0, take)];
          found.quantity += take;
        }
        if (take < row.quantity)
          (leftovers[id] ||= []).push({
            ...row,
            quantity: row.quantity - take,
            portions: normalizePortions(row.portions, row.quantity).slice(take),
            portionKeys: portionKeys(row).slice(take),
          });
      }
    }
    carts.value = result;
    guest.value = leftovers;
    memory.set(guestKey, leftovers);
    writeStorage(guestKey, JSON.stringify(leftovers));
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
    syncStoredCart();
    const previous = items(id).find((i) => i.product.id === product.id);
    const rows = items(id).filter((i) => i.product.id !== product.id);
    const q = Math.max(
      0,
      Math.min(remainingOrderQuantity(rows), Math.floor(quantity)),
    );
    if (q) {
      const nextPortions = normalizePortions(portions ?? previous?.portions, q);
      const oldPortions = normalizePortions(previous?.portions, previous?.quantity || 0);
      const oldKeys = previous ? portionKeys(previous) : [];
      rows.push({
        product: { ...product },
        quantity: q,
        portions: nextPortions,
        portionKeys: nextPortions.map((portion, index) =>
          oldKeys[index] && previous?.product.price_cents === product.price_cents &&
          portionSignature(oldPortions[index]!) === portionSignature(portion)
            ? oldKeys[index]! : newPortionKey()),
      });
    }
    carts.value[String(id)] = rows;
  }
  function capturePortions(id: number | string) {
    return items(id).map((item) => ({ productId: item.product.id, keys: [...(item.portionKeys || [])] }));
  }
  function consumePortions(id: number | string, submitted: ReturnType<typeof capturePortions>, userId: number) {
    if (session.user?.id !== userId) return;
    syncStoredCart();
    const submittedKeys = new Map(submitted.map((row) => [row.productId, new Set(row.keys)]));
    const remaining = items(id).flatMap((item) => {
      const keys = portionKeys(item), portions = normalizePortions(item.portions, item.quantity);
      const keep = keys.map((key, index) => ({ key, index })).filter(({ key }) => !submittedKeys.get(item.product.id)?.has(key));
      return keep.length ? [{ ...item, quantity: keep.length, portions: keep.map(({ index }) => portions[index]!), portionKeys: keep.map(({ key }) => key) }] : [];
    });
    if (remaining.length) carts.value[String(id)] = remaining;
    else delete carts.value[String(id)];
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
  function remaining(id: number | string) {
    return remainingOrderQuantity(items(id));
  }
  return {
    carts,
    items,
    setQuantity,
    clear,
    remove,
    total,
    count,
    remaining,
    capturePortions,
    consumePortions,
    guestCount,
    mergeGuest,
    dismissGuest,
  };
});
