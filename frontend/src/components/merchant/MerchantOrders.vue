<script setup lang="ts">
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from "vue";
import {
  ArrowUpRight,
  Bike,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  MapPin,
  PackageCheck,
  Phone,
  RefreshCw,
  Search,
  ShoppingBag,
  Wallet,
  X,
} from "lucide-vue-next";
import {
  acceptReady,
  activeOrderStatuses,
  cancellableDeliveryStatuses,
  fulfillmentLabel,
  isDelivery,
  verifiedOnlinePayment,
} from "./delivery";
import { api, ApiError, formatTime, money, statusText } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";
import { newRequestKey } from "../../lib/engagement";
import PortionSummary from "../PortionSummary.vue";
import MerchantPickupLookup from "./MerchantPickupLookup.vue";
import {
  followUpPriority,
  followUpReasons,
  needsFinancialFollowUp,
  needsMerchantFollowUp,
  paymentNeedsFollowUp,
} from "../../lib/orderFollowUp";
import MerchantPaymentInfo, {
  canConfirmOfflinePayment,
  merchantPaymentAmountLabel,
  merchantPaymentLabel,
} from "./MerchantPaymentInfo.vue";

const props = withDefaults(
  defineProps<{
    stall: any;
    orders: any[];
    ordersReady: boolean;
    syncError?: string;
    loading?: boolean;
    initialFilter?: string;
  }>(),
  { loading: false, initialFilter: "active", syncError: "" },
);
const emit = defineEmits<{ refresh: [updatedOrder?: any] }>();
const session = useSession();
type PrepRequest = {
  action: "accept" | "update_prep";
  prep_minutes: number;
  reason?: string;
  idempotency_key: string;
};
const prepMinutes = reactive<Record<string, number>>({});
const pendingPrep = reactive<Record<string, PrepRequest>>({});
const revisedMinutes = ref(10);
const prepStorageKey = () =>
  `merchant-prep:${session.user?.id}:${props.stall.id}`;
function savePrepRequests() {
  try {
    sessionStorage.setItem(prepStorageKey(), JSON.stringify(pendingPrep));
  } catch {
    /* The current page still retains the original request. */
  }
}
function loadPrepRequests() {
  for (const key of Object.keys(pendingPrep)) delete pendingPrep[key];
  try {
    const saved = JSON.parse(sessionStorage.getItem(prepStorageKey()) || "{}");
    for (const [id, raw] of Object.entries(saved)) {
      const value = raw as PrepRequest;
      if (
        ["accept", "update_prep"].includes(value.action) &&
        Number.isInteger(value.prep_minutes) &&
        value.prep_minutes >= 1 &&
        value.prep_minutes <= 180 &&
        typeof value.idempotency_key === "string" &&
        value.idempotency_key.length >= 8 &&
        value.idempotency_key.length <= 128 &&
        (value.reason === undefined ||
          (typeof value.reason === "string" && value.reason.length <= 200))
      )
        pendingPrep[id] = value;
    }
  } catch {
    /* Ignore malformed local drafts. */
  }
}
function prepValue(order: any) {
  return prepMinutes[order.id] ?? props.stall.prep_minutes ?? 10;
}
function preparationText(order: any) {
  if (!order.estimated_ready_at) return "接单后会显示商家预计出餐时间";
  const past =
    Date.parse(order.estimated_ready_at) < now.value &&
    order.status === "preparing";
  return `${past ? "已超过预计时间，请更新实际安排；原预计" : "预计出餐"} ${formatTime(order.estimated_ready_at)}`;
}
const filter = ref(
  props.initialFilter === "cancellation" ? "active" : props.initialFilter,
);
const boardMode = ref(
  ["active", "pending", "preparing", "ready"].includes(props.initialFilter)
    ? "kitchen"
    : "all",
);
const lookupOrder = ref<any>(null);
const toolsOpen = ref(false);
function setBoard(value: string) {
  boardMode.value = value;
  filter.value = value === "kitchen" ? "active" : "all";
  cancellationOnly.value = false;
  query.value = "";
}
const query = ref("");
const fulfillment = ref("all");
const cancellationOnly = ref(props.initialFilter === "cancellation");
const busy = ref("");
const now = ref(Date.now());
const updatedOrders = reactive<Record<string, any>>({});
const pickupCodes = reactive<Record<string, string>>({});
const drawer = ref<HTMLDialogElement>();
const confirmation = ref<HTMLDialogElement>();
const selectedId = ref<string | null>(null);
const confirmationState = ref<{ id: string; action: string } | null>(null);
const reason = ref("");
const simulationRefundOutcome = ref("success");
const reasonBytes = computed(
  () => new TextEncoder().encode(reason.value.trim()).length,
);
const actionError = ref("");
const expiryRefreshes = new Set<string>();
let ticker: ReturnType<typeof setInterval> | undefined;
let actionGeneration = 0;
let actionController: AbortController | undefined;

const allOrders = computed(() => {
  const values = props.orders.map(
    (order) => updatedOrders[String(order.id)] || order,
  );
  if (
    lookupOrder.value &&
    !values.some((order) => order.id === lookupOrder.value.id)
  )
    values.push(
      updatedOrders[String(lookupOrder.value.id)] || lookupOrder.value,
    );
  return values;
});
const kitchenStages = [
  "pending",
  "preparing",
  "ready",
  "pending_payment",
  "delivering",
  "arrived",
];
const stageNames: Record<string, string> = {
  pending: "先接新单",
  preparing: "正在制作",
  ready: "已经出餐",
  pending_payment: "待付款占位",
  delivering: "配送途中",
  arrived: "交接点待收餐",
};
function stageTitle(order: any) {
  return stageNames[order.status] || statusText(order.status);
}
function stageCount(status: string) {
  return shownOrders.value.filter((order) => order.status === status).length;
}
function orderPriorityTime(order: any) {
  const raw =
    order.status === "pending"
      ? order.expires_at
      : order.status === "preparing"
        ? order.estimated_ready_at || order.accepted_at || order.created_at
        : order.ready_at || order.created_at;
  const time = Date.parse(raw || "");
  return Number.isFinite(time) ? time : Number.MAX_SAFE_INTEGER;
}
const selectedOrder = computed(() =>
  allOrders.value.find((order) => String(order.id) === selectedId.value),
);
const confirmationOrder = computed(() =>
  allOrders.value.find(
    (order) => String(order.id) === confirmationState.value?.id,
  ),
);
const filters = [
  { value: "active", label: "进行中" },
  { value: "followup", label: "售后跟进" },
  { value: "pending_payment", label: "待付款" },
  { value: "pending", label: "待接单" },
  { value: "preparing", label: "制作中" },
  { value: "ready", label: "已出餐" },
  { value: "delivering", label: "配送中" },
  { value: "arrived", label: "待收餐" },
  { value: "completed", label: "已完成" },
  { value: "cancelled", label: "已取消" },
  { value: "all", label: "全部" },
];
const cancelCount = computed(
  () =>
    allOrders.value.filter(
      (order) =>
        order.cancel_requested &&
        cancellableDeliveryStatuses.includes(order.status),
    ).length,
);
function matchesFilter(order: any, value: string) {
  if (value === "all") return true;
  if (value === "followup") return needsMerchantFollowUp(order);
  if (value === "active") return activeOrderStatuses.includes(order.status);
  if (value === "cancelled")
    return ["cancelled", "rejected"].includes(order.status);
  return order.status === value;
}
const shownOrders = computed(() =>
  allOrders.value
    .filter(
      (order) =>
        matchesFilter(order, filter.value) &&
        (fulfillment.value === "all" ||
          (order.fulfillment_type || "pickup") === fulfillment.value) &&
        (!cancellationOnly.value || order.cancel_requested) &&
        (!query.value.trim() ||
          String(order.number)
            .toLowerCase()
            .includes(query.value.trim().replace(/^#/, "").toLowerCase())),
    )
    .sort((a, b) => {
      if (filter.value === "followup")
        return (
          followUpPriority(a) - followUpPriority(b) ||
          Date.parse(a.refund?.created_at || a.created_at) -
            Date.parse(b.refund?.created_at || b.created_at)
        );
      if (boardMode.value === "kitchen") {
        const rank = (order: any) =>
          kitchenStages.includes(order.status)
            ? kitchenStages.indexOf(order.status)
            : 99;
        if (rank(a) !== rank(b)) return rank(a) - rank(b);
        if (a.cancel_requested !== b.cancel_requested)
          return a.cancel_requested ? -1 : 1;
        return (
          orderPriorityTime(a) - orderPriorityTime(b) ||
          Date.parse(a.created_at) - Date.parse(b.created_at)
        );
      }
      if (a.cancel_requested !== b.cancel_requested)
        return a.cancel_requested ? -1 : 1;
      const aActive = activeOrderStatuses.includes(a.status);
      const bActive = activeOrderStatuses.includes(b.status);
      if (aActive !== bActive) return aActive ? -1 : 1;
      if (aActive && bActive)
        return Date.parse(a.created_at) - Date.parse(b.created_at);
      return Date.parse(b.created_at) - Date.parse(a.created_at);
    }),
);
function remainingSeconds(order: any) {
  const expiry = Date.parse(order.expires_at);
  return Number.isFinite(expiry)
    ? Math.max(0, Math.ceil((expiry - now.value) / 1000))
    : null;
}
function countdown(order: any) {
  const seconds = remainingSeconds(order);
  if (seconds === null) return "接单时限待同步";
  if (seconds === 0) return "接单已超时，等待状态同步";
  return `剩余 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} 接单`;
}
function totalQuantity(order: any) {
  return order.items.reduce((sum: number, item: any) => sum + item.quantity, 0);
}
function actions(order: any) {
  if (order.refund && order.refund.status !== "success")
    return [{ value: "refund_status", label: "查询退款进度", primary: false }];
  if (
    order.cancel_requested &&
    cancellableDeliveryStatuses.includes(order.status)
  )
    return [
      {
        value: "deny_cancel",
        label: isDelivery(order)
          ? "继续履行订单"
          : order.status === "ready"
            ? "继续等待取餐"
            : "继续制作",
        primary: false,
      },
      !isDelivery(order) && canAct(order, "refund")
        ? { value: "refund", label: "全额退款并取消", primary: false }
        : { value: "approve_cancel", label: "同意取消", primary: true },
    ];
  if (order.status === "pending")
    return [
      { value: "reject", label: "暂时无法接单", primary: false },
      { value: "accept", label: "确认接单", primary: true },
    ];
  if (order.status === "preparing")
    return [
      {
        value: "ready",
        label: isDelivery(order) ? "做好了，准备送餐" : "做好了，通知取餐",
        primary: true,
      },
    ];
  if (isDelivery(order) && order.status === "ready")
    return [
      {
        value: "dispatch",
        label: order.mode === "simulation" ? "模拟出发送餐" : "出发送餐",
        primary: true,
      },
      ...(canAct(order, "refund")
        ? [{ value: "refund", label: "全额原路退款", primary: false }]
        : []),
    ];
  if (isDelivery(order) && order.status === "delivering")
    return [
      {
        value: "arrive",
        label: order.mode === "simulation" ? "模拟到达交接点" : "已到交接点",
        primary: true,
      },
      ...(canAct(order, "refund")
        ? [{ value: "refund", label: "全额原路退款", primary: false }]
        : []),
    ];
  if (order.status === "ready" && canConfirmOfflinePayment(order))
    return [
      {
        value: "confirm_payment",
        label: `确认已收到 ¥${money(order.total_cents)}`,
        primary: true,
      },
    ];
  if (canAct(order, "refund"))
    return [{ value: "refund", label: "全额原路退款", primary: false }];
  return [];
}
function followupActions(order: any) {
  if (filter.value !== "followup") return [];
  const items: { value: string; label: string; primary: boolean }[] = [];
  if (canAct(order, "refund_status"))
    items.push({
      value: "refund_status",
      label: "查询退款进度",
      primary: true,
    });
  if (canAct(order, "resolve_delivery_issue"))
    items.push({
      value: "resolve_delivery_issue",
      label: "异常已解决，继续处理",
      primary: true,
    });
  if (
    paymentNeedsFollowUp(order) ||
    (needsFinancialFollowUp(order) && !items.length)
  )
    items.push({
      value: "refresh_status",
      label: "刷新订单状态",
      primary: false,
    });
  return items;
}
function mainActions(order: any) {
  if (pendingPrep[order.id])
    return [
      {
        value: pendingPrep[order.id]!.action,
        label: "确认原操作结果",
        primary: true,
      },
    ];
  const followup = followupActions(order);
  if (followup.length) return followup;
  return actions(order).filter(
    (item) => item.primary || order.cancel_requested,
  );
}
function moreActions(order: any) {
  if (pendingPrep[order.id]) return [];
  const items = actions(order).filter(
    (item) => !item.primary && !order.cancel_requested,
  );
  for (const [value, label] of [
    ["report_delivery_issue", "配送遇到问题"],
    ["resolve_delivery_issue", "异常已解决，继续处理"],
  ]) {
    if (value && label && canAct(order, value))
      items.push({ value, label, primary: false });
  }
  if (
    order.mode === "simulation" &&
    order.refund &&
    order.refund.status !== "success"
  ) {
    items.push({
      value: "simulate_refund_success",
      label: "模拟退款成功",
      primary: false,
    });
    items.push({
      value: "simulate_refund_failure",
      label: "模拟退款失败",
      primary: false,
    });
  }
  return items.filter(
    (item) => !followupActions(order).some((main) => main.value === item.value),
  );
}
function canAct(order: any, value: string) {
  if (!order) return false;
  if (pendingPrep[order.id]) return value === pendingPrep[order.id]!.action;
  if (value === "update_prep")
    return (
      order.status === "preparing" &&
      !order.cancel_requested &&
      !order.refund &&
      !order.payment_review_required &&
      order.payment?.status !== "review" &&
      (!isDelivery(order) || verifiedOnlinePayment(order))
    );
  if (value === "refresh_status")
    return needsMerchantFollowUp(order) && !props.loading;
  if (value.startsWith("simulate_refund_"))
    return (
      order.mode === "simulation" &&
      !!order.refund &&
      order.refund.status !== "success" &&
      !order.payment_review_required &&
      order.payment?.status !== "review"
    );
  if (["accept", "reject"].includes(value))
    return acceptReady(order) && remainingSeconds(order) !== 0;
  if (["approve_cancel", "deny_cancel"].includes(value))
    return (
      order.cancel_requested &&
      cancellableDeliveryStatuses.includes(order.status) &&
      !order.payment_review_required &&
      (value === "deny_cancel" ||
        canConfirmOfflinePayment(order) ||
        (isDelivery(order) && verifiedOnlinePayment(order)))
    );
  if (value === "ready")
    return (
      order.status === "preparing" &&
      !order.cancel_requested &&
      (!isDelivery(order) || verifiedOnlinePayment(order))
    );
  if (
    [
      "dispatch",
      "arrive",
      "report_delivery_issue",
      "resolve_delivery_issue",
    ].includes(value)
  )
    return (
      isDelivery(order) &&
      verifiedOnlinePayment(order) &&
      !order.cancel_requested &&
      !order.refund &&
      (!order.delivery_issue ||
        ["report_delivery_issue", "resolve_delivery_issue"].includes(value)) &&
      (value === "dispatch"
        ? order.status === "ready"
        : value === "arrive"
          ? order.status === "delivering"
          : cancellableDeliveryStatuses.includes(order.status) &&
            (value !== "resolve_delivery_issue" || !!order.delivery_issue))
    );
  if (value === "confirm_payment")
    return (
      order.status === "ready" &&
      canConfirmOfflinePayment(order) &&
      !order.cancel_requested
    );
  if (value === "refund")
    return (
      (isDelivery(order)
        ? [
            "pending",
            "preparing",
            "ready",
            "delivering",
            "arrived",
            "completed",
          ]
        : ["ready", "completed"]
      ).includes(order.status) &&
      order.payment_method === "wechat" &&
      order.payment_status === "paid" &&
      order.payment?.status === "paid" &&
      !order.payment_review_required &&
      order.payment?.status !== "review" &&
      !order.refund
    );
  if (value === "refund_status")
    return (
      !!order.refund &&
      order.refund.status !== "success" &&
      !order.payment_review_required &&
      order.payment?.status !== "review"
    );
  if (value === "complete")
    return (
      order.status === (isDelivery(order) ? "arrived" : "ready") &&
      order.payment_status === "paid" &&
      (order.payment_method !== "wechat" || order.payment?.status === "paid") &&
      !order.payment_review_required &&
      order.payment?.status !== "review" &&
      (!isDelivery(order) || !order.delivery_issue) &&
      !order.cancel_requested
    );
  return false;
}
function actionIcon(value: string) {
  return ["refund_status", "refresh_status"].includes(value)
    ? RefreshCw
    : value === "dispatch"
      ? Bike
      : value === "arrive"
        ? MapPin
        : value === "ready"
          ? PackageCheck
          : value === "confirm_payment"
            ? Wallet
            : value === "accept"
              ? Check
              : null;
}
const confirmationCopy = computed(() => {
  const copies: Record<
    string,
    { title: string; body: string; submit: string }
  > = {
    update_prep: {
      title: "更新预计出餐时间",
      body: "从这次确认起，还需要多久做好？填写实际原因，同学会看到新的预估和说明。只更新预估，不会自动出餐或完成订单。",
      submit: "更新并告知顾客",
    },
    confirm_payment: {
      title: "确认这笔线下收款",
      body: `请核对已收到顾客支付的 ¥${money(confirmationOrder.value?.total_cents)}。确认后会记录为已收款，仍需核验顾客的取餐码才能完成订单。`,
      submit: "确认收款",
    },
    reject: {
      title: "暂时无法接下这一单？",
      body: isDelivery(confirmationOrder.value)
        ? "拒单后会停止履约，发起含配送费的全额原路退款。退款以微信确认结果为准，请说明原因，让顾客及时作其他安排。"
        : "拒单后会通知顾客并释放预留库存。请说明原因，让顾客及时作其他安排。",
      submit: "确认拒单",
    },
    approve_cancel: {
      title: "同意顾客取消订单",
      body: isDelivery(confirmationOrder.value)
        ? "同意后停止履约并发起含配送费的全额原路退款。已制作餐点不会自动恢复库存，退款是否完成以微信结果为准。"
        : "同意后订单将取消，预留库存会恢复。请确认当前餐点还适合恢复售卖，必要时在商品管理中调整库存。",
      submit: "确认取消订单",
    },
    deny_cancel: {
      title: "继续处理这份订单",
      body: "取消申请将被拒绝，订单继续保留。请向顾客说明处理原因，有需要时电话沟通。",
      submit: "确认继续处理",
    },
    refund: {
      title: "确认全额原路退款",
      body: `将向顾客原微信付款账户退还 ¥${money(confirmationOrder.value?.total_cents)}。退款结果由微信确认，请勿另行线下退付。已取餐订单保留履约记录，不回补库存；未取餐订单也不自动恢复食品库存，请按实际情况处理。`,
      submit: `确认退款 ¥${money(confirmationOrder.value?.total_cents)}`,
    },
    dispatch: {
      title: "确认开始配送",
      body: "请确认餐点已封装、配送人员已实际出发，并核对本单交接点。出发后会通知顾客正在配送，请按约定地点交餐。",
      submit: "确认已出发",
    },
    arrive: {
      title: "确认已到达交接点",
      body: "请确认餐点已到达本单指定交接点，并联系顾客收餐。到达只更新进度，仍需顾客确认收餐或当面核验收餐码。",
      submit: "确认已到达",
    },
    report_delivery_issue: {
      title: "记录配送异常",
      body: "请填写延误、无法联系或餐点问题的实际情况。说明会同步给顾客，订单不会因此自动完成或退款，请及时联系顾客协商。",
      submit: "保存并通知顾客",
    },
    resolve_delivery_issue: {
      title: "确认配送异常已解决",
      body: "请先与顾客确认处理结果，再记录解决说明。解除异常后才可继续配送或完成交付；此操作不会自动完成订单。",
      submit: "记录解决，继续履约",
    },
  };
  if (confirmationOrder.value?.mode === "simulation") {
    for (const copy of Object.values(copies))
      copy.body =
        "模拟操作：不会真实扣款、退款或安排配送。" +
        copy.body.replaceAll("微信", "模拟系统");
    copies.refund!.title = "确认模拟全额退款";
    copies.refund!.body = `将练习退款 ¥${money(confirmationOrder.value?.total_cents)}，不涉及真实资金。默认模拟成功，也可展开异常测试。已完成订单保留交付记录，已制作餐点不会自动回补库存。`;
    copies.dispatch!.body =
      "这是送餐流程练习。确认后学生会看到模拟配送中，不会安排真实配送人员。";
    copies.arrive!.body =
      "这是送餐流程练习。确认到达示例交接点后，仍需学生确认收餐或出示收餐码完成模拟订单。";
    copies.refund!.submit = `模拟退款 ¥${money(confirmationOrder.value?.total_cents)}`;
  }
  return (
    copies[confirmationState.value?.action || ""] || {
      title: "确认操作",
      body: "",
      submit: "确认",
    }
  );
});
async function openDetails(order: any) {
  selectedId.value = String(order.id);
  await nextTick();
  if (!drawer.value?.open) drawer.value?.showModal();
}
async function pickupFound(order: any, code: string) {
  lookupOrder.value = order;
  updatedOrders[String(order.id)] = order;
  pickupCodes[order.id] = code;
  await openDetails(order);
}
function closeDetails() {
  drawer.value?.close();
  selectedId.value = null;
}
function closeConfirmation() {
  if (busy.value) return;
  confirmation.value?.close();
  confirmationState.value = null;
  actionError.value = "";
}
function backdropClose(event: MouseEvent, kind: "drawer" | "confirmation") {
  if (event.target === event.currentTarget)
    kind === "drawer" ? closeDetails() : closeConfirmation();
}
async function requestAction(order: any, value: string) {
  if (busy.value || !canAct(order, value)) return;
  if (pendingPrep[order.id]) {
    await performAction(order, value);
    return;
  }
  if (value === "refresh_status") {
    emit("refresh");
    return;
  }
  if (
    [
      "reject",
      "approve_cancel",
      "deny_cancel",
      "confirm_payment",
      "refund",
      "dispatch",
      "arrive",
      "report_delivery_issue",
      "resolve_delivery_issue",
      "update_prep",
    ].includes(value)
  ) {
    simulationRefundOutcome.value = "success";
    revisedMinutes.value = Math.min(
      180,
      Math.max(
        1,
        Math.ceil(
          (Date.parse(order.estimated_ready_at || "") - Date.now()) / 60000,
        ) ||
          props.stall.prep_minutes ||
          10,
      ),
    );
    confirmationState.value = { id: String(order.id), action: value };
    reason.value =
      value === "reject"
        ? "当前备餐繁忙，暂时无法接单"
        : value === "approve_cancel"
          ? "商家同意取消"
          : "";
    actionError.value = "";
    await nextTick();
    confirmation.value?.showModal();
    return;
  }
  await performAction(order, value);
}
async function confirmAction() {
  if (!confirmationOrder.value || !confirmationState.value) return;
  await performAction(
    confirmationOrder.value,
    confirmationState.value.action,
    reason.value.trim(),
  );
}
async function performAction(order: any, value: string, explanation = "") {
  if (busy.value || !canAct(order, value)) return;
  if (value === "refund" && !explanation.trim()) {
    actionError.value = "请填写退款原因，便于顾客了解和后续对账。";
    return;
  }
  if (value === "refund" && new TextEncoder().encode(explanation).length > 80) {
    actionError.value = "退款原因不能超过 80 字节，中文建议控制在 26 字以内。";
    return;
  }
  const generation = actionGeneration;
  const isPrep = value === "accept" || value === "update_prep";
  const existingPrep = pendingPrep[order.id];
  let body: any = existingPrep || { action: value };
  if (isPrep && !existingPrep) {
    const minutes =
      value === "accept"
        ? Number(prepValue(order))
        : Number(revisedMinutes.value);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 180) {
      actionError.value = "预计备餐请填写 1 至 180 分钟的整数。";
      notify(actionError.value, "error");
      return;
    }
    if (
      value === "update_prep" &&
      (!explanation.trim() || explanation.trim().length > 200)
    ) {
      actionError.value = "请填写 1 至 200 字的调整原因。";
      return;
    }
    body = {
      action: value,
      prep_minutes: minutes,
      idempotency_key: newRequestKey("prep"),
    };
    if (value === "update_prep") body.reason = explanation.trim();
    pendingPrep[order.id] = body;
    savePrepRequests();
  }
  if (
    [
      "reject",
      "approve_cancel",
      "deny_cancel",
      "report_delivery_issue",
      "resolve_delivery_issue",
    ].includes(value)
  )
    body.reason = explanation;
  if (value === "complete") {
    const code = (pickupCodes[order.id] || "").trim();
    if (!/^\d{8}$/.test(code)) {
      notify(
        `请输入顾客出示的 8 位${isDelivery(order) ? "收餐码" : "取餐码"}`,
        "error",
      );
      return;
    }
    body.pickup_code = code;
  }
  busy.value = String(order.id);
  actionError.value = "";
  const controller = new AbortController();
  actionController = controller;
  const timeout = window.setTimeout(() => controller.abort(), 20000);
  try {
    const simulateRefund = value.startsWith("simulate_refund_");
    const isRefund =
      ["refund", "refund_status"].includes(value) || simulateRefund;
    const result = await api<any>(
      `/merchant/orders/${order.id}/${simulateRefund ? "refunds/simulate" : isRefund ? "refund" : "action"}`,
      {
        method: "POST",
        signal: controller.signal,
        body: simulateRefund
          ? {
              refund_id: order.refund.id,
              outcome: value.slice("simulate_refund_".length),
            }
          : isRefund
            ? {
                ...(value === "refund" && order.mode === "simulation"
                  ? { simulation_outcome: simulationRefundOutcome.value }
                  : {}),
                reason:
                  value === "refund_status" ? order.refund.reason : explanation,
              }
            : body,
      },
    );
    if (generation !== actionGeneration) return;
    if (
      !result ||
      result.id !== order.id ||
      result.stall_id !== props.stall.id ||
      !Array.isArray(result.items) ||
      !Number.isSafeInteger(result.total_cents) ||
      result.total_cents < 0 ||
      ![
        "pending_payment",
        "pending",
        "preparing",
        "ready",
        "delivering",
        "arrived",
        "completed",
        "cancelled",
        "rejected",
      ].includes(result.status)
    ) {
      throw new ApiError(
        "未能确认服务器返回的订单，请保留原操作并重新确认结果。",
        502,
        { code: "invalid_response", submitted: true },
      );
    }
    if (isPrep) {
      delete pendingPrep[order.id];
      savePrepRequests();
    }
    updatedOrders[String(result.id)] = result;
    if (lookupOrder.value?.id === result.id) lookupOrder.value = result;
    emit("refresh", result);
    if (value === "complete") delete pickupCodes[order.id];
    busy.value = "";
    closeConfirmation();
    if (isRefund) {
      if (
        result.payment_status === "refunded" &&
        result.refund?.status === "success"
      )
        notify(
          result.mode === "simulation"
            ? "模拟退款已完成，没有真实资金变动"
            : "微信已确认全额原路退款",
          "success",
        );
      else if (["closed", "abnormal"].includes(result.refund?.status))
        notify(
          result.refund?.error_message ||
            "退款未完成，请查看退款说明并联系支付服务方处理。",
          "error",
        );
      else
        notify(
          result.mode === "simulation"
            ? "模拟退款尚未完成，可在更多操作中继续测试结果。"
            : "退款尚未完成，请等待微信确认后再次查询。",
        );
      return;
    }
    const messages: Record<string, string> = {
      accept: "已接单，请开始制作",
      update_prep: "已更新预计出餐时间和说明",
      reject: "已拒绝接单",
      ready: isDelivery(order) ? "已出餐，请安排配送人员" : "已通知顾客取餐",
      confirm_payment: "已记录线下收款，请继续核验取餐码",
      complete: `${isDelivery(order) ? "收餐码" : "取餐码"}核验成功，订单已完成`,
      dispatch: "已记录出发，请按本单交接点配送",
      arrive: "已通知顾客到达交接点，等待收餐确认",
      report_delivery_issue: "配送异常已记录，请及时联系顾客",
      resolve_delivery_issue: "异常已标记解决，请继续按真实进度履约",
      approve_cancel: "已同意取消订单",
      deny_cancel: "已拒绝取消申请，订单继续处理",
    };
    if (result.payment_status === "refunding") {
      notify(
        result.mode === "simulation"
          ? "已停止模拟履约并申请模拟退款。"
          : "已停止履约并申请退款，等待微信确认退款结果。",
        "info",
      );
      return;
    }
    notify(
      result.status === "cancelled" && ["accept", "reject"].includes(value)
        ? "订单已超时取消，请查看最新状态"
        : (result.mode === "simulation" ? "模拟操作：" : "") +
            (messages[value] || "订单已更新"),
      "success",
    );
  } catch (error) {
    if (generation !== actionGeneration) return;
    if (
      isPrep &&
      error instanceof ApiError &&
      [400, 404, 409, 422].includes(error.status) &&
      error.data?.submitted !== false &&
      [
        "invalid_prep_minutes",
        "invalid_prep_update",
        "invalid_transition",
        "payment_in_progress",
      ].includes(error.code || "")
    ) {
      delete pendingPrep[order.id];
      savePrepRequests();
    }
    actionError.value = controller.signal.aborted
      ? "请求结果尚未确认，请刷新订单核对状态。退款请查询原退款进度，不要另行退付。"
      : (error as Error).message;
    notify(actionError.value, "error");
    emit("refresh");
  } finally {
    window.clearTimeout(timeout);
    if (generation === actionGeneration) {
      busy.value = "";
      actionController = undefined;
    }
  }
}
function timeline(order: any) {
  if (isDelivery(order))
    return [
      { label: "微信付款", date: order.paid_at, done: Boolean(order.paid_at) },
      {
        label: "接单制作",
        date: order.accepted_at,
        done: Boolean(order.accepted_at),
      },
      {
        label: "配送出发",
        date: order.dispatched_at,
        done: Boolean(order.dispatched_at),
      },
      {
        label: "到达交接点",
        date: order.arrived_at,
        done: Boolean(order.arrived_at),
      },
      {
        label: "顾客收餐",
        date: order.completed_at,
        done: order.status === "completed",
      },
    ];
  return [
    { label: "顾客下单", date: order.created_at, done: true },
    {
      label: "接单制作",
      date: order.accepted_at,
      done: Boolean(order.accepted_at),
    },
    { label: "等待取餐", date: order.ready_at, done: Boolean(order.ready_at) },
    {
      label: "取餐完成",
      date: order.completed_at,
      done: order.status === "completed",
    },
  ];
}
watch(
  () => props.initialFilter,
  (value) => {
    if (!["active", "pending", "preparing", "ready"].includes(value))
      boardMode.value = "all";
    cancellationOnly.value = value === "cancellation";
    filter.value = filters.some((item) => item.value === value)
      ? value
      : "active";
  },
);
watch(
  () => props.orders,
  () => {
    // Fresh parent data remains authoritative when another device advances an order.
    for (const key of Object.keys(updatedOrders)) delete updatedOrders[key];
  },
);
watch(
  () => `${session.user?.id}:${props.stall?.id}`,
  () => {
    actionGeneration++;
    actionController?.abort();
    busy.value = "";
    closeConfirmation();
    closeDetails();
    query.value = "";
    lookupOrder.value = null;
    fulfillment.value = "all";
    for (const key of Object.keys(updatedOrders)) delete updatedOrders[key];
    for (const key of Object.keys(pickupCodes)) delete pickupCodes[key];
    expiryRefreshes.clear();
    for (const key of Object.keys(prepMinutes)) delete prepMinutes[key];
    loadPrepRequests();
  },
  { immediate: true, flush: "sync" },
);
onMounted(() => {
  ticker = setInterval(() => {
    now.value = Date.now();
    let expired = false;
    for (const order of allOrders.value) {
      if (
        order.status === "pending" &&
        remainingSeconds(order) === 0 &&
        !expiryRefreshes.has(String(order.id))
      ) {
        expiryRefreshes.add(String(order.id));
        expired = true;
      }
    }
    if (expired && !document.hidden) emit("refresh");
  }, 1000);
});
onUnmounted(() => {
  actionGeneration++;
  actionController?.abort();
  clearInterval(ticker);
  drawer.value?.close();
  confirmation.value?.close();
});
</script>

<template>
  <section
    class="m-orders"
    :class="{ 'm-kitchen-mode': boardMode === 'kitchen' }"
    aria-labelledby="m-orders-heading"
  >
    <div
      class="m-orders-heading"
      :class="{ 'm-kitchen-heading': boardMode === 'kitchen' }"
    >
      <div>
        <span class="m-orders-eyebrow">EVERY ORDER MATTERS</span>
        <h2 id="m-orders-heading">认真对待，每一份期待<span>.</span></h2>
        <p>自取与校园定点配送 · 按实际交付推进 · 每 10 秒同步订单</p>
      </div>
      <button
        class="m-orders-refresh"
        :disabled="loading || !!busy"
        @click="emit('refresh')"
      >
        <RefreshCw
          :size="16"
          :class="{ 'm-orders-spinning': loading }"
        />刷新订单
      </button>
    </div>
    <div class="m-board-tabs" role="group" aria-label="订单工作方式">
      <button
        type="button"
        :aria-pressed="boardMode === 'kitchen'"
        @click="setBoard('kitchen')"
      >
        出餐台</button
      ><button
        type="button"
        :aria-pressed="boardMode === 'all'"
        @click="setBoard('all')"
      >
        全部订单</button
      ><span>{{
        boardMode === "kitchen"
          ? "先接快到时限的新单，再按预计出餐安排制作"
          : "查看历史、付款、配送与售后记录"
      }}</span>
    </div>
    <button
      v-if="boardMode === 'kitchen'"
      class="m-kitchen-tools-toggle"
      :aria-expanded="toolsOpen"
      aria-controls="merchant-secondary-tools"
      @click="toolsOpen = !toolsOpen"
    >
      <Search :size="16" />{{
        toolsOpen ? "收起查单与筛选" : "查取餐码 · 搜索与筛选"
      }}
    </button>
    <div
      id="merchant-secondary-tools"
      v-show="boardMode === 'all' || toolsOpen"
    >
      <MerchantPickupLookup
        :key="`${session.user?.id}:${stall.id}`"
        :stall-id="stall.id"
        @found="pickupFound"
      />
      <div class="m-orders-toolbar">
        <label class="m-orders-search"
          ><Search :size="18" /><input
            v-model="query"
            type="search"
            aria-label="搜索订单号"
            placeholder="搜索订单号"
        /></label>
        <button
          class="m-orders-cancel-filter"
          :class="{ active: cancellationOnly }"
          :aria-pressed="cancellationOnly"
          @click="
            cancellationOnly = !cancellationOnly;
            if (cancellationOnly) filter = 'active';
          "
        >
          取消申请<span>{{ ordersReady ? cancelCount : "—" }}</span>
        </button>
      </div>
      <div class="m-fulfillment-filters" role="group" aria-label="取餐方式筛选">
        <button
          v-for="item in [
            { value: 'all', label: '全部方式' },
            { value: 'pickup', label: '到摊自取' },
            { value: 'delivery', label: '商家自配送' },
          ]"
          :key="item.value"
          :aria-pressed="fulfillment === item.value"
          :class="{ active: fulfillment === item.value }"
          @click="fulfillment = item.value"
        >
          {{ item.label }}
        </button>
      </div>
      <div class="m-orders-tabs" role="group" aria-label="订单状态筛选">
        <button
          v-for="item in filters"
          :key="item.value"
          :class="{ active: filter === item.value }"
          :aria-pressed="filter === item.value"
          @click="
            filter = item.value;
            if (!['active', ...kitchenStages].includes(item.value))
              boardMode = 'all';
            cancellationOnly = false;
          "
        >
          {{ item.label
          }}<span>{{
            ordersReady
              ? allOrders.filter((order) => matchesFilter(order, item.value))
                  .length
              : "—"
          }}</span>
        </button>
      </div>
    </div>
    <div class="m-orders-list-caption">
      <span>{{
        !ordersReady
          ? "订单尚未同步，当前数量待确认"
          : syncError
            ? "同步中断，当前显示上次同步的订单与数量"
            : cancellationOnly
              ? "优先处理顾客的取消申请"
              : boardMode === "kitchen"
                ? "先接快到时限的新单，再按预计出餐安排制作"
                : filter === "active"
                  ? "进行中订单按下单时间排列，先到先处理"
                  : filter === "followup"
                    ? "退款未完、付款待核对与配送异常集中跟进；一笔订单只计一次"
                    : "订单费用与交付地点均保留下单时记录"
      }}</span
      ><span>{{ ordersReady ? shownOrders.length : "—" }} 笔订单</span>
    </div>
    <div v-if="!ordersReady" class="m-orders-empty" role="status">
      <RefreshCw :size="38" :class="{ 'm-orders-spinning': loading }" />
      <h3>{{ loading ? "正在同步订单" : "订单尚未同步" }}</h3>
      <p>
        {{
          syncError
            ? "订单读取失败，暂时无法确认有没有待跟进事项。"
            : "同步完成后才会显示订单与售后数量。"
        }}
      </p>
      <button
        class="m-orders-button secondary"
        :disabled="loading || !!busy"
        @click="emit('refresh')"
      >
        {{ loading ? "正在同步…" : "重新同步订单" }}
      </button>
    </div>
    <div v-else-if="!shownOrders.length" class="m-orders-empty">
      <ShoppingBag :size="38" />
      <h3>
        {{
          query.trim()
            ? "没有找到这笔订单"
            : cancellationOnly
              ? "暂时没有取消申请"
              : filter === "followup"
                ? "当前筛选下没有待跟进的售后"
                : "这个分类暂时没有订单"
        }}
      </h3>
      <p>
        {{
          query.trim()
            ? "试试订单号的后几位，或切换到全部订单。"
            : filter === "followup"
              ? "退款确认完成、付款问题解决或配送异常解除后，会从这里移出。"
              : "有新订单时会在这里显示，安心准备下一份好味道。"
        }}
      </p>
      <button
        v-if="query || filter !== 'active' || cancellationOnly"
        class="m-orders-button secondary"
        @click="
          query = '';
          filter = 'active';
          cancellationOnly = false;
        "
      >
        查看进行中订单
      </button>
    </div>
    <div v-else class="m-orders-grid">
      <template v-for="(order, index) in shownOrders" :key="order.id">
        <h3
          v-if="
            boardMode === 'kitchen' &&
            (!index || shownOrders[index - 1]?.status !== order.status)
          "
          class="m-kitchen-stage"
          :data-stage="order.status"
        >
          {{ stageTitle(order) }}<span>{{ stageCount(order.status) }} 单</span
          ><small v-if="order.status === 'pending_payment'"
            >尚未付款，暂不制作</small
          >
        </h3>
        <article
          class="merchant-order m-orders-card"
          :class="{
            'm-orders-needs-attention':
              order.cancel_requested || needsMerchantFollowUp(order),
          }"
        >
          <div class="m-orders-card-top">
            <div>
              <span class="order-number">#{{ order.number }}</span>
              <span
                v-if="order.mode === 'simulation'"
                class="m-simulation-badge"
                >模拟订单 · 不扣款、不配送</span
              >
              <p>{{ formatTime(order.created_at) }} 下单</p>
            </div>
            <span class="merchant-order-status" :class="order.status">{{
              statusText(order.status, order.fulfillment_type)
            }}</span>
          </div>
          <div
            v-if="needsMerchantFollowUp(order)"
            class="m-order-followup"
            aria-label="待跟进事项"
          >
            <span v-for="issue in followUpReasons(order)" :key="issue.kind"
              ><Clock3 :size="13" />{{ issue.label }}</span
            >
            <p
              v-if="
                filter === 'followup' &&
                ['cancelled', 'rejected', 'completed'].includes(order.status)
              "
            >
              履约已结束，仍需跟进上述问题；查询进度不会恢复接单或配送。
            </p>
          </div>
          <div
            class="m-order-fulfillment"
            :class="{ delivery: isDelivery(order) }"
          >
            <component
              :is="isDelivery(order) ? Bike : ShoppingBag"
              :size="15"
            /><strong>{{ fulfillmentLabel(order) }}</strong
            ><span v-if="order.status === 'pending'"
              >共 {{ totalQuantity(order) }} 份</span
            ><span v-if="isDelivery(order)">{{
              order.delivery_point_name
            }}</span>
          </div>
          <div v-if="isDelivery(order)" class="m-delivery-destination">
            <MapPin :size="16" />
            <div>
              <strong>{{ order.delivery_point_address }}</strong
              ><small v-if="order.recipient_name"
                >收餐人：{{ order.recipient_name }}</small
              ><small
                v-if="order.delivery_eta_min_at && order.delivery_eta_max_at"
                >预计送达 {{ formatTime(order.delivery_eta_min_at) }} —
                {{ formatTime(order.delivery_eta_max_at) }}</small
              >
            </div>
          </div>
          <p
            v-if="order.status === 'pending'"
            class="m-orders-countdown"
            :class="{ urgent: (remainingSeconds(order) ?? 999) < 60 }"
          >
            <Clock3 :size="14" />{{ countdown(order) }}
          </p>
          <div
            v-if="order.status === 'pending' && mainActions(order).length"
            class="m-orders-actions m-pending-actions"
          >
            <button
              v-for="item in mainActions(order)"
              :key="item.value"
              class="m-orders-button"
              :class="item.primary ? 'primary' : 'secondary'"
              :disabled="!!busy || !canAct(order, item.value)"
              @click="requestAction(order, item.value)"
            >
              <component
                :is="actionIcon(item.value)"
                v-if="actionIcon(item.value)"
                :size="17"
              />{{ busy === String(order.id) ? "正在处理…" : item.label }}
            </button>
          </div>
          <div class="m-orders-card-items">
            <div
              v-for="item in order.items"
              :key="item.product_id"
              class="m-orders-item"
            >
              <img :src="item.image" :alt="item.name" loading="lazy" />
              <div>
                <strong>{{ item.name }}</strong
                ><span>¥{{ money(item.unit_price_cents) }} / 份</span>
                <PortionSummary :portions="item.portions" />
              </div>
              <span class="m-orders-item-quantity">× {{ item.quantity }}</span
              ><strong
                >¥{{ money(item.unit_price_cents * item.quantity) }}</strong
              >
            </div>
          </div>
          <p v-if="order.note" class="m-orders-note">
            <strong>顾客备注</strong>{{ order.note }}
          </p>
          <div
            v-if="order.status === 'preparing' || order.estimated_ready_at"
            class="m-prep-status"
            :class="{
              overdue:
                order.status === 'preparing' &&
                Date.parse(order.estimated_ready_at) < now,
            }"
          >
            <p><Clock3 :size="16" />{{ preparationText(order) }}</p>
            <small v-if="order.prep_delay_reason"
              >调整说明：{{ order.prep_delay_reason }}</small
            >
            <button
              v-if="canAct(order, 'update_prep') && !pendingPrep[order.id]"
              class="m-orders-button secondary"
              :disabled="!!busy"
              @click="requestAction(order, 'update_prep')"
            >
              还要等一会，更新预估
            </button>
          </div>
          <details
            v-if="order.status === 'pending' && !pendingPrep[order.id]"
            class="prep-accept"
          >
            <summary>
              预计约 {{ prepValue(order) }} 分钟出餐<span
                >可按当前忙闲调整</span
              >
            </summary>
            <label :for="`prep-${order.id}`"
              >接单后约需（分钟）<input
                :id="`prep-${order.id}`"
                :value="prepValue(order)"
                type="number"
                min="1"
                max="180"
                inputmode="numeric"
                :disabled="!!busy"
                @input="
                  prepMinutes[order.id] = Number(
                    ($event.target as HTMLInputElement).value,
                  )
                "
            /></label>
          </details>
          <p
            v-if="pendingPrep[order.id]"
            class="m-orders-warning"
            role="status"
          >
            原操作结果待确认。已保留当时的
            {{ pendingPrep[order.id]!.prep_minutes }}
            分钟预估，再次确认不会重复顺延。
          </p>
          <p
            v-if="order.location_changed && !isDelivery(order)"
            class="m-orders-warning"
          >
            <MapPin :size="15" />取餐位置已发生变化，请按本单原地址与顾客确认。
          </p>
          <p v-if="order.delivery_issue" class="m-orders-warning">
            <Clock3 :size="15" /><span
              ><strong>配送异常：</strong>{{ order.delivery_issue }}</span
            >
          </p>
          <div class="m-orders-card-summary">
            <span
              >共 {{ totalQuantity(order) }} 件
              <span
                class="paid-tag"
                :class="{
                  paid:
                    order.payment_status === 'paid' &&
                    !needsFinancialFollowUp(order),
                }"
                >{{ merchantPaymentLabel(order) }}</span
              ></span
            ><strong
              ><small>{{ merchantPaymentAmountLabel(order) }}</small
              >¥{{ money(order.total_cents) }}</strong
            >
          </div>
          <MerchantPaymentInfo
            v-if="
              order.payment_method === 'wechat' || order.payment || order.refund
            "
            :order="order"
            compact
          />
          <div
            v-if="
              order.cancel_requested &&
              cancellableDeliveryStatuses.includes(order.status)
            "
            class="m-orders-cancel-notice"
          >
            <strong>顾客申请取消</strong>
            <p>{{ order.cancel_reason || "顾客未填写原因，请及时处理。" }}</p>
          </div>
          <div
            v-if="order.status !== 'pending' && mainActions(order).length"
            class="m-orders-actions"
          >
            <button
              v-for="item in mainActions(order)"
              :key="item.value"
              class="m-orders-button"
              :class="item.primary ? 'primary' : 'secondary'"
              :disabled="!!busy || !canAct(order, item.value)"
              @click="requestAction(order, item.value)"
            >
              <component
                :is="actionIcon(item.value)"
                v-if="actionIcon(item.value)"
                :size="17"
              />{{ busy === String(order.id) ? "正在处理…" : item.label }}
            </button>
          </div>
          <details v-if="moreActions(order).length" class="m-order-more">
            <summary>更多操作<span>取消、退款与配送问题</span></summary>
            <div class="m-order-more-actions">
              <button
                v-for="item in moreActions(order)"
                :key="item.value"
                class="m-orders-button secondary"
                :disabled="!!busy || !canAct(order, item.value)"
                @click="requestAction(order, item.value)"
              >
                {{ item.label }}
              </button>
            </div>
          </details>
          <form
            v-if="canAct(order, 'complete')"
            class="m-orders-pickup"
            @submit.prevent="performAction(order, 'complete')"
          >
            <label :for="`pickup-${order.id}`"
              >核验顾客的 8 位{{
                isDelivery(order) ? "收餐码" : "取餐码"
              }}</label
            >
            <div>
              <input
                :id="`pickup-${order.id}`"
                v-model="pickupCodes[order.id]"
                class="pickup-code-input"
                inputmode="numeric"
                pattern="[0-9]{8}"
                maxlength="8"
                minlength="8"
                :placeholder="isDelivery(order) ? '输入收餐码' : '输入取餐码'"
                required
                autocomplete="off"
                :disabled="!!busy"
              /><button class="m-orders-button primary" :disabled="!!busy">
                <CheckCircle2 :size="17" />核销并完成
              </button>
            </div>
          </form>
          <p
            v-if="
              ['cancelled', 'rejected'].includes(order.status) &&
              order.cancel_reason
            "
            class="m-orders-history-reason"
          >
            {{ order.cancel_reason }}
          </p>
          <button
            class="m-orders-detail-link"
            @click="openDetails(order)"
            :aria-label="`查看订单 ${order.number} 详情`"
          >
            查看订单详情<ArrowUpRight :size="16" />
          </button>
        </article>
      </template>
    </div>

    <Teleport to="body">
      <dialog
        ref="drawer"
        class="m-orders-drawer"
        aria-labelledby="m-orders-detail-heading"
        @click="backdropClose($event, 'drawer')"
        @close="selectedId = null"
      >
        <div v-if="selectedOrder" class="m-orders-drawer-content">
          <header class="m-orders-drawer-header">
            <div>
              <span class="m-orders-eyebrow">ORDER DETAILS</span>
              <h2 id="m-orders-detail-heading">订单详情</h2>
              <span
                v-if="selectedOrder.mode === 'simulation'"
                class="m-simulation-badge"
                >模拟订单 · 不扣款、不配送</span
              >
            </div>
            <button
              class="m-orders-icon-button"
              aria-label="关闭订单详情"
              @click="closeDetails"
            >
              <X :size="22" />
            </button>
          </header>
          <div class="m-orders-drawer-body">
            <div class="m-orders-detail-status">
              <span
                class="merchant-order-status"
                :class="selectedOrder.status"
                >{{
                  statusText(
                    selectedOrder.status,
                    selectedOrder.fulfillment_type,
                  )
                }}</span
              ><strong>#{{ selectedOrder.number }}</strong>
              <p>
                {{ formatTime(selectedOrder.created_at) }} 下单 ·
                {{ fulfillmentLabel(selectedOrder) }}
              </p>
              <p
                v-if="selectedOrder.status === 'pending'"
                class="m-orders-countdown"
              >
                <Clock3 :size="15" />{{ countdown(selectedOrder) }}
              </p>
            </div>
            <div
              v-if="needsMerchantFollowUp(selectedOrder)"
              class="m-order-followup"
              aria-label="待跟进事项"
            >
              <span
                v-for="issue in followUpReasons(selectedOrder)"
                :key="issue.kind"
                ><Clock3 :size="13" />{{ issue.label }}</span
              >
            </div>
            <ol
              class="m-orders-timeline"
              :class="{ delivery: isDelivery(selectedOrder) }"
              aria-label="订单进度"
            >
              <li
                v-for="(step, index) in timeline(selectedOrder)"
                :key="step.label"
                :class="{ done: step.done }"
              >
                <span
                  ><Check v-if="step.done" :size="13" /><template v-else>{{
                    index + 1
                  }}</template></span
                ><strong>{{ step.label }}</strong
                ><small>{{
                  step.date ? formatTime(step.date) : "待完成"
                }}</small>
              </li>
            </ol>
            <p
              v-if="['cancelled', 'rejected'].includes(selectedOrder.status)"
              class="m-orders-warning"
            >
              {{ selectedOrder.cancel_reason || "订单已结束" }}
            </p>
            <section class="m-orders-detail-section">
              <h3>
                <MapPin :size="17" />{{
                  isDelivery(selectedOrder) ? "本单配送交接点" : "本单取餐位置"
                }}
              </h3>
              <strong class="m-orders-address">{{
                isDelivery(selectedOrder)
                  ? selectedOrder.delivery_point_name +
                    " · " +
                    selectedOrder.delivery_point_address
                  : selectedOrder.pickup_address
              }}</strong>
              <p
                v-if="
                  selectedOrder.location_changed && !isDelivery(selectedOrder)
                "
                class="m-orders-warning"
              >
                当前摊位位置为
                {{
                  selectedOrder.current_address
                }}。本单保留了原取餐位置，请联系顾客确认。
              </p>
              <p v-else class="m-orders-help">
                使用下单时的交付地点，请按约定地点交付餐点。
              </p>
              <p v-if="isDelivery(selectedOrder)" class="m-orders-help">
                收餐人：{{ selectedOrder.recipient_name }}<br />预计送达：{{
                  formatTime(selectedOrder.delivery_eta_min_at)
                }}
                — {{ formatTime(selectedOrder.delivery_eta_max_at) }}
              </p>
              <p v-if="selectedOrder.delivery_issue" class="m-orders-warning">
                配送异常：{{ selectedOrder.delivery_issue }}
              </p>
              <a
                v-if="selectedOrder.contact_phone"
                class="m-orders-phone"
                :href="`tel:${selectedOrder.contact_phone}`"
                ><Phone :size="17" /><span
                  >联系顾客<strong>{{
                    selectedOrder.contact_phone
                  }}</strong></span
                ><ChevronRight :size="17"
              /></a>
              <p v-else class="m-orders-help">顾客未留下联系电话。</p>
            </section>
            <section class="m-orders-detail-section">
              <h3>
                <ShoppingBag :size="17" />商品明细<span
                  >{{ totalQuantity(selectedOrder) }} 件</span
                >
              </h3>
              <div class="m-orders-card-items">
                <div
                  v-for="item in selectedOrder.items"
                  :key="item.product_id"
                  class="m-orders-item"
                >
                  <img :src="item.image" :alt="item.name" />
                  <div>
                    <strong>{{ item.name }}</strong
                    ><span>¥{{ money(item.unit_price_cents) }} / 份</span>
                    <PortionSummary :portions="item.portions" />
                  </div>
                  <span class="m-orders-item-quantity"
                    >× {{ item.quantity }}</span
                  ><strong
                    >¥{{ money(item.unit_price_cents * item.quantity) }}</strong
                  >
                </div>
              </div>
              <p v-if="selectedOrder.note" class="m-orders-note">
                <strong>顾客备注</strong>{{ selectedOrder.note }}
              </p>
              <dl class="m-orders-amount">
                <div>
                  <dt>商品金额</dt>
                  <dd>
                    ¥{{
                      money(
                        selectedOrder.items_total_cents ??
                          selectedOrder.total_cents,
                      )
                    }}
                  </dd>
                </div>
                <div>
                  <dt>取餐方式</dt>
                  <dd>{{ fulfillmentLabel(selectedOrder) }}</dd>
                </div>
                <div v-if="isDelivery(selectedOrder)">
                  <dt>配送费</dt>
                  <dd>¥{{ money(selectedOrder.delivery_fee_cents) }}</dd>
                </div>
                <div class="total">
                  <dt>
                    {{ merchantPaymentAmountLabel(selectedOrder) + "金额" }}
                  </dt>
                  <dd>¥{{ money(selectedOrder.total_cents) }}</dd>
                </div>
              </dl>
              <MerchantPaymentInfo :order="selectedOrder" />
            </section>
            <section
              v-if="selectedOrder.review"
              class="m-orders-detail-section"
            >
              <h3>
                顾客评价<span>{{ selectedOrder.review.rating }} / 5 星</span>
              </h3>
              <p class="m-orders-review">
                {{ selectedOrder.review.content || "顾客留下了星级评价。" }}
              </p>
            </section>
          </div>
          <footer
            class="m-orders-drawer-footer"
            v-if="
              mainActions(selectedOrder).length ||
              moreActions(selectedOrder).length ||
              canAct(selectedOrder, 'complete')
            "
          >
            <div
              v-if="selectedOrder.cancel_requested"
              class="m-orders-cancel-notice"
            >
              <strong>顾客申请取消</strong>
              <p>{{ selectedOrder.cancel_reason || "未填写原因" }}</p>
            </div>
            <div
              v-if="mainActions(selectedOrder).length"
              class="m-orders-actions"
            >
              <button
                v-for="item in mainActions(selectedOrder)"
                :key="item.value"
                class="m-orders-button"
                :class="item.primary ? 'primary' : 'secondary'"
                :disabled="!!busy || !canAct(selectedOrder, item.value)"
                @click="requestAction(selectedOrder, item.value)"
              >
                <component
                  :is="actionIcon(item.value)"
                  v-if="actionIcon(item.value)"
                  :size="17"
                />{{
                  busy === String(selectedOrder.id) ? "正在处理…" : item.label
                }}
              </button>
            </div>
            <details
              v-if="moreActions(selectedOrder).length"
              class="m-order-more"
            >
              <summary>更多操作<span>取消、退款与配送问题</span></summary>
              <div class="m-order-more-actions">
                <button
                  v-for="item in moreActions(selectedOrder)"
                  :key="item.value"
                  class="m-orders-button secondary"
                  :disabled="!!busy || !canAct(selectedOrder, item.value)"
                  @click="requestAction(selectedOrder, item.value)"
                >
                  {{ item.label }}
                </button>
              </div>
            </details>
            <form
              v-if="canAct(selectedOrder, 'complete')"
              class="m-orders-pickup"
              @submit.prevent="performAction(selectedOrder, 'complete')"
            >
              <label for="detail-pickup-code"
                >核验顾客的 8 位{{
                  isDelivery(selectedOrder) ? "收餐码" : "取餐码"
                }}</label
              >
              <div>
                <input
                  id="detail-pickup-code"
                  v-model="pickupCodes[selectedOrder.id]"
                  class="pickup-code-input"
                  inputmode="numeric"
                  pattern="[0-9]{8}"
                  maxlength="8"
                  minlength="8"
                  :placeholder="
                    isDelivery(selectedOrder) ? '输入收餐码' : '输入取餐码'
                  "
                  required
                  autocomplete="off"
                  :disabled="!!busy"
                /><button class="m-orders-button primary" :disabled="!!busy">
                  <CheckCircle2 :size="17" />核销并完成
                </button>
              </div>
            </form>
          </footer>
        </div>
      </dialog>
      <dialog
        ref="confirmation"
        class="m-orders-confirm"
        aria-labelledby="m-orders-confirm-heading"
        @click="backdropClose($event, 'confirmation')"
        @close="confirmationState = null"
        @cancel="busy && $event.preventDefault()"
      >
        <form v-if="confirmationState" @submit.prevent="confirmAction">
          <header>
            <span class="m-orders-confirm-icon"
              ><Wallet
                v-if="confirmationState.action === 'confirm_payment'"
                :size="25" /><ShoppingBag v-else :size="25" /></span
            ><button
              type="button"
              class="m-orders-icon-button"
              aria-label="关闭操作确认"
              :disabled="!!busy"
              @click="closeConfirmation"
            >
              <X :size="21" />
            </button>
          </header>
          <h2 id="m-orders-confirm-heading">{{ confirmationCopy.title }}</h2>
          <p>{{ confirmationCopy.body }}</p>
          <details
            v-if="
              confirmationOrder?.mode === 'simulation' &&
              confirmationState.action === 'refund'
            "
            class="m-order-more simulation-faults"
          >
            <summary>模拟异常测试</summary>
            <label
              >退款测试结果<select
                v-model="simulationRefundOutcome"
                aria-label="退款测试结果"
                :disabled="!!busy"
              >
                <option value="success">正常退款成功</option>
                <option value="failure">退款失败</option>
                <option value="pending">暂未返回结果</option>
              </select></label
            >
          </details>
          <div class="m-orders-confirm-number">
            订单 #{{ confirmationOrder?.number }}
          </div>
          <label
            v-if="confirmationState.action === 'update_prep'"
            class="m-prep-minutes"
            >从现在起还需（分钟）<input
              v-model.number="revisedMinutes"
              type="number"
              min="1"
              max="180"
              required
              inputmode="numeric"
              :disabled="!!busy || !!pendingPrep[confirmationOrder?.id]"
          /></label>
          <label
            v-if="
              !['confirm_payment', 'dispatch', 'arrive'].includes(
                confirmationState.action,
              )
            "
            class="m-orders-reason-label"
            >{{ confirmationState.action === "refund" ? "退款原因" : "处理说明"
            }}<textarea
              v-model="reason"
              :required="
                [
                  'reject',
                  'deny_cancel',
                  'refund',
                  'report_delivery_issue',
                  'resolve_delivery_issue',
                  'update_prep',
                ].includes(confirmationState.action)
              "
              :maxlength="confirmationState.action === 'refund' ? 80 : 200"
              :disabled="
                !!busy ||
                (confirmationState.action === 'update_prep' &&
                  !!pendingPrep[confirmationOrder?.id])
              "
              rows="3"
              placeholder="写下原因，方便顾客理解"
            />
          </label>
          <p v-if="confirmationState.action === 'refund'" class="m-orders-help">
            {{ reasonBytes }}/80 字节 · 中文建议不超过 26
            字，原因将记录到退款流水。
          </p>
          <p v-if="actionError" class="m-orders-error" role="alert">
            {{ actionError }}
          </p>
          <p
            v-if="!canAct(confirmationOrder, confirmationState.action)"
            class="m-orders-error"
            role="status"
          >
            订单状态已变化，请关闭后重新查看。
          </p>
          <div class="m-orders-confirm-buttons">
            <button
              type="button"
              class="m-orders-button secondary"
              :disabled="!!busy"
              @click="closeConfirmation"
            >
              返回订单</button
            ><button
              class="m-orders-button primary"
              :disabled="
                !!busy ||
                !canAct(confirmationOrder, confirmationState.action) ||
                (confirmationState.action === 'refund' &&
                  (!reason.trim() || reasonBytes > 80))
              "
            >
              {{
                busy
                  ? "正在处理…"
                  : pendingPrep[confirmationOrder?.id]
                    ? "确认原操作结果"
                    : confirmationCopy.submit
              }}
            </button>
          </div>
        </form>
      </dialog>
    </Teleport>
  </section>
</template>

<style scoped>
.m-kitchen-tools-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  padding: 10px 14px;
  margin: 0 0 12px;
  width: 100%;
  background: #fffaf2;
  color: #775034;
  border: 1px solid #e7d7c3;
  border-radius: 10px;
  cursor: pointer;
}
.m-orders-heading.m-kitchen-heading {
  position: relative;
  margin: 0;
  height: 0;
  min-height: 0;
}
.m-kitchen-heading > div {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
.m-kitchen-heading .m-orders-refresh {
  position: absolute;
  right: 0;
  top: 0;
  width: 44px;
  height: 44px;
  min-width: 44px;
  font-size: 0 !important;
}
.m-kitchen-heading + .m-board-tabs {
  padding-right: 50px;
  margin-top: 0;
  margin-bottom: 10px;
}
.m-kitchen-heading + .m-board-tabs > span {
  display: none;
}
.m-board-tabs {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
  margin: 18px 0;
}
.m-board-tabs button {
  min-height: 44px;
  padding: 10px 18px;
  border-radius: 12px;
  border: 1px solid #e7d7c3;
  background: #fffdf8;
  color: #755239;
  cursor: pointer;
  font-weight: 600;
}
.m-board-tabs button[aria-pressed="true"] {
  background: #b65a23;
  border-color: #b65a23;
  color: white;
}
.m-board-tabs span {
  font-size: 12px;
  color: #75604b;
  line-height: 1.7;
}
.m-kitchen-stage {
  grid-column: 1/-1;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 19px;
  margin: 12px 0 0;
  color: #60402a;
}
.m-kitchen-stage span {
  background: #f7e8d4;
  color: #9c562b;
  padding: 4px 9px;
  border-radius: 20px;
  font-size: 12px;
}
.m-kitchen-stage small {
  font-size: 12px;
  font-weight: 400;
  color: #836b55;
}
.m-prep-status {
  margin: 14px 0;
  padding: 12px;
  border-radius: 12px;
  background: #f6f5ed;
  color: #655840;
}
.m-prep-status p {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}
.m-prep-status small {
  display: block;
  margin: 7px 0;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.m-prep-status button {
  margin-top: 10px;
  min-height: 44px;
}
.m-prep-status.overdue {
  color: #934326;
  background: #fff1e4;
}
.prep-accept label,
.m-prep-minutes {
  display: grid;
  gap: 8px;
  font-size: 13px;
  margin: 12px 0;
}
.prep-accept input,
.m-prep-minutes input {
  width: 100%;
  box-sizing: border-box;
  min-height: 44px;
  border: 1px solid #ddcbb7;
  border-radius: 10px;
  padding: 10px 12px;
  background: #fffdf8;
  color: #4d3828;
}
.m-order-followup {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 14px;
}
.m-order-followup > span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 8px;
  border-radius: 7px;
  color: #8c4d2e;
  background: #fff0db;
  font-size: 12px;
  line-height: 1.5;
}
.m-order-followup p {
  margin: 2px 0 0;
  font-size: 12px;
  color: #856448;
  line-height: 1.8;
  flex-basis: 100%;
}
.m-simulation-badge {
  display: inline-block;
  margin-top: 8px;
  padding: 5px 8px;
  border-radius: 6px;
  color: #7c6236;
  background: #f4e9d0;
  font-size: 11px;
  line-height: 1.6;
}
.m-order-more,
.prep-accept {
  border-top: 1px solid #eee1ce;
  margin-top: 14px;
  padding-top: 4px;
}
.m-order-more summary,
.prep-accept summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  min-height: 46px;
  color: #7e624b;
  font-size: 12px;
  list-style: none;
}
.m-order-more summary::after,
.prep-accept summary::after {
  content: "+";
  margin-left: auto;
  font-size: 20px;
}
.m-order-more[open] summary::after,
.prep-accept[open] summary::after {
  content: "−";
}
.m-order-more summary span,
.prep-accept summary span {
  font-size: 10px;
  color: #8a745e;
}
.m-order-more-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
  margin: 8px 0 12px;
}
.m-order-more summary:focus-visible,
.prep-accept summary:focus-visible {
  outline: 3px solid #c78c57;
  outline-offset: 3px;
}
.simulation-faults label {
  display: grid;
  gap: 8px;
  font-size: 13px;
  margin-bottom: 15px;
}
.simulation-faults select {
  min-height: 44px;
  border: 1px solid #decbb6;
  border-radius: 8px;
  padding: 8px;
  color: #5e4730;
  background: #fffaf4;
  font: inherit;
}

.m-orders {
  color: #342b23;
  min-width: 0;
}
.m-fulfillment-filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 0 0 16px;
}
.m-fulfillment-filters button {
  min-height: 44px;
  padding: 9px 14px;
  border-radius: 10px;
  border: 1px solid #e4d4bc;
  background: #fffcf6;
  color: #796048;
  font-size: 12px;
}
.m-fulfillment-filters button.active {
  background: #f5e6cf;
  border-color: #cda675;
  color: #805124;
}
.m-order-fulfillment {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  font-size: 12px;
  color: #7b694f;
  margin-top: 16px;
}
.m-order-fulfillment.delivery {
  color: #736132;
}
.m-order-fulfillment strong {
  font-weight: 600;
}
.m-order-fulfillment span {
  font-size: 11px;
  padding: 4px 7px;
  border-radius: 5px;
  background: #f7ebd6;
}
.m-delivery-destination {
  display: flex;
  gap: 9px;
  margin-top: 12px;
  padding: 13px;
  background: #faf0df;
  border: 1px solid #ede0c8;
  border-radius: 10px;
  color: #775731;
}
.m-delivery-destination > svg {
  flex-shrink: 0;
  margin-top: 3px;
}
.m-delivery-destination > div {
  min-width: 0;
}
.m-delivery-destination strong {
  display: block;
  font-size: 12px;
  line-height: 1.7;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.m-delivery-destination small {
  display: block;
  font-size: 11px;
  line-height: 1.7;
  color: #826b50;
  margin-top: 5px;
}
.m-delivery-issue-button {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  min-height: 44px;
  margin-top: 9px;
  padding: 10px 0;
  border: 0;
  background: none;
  color: #966139;
  font-size: 12px !important;
}
.m-orders-timeline.delivery {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.merchant-order-status.pending_payment {
  background: #f6ecdc;
  color: #8b693d;
}
.merchant-order-status.delivering {
  background: #e8f1ed;
  color: #4c7562;
}
.merchant-order-status.arrived {
  background: #ecedd7;
  color: #6d773d;
}
.m-orders button,
.m-orders-drawer button,
.m-orders-confirm button {
  cursor: pointer;
  font: inherit;
}
.m-orders button:disabled,
.m-orders-drawer button:disabled,
.m-orders-confirm button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.m-orders-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 26px;
}
.m-orders-eyebrow {
  font-size: 10px;
  letter-spacing: 2px;
  color: #977459;
  font-weight: 600;
}
.m-orders-heading h2 {
  font-size: clamp(22px, 2.2vw, 30px);
  margin: 10px 0;
  font-weight: 650;
  letter-spacing: -0.6px;
}
.m-orders-heading h2 > span {
  color: #eb7840;
}
.m-orders-heading p {
  margin: 0;
  font-size: 12px;
  color: #87715c;
}
.m-orders-refresh {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  border: 1px solid #e9ddcb;
  background: #fffaf3;
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 12px !important;
  color: #6f5743;
  white-space: nowrap;
}
.m-orders-toolbar {
  display: flex;
  align-items: center;
  gap: 13px;
  margin-bottom: 20px;
}
.m-orders-search {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 15px;
  min-height: 48px;
  border: 1px solid #e9ddcb;
  background: #fffaf5;
  border-radius: 12px;
  max-width: 440px;
  flex: 1;
  color: #9b8874;
}
.m-orders-search input {
  min-width: 0;
  width: 100%;
  padding: 13px 0;
  background: transparent;
  border: 0;
  color: #4c3d30;
  outline: 0;
  font: inherit;
  font-size: 13px;
}
.m-orders-search:focus-within {
  outline: 2px solid #e6a06a;
  outline-offset: 2px;
}
.m-orders-cancel-filter {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 48px;
  padding: 0 14px;
  border: 1px solid #e9ddcb;
  border-radius: 12px;
  background: transparent;
  color: #77614d;
  font-size: 12px !important;
}
.m-orders-cancel-filter span {
  min-width: 21px;
  height: 21px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  background: #eee5d8;
  font-size: 11px;
}
.m-orders-cancel-filter.active {
  color: #b65426;
  background: #fff0df;
  border-color: #edba95;
}
.m-orders-tabs {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  border-bottom: 1px solid #eadfce;
  padding-bottom: 11px;
  scrollbar-width: thin;
  max-width: 100%;
}
.m-orders-tabs button {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 10px 14px;
  flex-shrink: 0;
  border: 0;
  background: transparent;
  color: #8c7762;
  border-radius: 10px;
  font-size: 12px !important;
}
.m-orders-tabs button.active {
  background: #f6dfc5;
  color: #9e4e24;
  font-weight: 650;
}
.m-orders-tabs button > span {
  font-size: 10px;
  opacity: 0.8;
}
.m-orders-list-caption {
  display: flex;
  justify-content: space-between;
  gap: 15px;
  font-size: 11px;
  line-height: 1.7;
  color: #99816b;
  margin: 18px 0;
}
.m-orders-list-caption > span:last-child {
  white-space: nowrap;
}
.m-orders-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  align-items: start;
}
.m-orders-card {
  background: #fffcf7;
  border: 1px solid #eee3d3;
  border-radius: 18px;
  padding: 22px;
  box-shadow: 0 4px 20px #7b492306;
  overflow: hidden;
}
.m-orders-needs-attention {
  border-color: #dea97d;
}
.m-orders-card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.order-number {
  display: block;
  font-size: 13px;
  font-weight: 700;
  overflow-wrap: anywhere;
  letter-spacing: 0.2px;
}
.m-orders-card-top p {
  color: #9d8570;
  font-size: 11px;
  margin: 7px 0 0;
}
.merchant-order-status {
  display: inline-flex;
  align-items: center;
  padding: 6px 9px;
  font-size: 11px;
  background: #f0ece5;
  border-radius: 6px;
  color: #867969;
  white-space: nowrap;
  flex-shrink: 0;
}
.merchant-order-status.pending {
  background: #fff0df;
  color: #c36930;
}
.merchant-order-status.preparing {
  background: #fff0cc;
  color: #9b7929;
}
.merchant-order-status.ready {
  background: #e9eedb;
  color: #668046;
}
.merchant-order-status.completed {
  background: #e8eee5;
  color: #697f54;
}
.m-orders-countdown {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 15px 0 3px;
  color: #a96d3a;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.m-orders-countdown.urgent {
  color: #b94730;
}
.m-orders-card-items {
  display: grid;
  gap: 16px;
  padding: 21px 0;
}
.m-orders-item {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
}
.m-orders-item img {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
  background: #f0e6d8;
}
.m-orders-item > div {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}
.m-orders-item > div > strong {
  font-size: 13px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.m-orders-item > div > span {
  font-size: 10px;
  color: #9b8067;
}
.m-orders-item-quantity {
  color: #9b8067;
  font-size: 12px;
  white-space: nowrap;
}
.m-orders-item > strong {
  font-size: 12px;
  white-space: nowrap;
  font-weight: 600;
  min-width: 37px;
  text-align: right;
}
.m-orders-note {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  background: #f9eedf;
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.75;
  color: #876042;
  margin: 0 0 12px;
  overflow-wrap: anywhere;
}
.m-orders-note > strong {
  flex-shrink: 0;
  font-weight: 600;
  font-size: 10px;
  padding-top: 1px;
}
.m-orders-warning {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 12px;
  margin: 12px 0;
  background: #fff0de;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.8;
  color: #a06032;
}
.m-orders-warning svg {
  flex-shrink: 0;
  margin-top: 3px;
}
.m-orders-card-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 0 18px;
}
.m-orders-card-summary > span {
  font-size: 11px;
  color: #927963;
}
.paid-tag {
  display: inline-block;
  margin-left: 7px;
  color: #a37e56;
  padding: 4px 6px;
  background: #f5ecdd;
  border-radius: 4px;
  font-size: 10px;
}
.paid-tag.paid {
  color: #677b48;
  background: #edf0df;
}
.m-orders-card-summary > strong {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 22px;
  color: #b56938;
  font-weight: 600;
}
.m-orders-card-summary small {
  font-size: 10px;
  font-weight: 400;
  color: #947d66;
}
.m-orders-actions {
  display: flex;
  gap: 10px;
}
.m-orders-button {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  justify-content: center;
  min-height: 46px;
  padding: 11px 15px;
  border-radius: 10px;
  border: 1px solid transparent;
  font-size: 12px !important;
  line-height: 1.5;
}
.m-orders-actions > .m-orders-button {
  flex: 1;
}
.m-orders-button.primary {
  background: #e27a40;
  color: #fff;
  border-color: #e27a40;
}
.m-orders-button.primary:hover:not(:disabled) {
  background: #cb6a34;
}
.m-orders-button.secondary {
  border-color: #e5d6c3;
  color: #8e6f50;
  background: #fffcf6;
}
.m-orders-button.secondary:hover:not(:disabled) {
  background: #f7ebdc;
}
.m-orders-detail-link {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  min-height: 44px;
  padding: 11px 0 0;
  margin-top: 8px;
  color: #9a7b5f;
  font-size: 11px !important;
  background: none;
  border: 0;
}
.m-orders-cancel-notice {
  padding: 13px;
  border-radius: 8px;
  background: #fff0db;
  margin-bottom: 13px;
  color: #a76832;
  font-size: 12px;
}
.m-orders-cancel-notice > strong {
  font-weight: 600;
}
.m-orders-cancel-notice p {
  margin: 7px 0 0;
  font-size: 11px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.m-orders-pickup {
  display: grid;
  gap: 9px;
  padding-top: 8px;
}
.m-orders-pickup label {
  font-size: 11px;
  color: #8b7057;
}
.m-orders-pickup > div {
  display: flex;
  align-items: stretch;
  gap: 9px;
}
.pickup-code-input {
  min-width: 0;
  width: 100%;
  border: 1px solid #decbb6;
  border-radius: 9px;
  padding: 12px;
  background: #fff;
  color: #3c322a;
  font: inherit;
  font-size: 14px;
  letter-spacing: 2px;
  min-height: 46px;
}
.m-orders-pickup .m-orders-button {
  flex-shrink: 0;
  padding-left: 12px;
  padding-right: 12px;
}
.m-orders-history-reason {
  margin: 0;
  color: #96826e;
  font-size: 12px;
  line-height: 1.8;
}
.m-orders-empty {
  text-align: center;
  background: #fffcf6;
  border: 1px dashed #e4d4bd;
  border-radius: 18px;
  padding: 65px 20px;
  color: #987e64;
}
.m-orders-empty > svg {
  color: #cbab83;
}
.m-orders-empty h3 {
  font-size: 17px;
  color: #705741;
  margin-top: 20px;
}
.m-orders-empty p {
  font-size: 12px;
  line-height: 1.8;
  margin-bottom: 22px;
}
.m-orders-drawer {
  position: fixed;
  inset: 0 0 0 auto;
  margin: 0;
  padding: 0;
  width: min(540px, 100%);
  max-width: 100%;
  height: 100dvh;
  max-height: 100dvh;
  border: 0;
  border-left: 1px solid #eadbc6;
  color: #3e3023;
  background: #fffbf4;
  overflow: auto;
}
.m-orders-drawer::backdrop,
.m-orders-confirm::backdrop {
  background: #33251966;
  backdrop-filter: blur(3px);
}
.m-orders-drawer-content {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}
.m-orders-drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 18px;
  padding: 24px 28px;
  background: #fffbf4;
  border-bottom: 1px solid #eee2d1;
}
.m-orders-drawer-header h2 {
  margin: 8px 0 0;
  font-size: 22px;
  font-weight: 600;
}
.m-orders-icon-button {
  display: grid;
  place-items: center;
  min-width: 44px;
  min-height: 44px;
  padding: 9px;
  border: 1px solid #eaddcb;
  background: #fff8ee;
  border-radius: 50%;
  color: #8d7158;
}
.m-orders-drawer-body {
  padding: 24px 28px;
  flex: 1;
}
.m-orders-detail-status {
  text-align: center;
}
.m-orders-detail-status > strong {
  display: block;
  font-size: 19px;
  margin: 16px 0 10px;
  overflow-wrap: anywhere;
}
.m-orders-detail-status > p {
  font-size: 11px;
  color: #9a816b;
}
.m-orders-detail-status .m-orders-countdown {
  justify-content: center;
  color: #b06f37;
}
.m-orders-timeline {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  list-style: none;
  padding: 0;
  margin: 29px 0;
}
.m-orders-timeline li {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 9px;
  color: #ac9b89;
  text-align: center;
}
.m-orders-timeline li::before {
  content: "";
  position: absolute;
  top: 12px;
  width: 100%;
  height: 1px;
  background: #e7dbc9;
  left: -50%;
}
.m-orders-timeline li:first-child::before {
  display: none;
}
.m-orders-timeline li > span {
  position: relative;
  width: 25px;
  height: 25px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #f0e7da;
  color: #a08a73;
  font-size: 10px;
  z-index: 1;
}
.m-orders-timeline li > strong {
  font-weight: 500;
  font-size: 11px;
}
.m-orders-timeline li > small {
  font-size: 9px;
  line-height: 1.5;
  max-width: 67px;
}
.m-orders-timeline li.done {
  color: #ac6b3e;
}
.m-orders-timeline li.done > span {
  color: #fff;
  background: #dc905a;
}
.m-orders-timeline li.done::before {
  background: #ddb58e;
}
.m-orders-detail-section {
  margin-top: 22px;
  padding: 21px 0 0;
  border-top: 1px solid #ecdfce;
}
.m-orders-detail-section h3 {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
  margin: 0 0 17px;
}
.m-orders-detail-section h3 > svg {
  color: #b48a60;
}
.m-orders-detail-section h3 > span {
  margin-left: auto;
  font-size: 11px;
  color: #987b60;
  font-weight: 400;
}
.m-orders-address {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.8;
}
.m-orders-help {
  font-size: 11px;
  color: #9b836b;
  line-height: 1.8;
  margin: 10px 0;
}
.m-orders-phone {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 58px;
  padding: 11px 14px;
  border-radius: 9px;
  background: #f6ecdf;
  color: #8f6946;
  margin-top: 16px;
  text-decoration: none;
}
.m-orders-phone > span {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 5px;
  font-size: 10px;
}
.m-orders-phone strong {
  font-size: 13px;
  letter-spacing: 0.5px;
  font-weight: 500;
}
.m-orders-detail-section .m-orders-card-items {
  padding-top: 0;
}
.m-orders-amount {
  margin: 5px 0 0;
}
.m-orders-amount > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 15px;
  padding: 9px 0;
  font-size: 12px;
  color: #987c62;
}
.m-orders-amount dd {
  margin: 0;
  color: #6c513a;
}
.m-orders-amount .total {
  padding-top: 16px;
  border-top: 1px solid #eee1ce;
  margin-top: 8px;
}
.m-orders-amount .total dd {
  font-size: 24px;
  font-weight: 600;
  color: #b46a3a;
}
.m-orders-payment-note {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 8px 0 0;
  color: #a17e58;
  font-size: 11px;
  line-height: 1.7;
}
.m-orders-drawer-footer {
  padding: 20px 28px max(20px, env(safe-area-inset-bottom));
  border-top: 1px solid #e9dac4;
  background: #fffcf6;
}
.m-orders-review {
  font-size: 13px;
  line-height: 1.9;
  color: #8d6f51;
  overflow-wrap: anywhere;
}
.m-orders-confirm {
  padding: 28px;
  border: 1px solid #e7d4bb;
  border-radius: 20px;
  width: min(440px, calc(100% - 32px));
  max-width: calc(100% - 32px);
  max-height: calc(100dvh - 32px);
  overflow-y: auto;
  background: #fffaf2;
  color: #493723;
}
.m-orders-confirm header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 18px;
}
.m-orders-confirm-icon {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  background: #f6e5cf;
  color: #bb7b46;
  border-radius: 15px;
}
.m-orders-confirm h2 {
  font-size: 22px;
  line-height: 1.5;
  margin: 23px 0 12px;
}
.m-orders-confirm p {
  font-size: 13px;
  line-height: 1.9;
  color: #725b44;
  margin: 0 0 15px;
}
.m-orders-confirm-number {
  padding: 12px;
  border-radius: 8px;
  background: #f5ecdf;
  font-size: 12px;
  margin-bottom: 20px;
  overflow-wrap: anywhere;
}
.m-orders-reason-label {
  display: grid;
  gap: 8px;
  font-size: 12px;
  color: #8c6e4f;
  margin-bottom: 20px;
}
.m-orders-reason-label textarea {
  resize: vertical;
  width: 100%;
  min-height: 92px;
  padding: 12px;
  border: 1px solid #e2cdb3;
  border-radius: 9px;
  color: #5e4730;
  background: #fffdf8;
  font: inherit;
  line-height: 1.8;
}
.m-orders-confirm .m-orders-error {
  color: #b1412b;
  font-size: 12px;
}
.m-orders-confirm-buttons {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 23px;
}
.m-orders-spinning {
  animation: m-orders-spin 1s linear infinite;
}
@keyframes m-orders-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 1100px) {
  .m-orders-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .m-kitchen-mode .m-orders-list-caption {
    margin: 8px 0;
  }
  .m-kitchen-mode .m-kitchen-tools-toggle {
    margin-bottom: 4px;
  }
  .m-kitchen-mode .m-orders-card {
    padding: 16px;
  }
  .m-kitchen-mode .m-orders-grid {
    gap: 12px;
  }
  .m-orders-list-caption {
    margin-bottom: 8px;
  }
  .m-kitchen-stage {
    margin-top: 0;
  }
  .m-kitchen-heading {
    margin-bottom: 0;
  }
  .m-pending-actions {
    margin-top: 10px;
  }
  .m-orders-heading {
    gap: 12px;
    align-items: flex-start;
    margin-bottom: 23px;
  }
  .m-orders-heading h2 {
    font-size: 22px;
    max-width: 240px;
    line-height: 1.45;
  }
  .m-orders-heading p {
    font-size: 10px;
    line-height: 1.8;
  }
  .m-orders-eyebrow {
    font-size: 8px;
    letter-spacing: 1.6px;
  }
  .m-orders-refresh {
    font-size: 0 !important;
    min-width: 44px;
    padding: 12px;
  }
  .m-orders-refresh svg {
    width: 17px;
    height: 17px;
  }
  .m-orders-toolbar {
    gap: 9px;
    margin-bottom: 16px;
  }
  .m-orders-search {
    padding: 0 12px;
  }
  .m-orders-search input {
    font-size: 12px;
  }
  .m-orders-cancel-filter {
    padding: 0 10px;
    font-size: 11px !important;
    gap: 6px;
    flex-shrink: 0;
  }
  .m-orders-tabs {
    gap: 3px;
  }
  .m-orders-tabs button {
    padding: 10px 12px;
  }
  .m-orders-list-caption {
    font-size: 10px;
  }
  .m-orders-card {
    padding: 18px;
    border-radius: 15px;
  }
  .m-orders-grid {
    gap: 16px;
  }
  .order-number {
    font-size: 11px;
  }
  .m-orders-card-top p {
    font-size: 10px;
  }
  .merchant-order-status {
    font-size: 10px;
    padding: 5px 7px;
  }
  .m-orders-item {
    gap: 9px;
  }
  .m-orders-item img {
    width: 44px;
    height: 44px;
  }
  .m-orders-item > div > strong {
    font-size: 12px;
  }
  .m-orders-item > strong {
    font-size: 11px;
  }
  .m-orders-button {
    font-size: 11px !important;
    padding: 11px 10px;
  }
  .m-orders-note {
    font-size: 11px;
    padding: 10px;
  }
  .m-orders-pickup > div {
    flex-wrap: wrap;
  }
  .m-orders-pickup .m-orders-button {
    width: 100%;
  }
  .m-orders-drawer-header {
    padding: 20px;
  }
  .m-orders-drawer-body {
    padding: 21px 20px;
  }
  .m-orders-drawer-footer {
    padding: 18px 20px max(18px, env(safe-area-inset-bottom));
  }
  .m-orders-drawer .m-orders-pickup > div {
    flex-wrap: nowrap;
  }
  .m-orders-drawer .m-orders-pickup .m-orders-button {
    width: auto;
  }
  .m-orders-confirm {
    padding: 23px;
    border-radius: 16px;
  }
  .m-orders-confirm h2 {
    font-size: 20px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .m-orders-spinning {
    animation: none;
  }
}
</style>
