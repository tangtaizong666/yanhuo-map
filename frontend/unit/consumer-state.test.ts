import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useSession } from "../src/stores/session";
import { useCart } from "../src/stores/cart";
import { useCheckoutDrafts } from "../src/stores/checkoutDrafts";
import { api } from "../src/lib/api";
import {
  mergeDiscoveryRows,
  remainingOrderQuantity,
} from "../src/lib/orderQuantity";
import type { Product, User, Config } from "../src/lib/types";

vi.mock("../src/lib/api", () => ({ api: vi.fn() }));
const user = (id: number): User => ({
  id,
  username: `u${id}`,
  display_name: "同学",
  is_merchant: false,
  is_staff: false,
});
const product = (id: number): Product => ({
  id,
  name: `菜${id}`,
  description: "",
  image: "",
  price_cents: 500,
  availability: "available",
  max_order_quantity: 10,
});
function storage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => {
  setActivePinia(createPinia());
  vi.stubGlobal("localStorage", storage());
  vi.stubGlobal("sessionStorage", storage());
  vi.mocked(api).mockReset();
});

describe("consumer account and draft boundaries", () => {
  it("isolates cart notes and quantities across accounts, restoring only their own cart", () => {
    const session = useSession();
    session.user = user(1);
    const cart = useCart();
    cart.setQuantity(1, product(1), 2, [
      { options: {}, note: "账号1备注" },
      { options: {}, note: "" },
    ]);
    session.user = user(2);
    expect(cart.items(1)).toEqual([]);
    cart.setQuantity(1, product(2), 1);
    session.user = user(1);
    expect(cart.items(1)[0].product.id).toBe(1);
    expect(cart.items(1)[0].portions?.[0].note).toBe("账号1备注");
  });
  it("enforces ten portions across different dishes rather than ten per dish", () => {
    const cart = useCart();
    cart.setQuantity(1, product(1), 7);
    cart.setQuantity(1, product(2), 7);
    expect(cart.count(1)).toBe(10);
    expect(cart.items(1)[1].quantity).toBe(3);
    cart.setQuantity(2, product(3), 10);
    expect(cart.count(2)).toBe(10);
  });
  it("guest merge preserves overflow instead of losing unmerged portions", () => {
    const session = useSession(),
      cart = useCart();
    cart.setQuantity(1, product(1), 8);
    session.user = user(1);
    cart.setQuantity(1, product(2), 6);
    cart.mergeGuest();
    expect(cart.count(1)).toBe(10);
    expect(cart.guestCount).toBe(4);
  });
  it('consumes exactly the submitted portions and preserves later additions and other stalls', () => {
    useSession().user = user(1);
    const cart = useCart();
    cart.setQuantity(1, product(1), 2);
    const submitted = cart.capturePortions(1);
    cart.setQuantity(1, product(1), 3);
    cart.setQuantity(1, product(2), 1);
    cart.setQuantity(2, product(3), 2);
    cart.consumePortions(1, submitted, 1);
    expect(cart.items(1).map(row => [row.product.id, row.quantity])).toEqual([[1, 1], [2, 1]]);
    expect(cart.count(2)).toBe(2);
    // Replaying the same successful response cannot consume any new food.
    cart.consumePortions(1, submitted, 1);
    expect(cart.count(1)).toBe(2);
  });
  it('preserves changed portion requirements and an independently replaced selection', () => {
    useSession().user = user(1);
    const cart = useCart();
    cart.setQuantity(1, product(1), 2, [{ options: {}, note: '原备注' }, { options: {}, note: '' }]);
    const submitted = cart.capturePortions(1);
    cart.setQuantity(1, product(1), 2, [{ options: {}, note: '后来改了口味' }, { options: {}, note: '' }]);
    cart.consumePortions(1, submitted, 1);
    expect(cart.count(1)).toBe(1);
    expect(cart.items(1)[0].portions?.[0].note).toBe('后来改了口味');
    const replaced = cart.capturePortions(1);
    cart.remove(1, 1);
    cart.setQuantity(1, product(1), 1, [{ options: {}, note: '后来改了口味' }]);
    cart.consumePortions(1, replaced, 1);
    expect(cart.count(1)).toBe(1);
  });
  it('reads newer persisted portions before applying a success from another tab', () => {
    useSession().user = user(1);
    const cart = useCart();
    cart.setQuantity(1, product(1), 1);
    const submitted = cart.capturePortions(1);
    const saved = JSON.parse(localStorage.getItem('yanhuo-cart-v2:user:1')!);
    saved[1][0].quantity = 2;
    saved[1][0].portions.push({ options: {}, note: '另一页添加' });
    saved[1][0].portionKeys.push('another-tab-portion');
    localStorage.setItem('yanhuo-cart-v2:user:1', JSON.stringify(saved));
    cart.consumePortions(1, submitted, 1);
    expect(cart.count(1)).toBe(1);
    expect(cart.items(1)[0].portions?.[0].note).toBe('另一页添加');
  });
  it('does not let an old success consume the current account cart', () => {
    const session = useSession();
    session.user = user(1);
    const cart = useCart();
    cart.setQuantity(1, product(1), 1);
    const submitted = cart.capturePortions(1);
    session.user = user(2);
    cart.setQuantity(1, product(1), 2);
    cart.consumePortions(1, submitted, 1);
    expect(cart.count(1)).toBe(2);
    session.user = user(1);
    expect(cart.count(1)).toBe(1);
  });
  it('keeps new in-memory portions when an existing cart is readable but storage writes exceed quota', () => {
    const session = useSession();
    session.user = user(1);
    localStorage.setItem('yanhuo-cart-v2:user:1', JSON.stringify({ 1: [{ product: product(1), quantity: 1, portionKeys: ['already-stored'] }] }));
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new DOMException('Full', 'QuotaExceededError'); });
    const cart = useCart();
    const submitted = cart.capturePortions(1);
    cart.setQuantity(1, product(1), 2);
    cart.setQuantity(2, product(2), 1);
    expect(cart.count(1)).toBe(2);
    cart.consumePortions(1, submitted, 1);
    expect(cart.count(1)).toBe(1);
    expect(cart.count(2)).toBe(1);
    session.user = user(2);
    session.user = user(1);
    expect(cart.count(1)).toBe(1);
    expect(cart.count(2)).toBe(1);
    expect(cart.items(1)[0].portionKeys).not.toContain('already-stored');
  });
  it('restores the account memory when storage becomes unreadable and preserves it when reads recover', () => {
    const session = useSession();
    session.user = user(1);
    const cart = useCart();
    cart.setQuantity(1, product(1), 1);
    const getter = vi.spyOn(localStorage, 'getItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
    cart.setQuantity(1, product(1), 2);
    session.user = user(2);
    expect(cart.count(1)).toBe(0);
    session.user = user(1);
    expect(cart.count(1)).toBe(2);
    getter.mockRestore();
    cart.setQuantity(2, product(2), 1);
    expect(cart.count(1)).toBe(2);
    expect(cart.count(2)).toBe(1);
  });
  it("keeps checkout contacts in memory and clears them synchronously on account switch", () => {
    const session = useSession();
    session.user = user(1);
    const drafts = useCheckoutDrafts();
    drafts.update(1, { phone: "13800000000", note: "联系人备注" });
    expect(drafts.read(1)?.phone).toBe("13800000000");
    session.user = user(2);
    expect(drafts.read(1)).toBeUndefined();
    session.user = user(1);
    expect(drafts.read(1)).toBeUndefined();
    expect(localStorage.getItem("checkout")).toBeNull();
  });
  it("ignores an old auth refresh after another account has become current", async () => {
    const session = useSession();
    session.user = user(1);
    const pending = deferred<User>();
    vi.mocked(api).mockReturnValueOnce(pending.promise);
    const refreshing = session.refreshUser();
    session.user = user(2);
    pending.resolve(user(1));
    await refreshing;
    expect(session.user?.id).toBe(2);
  });
  it("initial configuration response cannot overwrite a newer identity", async () => {
    const session = useSession();
    const pending = deferred<Config>();
    vi.mocked(api).mockReturnValueOnce(pending.promise);
    const loading = session.load();
    session.user = user(2);
    pending.resolve({
      demo_mode: true,
      brand: "烟火",
      amap_key: "",
      amap_proxy: "",
      areas: [],
      user: user(1),
    });
    await loading;
    expect(session.user?.id).toBe(2);
    expect(session.config?.user?.id).toBe(2);
  });
  it("deduplicates overlapping list pages and uses the newly confirmed row", () => {
    expect(
      mergeDiscoveryRows(
        [
          { id: 1, name: "旧" },
          { id: 2, name: "二" },
        ],
        [
          { id: 1, name: "新" },
          { id: 3, name: "三" },
        ],
      ),
    ).toEqual([
      { id: 1, name: "新" },
      { id: 2, name: "二" },
      { id: 3, name: "三" },
    ]);
    expect(remainingOrderQuantity([{ quantity: 12 }])).toBe(0);
  });
});
