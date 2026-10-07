<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  AlertCircle,
  Check,
  ChevronRight,
  Clock3,
  MapPin,
  Minus,
  Plus,
  ShoppingBag,
  Store,
  Wallet,
} from "lucide-vue-next";
import { api, ApiError, money } from "../lib/api";
import { useCart } from "../stores/cart";
import { useSession } from "../stores/session";
import { useCheckoutDrafts } from "../stores/checkoutDrafts";
import { notify } from "../lib/notify";
import { useBrowseReturn } from "../lib/browseReturn";
import CheckoutPaymentMethods from "../components/CheckoutPaymentMethods.vue";
import CheckoutFulfillment from "../components/CheckoutFulfillment.vue";
import SimulationNotice from "../components/SimulationNotice.vue";
import PortionEditor from "../components/PortionEditor.vue";
import ReceivingNotice from "../components/ReceivingNotice.vue";
import { productAvailable } from "../lib/availability";
import { invalidPortions, specifiedPortions } from "../lib/portions";
import type { CartItem, Product } from "../lib/types";
import {
  readSubmission,
  removeSubmission,
  saveSubmission,
  type CheckoutSubmission,
} from "../lib/checkoutSubmission";

const route = useRoute();
const router = useRouter();
const cart = useCart();
const session = useSession();
const drafts = useCheckoutDrafts();
const stallId = Number(route.params.id);
// App mounts this page after the session is loaded and recreates it on account changes.
const pageUserId = session.user?.id;
const requestController = new AbortController();
let disposed = false;
function isCurrentPage() {
  return !disposed && session.user?.id === pageUserId;
}
onUnmounted(() => {
  disposed = true;
  // This only stops waiting in the browser. A submitted order may already exist,
  // so leave its account-specific idempotency record intact for a later retry.
  requestController.abort();
});
const stall = ref<any>(null);
const loading = ref(true);
const busy = ref(false);
const error = ref("");
useBrowseReturn({
  capture: () => ({}),
  async restore(snapshot, control) {
    if (!await control.wait(() => !loading.value)) return false;
    const productId = snapshot.anchor.match(/\/products\/(\d+)$/)?.[1];
    return !error.value && !!stall.value && (!productId || stall.value.products.some((product: Product) => product.id === Number(productId)));
  },
});
const relatedOrderIds = ref<string[]>([]);
const priceChanged = ref(false);
const keyboardInset = ref(0);
const checkoutPage = ref<HTMLElement>();
const actionBar = ref<HTMLElement>();
const actionBarHeight = ref(86);
watch(actionBar, (element, _previous, onCleanup) => {
  if (!element) {
    actionBarHeight.value = 0;
    return;
  }
  const measure = () => {
    actionBarHeight.value = Math.ceil(element.getBoundingClientRect().height);
  };
  const observer = new ResizeObserver(measure);
  observer.observe(element);
  measure();
  onCleanup(() => observer.disconnect());
});
function updateViewport() {
  const viewport = window.visualViewport;
  const active = document.activeElement as HTMLElement | null;
  const editing = active?.matches("input,textarea,select");
  keyboardInset.value =
    viewport && editing
      ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)
      : 0;
  if (
    keyboardInset.value > 80 &&
    window.innerWidth <= 800 &&
    active &&
    !active.closest("dialog")
  ) {
    requestAnimationFrame(() => {
      if (disposed || document.activeElement !== active) return;
      const actionHeight =
        actionBar.value?.getBoundingClientRect().height || 86;
      const bottom =
        (viewport?.height || window.innerHeight) +
        (viewport?.offsetTop || 0) -
        actionHeight -
        16;
      const rect = active.getBoundingClientRect();
      if (rect.bottom > bottom)
        window.scrollBy({ top: rect.bottom - bottom, behavior: "auto" });
    });
  }
}
onMounted(() => {
  window.visualViewport?.addEventListener("resize", updateViewport);
  window.visualViewport?.addEventListener("scroll", updateViewport);
  document.addEventListener("focusin", updateViewport);
  document.addEventListener("focusout", updateViewport);
});
onUnmounted(() => {
  window.visualViewport?.removeEventListener("resize", updateViewport);
  window.visualViewport?.removeEventListener("scroll", updateViewport);
  document.removeEventListener("focusin", updateViewport);
  document.removeEventListener("focusout", updateViewport);
});
const note = computed({
  get: () => drafts.read(stallId)?.note || "",
  set: (value: string) => drafts.update(stallId, { note: value }),
});
const phone = computed({
  get: () => drafts.read(stallId)?.phone || "",
  set: (value: string) => drafts.update(stallId, { phone: value }),
});
const fulfillment = computed({
  get: () => drafts.read(stallId)?.fulfillment || "pickup",
  set: (value: "pickup" | "delivery") =>
    drafts.update(stallId, { fulfillment: value }),
});
const pointId = computed({
  get: () => drafts.read(stallId)?.pointId ?? null,
  set: (value: number | null) => drafts.update(stallId, { pointId: value }),
});
const recipient = computed({
  get: () => drafts.read(stallId)?.recipient || "",
  set: (value: string) => drafts.update(stallId, { recipient: value }),
});
const isDelivery = computed(() => fulfillment.value === "delivery");
const contactExpanded = ref(false);
const contactSummary = computed(
  () =>
    [phone.value.trim(), note.value.trim()].filter(Boolean).join(" · ") ||
    "手机号、整单备注均可不填",
);
const isSimulation = computed(
  () => stall.value?.wechat_payment?.mode === "simulation",
);
const deliveryFee = computed(() =>
  isDelivery.value ? stall.value?.delivery?.fee_cents || 0 : 0,
);
const selectedPoint = computed(() =>
  stall.value?.delivery?.points?.find((p: any) => p.id === pointId.value),
);
const deliveryCanSubmit = computed(
  () =>
    !isDelivery.value ||
    !!(
      stall.value?.delivery?.available &&
      selectedPoint.value &&
      recipient.value.trim() &&
      /^[0-9+() \-]+$/.test(phone.value.trim()) &&
      phone.value.replace(/\D/g, "").length >= 7 &&
      phone.value.replace(/\D/g, "").length <= 15 &&
      total.value >= stall.value.delivery.min_order_cents
    ),
);
const items = computed(() => cart.items(stallId));
const total = computed(() => cart.total(stallId));
function currentProduct(item: CartItem): Product {
  return (
    stall.value?.products?.find(
      (product: Product) => product.id === item.product.id,
    ) || item.product
  );
}
const tastesChanged = computed(() =>
  items.value.some((item) =>
    invalidPortions(currentProduct(item), item.portions, item.quantity),
  ),
);
function productProblem(item: CartItem) {
  const product = stall.value?.products?.find(
    (row: Product) => row.id === item.product.id,
  ) as Product | undefined;
  if (!product) return "这道餐点已下架，请移除后继续。";
  if (!productAvailable(product))
    return product.sale_paused
      ? "商家暂停了这道餐点的销售，请移除或稍后再来。"
      : "这道餐点暂不可售，请移除后继续。";
  if (cart.count(stallId) > 10) return "每单合计最多 10 份，请调整数量。";
  return "";
}
const unavailableItems = computed(
  () => !!stall.value && items.value.some((item) => !!productProblem(item)),
);
const mealsExpanded = ref(false);
function itemPriceChanged(item: CartItem) {
  return currentProduct(item).price_cents !== item.product.price_cents;
}
const mealsNeedReview = computed(
  () =>
    priceChanged.value ||
    (!!stall.value &&
      items.value.some(
        (item) =>
          !!productProblem(item) ||
          itemPriceChanged(item) ||
          invalidPortions(currentProduct(item), item.portions, item.quantity),
      )),
);
const showAllMeals = computed(
  () => items.value.length <= 3 || mealsExpanded.value || mealsNeedReview.value,
);
const shownMeals = computed(() =>
  showAllMeals.value ? items.value : items.value.slice(0, 2),
);
const hiddenMealQuantity = computed(() =>
  items.value.slice(2).reduce((count, item) => count + item.quantity, 0),
);
const cartFingerprint = computed(() =>
  JSON.stringify(
    items.value
      .slice()
      .sort((a, b) => a.product.id - b.product.id)
      .map((item) => {
        const portions = specifiedPortions(item.portions, item.quantity);
        return [
          item.product.id,
          item.product.price_cents,
          item.quantity,
          ...(portions ? [portions] : []),
        ];
      }),
  ),
);
const fingerprint = computed(() =>
  JSON.stringify([
    cartFingerprint.value,
    note.value.trim(),
    phone.value.trim(),
    fulfillment.value,
    isDelivery.value
      ? [pointId.value, recipient.value.trim(), deliveryFee.value]
      : null,
  ]),
);
const keyName = `yanhuo-checkout-${pageUserId}-${stallId}`;
const submission = ref<CheckoutSubmission | null>(null);
const unresolved = computed(() => !!submission.value);
const editingLocked = computed(() => busy.value || unresolved.value);
type CheckoutProblem = {
  reason: string;
  target:
    | "stall"
    | "item"
    | "taste"
    | "fulfillment"
    | "minimum"
    | "point"
    | "recipient"
    | "phone";
  productId?: number;
};
// This explains the existing submit guards; it does not change admission or writes.
const firstProblem = computed<CheckoutProblem | null>(() => {
  if (!stall.value) return null;
  if (!stall.value.can_order)
    return {
      target: "stall",
      reason:
        stall.value.order_unavailable_reason ||
        (!stall.value.transaction_enabled
          ? "该摊位暂未开放在线点单。"
          : "摊位当前无法接单，请确认最新营业状态。"),
    };
  const unavailable = items.value.find((item) => productProblem(item));
  if (unavailable)
    return {
      target: "item",
      productId: unavailable.product.id,
      reason: `${unavailable.product.name}：${productProblem(unavailable)}`,
    };
  const taste = items.value.find((item) =>
    invalidPortions(currentProduct(item), item.portions, item.quantity),
  );
  if (taste)
    return {
      target: "taste",
      productId: taste.product.id,
      reason: `${taste.product.name}的口味选项已变更，请重新选择。`,
    };
  if (!isDelivery.value) return null;
  if (!stall.value.delivery?.available)
    return {
      target: "fulfillment",
      reason: stall.value.delivery?.reason || "配送尚未开放，可改为到摊自取。",
    };
  if (total.value < stall.value.delivery.min_order_cents)
    return {
      target: "minimum",
      reason: `餐费还差 ¥${money(stall.value.delivery.min_order_cents - total.value)} 达到起送金额。`,
    };
  if (!selectedPoint.value)
    return { target: "point", reason: "请选择校园交接点。" };
  if (!recipient.value.trim())
    return { target: "recipient", reason: "请填写收餐人称呼。" };
  if (!deliveryCanSubmit.value)
    return { target: "phone", reason: "请填写有效的配送联系号码。" };
  return null;
});
async function goToProblem() {
  const problem = firstProblem.value;
  if (editingLocked.value || !problem || problem.target === "stall") return;
  if (["item", "taste", "minimum"].includes(problem.target))
    mealsExpanded.value = true;
  if (problem.target === "recipient" || problem.target === "phone")
    contactExpanded.value = true;
  await nextTick();
  if (!isCurrentPage() || editingLocked.value) return;
  const selectors = {
    item: `[data-checkout-product="${problem.productId}"] .quantity-control button`,
    taste: `[data-checkout-product="${problem.productId}"] .portion-open`,
    fulfillment: ".fulfillment-options button",
    minimum: ".checkout-product .quantity-control button:last-child",
    point: ".fulfillment-card select",
    recipient: '[data-checkout-field="recipient"]',
    phone: '[data-checkout-field="phone"]',
  };
  const target = checkoutPage.value?.querySelector<HTMLElement>(
    selectors[problem.target],
  );
  if (!target) return;
  target.scrollIntoView({ block: "center", behavior: "instant" });
  target.focus({ preventScroll: true });
  if (problem.target === "taste") {
    target.click();
    await nextTick();
    const invalidChoice = checkoutPage.value?.querySelector<HTMLElement>(
      ".portion-dialog[open] .portion-removed button",
    );
    invalidChoice?.focus();
  }
}
function restoreSubmission() {
  const saved = readSubmission(keyName, stallId);
  if (!saved) return;
  submission.value = saved;
  if (!drafts.read(stallId)) {
    drafts.update(stallId, {
      note: saved.note,
      phone: saved.phone,
      fulfillment: saved.fulfillment,
      pointId: saved.pointId,
      recipient: saved.recipient,
    });
  }
}
function forgetSubmission() {
  submission.value = null;
  removeSubmission(keyName);
}
function prepareSubmission() {
  const key =
    globalThis.crypto?.randomUUID?.() ||
    `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const record: CheckoutSubmission = {
    version: 2,
    key,
    fingerprint: fingerprint.value,
    cart: cartFingerprint.value,
    cartPortions: cart.capturePortions(stallId),
    note: note.value,
    phone: phone.value,
    fulfillment: fulfillment.value,
    pointId: pointId.value,
    recipient: recipient.value,
    body: {
      stall_id: stallId,
      items: items.value.map((item) => ({
        product_id: item.product.id,
        quantity: item.quantity,
        expected_price_cents: item.product.price_cents,
        ...(specifiedPortions(item.portions, item.quantity)
          ? { portions: specifiedPortions(item.portions, item.quantity) }
          : {}),
      })),
      note: note.value.trim(),
      contact_phone: phone.value.trim(),
      ...(isDelivery.value
        ? {
            fulfillment_type: "delivery" as const,
            delivery_point_id: pointId.value,
            recipient_name: recipient.value.trim(),
            expected_delivery_fee_cents: deliveryFee.value,
          }
        : {}),
      idempotency_key: key,
    },
    summary: items.value
      .map((item) => `${item.product.name} × ${item.quantity}`)
      .join("、"),
    totalCents: total.value + deliveryFee.value,
  };
  submission.value = record;
  saveSubmission(keyName, record);
  return record;
}
async function refreshStall() {
  const latest = await api(`/stalls/${stallId}`, {
    signal: requestController.signal,
  });
  if (!isCurrentPage()) return false;
  stall.value = latest;
  return true;
}
onMounted(async () => {
  try {
    await session.load();
    if (!isCurrentPage()) return;
    if (!session.user) {
      await router.replace({
        path: "/login",
        query: { returnTo: route.fullPath },
      });
      return;
    }
    restoreSubmission();
    await refreshStall();
  } catch (e) {
    if (isCurrentPage()) error.value = (e as Error).message;
  } finally {
    if (isCurrentPage()) loading.value = false;
  }
});
function updateQuantity(item: any, quantity: number) {
  if (editingLocked.value) return;
  const current = stall.value?.products?.find(
    (p: any) => p.id === item.product.id,
  );
  if (
    quantity > item.quantity &&
    (!productAvailable(current) ||
      cart.remaining(stallId) < quantity - item.quantity)
  ) {
    notify("每个摊位一单合计最多 10 份", "info");
    return;
  }
  cart.setQuantity(stallId, item.product, quantity);
  priceChanged.value = false;
}
async function submit() {
  if (
    !isCurrentPage() ||
    !session.user ||
    busy.value ||
    unresolved.value ||
    !items.value.length ||
    !stall.value?.can_order ||
    tastesChanged.value ||
    unavailableItems.value ||
    !deliveryCanSubmit.value
  )
    return;
  await sendSubmission(prepareSubmission());
}
async function confirmSubmission() {
  if (!isCurrentPage() || !session.user || busy.value || !submission.value)
    return;
  // Deliberately bypass current cart/stall availability: the original order may
  // already exist, even if its stock is now sold out or the shop has closed.
  await sendSubmission(submission.value, true);
}
async function sendSubmission(record: CheckoutSubmission, recovering = false) {
  busy.value = true;
  error.value = "";
  relatedOrderIds.value = [];
  try {
    const order = await api<any>("/orders", {
      method: "POST",
      signal: requestController.signal,
      body: record.body,
    });
    if (!isCurrentPage()) return;
    if (!order?.id || order.stall_id !== stallId)
      throw new ApiError("订单返回信息不完整，请确认原订单结果。", 502, null);
    const unchangedCart = record.cart === cartFingerprint.value;
    const unchangedDraft = record.fingerprint === fingerprint.value;
    if (record.cartPortions)
      cart.consumePortions(stallId, record.cartPortions, pageUserId!);
    else if (unchangedCart) cart.clear(stallId);
    forgetSubmission();
    if (unchangedDraft && !cart.count(stallId)) drafts.clear(stallId);
    notify(
      order.fulfillment_type === "delivery"
        ? "配送订单已创建，请完成微信付款"
        : "订单已提交，正在等待商家接单",
      "success",
    );
    await router.replace(`/orders/${order.id}`);
  } catch (e) {
    if (!isCurrentPage()) {
      // Identity verification may unmount this page before the first write is
      // ever sent. Do not leave a phantom recovery request for the old account;
      // an actual retry still retains its original uncertain operation.
      if (
        !recovering &&
        e instanceof ApiError &&
        e.data?.submitted === false &&
        readSubmission(keyName, stallId)?.key === record.key
      )
        removeSubmission(keyName);
      return;
    }
    error.value = (e as Error).message;
    if (e instanceof ApiError && Array.isArray(e.data?.order_ids)) {
      relatedOrderIds.value = e.data.order_ids
        .filter(
          (id: unknown) =>
            typeof id === "string" && /^[a-f0-9-]{36}$/i.test(id),
        )
        .slice(0, 10);
    }
    // During recovery, CSRF/auth/schema failures only describe this retry, not
    // the original write. Only business checks after server-side duplicate
    // lookup prove there is no committed original order to recover.
    const rejectedAfterDuplicateCheck = new Set([
      "stall_unavailable",
      "location_missing",
      "invalid_items",
      "price_changed",
      "out_of_stock",
      "amount_limit",
      "delivery_unavailable",
      "delivery_point_unavailable",
      "delivery_minimum",
      "delivery_fee_changed",
      "tastes_changed",
      "product_sale_paused",
      "prep_capacity_reached",
      "ordering_stopped",
      "stall_reservation_limit",
      "active_reservation_limit",
      "order_quantity_limit",
      "checkout_rate_limited",
    ]);
    const rejected =
      e instanceof ApiError &&
      ((e.status >= 400 &&
        e.status < 500 &&
        rejectedAfterDuplicateCheck.has(e.code || "")) ||
        (!recovering &&
          (e.data?.submitted === false ||
            (e.status === 400 && !!e.data?.errors))));
    if (rejected) forgetSubmission();
    if (e instanceof ApiError && [401, 403].includes(e.status)) {
      try {
        await session.refreshUser();
        if (!isCurrentPage()) return;
        if (!session.user)
          await router.replace({
            path: "/login",
            query: { returnTo: route.fullPath },
          });
      } catch {
        /* keep the original error */
      }
    }
    try {
      // A transport error may have occurred after the server committed an order.
      // Preserve its exact request/key; only re-price an explicitly rejected order.
      if (!rejected || !(e instanceof ApiError) || !e.code) return;
      if (!(await refreshStall()) || !isCurrentPage()) return;
      if (e.code === "delivery_fee_changed") {
        forgetSubmission();
        priceChanged.value = true;
        error.value = "配送费刚刚更新，已显示最新应付金额。请核对后再次提交。";
        return;
      }
      if (e.code !== "price_changed") return;
      let changed = false;
      for (const item of [...items.value]) {
        const current = stall.value.products.find(
          (p: any) => p.id === item.product.id,
        );
        if (current && current.price_cents !== item.product.price_cents) {
          cart.setQuantity(stallId, current, item.quantity);
          changed = true;
        }
      }
      if (changed) {
        forgetSubmission();
        priceChanged.value = true;
        error.value =
          "商家刚刚更新了商品价格。已显示最新金额，请核对后再次提交。";
      }
    } catch {
      /* Preserve the actionable original request error. */
    }
  } finally {
    if (isCurrentPage()) busy.value = false;
  }
}
</script>

<template>
  <div
    ref="checkoutPage"
    class="page checkout-page"
    :style="{
      '--checkout-keyboard-inset': `${keyboardInset}px`,
      '--checkout-action-height': `${actionBarHeight}px`,
    }"
  >
    <RouterLink :to="`/stalls/${stallId}`" class="back-link"
      ><ArrowLeft :size="18" /> 继续挑选</RouterLink
    >
    <div class="page-heading">
      <div>
        <span class="eyebrow">ALMOST THERE</span>
        <h1>把好味道，带走。</h1>
        <p class="muted">核对餐点与取餐方式，再安心下单。</p>
      </div>
      <span class="checkout-step"
        ><Check :size="16" /> 选好美食 <ChevronRight :size="14" /><strong
          >确认订单</strong
        ></span
      >
    </div>
    <section
      v-if="unresolved"
      class="card submission-recovery"
      aria-labelledby="submission-recovery-title"
    >
      <div class="recovery-icon"><AlertCircle :size="24" /></div>
      <div class="recovery-copy">
        <h2 id="submission-recovery-title">
          {{ busy ? "正在确认这份订单" : "上一笔提交结果待确认" }}
        </h2>
        <p>
          订单可能已经提交成功。请先确认原订单结果，再修改餐点或备注，避免重复下单。
        </p>
        <p class="recovery-summary">
          {{ submission?.summary }} · 合计 ¥{{ money(submission?.totalCents) }}
        </p>
        <p v-if="submission?.body.note" class="recovery-note">
          原备注：{{ submission.body.note }}
        </p>
        <p class="recovery-hint">
          确认时只会按原餐点、原备注和原金额重试；如果已经下单，会直接打开那一单，不会创建重复订单。
        </p>
        <p v-if="error" class="error-message" role="alert">{{ error }}</p>
        <div class="recovery-actions">
          <button
            type="button"
            class="btn btn-primary"
            :disabled="busy"
            @click="confirmSubmission"
          >
            {{ busy ? "正在确认…" : "确认原订单结果" }}
          </button>
          <RouterLink class="btn btn-secondary" to="/orders"
            >查看我的订单</RouterLink
          >
        </div>
      </div>
    </section>
    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
      <p>正在准备你的订单…</p>
    </div>
    <div v-else-if="!items.length" class="empty-state card">
      <ShoppingBag :size="42" />
      <h2>购物袋还是空的</h2>
      <p>先去挑选一份喜欢的味道吧。</p>
      <RouterLink :to="`/stalls/${stallId}`" class="btn btn-primary"
        >去看看菜单</RouterLink
      >
    </div>
    <form v-else-if="stall" @submit.prevent="submit" class="checkout-layout">
      <div class="stack checkout-main">
        <SimulationNotice v-if="isSimulation" :delivery="isDelivery" />
        <ReceivingNotice :stall="stall" />
        <section class="card checkout-card">
          <div class="section-heading">
            <h2><Store :size="20" /> {{ stall.name }}</h2>
            <span class="muted small">{{ cart.count(stallId) }} 件商品</span>
          </div>
          <article
            v-for="item in shownMeals"
            :key="item.product.id"
            class="checkout-product"
            :data-checkout-product="item.product.id"
          >
            <RouterLink
              class="checkout-product-photo"
              :to="`/stalls/${stallId}/products/${item.product.id}`"
              :aria-label="`查看${item.product.name}图片与详情`"
              ><img :src="item.product.image" :alt="item.product.name"
            /></RouterLink>
            <div class="product-text">
              <h3>
                <RouterLink
                  :to="`/stalls/${stallId}/products/${item.product.id}`"
                  :aria-label="`查看${item.product.name}详情`"
                  >{{ item.product.name }}<ChevronRight :size="12"
                /></RouterLink>
              </h3>
              <p class="muted">
                现点现做 · {{ isDelivery ? "商家配送" : "到摊自取" }}
              </p>
              <strong class="price"
                >¥{{ money(item.product.price_cents) }}</strong
              >
            </div>
            <div class="quantity-control">
              <button
                type="button"
                :aria-label="`减少${item.product.name}`"
                :disabled="editingLocked"
                @click="updateQuantity(item, item.quantity - 1)"
              >
                <Minus :size="14" /></button
              ><span>{{ item.quantity }}</span
              ><button
                type="button"
                :aria-label="`增加${item.product.name}`"
                :disabled="editingLocked"
                @click="updateQuantity(item, item.quantity + 1)"
              >
                <Plus :size="14" />
              </button>
            </div>
            <PortionEditor
              :product="currentProduct(item)"
              :quantity="item.quantity"
              :model-value="item.portions"
              :disabled="editingLocked"
              @update:model-value="
                cart.setQuantity(stallId, item.product, item.quantity, $event)
              "
            />
            <p
              v-if="productProblem(item)"
              class="checkout-product-problem"
              role="alert"
            >
              {{ productProblem(item) }}
            </p>
            <p
              v-if="itemPriceChanged(item)"
              class="checkout-product-problem"
              role="alert"
            >
              当前标价已变为 ¥{{
                money(currentProduct(item).price_cents)
              }}，提交时将重新核对价格，请确认更新后的金额。
            </p>
          </article>
          <div v-if="items.length > 3" class="checkout-meal-summary">
            <p v-if="mealsNeedReview" role="status">
              餐点信息有变化，已展开全部餐点，请核对提示。
            </p>
            <template v-else>
              <p v-if="!showAllMeals">
                另有 {{ items.length - 2 }} 种餐点，共
                {{ hiddenMealQuantity }} 份；逐份口味与备注均已保留。
              </p>
              <button
                type="button"
                :aria-expanded="showAllMeals"
                @click="mealsExpanded = !mealsExpanded"
              >
                {{
                  showAllMeals
                    ? "收起餐点明细"
                    : `展开全部 ${items.length} 种餐点与逐份要求`
                }}
              </button>
            </template>
          </div>
        </section>
        <CheckoutFulfillment
          v-model="fulfillment"
          v-model:point-id="pointId"
          :delivery="stall.delivery"
          :subtotal="total"
          :busy="editingLocked"
        >
          <div class="pickup-address">
            <div class="location-icon"><MapPin :size="22" /></div>
            <div>
              <strong>{{
                isDelivery
                  ? selectedPoint?.address || "请在上方选择校园交接点"
                  : stall.address
              }}</strong>
              <p class="muted">{{ stall.area_name }}</p>
            </div>
          </div>
          <p v-if="!isDelivery" class="prep-hint">
            <Clock3 :size="16" /> 预计备餐
            {{ stall.prep_minutes }} 分钟，以商家实际出餐为准
          </p>
        </CheckoutFulfillment>

        <CheckoutPaymentMethods
          :readiness="stall.wechat_payment"
          :delivery="isDelivery"
        />
        <details
          class="card checkout-card checkout-contact-card"
          aria-label="联系与备注"
          :open="isDelivery || contactExpanded"
          @toggle="contactExpanded = ($event.target as HTMLDetailsElement).open"
        >
          <summary @click="isDelivery && $event.preventDefault()">
            <span class="contact-heading">
              联系与备注
              <span class="muted small">{{
                fulfillment === "delivery" ? "方便交接" : "自取选填"
              }}</span>
            </span>
            <span v-if="!isDelivery" class="contact-summary">{{
              contactSummary
            }}</span>
            <span v-if="!isDelivery" class="contact-expand"
              >{{ contactExpanded ? "收起" : "填写或修改"
              }}<ChevronRight :size="16"
            /></span>
          </summary>
          <label v-if="isDelivery" class="field"
            >收餐人称呼<input
              v-model="recipient"
              data-checkout-field="recipient"
              class="input"
              :disabled="editingLocked"
              required
              maxlength="30"
              autocomplete="name"
              placeholder="方便交接时称呼你"
          /></label>
          <label class="field contact-field"
            >联系手机号
            <span class="muted">{{
              isDelivery ? "必填，仅供本单配送联系" : "选填，仅供本单联系"
            }}</span
            ><input
              class="input"
              v-model="phone"
              data-checkout-field="phone"
              type="tel"
              inputmode="tel"
              maxlength="20"
              :required="isDelivery"
              :placeholder="
                isDelivery
                  ? '请填写可联系的手机号码'
                  : '如遇缺货，方便商家联系你'
              "
              :disabled="editingLocked"
          /></label>
          <label class="field note-field"
            >整单备注 <span class="muted">选填</span
            ><textarea
              class="input"
              v-model="note"
              maxlength="200"
              rows="3"
              placeholder="例如：餐具按需提供（每份口味请在上方分别填写）"
              :disabled="editingLocked"
            ></textarea
            ><span class="character-count">{{ note.length }}/200</span></label
          >
        </details>
      </div>
      <aside class="checkout-summary card">
        <span class="eyebrow">YOUR LITTLE HAPPINESS</span>
        <h2>这一单的幸福</h2>
        <div class="summary-line">
          <span>商品金额</span><strong>¥{{ money(total) }}</strong>
        </div>
        <div class="summary-line">
          <span>取餐方式</span
          ><span>{{ isDelivery ? "商家配送至交接点" : "到摊自取" }}</span>
        </div>
        <div v-if="isDelivery" class="summary-line">
          <span>配送费</span><strong>¥{{ money(deliveryFee) }}</strong>
        </div>
        <div class="summary-total">
          <span>{{ isSimulation ? "模拟应付金额" : "应付金额" }}</span
          ><strong>¥{{ money(total + deliveryFee) }}</strong>
        </div>
        <div
          v-if="isSimulation || isDelivery || stall.wechat_payment?.available"
          class="pay-at-stall"
        >
          <Wallet :size="22" />
          <div>
            <strong>{{
              isDelivery
                ? "先付款，再由商家接单配送"
                : stall.wechat_payment?.available
                  ? "餐点做好后，再付款取餐"
                  : "取餐时向商家付款"
            }}</strong>
            <p>
              {{
                isSimulation
                  ? "这是流程演练。下单后可在订单页尝试模拟付款，再到商家工作台推进订单；不会真实收付款或送货。"
                  : isDelivery
                    ? "提交后需在订单页完成微信付款。餐费和配送费一次支付，商家拒单或接单超时将发起全额退款。"
                    : stall.wechat_payment?.available
                      ? "商家出餐后，可在订单页微信支付，也可到摊付款。提交订单不会扣款。"
                      : "到摊扫摊主收款码付款，平台不经手款项。商家确认接单后才开始制作。"
              }}
            </p>
          </div>
        </div>
        <p
          v-if="!stall.can_order && unresolved"
          class="error-message"
          role="alert"
        >
          {{
            stall.order_unavailable_reason ||
            (!stall.transaction_enabled
              ? "该摊位仅支持线下到访，暂未开放在线点单。"
              : "摊位当前无法接单，请返回确认最新营业状态。")
          }}
        </p>
        <p v-if="error && !unresolved" class="error-message" role="alert">
          {{ error }}
        </p>
        <nav
          v-if="relatedOrderIds.length && !unresolved"
          class="recovery-actions"
          aria-label="已有订单"
        >
          <RouterLink
            v-for="(id, index) in relatedOrderIds"
            :key="id"
            :to="`/orders/${id}`"
            class="btn btn-secondary"
            >查看已有订单 {{ index + 1 }}</RouterLink
          >
        </nav>
        <p
          v-if="isDelivery && !deliveryCanSubmit && unresolved"
          class="error-message"
          role="status"
        >
          {{
            !stall.delivery?.available
              ? stall.delivery?.reason || "配送尚未开放，请选择到摊自取。"
              : "请核对交接点、收餐人、有效联系号码及起送金额。"
          }}
        </p>
        <div
          v-if="!unresolved || busy"
          ref="actionBar"
          class="checkout-action-bar"
          aria-label="确认金额并提交"
        >
          <div v-if="!editingLocked && firstProblem" class="checkout-guidance">
            <p id="checkout-guidance-reason" role="status">
              {{ firstProblem.reason }}
            </p>
            <RouterLink
              v-if="firstProblem.target === 'stall'"
              :to="`/stalls/${stallId}`"
              >查看摊位</RouterLink
            >
            <button v-else type="button" @click="goToProblem">
              去修改<ChevronRight :size="16" />
            </button>
          </div>
          <div class="mobile-checkout-total">
            <small>{{ isSimulation ? "模拟应付" : "合计" }}</small
            ><strong>¥{{ money(total + deliveryFee) }}</strong
            ><span>{{ isDelivery ? "含配送费" : "到摊自取" }}</span>
          </div>
          <button
            class="btn btn-primary checkout-submit"
            :aria-describedby="
              !editingLocked && firstProblem
                ? 'checkout-guidance-reason'
                : undefined
            "
            :disabled="
              editingLocked ||
              !stall.can_order ||
              !deliveryCanSubmit ||
              tastesChanged ||
              unavailableItems
            "
          >
            {{
              busy
                ? "正在提交…"
                : unresolved
                  ? "请先确认原订单结果"
                  : priceChanged
                    ? "确认新价格并提交"
                    : isDelivery
                      ? "提交配送订单，去付款"
                      : "提交自取订单"
            }}<ChevronRight :size="18" />
          </button>
        </div>
        <p class="summary-note">
          {{
            isDelivery
              ? "付款成功后开始等待接单。5 分钟未接单自动取消并发起退款；接单后取消须商家处理。联系方式仅用于本单履约。"
              : "5 分钟未接单将自动取消。接单前可直接取消，接单后须向商家申请。"
          }}
        </p>
      </aside>
    </form>
    <div v-else class="empty-state card">
      <p class="error-message">{{ error || "暂时无法加载摊位" }}</p>
      <button class="btn btn-secondary" @click="router.go(0)">重新加载</button>
    </div>
  </div>
</template>

<style scoped>
.submission-recovery {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
  padding: 24px;
  background: #fff5e7;
  border-color: #e6ca9e;
}
.recovery-icon {
  color: #a65b27;
  flex: none;
  padding-top: 2px;
}
.recovery-copy {
  min-width: 0;
}
.recovery-copy h2 {
  margin: 0 0 10px;
  font-size: 19px;
  color: #744322;
}
.recovery-copy p {
  font-size: 13px;
  line-height: 1.85;
  color: #745c45;
  overflow-wrap: anywhere;
}
.recovery-copy .recovery-summary {
  font-weight: 600;
  color: #473528;
}
.recovery-copy .recovery-hint {
  color: #827362;
  font-size: 12px;
}
.recovery-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}
@media (max-width: 600px) {
  .submission-recovery {
    padding: 19px 16px;
    gap: 10px;
  }
  .recovery-copy h2 {
    font-size: 17px;
  }
  .recovery-actions .btn {
    flex: 1;
    white-space: nowrap;
  }
}
.checkout-page {
  padding-top: 28px;
}
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #787266;
  font-size: 13px;
  margin-bottom: 25px;
}
.checkout-step {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12px;
  color: #9a9285;
}
.checkout-step strong {
  color: #de6b31;
  font-weight: 600;
}
.checkout-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(290px, 1fr);
  align-items: start;
  gap: 25px;
}
.checkout-main {
  gap: 16px;
}
.checkout-meal-summary {
  padding-top: 12px;
  border-top: 1px solid #eee2d5;
}
.checkout-meal-summary p {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.8;
  color: #795e43;
}
.checkout-meal-summary button {
  min-height: 44px;
  width: 100%;
  padding: 9px 12px;
  border: 1px solid #ecd4bb;
  border-radius: 10px;
  background: #fff6eb;
  color: #954517;
  font-size: 13px;
}
.checkout-product-problem {
  flex-basis: 100%;
  color: #a74225;
  font-size: 12px;
  line-height: 1.7;
  margin: 0;
}
.mobile-checkout-total {
  display: none;
}
.checkout-card {
  padding: 27px 30px;
}
.checkout-card > .section-heading {
  margin: 0 0 5px;
}
.checkout-card h2 {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 18px;
  margin: 0;
}
.checkout-card h2 svg {
  color: #dc713b;
}
.small {
  font-size: 12px;
}
.checkout-product {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 17px;
  padding: 22px 0;
  border-bottom: 1px solid #eee8df;
}
.checkout-product > .portion-editor {
  flex-basis: 100%;
}
.checkout-product img {
  height: 87px;
  width: 87px;
  object-fit: cover;
  border-radius: 13px;
}
.checkout-product-photo {
  flex: none;
  line-height: 0;
  border-radius: 13px;
}
.product-text h3 a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 44px;
  overflow-wrap: anywhere;
}
.product-text h3 a svg {
  flex-shrink: 0;
  color: #b08d68;
}
.product-text h3 a:hover {
  color: var(--orange-dark);
}
.product-text {
  flex: 1;
  min-width: 0;
}
.product-text h3 {
  margin: 0;
  font-size: 15px;
}
.product-text p {
  font-size: 11px;
  margin: 7px 0 10px;
}
.product-text .price {
  font-size: 16px;
}
.quantity-control {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.quantity-control button {
  border: 0;
  border-radius: 50%;
  width: 44px;
  min-height: 44px;
  display: grid;
  place-items: center;
  background: transparent;
  color: #e87335;
}
.quantity-control button:disabled {
  opacity: 0.4;
}
.quantity-control button:hover {
  background: #fff0e4;
}
.note-field {
  margin-top: 22px;
  position: relative;
}
.checkout-contact-card > summary {
  list-style: none;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 7px 12px;
  min-height: 44px;
  cursor: pointer;
}
.checkout-contact-card > summary::-webkit-details-marker {
  display: none;
}
.contact-heading {
  font-size: 16px;
  font-weight: 600;
}
.contact-heading > span {
  margin-left: 6px;
  font-weight: 400;
}
.contact-summary {
  grid-column: 1;
  font-size: 12px;
  line-height: 1.6;
  color: #796852;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.contact-expand {
  grid-column: 2;
  grid-row: 1 / 3;
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: #a14f25;
}
.checkout-contact-card[open] .contact-expand svg {
  transform: rotate(90deg);
}
.field > .muted {
  font-size: 11px;
  font-weight: 400;
  margin-left: 4px;
}
.note-field textarea {
  resize: vertical;
  padding-bottom: 28px;
}
.character-count {
  position: absolute;
  right: 12px;
  bottom: 11px;
  font-size: 11px;
  color: #aaa095;
  font-weight: 400;
}
.pickup-address {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-top: 24px;
}
.location-icon {
  width: 45px;
  height: 45px;
  background: #fff0df;
  border-radius: 14px;
  display: grid;
  place-items: center;
  color: #e07737;
  flex: none;
}
.pickup-address strong {
  font-size: 15px;
}
.pickup-address p {
  font-size: 12px;
  margin: 6px 0 0;
}
.prep-hint {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: #8e8577;
  margin: 14px 0 0;
  line-height: 1.8;
}
.checkout-summary {
  padding: 30px;
  position: sticky;
  top: 110px;
}
.checkout-summary .eyebrow {
  font-size: 10px;
  letter-spacing: 1.6px;
}
.checkout-summary h2 {
  font-size: 23px;
  margin: 12px 0 30px;
}
.summary-line {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin: 18px 0;
  color: #82796c;
}
.summary-line strong {
  color: #39352f;
}
.summary-total {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px dashed #ddd3c5;
  padding-top: 24px;
  margin-top: 23px;
  font-size: 14px;
}
.summary-total strong {
  font-size: 30px;
  color: #ef7339;
  letter-spacing: -1px;
}
.pay-at-stall {
  display: flex;
  gap: 13px;
  padding: 18px 14px;
  background: #fbf1e5;
  border-radius: 13px;
  margin: 25px 0;
  color: #b96935;
}
.pay-at-stall svg {
  flex: none;
  margin-top: 2px;
}
.pay-at-stall strong {
  font-size: 13px;
}
.pay-at-stall p {
  font-size: 11px;
  line-height: 1.9;
  margin: 6px 0 0;
  color: #9c846d;
}
.checkout-submit {
  width: 100%;
  justify-content: center;
}
.checkout-guidance {
  display: flex;
  align-items: center;
  gap: 12px;
  grid-column: 1 / -1;
  margin-bottom: 10px;
  color: #804724;
}
.checkout-guidance p {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.checkout-guidance button,
.checkout-guidance a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  min-height: 44px;
  gap: 3px;
  padding: 0 8px;
  border: 1px solid #d9baa0;
  border-radius: 9px;
  background: #fff4e5;
  color: #803c19;
  font-size: 13px;
  font-weight: 600;
}
.summary-note {
  font-size: 11px;
  line-height: 1.9;
  text-align: center;
  color: #9b9386;
  margin: 15px 0 0;
}
@media (max-width: 800px) {
  .checkout-page {
    padding-bottom: calc(
      var(--checkout-action-height, 86px) + 54px + env(safe-area-inset-bottom)
    );
  }
  .checkout-action-bar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: var(--checkout-keyboard-inset, 0px);
    z-index: 40;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    column-gap: 20px;
    row-gap: 10px;
    padding: 12px max(18px, env(safe-area-inset-right))
      calc(12px + env(safe-area-inset-bottom))
      max(18px, env(safe-area-inset-left));
    background: #fffaf3;
    border-top: 1px solid #ebdbc6;
    box-shadow: 0 -5px 25px #5037180a;
  }
  .checkout-action-bar .checkout-submit {
    min-height: 48px;
    font-size: 13px;
    padding: 12px;
    white-space: normal;
  }
  .checkout-action-bar .checkout-guidance {
    margin: 0;
  }
  .mobile-checkout-total {
    display: grid;
    grid-template-columns: auto auto;
    align-items: baseline;
    gap: 1px 7px;
  }
  .mobile-checkout-total small {
    color: #705c47;
    font-size: 11px;
  }
  .mobile-checkout-total strong {
    color: #b84f1f;
    font-size: 24px;
  }
  .mobile-checkout-total span {
    grid-column: 1/-1;
    color: #89745d;
    font-size: 11px;
  }
  .checkout-page :deep(input),
  .checkout-page :deep(textarea),
  .checkout-page :deep(select) {
    scroll-margin-block: 160px;
  }
  .checkout-layout {
    grid-template-columns: 1fr;
  }
  .checkout-step {
    display: none;
  }
  .checkout-summary {
    position: static;
  }
  .checkout-card,
  .checkout-summary {
    padding: 18px;
  }
  .checkout-page {
    padding-top: 18px;
  }
  .checkout-product {
    display: grid;
    grid-template-columns: 64px minmax(0, 1fr);
    gap: 12px;
  }
  .checkout-product-photo {
    grid-row: 1 / 3;
    align-self: start;
  }
  .checkout-product > .portion-editor,
  .checkout-product-problem {
    grid-column: 1 / -1;
  }
  .checkout-product img {
    width: 64px;
    height: 64px;
  }
  .product-text p {
    display: none;
  }
  .product-text h3 a {
    min-height: 0;
    padding: 0 0 6px;
    line-height: 1.5;
  }
  .quantity-control {
    grid-column: 2;
    justify-self: start;
    gap: 5px;
  }
  .checkout-card > .section-heading {
    margin: 0 0 5px;
  }
  .checkout-card h2 {
    font-size: 16px;
  }
  .back-link {
    margin-bottom: 12px;
  }
  .checkout-summary h2 {
    margin-bottom: 20px;
  }
  .checkout-summary .eyebrow {
    display: none;
  }
  .checkout-page .page-heading {
    margin-bottom: 22px;
  }
  .checkout-page .page-heading h1 {
    font-size: 28px;
  }
  .checkout-page .page-heading .eyebrow {
    display: none;
  }
  .checkout-product {
    padding: 14px 0;
  }
  .checkout-product:last-child {
    border-bottom: 0;
    padding-bottom: 0;
  }
  .pickup-address {
    margin-top: 17px;
  }
}
</style>
