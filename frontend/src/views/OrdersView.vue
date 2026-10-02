<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  ArrowUpRight,
  ChevronRight,
  Clock3,
  CreditCard,
  MapPin,
  RefreshCw,
  ShoppingBag,
  Store,
  AlertCircle,
} from "lucide-vue-next";
import { api, formatTime, money, statusText } from "../lib/api";
import {
  needsFinancialFollowUp,
  refundNeedsFollowUp,
} from "../lib/orderFollowUp";
import { useSession } from "../stores/session";

const router = useRouter();
const session = useSession();
const orders = ref<any[]>([]);
const loading = ref(true);
const error = ref("");
const selected = ref("all");
const tabs = [
  { id: "all", label: "全部订单" },
  { id: "active", label: "进行中" },
  { id: "followup", label: "退款与待处理" },
  { id: "completed", label: "已完成" },
  { id: "cancelled", label: "已取消" },
];
const followUp = computed(() => orders.value.filter(needsFinancialFollowUp));
const active = computed(() =>
  orders.value.filter((order) =>
    [
      "pending_payment",
      "pending",
      "preparing",
      "ready",
      "delivering",
      "arrived",
    ].includes(order.status),
  ),
);
const filtered = computed(() =>
  orders.value.filter(
    (order) =>
      selected.value === "all" ||
      (selected.value === "active"
        ? [
            "pending_payment",
            "pending",
            "preparing",
            "ready",
            "delivering",
            "arrived",
          ].includes(order.status)
        : selected.value === "followup"
          ? needsFinancialFollowUp(order)
          : selected.value === "cancelled"
            ? ["cancelled", "rejected"].includes(order.status)
            : order.status === selected.value),
  ),
);
let timer: ReturnType<typeof setInterval> | undefined;
const fetching = ref(false);
const lastSynced = ref("");
const lifetime = new AbortController();
let disposed = false;
async function load() {
  if (fetching.value || disposed) return;
  fetching.value = true;
  try {
    const result = await api<any[]>("/orders", { signal: lifetime.signal });
    if (disposed) return;
    orders.value = result;
    lastSynced.value = new Date().toISOString();
    error.value = "";
  } catch (e) {
    if (disposed) return;
    error.value = (e as Error).message;
  } finally {
    if (!disposed) {
      loading.value = false;
      fetching.value = false;
    }
  }
}
onMounted(async () => {
  try {
    await session.load();
    if (disposed) return;
    if (!session.user) {
      await router.replace({ path: "/login", query: { returnTo: "/orders" } });
      return;
    }
    await load();
    if (!disposed)
      timer = setInterval(() => {
        if (!document.hidden) void load();
      }, 10000);
  } catch (e) {
    if (disposed) return;
    error.value = (e as Error).message;
    loading.value = false;
  }
});
onUnmounted(() => {
  disposed = true;
  lifetime.abort();
  clearInterval(timer);
});
function caption(order: any) {
  if (order.cancel_requested) return "取消申请已提交，等待商家处理";
  if (order.fulfillment_type === "delivery")
    return (
      {
        pending_payment: "请先完成微信付款，商家才会接单",
        pending: "付款已确认，等待商家接单",
        preparing: "商家正在制作你的餐点",
        ready: "餐点已备好，等待配送出发",
        delivering: "商家配送中，请留意交接点到达通知",
        arrived: "已到交接点，收到餐点后再确认",
        completed: "餐点已交到你手中",
        cancelled: "配送订单已取消",
        rejected: "商家暂时无法配送",
      } as Record<string, string>
    )[order.status];
  return (
    {
      pending: "商家正在确认你的订单",
      preparing: "美味正在制作中，请耐心等待",
      ready: "餐点已备好，记得及时来取",
      completed: "谢谢你，让小摊的烟火继续",
      cancelled: "订单已取消",
      rejected: "商家暂时无法接单",
    } as Record<string, string>
  )[order.status];
}
function paymentSummary(order: any) {
  if (order.payment_review_required || order.payment?.status === "review")
    return "付款状态需要核对，请勿重复付款";
  if (order.payment?.status === "reconcile")
    return "微信付款结果待确认，请勿重复付款";
  if (refundNeedsFollowUp(order)) {
    if (order.refund?.status === "closed") return "退款已关闭 · 款项尚未退回";
    if (order.refund?.status === "abnormal") return "退款异常 · 请联系商家处理";
    if (order.refund?.status === "reconcile")
      return "退款结果待确认 · 请勿重复付款";
    return "微信支付 · 退款处理中，尚未确认退回";
  }
  if (order.payment_status === "refunded") return "微信支付 · 已退款";
  if (order.payment_status === "refunding") return "微信支付 · 退款处理中";
  if (order.payment_status === "paid")
    return order.payment_method === "wechat"
      ? "微信支付 · 已付款"
      : "到摊付款 · 已收款";
  if (["cancelled", "rejected", "completed"].includes(order.status)) return "";
  if (["creating", "pending"].includes(order.payment?.status))
    return "微信支付 · 请核对付款进度";
  if (order.cancel_requested) return "付款已暂停 · 取消申请处理中";
  if (order.fulfillment_type === "delivery")
    return order.wechat_payment?.available
      ? "微信支付 · 配送需先付款"
      : "微信支付 · 暂时无法付款";
  if (order.wechat_payment?.available)
    return order.status === "ready"
      ? "微信支付 · 待付款"
      : "微信支付 · 出餐后可支付";
  return "微信支付 · 尚未开通，可到摊付款";
}
function orderAction(order: any) {
  if (refundNeedsFollowUp(order)) return "查看退款进度";
  if (
    order.payment_review_required ||
    ["refunding", "refunded"].includes(order.payment_status) ||
    ["creating", "pending", "reconcile", "review"].includes(
      order.payment?.status,
    )
  )
    return "查看付款进度";
  if (
    (order.status === "pending_payment" ||
      (order.fulfillment_type !== "delivery" && order.status === "ready")) &&
    order.payment_status === "unpaid" &&
    !order.cancel_requested
  )
    return order.wechat_payment?.available ? "去付款" : "查看付款方式";
  if (
    order.fulfillment_type === "delivery" &&
    !["completed", "cancelled", "rejected"].includes(order.status)
  )
    return order.status === "arrived" ? "去收餐" : "查看配送进度";
  return order.status === "ready"
    ? "查看取餐码"
    : order.status === "completed" && !order.review
      ? "去评价"
      : "查看订单";
}
</script>

<template>
  <div class="page orders-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">A TASTE OF YOUR DAY</span>
        <h1>我的订单<span class="heading-dot">.</span></h1>
        <p class="muted">每一份期待，都有热气腾腾的回应。</p>
      </div>
      <RouterLink to="/" class="btn btn-secondary"
        >再去逛逛 <ArrowUpRight :size="17"
      /></RouterLink>
    </div>
    <div v-if="active.length" class="active-notice">
      <span class="notice-dot"></span
      ><span
        >有 <strong>{{ active.length }}</strong> 份好味道正在等待你</span
      ><span class="muted">{{
        error ? "订单状态暂未同步" : "订单状态每 10 秒自动更新"
      }}</span>
    </div>
    <button
      v-if="followUp.length"
      class="financial-notice"
      @click="selected = 'followup'"
    >
      <AlertCircle :size="19" />
      <span
        ><strong>{{ followUp.length }} 笔订单的款项仍待处理</strong
        ><small>退款与款项核对记录，在这里继续查看。</small></span
      >
      <ChevronRight :size="18" />
    </button>
    <div class="orders-toolbar">
      <div class="tabs">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          :class="{ active: selected === tab.id }"
          @click="selected = tab.id"
        >
          {{ tab.label }}
          <span v-if="tab.id === 'active' && active.length" class="tab-count">{{
            active.length
          }}</span>
          <span
            v-if="tab.id === 'followup' && followUp.length"
            class="tab-count"
            >{{ followUp.length }}</span
          >
        </button>
      </div>
      <button
        class="refresh-button"
        aria-label="刷新订单"
        :disabled="fetching"
        :aria-busy="fetching"
        @click="load"
      >
        <RefreshCw :size="16" />
      </button>
    </div>
    <p v-if="!loading" class="sync-status" role="status">
      {{
        fetching
          ? "正在同步最新订单…"
          : error
            ? "尚未获取最新订单状态"
            : `最近同步 ${formatTime(lastSynced)}`
      }}
    </p>
    <p v-if="error" class="error-message" role="alert">
      {{ error }}<span v-if="lastSynced"> 当前显示上次同步的记录。</span>
      <button class="inline-retry" :disabled="fetching" @click="load">
        {{ fetching ? "正在重试…" : "重新加载" }}
      </button>
    </p>
    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
      <p>正在查找你的订单…</p>
    </div>
    <div v-else-if="!filtered.length && !error" class="empty-state card">
      <ShoppingBag :size="45" />
      <h2>
        {{
          selected === "all"
            ? "第一份好味道，等你发现"
            : selected === "followup"
              ? "目前没有待处理的款项"
              : "这里还没有订单"
        }}
      </h2>
      <p>
        {{
          selected === "all"
            ? "去附近的小摊看看，挑一份今天想吃的。"
            : selected === "followup"
              ? "已完成的退款可在全部订单中查看。"
              : "换个分类看看，或者去发现新的味道。"
        }}
      </p>
      <RouterLink to="/" class="btn btn-primary"
        >去逛逛小摊 <ArrowUpRight :size="17"
      /></RouterLink>
    </div>
    <div v-else class="order-grid">
      <RouterLink
        v-for="order in filtered"
        :key="order.id"
        :to="`/orders/${order.id}`"
        class="card order-card"
      >
        <div class="order-card-top">
          <h2>
            <Store :size="17" /> {{ order.stall_name }}
            <small v-if="order.mode === 'simulation'" class="simulation-badge"
              >模拟订单</small
            >
            <ChevronRight :size="15" />
          </h2>
          <span class="order-status" :class="order.status">{{
            order.cancel_requested
              ? "取消申请中"
              : statusText(order.status, order.fulfillment_type)
          }}</span>
        </div>
        <div class="order-food">
          <img
            :src="order.items?.[0]?.image || order.stall_image"
            :alt="order.items?.[0]?.name || order.stall_name"
          />
          <div class="food-copy">
            <h3>{{ order.items?.map((item: any) => item.name).join("、") }}</h3>
            <p class="muted">
              共
              {{
                order.items?.reduce(
                  (sum: number, item: any) => sum + item.quantity,
                  0,
                )
              }}
              件商品 ·
              {{
                order.fulfillment_type === "delivery" ? "商家配送" : "到摊自取"
              }}
            </p>
            <span class="order-time"
              ><Clock3 :size="12" /> {{ formatTime(order.created_at) }}</span
            >
          </div>
          <strong class="order-amount">¥{{ money(order.total_cents) }}</strong>
        </div>
        <p
          v-if="paymentSummary(order)"
          class="payment-summary"
          :class="{ 'needs-followup': needsFinancialFollowUp(order) }"
        >
          <CreditCard :size="16" />{{
            order.mode === "simulation" ? "模拟 · " : ""
          }}{{ paymentSummary(order) }}
        </p>
        <div class="order-caption">
          <span
            v-if="
              order.fulfillment_type === 'delivery' || order.status === 'ready'
            "
            ><MapPin :size="15" />
            {{
              order.fulfillment_type === "delivery"
                ? order.delivery_point_name
                : order.pickup_address
            }}</span
          ><span v-else>{{ caption(order) }}</span
          ><strong>{{ orderAction(order) }} <ArrowUpRight :size="14" /></strong>
        </div>
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.financial-notice {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 16px 19px;
  margin: 0 0 20px;
  text-align: left;
  border: 1px solid #e8d0ad;
  border-radius: 12px;
  background: #fff2df;
  color: #85552d;
}
.financial-notice span {
  flex: 1;
}
.financial-notice strong {
  display: block;
  font-size: 13px;
  font-weight: 600;
}
.financial-notice small {
  display: block;
  margin-top: 6px;
  color: #7d6a54;
  font-size: 11px;
  line-height: 1.7;
}
.financial-notice svg {
  flex: none;
}
.payment-summary.needs-followup {
  background: #fff2df;
  color: #85552d;
}
.orders-toolbar .tabs {
  min-width: 0;
  overflow-x: auto;
}
.orders-toolbar .tabs button {
  flex-shrink: 0;
  min-height: 44px;
}
.simulation-badge {
  color: #80622e;
  background: #f7edda;
  padding: 3px 6px;
  border-radius: 5px;
  font-size: 10px;
  font-weight: 500;
  white-space: nowrap;
}
.orders-page {
  padding-top: 40px;
}
.heading-dot {
  color: #ed7337;
}
.active-notice {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid #e9dfca;
  background: #f9f1e3;
  border-radius: 12px;
  padding: 15px 19px;
  font-size: 13px;
  margin: 12px 0 25px;
}
.active-notice strong {
  color: #cf6b33;
}
.active-notice > .muted {
  margin-left: auto;
  font-size: 11px;
}
.notice-dot {
  width: 7px;
  height: 7px;
  border-radius: 100%;
  background: #ed823d;
  box-shadow: 0 0 0 4px #f5dcc1;
}
.orders-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 25px;
  gap: 10px;
}
.refresh-button {
  display: grid;
  place-items: center;
  border: 1px solid #e5ddcf;
  background: transparent;
  border-radius: 50%;
  min-width: 44px;
  height: 44px;
  color: #756957;
}
.refresh-button:disabled {
  opacity: 0.55;
  cursor: wait;
}
.sync-status {
  margin: -12px 0 20px;
  color: #766955;
  font-size: 12px;
  line-height: 1.8;
}
.tab-count {
  font-size: 10px;
  background: #f8dfca;
  padding: 2px 5px;
  border-radius: 5px;
  margin-left: 4px;
}
.order-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}
.order-card {
  padding: 24px;
  transition:
    transform 0.2s,
    box-shadow 0.2s;
  display: block;
}
.order-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 9px 24px #49351c0a;
}
.order-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.order-card-top h2 {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 15px;
  margin: 0;
}
.order-card-top h2 > svg:first-child {
  color: #9c8162;
}
.order-card-top h2 > svg:last-child {
  color: #b0a493;
}
.order-status {
  font-size: 11px;
  color: #989183;
  white-space: nowrap;
}
.order-status.pending,
.order-status.preparing {
  color: #dd7735;
}
.order-status.ready {
  color: #59864b;
}
.order-status.completed {
  color: #788872;
}
.order-food {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 24px 0;
}
.order-food img {
  width: 73px;
  height: 73px;
  object-fit: cover;
  border-radius: 12px;
}
.food-copy {
  min-width: 0;
  flex: 1;
}
.food-copy h3 {
  font-size: 14px;
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}
.food-copy p {
  font-size: 11px;
  margin: 9px 0;
}
.order-time {
  display: flex;
  align-items: center;
  gap: 5px;
  color: #a1998b;
  font-size: 10px;
}
.order-amount {
  font-size: 18px;
  align-self: center;
  font-weight: 600;
}
.order-caption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid #efe8dc;
  padding-top: 16px;
  font-size: 11px;
  color: #9a907f;
}
.payment-summary {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 36px;
  padding: 8px 10px;
  margin: 0 0 15px;
  border-radius: 8px;
  background: #f1f5eb;
  color: #607353;
  font-size: 11px;
  line-height: 1.7;
}
.payment-summary svg {
  flex: none;
}
.order-caption > span {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.order-caption strong {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
  white-space: nowrap;
  color: #d47d41;
}
.inline-retry {
  border: 0;
  background: transparent;
  color: #bb562b;
  text-decoration: underline;
  margin-left: 10px;
  min-height: 44px;
}
@media (max-width: 1000px) {
  .order-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .orders-page {
    padding-top: 23px;
  }
  .page-heading > .btn {
    display: none;
  }
  .active-notice {
    padding: 13px;
    font-size: 12px;
    margin-bottom: 20px;
  }
  .active-notice > .muted {
    display: none;
  }
  .orders-toolbar {
    gap: 4px;
    margin-bottom: 20px;
  }
  .tabs {
    gap: 1px;
  }
  .tabs button {
    font-size: 12px;
    padding: 11px 10px;
  }
  .refresh-button {
    border: 0;
    min-width: 44px;
  }
  .order-card {
    padding: 19px;
  }
  .order-food {
    gap: 11px;
    padding: 20px 0;
  }
  .order-food img {
    width: 62px;
    height: 62px;
  }
  .order-amount {
    font-size: 16px;
  }
  .food-copy h3 {
    font-size: 13px;
  }
  .order-caption {
    font-size: 10px;
  }
  .order-card-top h2 {
    font-size: 14px;
  }
}
</style>
