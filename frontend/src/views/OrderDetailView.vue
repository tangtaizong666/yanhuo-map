<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  CheckCircle2,
  ChefHat,
  Clock3,
  MapPin,
  MessageSquare,
  Navigation,
  Phone,
  RefreshCw,
  ShoppingBag,
  Star,
  Store,
  Wallet,
  X,
} from "lucide-vue-next";
import { api, formatTime, money, statusText } from "../lib/api";
import { useSession } from "../stores/session";
import { notify } from "../lib/notify";
import ReorderDialog from "../components/ReorderDialog.vue";
import StudentPaymentPanel from "../components/StudentPaymentPanel.vue";
import DeliveryOrderProgress from "../components/DeliveryOrderProgress.vue";
import PortionSummary from "../components/PortionSummary.vue";
import OrderPreparation from "../components/OrderPreparation.vue";
import PickupCard from "../components/PickupCard.vue";
import OrderContactHelp from "../components/OrderContactHelp.vue";
import type { Order } from "../lib/types";
import { allows } from "../lib/orderActions";
import {
  needsFinancialFollowUp,
  refundNeedsFollowUp,
  paymentNeedsFollowUp,
} from "../lib/orderFollowUp";

const route = useRoute();
const router = useRouter();
const session = useSession();
const order = ref<any>(null);
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const merchantPhone = computed(() =>
  (order.value?.merchant_contact_phone || "").replace(/[^\d+]/g, ""),
);
const cancelOpen = ref(false);
const cancelDialog = ref<HTMLDialogElement>();
let cancelTrigger: HTMLElement | null = null;
function openCancel(event: MouseEvent) {
  // Safari does not focus buttons on pointer click. Keep the actual trigger.
  cancelTrigger =
    event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
  cancelOpen.value = true;
}
watch(cancelOpen, async (open) => {
  if (open) {
    await nextTick();
    if (cancelOpen.value) cancelDialog.value?.showModal();
  } else {
    cancelDialog.value?.close();
    await nextTick();
    if (!cancelOpen.value) cancelTrigger?.focus();
  }
});
function dismissCancel() {
  if (!busy.value) cancelOpen.value = false;
}
function trapCancelFocus(event: KeyboardEvent) {
  if (event.key !== "Tab") return;
  const targets = Array.from(
    cancelDialog.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), textarea:not(:disabled), input:not(:disabled), [tabindex="0"]',
    ) || [],
  );
  const first = targets[0],
    last = targets[targets.length - 1];
  if (!first) {
    event.preventDefault();
    return;
  }
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

const cancelReason = ref("");
const rating = ref(5);
const reviewContent = ref("");
const reviewing = ref(false);
const reorderOpen = ref(false);
const paymentWorking = ref(false);
const isDelivery = computed(() => order.value?.fulfillment_type === "delivery");
const isSimulation = computed(() => order.value?.mode === "simulation");
const financialFollowUp = computed(
  () => !!order.value && needsFinancialFollowUp(order.value),
);
const paymentPending = computed(() =>
  ["creating", "pending", "reconcile", "review"].includes(
    order.value?.payment?.status,
  ),
);
const readyPickup = computed(
  () =>
    !!order.value &&
    !isDelivery.value &&
    order.value.status === "ready" &&
    !order.value.cancel_requested &&
    !!order.value.pickup_code &&
    !financialFollowUp.value &&
    !paymentPending.value &&
    !["refunding", "refunded"].includes(order.value.payment_status) &&
    !paymentWorking.value,
);
const refundFollowUp = computed(
  () => !!order.value && refundNeedsFollowUp(order.value),
);
const refundProblem = computed(
  () =>
    refundFollowUp.value &&
    ["closed", "abnormal"].includes(order.value?.refund?.status),
);
let timer: ReturnType<typeof setInterval> | undefined;
let fetching = false;
let refreshQueued = false;
let disposed = false;
let version = 0;
const pageUserId = session.user?.id;
const controller = new AbortController();
const current = () => !disposed && session.user?.id === pageUserId;
function paymentChanging() {
  ++version;
  paymentWorking.value = true;
}
function paymentSettled() {
  if (!current()) return;
  paymentWorking.value = false;
  refreshAfterChange();
}
function applyConfirmedOrder(updated: Order) {
  if (!current()) return false;
  if (
    !updated || updated.id !== order.value?.id || updated.stall_id !== order.value?.stall_id ||
    !["pending_payment", "pending", "preparing", "ready", "delivering", "arrived", "completed", "cancelled", "rejected"].includes(updated.status) ||
    !["unpaid", "paid", "refunding", "refunded"].includes(updated.payment_status) ||
    !Array.isArray(updated.items)
  ) {
    error.value = "订单返回信息不完整，请刷新确认当前状态。";
    return false;
  }
  ++version;
  order.value = updated;
  error.value = "";
  return true;
}
const steps = [
  { key: "pending", label: "已下单", icon: ShoppingBag },
  { key: "preparing", label: "制作中", icon: ChefHat },
  { key: "ready", label: "待取餐", icon: Store },
  { key: "completed", label: "已完成", icon: CheckCircle2 },
];
const stepIndex = computed(() =>
  steps.findIndex((step) => step.key === order.value?.status),
);
const terminal = computed(() =>
  ["completed", "cancelled", "rejected"].includes(order.value?.status),
);
const cancelled = computed(() =>
  ["cancelled", "rejected"].includes(order.value?.status),
);
const heading = computed(() => {
  if (order.value?.financial_hold_reason)
    return "款项需要核对，请先确认处理结果";
  if (order.value && paymentNeedsFollowUp(order.value))
    return "这笔款项，正在核对";
  if (refundProblem.value) return "退款未完成，请联系商家";
  if (refundFollowUp.value) return "退款处理中，请留意结果";
  if (order.value?.cancel_requested) return "正在等待商家处理取消申请";
  if (paymentPending.value) return "正在确认付款，请留意结果";
  if (
    order.value?.status === "ready" &&
    order.value?.payment_status === "refunded"
  )
    return "款项已退回，请核对订单";
  if (isDelivery.value)
    return (
      (
        {
          pending_payment: "先付好款，让这一餐出发",
          pending: "商家正在确认你的配送订单",
          preparing: "小摊正忙着，为你做一餐",
          ready: "餐点做好了，等待出发",
          delivering: "你的好味道，正在送来",
          arrived: "餐点到了，去交接点收餐吧",
          completed: "这一餐，送到你身边",
          cancelled: "配送订单已取消",
          rejected: "商家暂时无法配送",
        } as Record<string, string>
      )[order.value.status] || "配送订单"
    );
  return (
    (
      {
        pending: "订单已提交，等待商家接单",
        preparing: "小摊正忙着，为你做一餐",
        ready: "热乎的餐点，等你来取",
        completed: "这一餐，刚刚好",
        cancelled: "订单已取消",
        rejected: "商家暂时无法接单",
      } as Record<string, string>
    )[order.value?.status] || "订单详情"
  );
});
const subtitle = computed(
  () =>
    order.value?.financial_hold_reason ||
    (order.value && paymentNeedsFollowUp(order.value)
      ? "请联系商家核对，暂时不要重复付款或核销取餐码。"
      : refundProblem.value
        ? isSimulation.value
          ? "这笔模拟退款尚未完成，可请商家继续演练处理，没有真实资金变动。"
          : "款项尚未确认退回，请联系商家处理。订单结束不代表退款成功。"
        : refundFollowUp.value
          ? "退款尚未确认成功，款项与取餐状态分别记录。"
          : order.value?.cancel_requested
            ? "商家处理前请继续留意状态，暂不出示取餐凭证。"
            : paymentPending.value
              ? "暂时不要重复付款或核销取餐码，先确认这一笔付款结果。"
              : order.value?.status === "ready" &&
                  order.value?.payment_status === "refunded"
                ? "本单暂不提供取餐凭证，请联系商家确认处理结果。"
                : isDelivery.value
                  ? "商家自配送至指定交接点。收到餐点后再确认收餐，不会自动签收。"
                  : order.value?.status === "ready" &&
                      order.value?.payment_status === "paid"
                    ? "款项已确认，请出示取餐码领取餐点。"
                    : (
                        {
                          pending:
                            "商家接单后开始制作，5 分钟未接单将自动取消。",
                          preparing:
                            "请留意订单状态，出餐后会在这里显示取餐码。",
                          ready: "确认付款后，向商家出示取餐码领取餐点。",
                          completed:
                            "把这份好味道记下来，也把你的感受分享给大家。",
                          cancelled: "期待下一次，与你在校园转角相遇。",
                          rejected:
                            "这份订单已结束，去附近看看其他正在出摊的好味道吧。",
                        } as Record<string, string>
                      )[order.value?.status]),
);
const navigationUrl = computed(() =>
  order.value?.pickup_latitude != null && order.value?.pickup_longitude != null
    ? `https://uri.amap.com/navigation?to=${order.value.pickup_longitude},${order.value.pickup_latitude},${encodeURIComponent(order.value.pickup_address)}&mode=walk&coordinate=gaode&callnative=0`
    : "",
);
async function load() {
  if (fetching || paymentWorking.value || !current()) return;
  fetching = true;
  const requestVersion = version;
  try {
    const result = await api(`/orders/${route.params.id}`, {
      signal: controller.signal,
    });
    if (!current() || requestVersion !== version) return;
    order.value = result;
    error.value = "";
    if (terminal.value || order.value.cancel_requested) cancelOpen.value = false;
  } catch (e) {
    if (current() && requestVersion === version)
      error.value = (e as Error).message;
  } finally {
    if (current()) loading.value = false;
    fetching = false;
    if (refreshQueued && current()) {
      refreshQueued = false;
      void load();
    }
  }
}
function refreshAfterChange() {
  if (!current()) return;
  ++version;
  // An obsolete poll is still allowed to finish, but must not swallow the
  // authoritative read needed after an uncertain mutation or payment change.
  if (fetching) refreshQueued = true;
  else void load();
}
function refreshVisible() {
  if (
    !document.hidden &&
    order.value &&
    (!terminal.value || financialFollowUp.value)
  )
    void load();
}
onMounted(async () => {
  window.addEventListener("focus", refreshVisible);
  document.addEventListener("visibilitychange", refreshVisible);
  try {
    await session.load();
    if (!current()) return;
    if (!session.user) {
      await router.replace({
        path: "/login",
        query: { returnTo: route.fullPath },
      });
      return;
    }
    await load();
    if (!disposed) timer = setInterval(refreshVisible, 10000);
  } catch (e) {
    error.value = (e as Error).message;
    loading.value = false;
  }
});
onUnmounted(() => {
  disposed = true;
  ++version;
  controller.abort();
  clearInterval(timer);
  window.removeEventListener("focus", refreshVisible);
  document.removeEventListener("visibilitychange", refreshVisible);
});
async function cancelOrder() {
  if (!allows(order.value, "cancel", true)) return;
  if (busy.value || paymentWorking.value || !current()) return;
  busy.value = true;
  ++version;
  try {
    const result = await api<Order>(`/orders/${order.value.id}/cancel`, {
      method: "POST",
      body: { reason: cancelReason.value.trim() || "用户申请取消" },
      signal: controller.signal,
    });
    if (!current()) return;
    if (!applyConfirmedOrder(result)) {
      refreshAfterChange();
      return;
    }
    cancelOpen.value = false;
    notify(
      result.status === "cancelled"
        ? "订单已取消"
        : "取消申请已发送给商家",
      "success",
    );
  } catch (e) {
    if (!current()) return;
    notify((e as Error).message, "error");
    refreshAfterChange();
  } finally {
    busy.value = false;
  }
}
async function confirmReceipt() {
  if (!allows(order.value, "confirm_receipt", true)) return;
  if (busy.value || paymentWorking.value || !current()) return;
  busy.value = true;
  paymentChanging();
  try {
    const result = await api<Order>(
      `/orders/${order.value.id}/confirm-receipt`,
      { method: "POST", body: {}, signal: controller.signal },
    );
    if (!current()) return;
    if (!applyConfirmedOrder(result)) return;
    notify("已确认收餐，祝你用餐愉快", "success");
  } catch (e) {
    if (current()) notify((e as Error).message, "error");
  } finally {
    if (current()) {
      busy.value = false;
      paymentSettled();
    }
  }
}
async function submitReview() {
  if (reviewing.value || !current() || order.value?.review) return;
  reviewing.value = true;
  ++version;
  try {
    const result = await api<Order>(`/orders/${order.value.id}/review`, {
      method: "POST",
      body: { rating: rating.value, content: reviewContent.value.trim() },
      signal: controller.signal,
    });
    if (!current()) return;
    if (!applyConfirmedOrder(result)) {
      refreshAfterChange();
      return;
    }
    notify("谢谢你的评价，让好味道被更多人看见", "success");
  } catch (e) {
    if (!current()) return;
    notify((e as Error).message, "error");
    refreshAfterChange();
  } finally {
    reviewing.value = false;
  }
}
</script>

<template>
  <div class="page order-detail-page">
    <RouterLink to="/orders" class="back-link"
      ><ArrowLeft :size="18" /> 我的订单</RouterLink
    >
    <p v-if="error" class="error-message" role="alert">
      {{ error }} <button class="btn btn-ghost" @click="load">重试</button>
    </p>
    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
      <p>正在加载订单…</p>
    </div>
    <template v-else-if="order">
      <div
        class="order-detail-heading"
        :class="{ 'pickup-ready-heading': readyPickup }"
      >
        <div>
          <span v-if="isSimulation" class="order-mode-label"
            >模拟订单 · 不会扣款{{ isDelivery ? "、不实际送货" : "" }}</span
          >
          <h1>{{ heading }}</h1>
          <p class="muted">{{ subtitle }}</p>
        </div>
        <button
          class="refresh-button"
          :disabled="paymentWorking"
          @click="load"
          aria-label="刷新订单状态"
        >
          <RefreshCw :size="18" />
        </button>
      </div>
      <div class="detail-layout">
        <div class="stack detail-main">
          <PickupCard v-if="readyPickup" :order="order" :sync-error="!!error" />
          <OrderPreparation
            v-else-if="isDelivery || order.status !== 'ready'"
            :order="order"
            :sync-error="!!error"
          />
          <StudentPaymentPanel
            :order="order"
            @changing="paymentChanging"
            @updated="applyConfirmedOrder"
            @settled="paymentSettled"
          />
          <section class="card progress-card">
            <DeliveryOrderProgress
              v-if="isDelivery"
              :order="order"
              :busy="busy || paymentWorking"
              @confirm="confirmReceipt"
            />
            <div v-if="!isDelivery && stepIndex >= 0" class="order-progress">
              <div
                v-for="(step, index) in steps"
                :key="step.key"
                class="progress-step"
                :class="{
                  done: index < stepIndex,
                  current: index === stepIndex,
                }"
              >
                <div class="step-symbol">
                  <Check v-if="index < stepIndex" :size="19" /><component
                    v-else
                    :is="step.icon"
                    :size="19"
                  />
                </div>
                <span>{{ step.label }}</span>
              </div>
            </div>
            <div v-else-if="!isDelivery" class="closed-order">
              <X :size="26" /><strong>{{ statusText(order.status) }}</strong>
              <p v-if="order.cancel_reason">{{ order.cancel_reason }}</p>
            </div>
            <div
              v-if="!isDelivery && order.status === 'pending'"
              class="pending-hint"
            >
              <Clock3 :size="19" /><span
                >等待商家接单<span class="muted"
                  >订单状态每 10 秒更新</span
                ></span
              >
            </div>
            <div v-if="order.cancel_requested" class="cancel-notice">
              <MessageSquare :size="17" />
              取消申请已提交，商家处理前请继续留意订单状态。
            </div>
            <div
              v-else-if="!terminal && order.cancel_reason"
              class="cancel-notice"
            >
              <MessageSquare :size="17" />
              取消申请处理结果：{{ order.cancel_reason }}
            </div>
            <div v-if="!isDelivery && !readyPickup" class="order-location">
              <div class="location-label">
                <MapPin :size="19" />
                <div>
                  <span class="muted">下单时的取餐位置</span>
                  <h3>{{ order.pickup_address }}</h3>
                </div>
              </div>
              <a
                v-if="navigationUrl"
                :href="navigationUrl"
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-secondary"
                ><Navigation :size="15" /> 路线</a
              >
            </div>
            <p
              v-if="!isDelivery && !readyPickup && order.location_changed"
              class="location-warning"
            >
              商家当前位置已变更为「{{
                order.current_address
              }}」。本单保留原取餐位置，请先联系商家确认。
            </p>
            <div class="contact-actions">
              <a
                v-if="!readyPickup && merchantPhone"
                :href="`tel:${merchantPhone}`"
                ><Phone :size="17" /> 联系商家</a
              ><RouterLink :to="`/stalls/${order.stall_id}`"
                ><Store :size="17" /> 查看摊位</RouterLink
              ><button
                v-if="
                  !terminal &&
                  allows(order, 'cancel', true) &&
                  !order.cancel_requested &&
                  (order.payment_status === 'unpaid' ||
                    (isDelivery && order.payment_status === 'paid')) &&
                  !order.payment_review_required &&
                  !['creating', 'pending', 'reconcile', 'review'].includes(
                    order.payment?.status,
                  )
                "
                @click="openCancel"
                :disabled="busy || paymentWorking"
              >
                <X :size="17" />
                {{
                  ["pending", "pending_payment"].includes(order.status)
                    ? "取消订单"
                    : "申请取消"
                }}
              </button>
            </div>
            <template v-if="!readyPickup && !merchantPhone"
              ><p class="missing-order-contact">商家暂未提供联系电话。</p>
              <OrderContactHelp :order="order"
            /></template>
          </section>
          <section v-if="terminal" class="card reorder-card">
            <span class="reorder-card-icon"><RefreshCw :size="22" /></span>
            <div>
              <h2>熟悉的味道，再尝一次</h2>
              <p>核对今天的菜单，把喜欢的餐点重新加入餐袋。</p>
            </div>
            <button class="btn btn-primary" @click="reorderOpen = true">
              再来一单<ArrowUpRight :size="16" />
            </button>
          </section>
          <section v-if="order.status === 'completed'" class="card review-card">
            <div class="section-heading">
              <h2>这份味道，合心意吗？</h2>
              <span class="muted" style="font-size: 11px">{{
                isSimulation ? "模拟订单评价" : "真实订单评价"
              }}</span>
            </div>
            <template v-if="order.review"
              ><div class="stars static-stars">
                <Star
                  v-for="star in 5"
                  :key="star"
                  :size="24"
                  :fill="star <= order.review.rating ? 'currentColor' : 'none'"
                />
              </div>
              <p class="submitted-review">
                {{ order.review.content || "用户给出了星级评价。" }}
              </p>
              <div
                v-if="order.review.merchant_reply"
                class="merchant-public-reply"
              >
                <strong>商家回复</strong>
                <p>{{ order.review.merchant_reply }}</p>
                <small>{{ formatTime(order.review.replied_at) }}</small>
              </div>
              <span class="review-saved"
                ><CheckCircle2 :size="14" /> 已评价，感谢你的分享</span
              ></template
            >
            <form v-else @submit.prevent="submitReview">
              <div class="stars" role="group" aria-label="选择评分">
                <button
                  v-for="star in 5"
                  :key="star"
                  type="button"
                  :aria-label="`${star} 星`"
                  :aria-pressed="rating === star"
                  @click="rating = star"
                >
                  <Star
                    :size="30"
                    :fill="star <= rating ? 'currentColor' : 'none'"
                  /></button
                ><span>{{
                  ["", "不太满意", "有待改进", "还不错", "很喜欢", "超出期待"][
                    rating
                  ]
                }}</span>
              </div>
              <textarea
                class="input"
                v-model="reviewContent"
                rows="3"
                maxlength="500"
                placeholder="说说口味、分量，或是让你记住这个小摊的瞬间…"
                required
                minlength="2"
              ></textarea>
              <div class="review-bottom">
                <span class="muted">{{ reviewContent.length }}/500</span
                ><button class="btn btn-primary" :disabled="reviewing">
                  {{ reviewing ? "正在提交…" : "分享这份好味道"
                  }}<ArrowUpRight :size="16" />
                </button>
              </div>
            </form>
          </section>
        </div>
        <aside class="card order-receipt">
          <div class="receipt-header">
            <Store :size="20" />
            <h2>{{ order.stall_name }}</h2>
            <span>ORDER RECEIPT</span>
          </div>
          <article
            v-for="item in order.items"
            :key="item.product_id"
            class="receipt-product"
          >
            <RouterLink
              v-if="item.product_id"
              :to="`/stalls/${order.stall_id}/products/${item.product_id}`"
              class="receipt-photo"
              :aria-label="`查看${item.name}详情`"
              ><img :src="item.image" :alt="item.name"
            /></RouterLink>
            <img v-else :src="item.image" :alt="item.name" />
            <div>
              <h3>
                <RouterLink
                  v-if="item.product_id"
                  :to="`/stalls/${order.stall_id}/products/${item.product_id}`"
                  >{{ item.name }}</RouterLink
                ><span v-else>{{ item.name }}</span>
              </h3>
              <p>× {{ item.quantity }}</p>
              <PortionSummary :portions="item.portions" />
            </div>
            <strong>¥{{ money(item.unit_price_cents * item.quantity) }}</strong>
          </article>
          <div v-if="isDelivery" class="delivery-receipt-fee">
            <span>商品金额</span><b>¥{{ money(order.items_total_cents) }}</b
            ><span>配送费</span><b>¥{{ money(order.delivery_fee_cents) }}</b>
          </div>
          <div class="receipt-total">
            <span
              >{{ isSimulation ? "模拟 · " : ""
              }}{{
                order.payment_status === "refunded"
                  ? "已退款"
                  : refundFollowUp
                    ? "尚未退回金额"
                    : order.payment_status === "paid"
                      ? "已收款"
                      : cancelled
                        ? "订单金额"
                        : "应付金额"
              }}</span
            ><strong>¥{{ money(order.total_cents) }}</strong>
          </div>
          <p class="payment-note">
            <Wallet :size="15" />
            {{
              isSimulation
                ? "仅记录模拟付款与退款，没有实际资金变动"
                : order.payment_status === "refunded"
                  ? "微信已确认原路退款"
                  : refundFollowUp
                    ? refundProblem
                      ? "退款尚未完成，请联系商家处理"
                      : "退款尚未确认到账，请留意进度"
                    : order.payment_status === "paid"
                      ? order.payment_method === "wechat"
                        ? "微信支付已核验"
                        : "商家已确认收到线下款项"
                      : cancelled
                        ? "订单已结束，无需付款"
                        : order.payment_method === "wechat"
                          ? isDelivery &&
                            order.status === "pending_payment" &&
                            !order.payment
                            ? "请在付款区完成微信支付"
                            : "微信付款核对中，请勿重复付款"
                          : "取餐时付款 · 可用方式见付款区"
            }}
          </p>
          <details class="receipt-details">
            <summary>订单编号、时间与备注</summary>
            <dl class="receipt-meta">
              <div>
                <dt>订单编号</dt>
                <dd>{{ order.number }}</dd>
              </div>
              <div>
                <dt>下单时间</dt>
                <dd>{{ formatTime(order.created_at) }}</dd>
              </div>
              <div v-if="order.completed_at">
                <dt>完成时间</dt>
                <dd>{{ formatTime(order.completed_at) }}</dd>
              </div>
              <div v-if="order.contact_phone">
                <dt>联系电话</dt>
                <dd>{{ order.contact_phone }}</dd>
              </div>
              <div v-if="order.note" class="note-meta">
                <dt>口味备注</dt>
                <dd>{{ order.note }}</dd>
              </div>
            </dl>
          </details>
        </aside>
      </div>
    </template>
    <ReorderDialog
      v-if="reorderOpen && order && terminal"
      :order="order"
      @close="reorderOpen = false"
    />
    <dialog
      ref="cancelDialog"
      class="cancel-modal"
      aria-labelledby="cancel-title"
      @cancel="busy ? $event.preventDefault() : dismissCancel()"
      @close="cancelOpen = false"
      @click.self="dismissCancel"
      @keydown="trapCancelFocus"
    >
      <section class="card cancel-dialog" v-if="order">
        <button
          class="close-dialog"
          @click="cancelOpen = false"
          :disabled="busy"
          aria-label="关闭"
        >
          <X :size="20" /></button
        ><span class="eyebrow">A CHANGE OF PLANS</span>
        <h2 id="cancel-title">
          {{
            ["pending", "pending_payment"].includes(order.status)
              ? "确定取消这份订单？"
              : "向商家申请取消"
          }}
        </h2>
        <p class="muted">
          {{
            ["pending", "pending_payment"].includes(order.status)
              ? isDelivery && order.payment_status === "paid"
                ? isSimulation
                  ? "取消后会模拟退回餐费与配送费，不发生真实资金变动。"
                  : "商家尚未接单，取消后将申请退回餐费和配送费。退款到账情况以微信确认结果为准。"
                : "商家尚未接单，可以直接取消。"
              : "商家可能已经开始制作，需要由商家确认后取消。"
          }}
        </p>
        <label class="field"
          >取消原因 <span class="muted">选填</span
          ><textarea
            class="input"
            v-model="cancelReason"
            rows="3"
            maxlength="200"
            placeholder="告诉商家你的原因…"
          ></textarea>
        </label>
        <div class="dialog-actions">
          <button
            class="btn btn-secondary"
            @click="cancelOpen = false"
            :disabled="busy"
          >
            再想想</button
          ><button
            class="btn btn-primary"
            @click="cancelOrder"
            :disabled="busy"
          >
            {{
              busy
                ? "正在处理…"
                : ["pending", "pending_payment"].includes(order.status)
                  ? "确认取消"
                  : "提交申请"
            }}
          </button>
        </div>
      </section>
    </dialog>
  </div>
</template>

<style scoped>
.missing-order-contact {
  font-size: 12px;
  color: #897258;
  margin: 14px 0 0;
}
.pickup-ready-heading .eyebrow {
  display: none;
}
.pickup-ready-heading {
  margin-bottom: 20px;
}
.pickup-ready-heading h1 {
  font-size: 26px;
}
.pickup-ready-heading p {
  display: none;
}
.delivery-receipt-fee {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  padding-top: 20px;
  color: #7c6d57;
  font-size: 13px;
}
.order-detail-page {
  padding-top: 28px;
}
.order-mode-label {
  display: inline-block;
  color: #8a622a;
  font-size: 12px;
  line-height: 1.5;
}
.receipt-details summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
  color: #725d49;
  font-size: 13px;
  list-style: none;
}
.receipt-details summary::after {
  content: "+";
  font-size: 20px;
}
.receipt-details[open] summary::after {
  content: "−";
}
.receipt-details summary::-webkit-details-marker {
  display: none;
}
.receipt-details summary:focus-visible {
  outline: 3px solid #c8753a;
  outline-offset: 3px;
}
.reorder-card {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 24px;
  background: linear-gradient(135deg, #fff6e8, #fffaf4);
}
.reorder-card-icon {
  display: grid;
  place-items: center;
  width: 45px;
  height: 45px;
  flex: none;
  border-radius: 14px;
  background: #fbe7cd;
  color: #b8753c;
}
.reorder-card > div {
  flex: 1;
  min-width: 0;
}
.reorder-card h2 {
  font-size: 16px;
  margin: 0 0 7px;
}
.reorder-card p {
  font-size: 12px;
  color: #8b775e;
  line-height: 1.7;
  margin: 0;
}
.reorder-card .btn {
  min-height: 44px;
  flex: none;
  font-size: 12px;
}
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #82796c;
  font-size: 13px;
  margin-bottom: 27px;
}
.order-detail-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 30px;
}
.order-detail-heading h1 {
  font-size: 29px;
  letter-spacing: -0.7px;
  margin: 12px 0;
}
.order-detail-heading p {
  font-size: 13px;
  line-height: 1.8;
  margin: 0;
}
.refresh-button {
  display: grid;
  place-items: center;
  border: 1px solid #e7ddcd;
  min-width: 44px;
  height: 44px;
  border-radius: 50%;
  background: transparent;
  color: #8d806b;
}
.detail-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr);
  gap: 24px;
  align-items: start;
}
.detail-main {
  gap: 22px;
}
.progress-card {
  padding: 30px;
}
.order-progress {
  display: flex;
  justify-content: space-between;
  margin: 2px 0 30px;
}
.progress-step {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: #b7aea0;
  position: relative;
}
.progress-step:not(:last-child)::after {
  content: "";
  position: absolute;
  height: 1px;
  background: #ebe1d2;
  left: calc(50% + 27px);
  right: calc(-50% + 27px);
  top: 22px;
}
.progress-step.done::after {
  background: #edb289;
}
.step-symbol {
  display: grid;
  place-items: center;
  width: 45px;
  height: 45px;
  border-radius: 50%;
  background: #f6f0e7;
  position: relative;
  z-index: 1;
}
.progress-step.current {
  color: #dc783d;
  font-weight: 600;
}
.progress-step.current .step-symbol {
  background: #eb8146;
  color: #fff;
  box-shadow: 0 0 0 5px #fbecdd;
}
.progress-step.done {
  color: #b98a61;
}
.progress-step.done .step-symbol {
  color: #d7854d;
  background: #faeadb;
}
.pickup-code {
  text-align: center;
  background: #fbf3e7;
  border: 1px dashed #e3cbae;
  border-radius: 16px;
  padding: 22px;
  margin-bottom: 28px;
}
.pickup-code > span {
  font-size: 12px;
  color: #9a8264;
}
.pickup-code strong {
  display: block;
  font-size: 43px;
  letter-spacing: 6px;
  color: #b7612f;
  padding-left: 6px;
  margin: 9px 0;
  font-weight: 700;
}
.pickup-code p {
  font-size: 10px;
  color: #a28d74;
  margin: 0;
}
.pending-hint {
  display: flex;
  align-items: center;
  gap: 13px;
  background: #faf4e9;
  padding: 18px;
  border-radius: 13px;
  color: #b77e45;
  font-size: 13px;
  margin-bottom: 27px;
}
.pending-hint > span > .muted {
  display: block;
  font-size: 11px;
  margin-top: 5px;
}
.cancel-notice {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  font-size: 12px;
  background: #fff0df;
  color: #bd792d;
  padding: 13px;
  border-radius: 10px;
  line-height: 1.7;
  margin-bottom: 17px;
}
.cancel-notice svg {
  flex: none;
  margin-top: 2px;
}
.order-location {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 15px;
}
.location-label {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.location-label > svg {
  color: #ca8a52;
  flex: none;
}
.location-label .muted {
  font-size: 10px;
}
.location-label h3 {
  font-size: 14px;
  margin: 6px 0;
  line-height: 1.5;
}
.order-location .btn {
  font-size: 12px;
  flex: none;
  padding: 9px 13px;
}
.location-warning {
  background: #fff0dd;
  padding: 12px;
  border-radius: 8px;
  color: #986622;
  font-size: 11px;
  line-height: 1.8;
}
.contact-actions {
  display: flex;
  justify-content: center;
  gap: 0;
  margin-top: 26px;
  padding-top: 21px;
  border-top: 1px solid #eee4d6;
}
.contact-actions > * {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  font-size: 12px;
  color: #8b7c68;
  border: 0;
  background: none;
  min-height: 44px;
}
.contact-actions > * + * {
  border-left: 1px solid #e9dfd1;
}
.closed-order {
  text-align: center;
  padding: 15px 0 30px;
  color: #9c8c79;
}
.closed-order svg {
  margin: auto auto 9px;
}
.closed-order strong {
  display: block;
  font-size: 20px;
}
.closed-order p {
  font-size: 12px;
}
.order-receipt {
  padding: 27px;
}
.receipt-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding-bottom: 22px;
  border-bottom: 1px dashed #ddd1c1;
}
.receipt-header > svg {
  color: #bd8a57;
}
.receipt-header h2 {
  font-size: 17px;
  margin: 0;
}
.receipt-header > span {
  display: block;
  flex-basis: 100%;
  font-size: 9px;
  letter-spacing: 2px;
  color: #b0a391;
  margin-top: 5px;
}
.receipt-product {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 17px 0;
}
.receipt-product img {
  width: 49px;
  height: 49px;
  border-radius: 8px;
  object-fit: cover;
}
.receipt-photo {
  display: block;
  flex: none;
  width: 49px;
  height: 49px;
  border-radius: 8px;
  overflow: hidden;
}
.receipt-product h3 a {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  margin: -10px 0 -8px;
}
.receipt-product > div {
  flex: 1;
}
.receipt-product h3 {
  font-size: 12px;
  margin: 0;
  line-height: 1.6;
  font-weight: 500;
}
.receipt-product p {
  font-size: 11px;
  color: #9d9282;
  margin: 5px 0 0;
}
.receipt-product > strong {
  font-size: 13px;
  font-weight: 500;
}
.receipt-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px dashed #ddd1c1;
  padding-top: 23px;
  margin-top: 10px;
  font-size: 13px;
}
.receipt-total strong {
  font-size: 25px;
  color: #e07a3d;
}
.payment-note {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #aa8f6f;
  font-size: 10px;
  margin: 14px 0 23px;
}
.receipt-meta {
  font-size: 10px;
  border-top: 1px solid #eee5d8;
  padding-top: 12px;
  margin: 0;
}
.receipt-meta > div {
  display: flex;
  justify-content: space-between;
  gap: 15px;
  margin: 13px 0;
}
.receipt-meta dt {
  color: #a19684;
  white-space: nowrap;
}
.receipt-meta dd {
  margin: 0;
  color: #7e7465;
  overflow-wrap: anywhere;
  text-align: right;
}
.receipt-meta .note-meta {
  flex-direction: column;
  gap: 7px;
}
.receipt-meta .note-meta dd {
  text-align: left;
  line-height: 1.8;
}
.review-card {
  padding: 27px;
}
.review-card > .section-heading {
  margin: 0;
}
.review-card h2 {
  font-size: 17px;
  margin: 0;
}
.stars {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 18px 0;
  color: #e8944c;
}
.stars > button {
  padding: 5px;
  border: 0;
  background: none;
  color: inherit;
  min-width: 40px;
  min-height: 44px;
  display: grid;
  place-items: center;
}
.stars > span {
  font-size: 11px;
  margin-left: 10px;
  color: #ad8761;
}
.review-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
}
.review-bottom > .muted {
  font-size: 10px;
}
.review-bottom .btn {
  font-size: 12px;
}
.submitted-review {
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
}
.review-saved {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #90987e;
}
.static-stars {
  gap: 6px;
}
.cancel-modal {
  border: 0;
  padding: 0;
  background: transparent;
  max-width: min(450px, calc(100vw - 32px));
  width: 100%;
}
.cancel-modal::backdrop {
  background: #28211670;
  backdrop-filter: blur(4px);
}
.cancel-dialog {
  position: relative;
  width: 100%;
  max-width: 450px;
  padding: 32px;
}
.cancel-dialog h2 {
  font-size: 23px;
  margin: 14px 0;
}
.cancel-dialog > .muted {
  font-size: 13px;
  line-height: 1.8;
  margin-bottom: 24px;
}
.cancel-dialog .field > .muted {
  font-size: 11px;
}
.close-dialog {
  position: absolute;
  top: 13px;
  right: 13px;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: 0;
  background: transparent;
  color: #8c8071;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 22px;
}
@media (max-width: 900px) {
  .detail-layout {
    grid-template-columns: 1fr;
  }
  .order-receipt {
    position: static;
  }
}
@media (max-width: 600px) {
  .reorder-card {
    padding: 21px 19px;
    flex-wrap: wrap;
    gap: 13px;
  }
  .reorder-card .btn {
    width: 100%;
  }
  .order-detail-page {
    padding-top: 8px;
  }
  .back-link {
    min-height: 44px;
    margin-bottom: 6px;
  }
  .order-detail-heading {
    margin-bottom: 16px;
    align-items: flex-start;
    gap: 10px;
  }
  .order-detail-heading h1 {
    font-size: 22px;
    line-height: 1.4;
    letter-spacing: -0.3px;
    margin: 4px 0 7px;
  }
  .order-detail-heading p {
    font-size: 12px;
    line-height: 1.65;
  }
  .detail-layout,
  .detail-main {
    gap: 14px;
  }
  .refresh-button {
    flex: none;
  }
  .pickup-ready-heading p {
    display: none;
  }
  .progress-card,
  .order-receipt,
  .review-card {
    padding: 16px;
  }
  .order-progress {
    margin: 0 0 18px;
  }
  .progress-step {
    font-size: 11px;
    gap: 7px;
  }
  .step-symbol {
    width: 32px;
    height: 32px;
  }
  .progress-step:not(:last-child)::after {
    top: 16px;
    left: calc(50% + 22px);
    right: calc(-50% + 22px);
  }
  .pending-hint {
    padding: 10px 12px;
    gap: 8px;
    margin-bottom: 16px;
  }
  .pending-hint > span > .muted {
    display: inline;
    margin-left: 6px;
  }
  .contact-actions {
    margin-top: 14px;
    padding-top: 10px;
  }
  .receipt-header {
    padding-bottom: 12px;
  }
  .receipt-header > span {
    display: none;
  }
  .receipt-total {
    padding-top: 14px;
    margin-top: 0;
  }
  .payment-note {
    font-size: 11px;
    margin-bottom: 12px;
  }
  .order-location .btn {
    padding: 8px 11px;
  }
  .location-label h3 {
    font-size: 13px;
  }
  .contact-actions > * {
    font-size: 11px;
    gap: 5px;
  }
  .stars > span {
    margin-left: 5px;
  }
  .stars > button {
    min-width: 34px;
  }
  .pickup-code strong {
    font-size: 32px;
    letter-spacing: 4px;
    padding-left: 4px;
  }
  .cancel-dialog {
    padding: 27px 23px;
  }
}
</style>
