<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  CheckCircle2,
  CreditCard,
  ExternalLink,
  RefreshCw,
  ShieldCheck,
} from "lucide-vue-next";
import { api, money } from "../lib/api";
import type { Order } from "../lib/types";
import { useSession } from "../stores/session";
import SimulationCashier from "./SimulationCashier.vue";
import {
  needsFinancialFollowUp,
  refundNeedsFollowUp,
} from "../lib/orderFollowUp";

const props = defineProps<{ order: Order }>();
const emit = defineEmits<{
  updated: [order: Order];
  changing: [];
  settled: [];
}>();
const session = useSession();
const userId = session.user?.id;
const busy = ref("");
const error = ref("");
const qr = ref("");
const qrError = ref(false);
const uncertain = ref(false);
const now = ref(Date.now());
const lastRead = ref("");
let disposed = false;
let timer: ReturnType<typeof setInterval> | undefined;
let ticks = 0;
let lastSync = 0;
let qrVersion = 0;
const controller = new AbortController();
const current = () => !disposed && session.user?.id === userId;
const inWechat = /MicroMessenger/i.test(navigator.userAgent);
const mobile = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
const payment = computed(() => props.order.payment);
const isSimulation = computed(() => props.order.mode === "simulation");
const isDelivery = computed(() => props.order.fulfillment_type === "delivery");
const financialFollowUp = computed(() => needsFinancialFollowUp(props.order));
const refundFollowUp = computed(() => refundNeedsFollowUp(props.order));
const refundProblem = computed(
  () =>
    refundFollowUp.value &&
    ["closed", "abnormal"].includes(props.order.refund?.status || ""),
);
const payableStage = computed(() =>
  isDelivery.value
    ? props.order.status === "pending_payment"
    : props.order.status === "ready",
);
const deliveryWindowClosing = computed(
  () =>
    isDelivery.value &&
    props.order.status === "pending_payment" &&
    !!props.order.expires_at &&
    Date.parse(props.order.expires_at) - now.value <= 75000,
);
const active = computed(
  () =>
    !!payment.value &&
    ["creating", "pending", "reconcile"].includes(payment.value.status),
);
const review = computed(
  () =>
    props.order.payment_review_required || payment.value?.status === "review",
);
const visible = computed(
  () =>
    [
      "pending_payment",
      "pending",
      "preparing",
      "ready",
      "delivering",
      "arrived",
    ].includes(props.order.status) ||
    !!payment.value ||
    !!props.order.refund ||
    financialFollowUp.value ||
    ["paid", "refunding", "refunded"].includes(props.order.payment_status),
);
const remaining = computed(
  () =>
    Math.max(
      0,
      Math.ceil(
        (Date.parse(payment.value?.expires_at || "") - now.value) / 1000,
      ),
    ) || 0,
);
const canStart = computed(
  () =>
    payableStage.value &&
    !deliveryWindowClosing.value &&
    props.order.payment_status === "unpaid" &&
    !props.order.cancel_requested &&
    !active.value &&
    !uncertain.value &&
    !review.value &&
    props.order.wechat_payment?.available,
);
const channels = computed(() => props.order.wechat_payment?.channels || []);
const channel = computed(() =>
  isSimulation.value && channels.value.includes("simulation")
    ? "simulation"
    : mobile && channels.value.includes("h5")
      ? "h5"
      : channels.value.includes("native")
        ? "native"
        : "h5",
);
const browserSupported = computed(() =>
  isSimulation.value
    ? channels.value.includes("simulation")
    : !inWechat && channels.value.includes(mobile ? "h5" : "native"),
);
const canRetry = computed(
  () =>
    active.value &&
    !isSimulation.value &&
    !review.value &&
    orderUnpaid() &&
    remaining.value > 60 &&
    !inWechat &&
    payment.value?.channel === (mobile ? "h5" : "native") &&
    payment.value.status !== "pending",
);
const canSimulate = computed(
  () =>
    isSimulation.value &&
    session.config?.services_simulation_enabled !== false &&
    payment.value?.mode === "simulation" &&
    active.value &&
    orderUnpaid() &&
    !review.value &&
    !uncertain.value &&
    remaining.value > 0,
);
function orderUnpaid() {
  return (
    payableStage.value &&
    props.order.payment_status === "unpaid" &&
    !props.order.cancel_requested
  );
}
const h5Link = computed(() => {
  if (!active.value || !remaining.value || payment.value?.channel !== "h5")
    return "";
  try {
    const url = new URL(payment.value.h5_url);
    if (
      url.protocol !== "https:" ||
      url.hostname !== "wx.tenpay.com" ||
      url.username ||
      url.password ||
      url.port
    )
      return "";
    url.searchParams.set(
      "redirect_url",
      `${location.origin}/orders/${encodeURIComponent(props.order.id)}`,
    );
    return url.href;
  } catch {
    return "";
  }
});
watch(
  () => payment.value?.code_url,
  async (url) => {
    const version = ++qrVersion;
    qr.value = "";
    qrError.value = false;
    if (!url || !/^weixin:\/\/wxpay\//.test(url)) return;
    try {
      const { toDataURL } = await import("qrcode");
      const result = await toDataURL(url, {
        width: 240,
        margin: 2,
        errorCorrectionLevel: "M",
        color: { dark: "#193a2e", light: "#ffffff" },
      });
      if (current() && version === qrVersion) qr.value = result;
    } catch {
      if (current() && version === qrVersion) qrError.value = true;
    }
  },
  { immediate: true },
);

async function request(
  path: string,
  options: { method?: string; body?: any } = {},
) {
  const abort = new AbortController();
  const stop = () => abort.abort();
  controller.signal.addEventListener("abort", stop, { once: true });
  const timeout = setTimeout(stop, 20000);
  try {
    return await api<Order>(path, { ...options, signal: abort.signal });
  } finally {
    clearTimeout(timeout);
    controller.signal.removeEventListener("abort", stop);
  }
}

async function action(
  kind: "wechat" | "sync" | "close" | "simulate" | "read",
  outcome?: "success" | "failure" | "pending",
) {
  if (busy.value || !current()) return;
  if (kind === "sync" && Date.now() - lastSync < 3000) return;
  if (kind === "simulate" && (!outcome || !canSimulate.value)) return;
  if (
    kind === "wechat" &&
    !(canRetry.value || (canStart.value && browserSupported.value))
  )
    return;
  busy.value = kind;
  error.value = "";
  lastSync = Date.now();
  emit("changing");
  try {
    const updated =
      kind === "read"
        ? await request(`/orders/${props.order.id}`)
        : await request(`/orders/${props.order.id}/payments/${kind}`, {
            method: "POST",
            body:
              kind === "wechat"
                ? {
                    channel: active.value
                      ? payment.value!.channel
                      : channel.value,
                  }
                : kind === "simulate"
                  ? { payment_id: payment.value!.id, outcome }
                  : {},
          });
    if (current()) {
      uncertain.value = false;
      if (kind === "read")
        lastRead.value = new Date().toLocaleTimeString("zh-CN", {
          hour12: false,
        });
      emit("updated", updated);
    }
  } catch (e) {
    if (!current()) return;
    error.value = (e as Error).message;
    if (kind === "read") {
      error.value = `未获取最新处理结果，当前显示上次记录。${error.value}`;
      return;
    }
    if (kind === "wechat" || kind === "simulate") uncertain.value = true;
    // A lost response can still have created a durable payment intent. Read it
    // before enabling any new attempt; never infer a failed charge from a timeout.
    try {
      const latest = await request(`/orders/${props.order.id}`);
      if (current()) {
        uncertain.value = false;
        emit("updated", latest);
      }
    } catch {
      /* Keep the error; no optimistic payment transition. */
    }
  } finally {
    if (current()) {
      busy.value = "";
      emit("settled");
    }
  }
}
function refreshVisible() {
  if (!document.hidden && (active.value || uncertain.value))
    void action("sync");
}
onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now();
    if (++ticks % 10 === 0) refreshVisible();
  }, 1000);
  document.addEventListener("visibilitychange", refreshVisible);
  window.addEventListener("focus", refreshVisible);
});
onUnmounted(() => {
  disposed = true;
  ++qrVersion;
  controller.abort();
  clearInterval(timer);
  document.removeEventListener("visibilitychange", refreshVisible);
  window.removeEventListener("focus", refreshVisible);
});
</script>

<template>
  <section
    v-if="visible"
    id="payment"
    class="card student-payment"
    aria-labelledby="payment-heading"
  >
    <header>
      <span class="payment-symbol"><CreditCard :size="23" /></span>
      <div>
        <span class="eyebrow">PAY & PICK UP</span>
        <h2 id="payment-heading">支付方式</h2>
      </div>
      <strong>¥{{ money(order.total_cents) }}</strong>
    </header>
    <p v-if="isSimulation" class="simulation-label" role="note">
      模拟体验 · 不会扣款，也不会产生真实退款
    </p>
    <div v-if="review" class="payment-message attention" role="status">
      <strong>这笔款项需要核对</strong>
      <p>请联系商家处理，暂时不要重复付款。订单不会自动核销。</p>
    </div>
    <div
      v-else-if="order.payment_status === 'refunded'"
      class="payment-message"
      role="status"
    >
      <CheckCircle2 :size="20" />
      <div>
        <strong>{{
          isSimulation ? "模拟退款已完成" : "微信退款已成功"
        }}</strong>
        <p>
          ¥{{ money(order.refund?.amount_cents || order.total_cents) }}
          {{
            isSimulation
              ? "已在模拟账目中退回，没有真实资金变动。"
              : "已按原支付渠道退款，到账情况请查看微信账单。"
          }}
        </p>
      </div>
    </div>
    <div
      v-else-if="refundFollowUp"
      class="payment-message attention"
      role="status"
    >
      <div>
        <strong>{{
          refundProblem
            ? isSimulation
              ? "模拟退款需要商家处理"
              : "退款需要商家处理"
            : isSimulation
              ? "模拟退款正在处理中"
              : "退款正在处理中"
        }}</strong>
        <p>
          {{
            refundProblem
              ? isSimulation
                ? "这笔模拟退款尚未完成，请联系商家继续演练退款结果，没有真实资金变动。"
                : order.refund?.status === "closed"
                  ? "退款申请已关闭，款项尚未退回。请联系商家核对后续处理，暂时不要重复付款。"
                  : "退款处理出现异常，款项尚未确认退回。请联系商家处理，暂时不要重复付款。"
              : isSimulation
                ? "模拟退款结果尚未确认，可由商家继续演练退款结果，没有真实资金变动。"
                : "款项尚未确认退回。请勿重复付款，退款进度将在这里更新。"
          }}
        </p>
      </div>
    </div>
    <div
      v-else-if="order.payment_status === 'paid'"
      class="payment-message"
      role="status"
    >
      <CheckCircle2 :size="20" />
      <div>
        <strong>{{
          isSimulation
            ? "模拟付款已确认"
            : order.payment_method === "wechat"
              ? "微信支付已确认"
              : "商家已确认线下收款"
        }}</strong>
        <p>
          {{
            isSimulation
              ? "这是一笔模拟记录，没有实际扣款。可以继续体验后续订单操作。"
              : order.status === "completed"
                ? "本次取餐已完成，感谢你的光顾。"
                : isDelivery
                  ? "无需再次付款。请留意配送进度，实际收到餐点后再确认收餐。"
                  : "无需再次付款，请向商家出示取餐码。"
          }}
        </p>
      </div>
    </div>
    <template v-else-if="active || uncertain">
      <SimulationCashier
        v-if="isSimulation && payment?.mode === 'simulation'"
        :amount="order.total_cents"
        :busy="!!busy"
        :allowed="canSimulate"
        :pending="payment.status === 'reconcile'"
        @result="action('simulate', $event)"
      />
      <div v-else class="payment-session">
        <template
          v-if="
            payment?.status === 'pending' &&
            remaining > 0 &&
            (payment.channel === 'native' ? payment.code_url : h5Link)
          "
        >
          <div v-if="payment.channel === 'native'" class="qr-payment">
            <img
              v-if="qr"
              :src="qr"
              alt="本订单微信支付二维码"
              width="220"
              height="220"
            />
            <p v-else>
              {{
                qrError
                  ? "二维码暂时无法显示，请重新打开本页，或先关闭本次支付。"
                  : "正在生成付款二维码…"
              }}
            </p>
            <strong>使用微信扫一扫付款</strong
            ><small
              >二维码 {{ Math.floor(remaining / 60) }}:{{
                String(remaining % 60).padStart(2, "0")
              }}
              后到期</small
            >
          </div>
          <div v-else class="h5-payment">
            <strong>通过微信完成付款</strong>
            <p>付款后返回本页核对结果。仅打开微信不会被记为已付款。</p>
            <a
              v-if="h5Link && !inWechat && mobile"
              :href="h5Link"
              class="btn pay-button"
              >前往微信支付<ExternalLink :size="16"
            /></a>
            <p v-else>请在手机系统浏览器打开本页，再继续付款。</p>
          </div>
        </template>
        <div v-else class="payment-message attention">
          <div>
            <strong>正在确认微信付款状态</strong>
            <p>
              请先核对这笔款项，不要另行付款。过期或关闭的二维码不能继续使用。
            </p>
            <p v-if="payment?.status === 'pending'">
              付款入口未就绪时，可先关闭本次微信支付，再重新发起。系统会先核对是否已付款。
            </p>
          </div>
        </div>
      </div>
      <div class="payment-actions">
        <button
          class="btn btn-secondary"
          :disabled="!!busy"
          @click="action('sync')"
        >
          <RefreshCw :size="16" />{{
            busy === "sync" ? "正在核对…" : "刷新付款状态"
          }}</button
        ><button
          v-if="canRetry"
          class="btn btn-secondary"
          :disabled="!!busy"
          @click="action('wechat')"
        >
          {{ busy === "wechat" ? "正在恢复…" : "重试获取付款入口" }}</button
        ><button
          v-if="order.payment_can_close"
          class="btn btn-ghost"
          :disabled="!!busy"
          @click="action('close')"
        >
          {{
            busy === "close"
              ? "正在关闭…"
              : isSimulation
                ? "关闭本次模拟支付"
                : isDelivery
                  ? "关闭本次微信支付"
                  : "关闭微信支付，改为到摊付款"
          }}
        </button>
      </div>
      <p class="payment-footnote">
        {{
          isDelivery
            ? "系统会先核对支付结果。关闭支付不代表取消订单，配送订单仍须在线付款。"
            : "切换付款方式前，系统会先核对并关闭当前微信支付。"
        }}
      </p>
    </template>
    <template
      v-else-if="
        ['pending_payment', 'pending', 'preparing', 'ready'].includes(
          order.status,
        ) && !order.cancel_requested
      "
    >
      <div class="payment-choice">
        <div>
          <strong>{{
            isSimulation
              ? order.wechat_payment?.available
                ? "模拟微信支付"
                : "模拟线上支付已关闭"
              : order.wechat_payment?.available
                ? "微信支付"
                : "微信支付尚未开通"
          }}</strong
          ><span class="online-label">线上支付</span>
          <p>
            {{
              isSimulation
                ? order.wechat_payment?.available
                  ? isDelivery
                    ? "点击进入模拟收银台，体验付款后商家接单的流程。"
                    : "商家出餐后可以模拟微信付款，也可体验到摊付款。"
                  : "商家暂未开启模拟线上支付。可以联系商家在经营服务中开启。"
                : order.wechat_payment?.available
                  ? isDelivery
                    ? "请先完成付款，商家确认接单后开始制作并配送至交接点。"
                    : order.status === "ready"
                      ? "在线付款后，向商家出示取餐码即可。也可以在取餐时直接向商家付款。"
                      : "商家出餐后，在这里使用微信付款。现在无需付款。"
                  : isDelivery
                    ? "微信支付当前不可用，配送订单不能改为线下付款。请稍后重试或取消订单。"
                    : order.wechat_payment?.reason ||
                      "当前支持到摊付款，请向商家确认支付方式。"
            }}
          </p>
        </div>
        <button
          class="btn pay-button"
          :disabled="!canStart || !browserSupported || !!busy"
          @click="action('wechat')"
        >
          {{
            busy === "wechat"
              ? "正在创建…"
              : deliveryWindowClosing
                ? "付款时限将至"
                : order.wechat_payment?.available && !payableStage
                  ? "出餐后可支付"
                  : isSimulation
                    ? "模拟微信付款"
                    : "微信支付"
          }}
        </button>
      </div>
      <p v-if="deliveryWindowClosing" class="payment-footnote">
        剩余时间不足以发起新支付，请取消本单后重新下单。已发起的支付请先核对结果。
      </p>
      <p v-else-if="canStart && !browserSupported" class="payment-footnote">
        {{
          inWechat
            ? "请使用手机系统浏览器打开本页；当前尚未接入微信内网页支付。"
            : mobile
              ? isDelivery
                ? "该商家暂未开通手机网页支付，请在电脑打开本单使用微信扫码。配送订单不可线下付款。"
                : "该商家暂未开通手机网页支付。请到摊付款，或在电脑打开本单后使用微信扫码。"
              : isDelivery
                ? "请用手机系统浏览器打开本单完成微信付款。配送订单不可线下付款。"
                : "该商家暂未开通电脑扫码支付。请用手机系统浏览器打开本单，或到摊付款。"
        }}
      </p>
      <p v-else class="payment-footnote">
        <ShieldCheck :size="14" />付款状态以服务端核验结果为准
      </p>
    </template>
    <p v-else-if="order.cancel_requested" class="payment-footnote">
      取消申请正在处理中，暂时不能发起付款。
    </p>
    <div
      v-if="financialFollowUp && (!active || review || refundFollowUp)"
      class="financial-refresh"
    >
      <button
        class="btn btn-secondary"
        :disabled="!!busy"
        @click="action('read')"
      >
        <RefreshCw :size="16" />{{
          busy === "read"
            ? "正在刷新…"
            : refundFollowUp
              ? "刷新退款状态"
              : "刷新款项处理状态"
        }}
      </button>
      <p>
        这里展示最新处理记录，刷新不会再次付款。{{
          lastRead ? `最近刷新 ${lastRead}` : ""
        }}
      </p>
    </div>
    <p
      v-if="
        !isDelivery &&
        order.payment_status === 'unpaid' &&
        !active &&
        !uncertain &&
        !review &&
        !order.cancel_requested &&
        ['pending', 'preparing', 'ready'].includes(order.status)
      "
      class="offline-option"
    >
      <ShieldCheck :size="16" /><span
        ><strong>也可到摊付款</strong>取餐时直接向商家付款，无需提前支付。</span
      >
    </p>
    <p v-if="error" class="error-message" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.financial-refresh {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px 16px;
  margin-top: 18px;
}
.financial-refresh .btn {
  min-height: 44px;
}
.financial-refresh p {
  flex: 1 1 200px;
  margin: 0;
  font-size: 12px;
  color: #7d725f;
  line-height: 1.7;
}
.student-payment {
  padding: 27px;
  overflow: hidden;
  scroll-margin-top: 100px;
}
.simulation-label {
  padding: 11px 13px;
  border-radius: 10px;
  background: #f6efdb;
  color: #886329;
  font-size: 12px;
  line-height: 1.8;
  margin: 17px 0;
}
.online-label {
  display: inline-block;
  margin-left: 8px;
  color: #668160;
  font-size: 10px;
  background: #e4eddd;
  border-radius: 5px;
  padding: 2px 6px;
  vertical-align: middle;
}
.offline-option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 18px 0 0;
  padding-top: 16px;
  border-top: 1px solid #ece7dc;
  font-size: 11px;
  line-height: 1.8;
  color: #7a735f;
}
.offline-option > svg {
  flex: none;
  margin-top: 3px;
  color: #988665;
}
.offline-option strong {
  display: block;
  font-size: 12px;
  color: #6b6251;
  margin-bottom: 3px;
}
.student-payment header {
  display: flex;
  align-items: center;
  gap: 13px;
  margin-bottom: 22px;
}
.payment-symbol {
  width: 45px;
  height: 45px;
  border-radius: 14px;
  background: #e9f1e8;
  color: #37614b;
  display: grid;
  place-items: center;
  flex: none;
}
.student-payment header > div {
  flex: 1;
  min-width: 0;
}
.student-payment .eyebrow {
  font-size: 9px;
  letter-spacing: 1.5px;
  color: #667e69;
}
.student-payment h2 {
  font-size: 17px;
  margin: 6px 0 0;
}
.student-payment header > strong {
  font-size: 23px;
  color: #37614b;
  white-space: nowrap;
}
.payment-choice {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 19px;
  background: #f4f7f0;
  border: 1px solid #e0e7d8;
  border-radius: 14px;
}
.payment-choice > div {
  flex: 1;
}
.payment-choice strong,
.payment-message strong,
.h5-payment > strong,
.qr-payment > strong {
  font-size: 14px;
}
.payment-choice p,
.payment-message p,
.h5-payment p {
  font-size: 12px;
  color: #667365;
  line-height: 1.8;
  margin: 7px 0 0;
}
.pay-button {
  background: #2e6c49;
  color: #fff;
  flex: none;
  min-height: 44px;
}
.pay-button:hover:not(:disabled) {
  background: #235739;
}
.pay-button:disabled {
  opacity: 1;
  background: #e1e5dd;
  color: #737d6b;
  cursor: not-allowed;
}
.payment-footnote {
  font-size: 11px;
  line-height: 1.8;
  color: #6f796a;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 14px 0 0;
}
.payment-footnote svg {
  flex: none;
  margin-top: 3px;
}
.payment-message {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: #f0f6ed;
  padding: 18px;
  border-radius: 12px;
  color: #37614b;
}
.payment-message svg {
  flex: none;
}
.attention {
  background: #fff5e4;
  color: #916326;
}
.payment-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
}
.payment-actions button {
  min-height: 44px;
  font-size: 12px;
}
.qr-payment {
  display: flex;
  align-items: center;
  flex-direction: column;
  gap: 10px;
}
.qr-payment img {
  max-width: 100%;
  height: auto;
  border: 1px solid #dfe6da;
  border-radius: 12px;
}
.qr-payment small {
  color: #697763;
  font-size: 12px;
}
.h5-payment {
  border: 1px solid #e0e7d8;
  border-radius: 14px;
  padding: 20px;
  text-align: center;
}
.h5-payment .btn {
  margin-top: 16px;
}
.student-payment .error-message {
  margin-bottom: 0;
}
.student-payment a:focus-visible,
.student-payment button:focus-visible {
  outline: 3px solid #85ab84;
  outline-offset: 3px;
}
@media (max-width: 600px) {
  .student-payment {
    padding: 23px 19px;
  }
  .student-payment header {
    gap: 10px;
  }
  .student-payment h2 {
    font-size: 15px;
  }
  .student-payment header > strong {
    font-size: 21px;
  }
  .payment-choice {
    flex-direction: column;
    align-items: stretch;
    padding: 16px;
  }
  .payment-actions {
    flex-direction: column;
  }
  .payment-actions .btn {
    width: 100%;
  }
}
</style>
