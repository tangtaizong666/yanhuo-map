<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import {
  ChevronDown,
  ChevronUp,
  Download,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-vue-next";
import { api, statusText } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const emit = defineEmits<{
  (e: "changed"): void;
}>();

const session = useSession();
const loading = ref(true);
const loadingMore = ref(false);
const error = ref("");

const filterTab = ref("attention");
const modeFilter = ref("all");
const stallFilter = ref("");
const keyword = ref("");

const counts = ref<Record<string, number>>({});
const orders = ref<any[]>([]);
const nextCursor = ref<string | null>(null);
const merchants = ref<any[]>([]);
const stalls = ref<any[]>([]);

const expandedOrderId = ref<string | null>(null);
const detailLoading = ref(false);
const detailMap = reactive<Record<string, any>>({});

const finBusy = ref(false);
const finError = ref("");
const finForm = reactive({
  operation: "verify" as
    | "verify"
    | "uncreated"
    | "external"
    | "retry"
    | "compensation",
  payment_id: "",
  refund_id: "",
  out_refund_no: "",
  expected_amount_cents: 0,
  reason: "",
  confirm_refund: false,
  operation_key: "",
});

const todayIso = new Date().toISOString().slice(0, 10);
const weekAgoIso = new Date(Date.now() - 6 * 86400_000)
  .toISOString()
  .slice(0, 10);
const exportBusy = ref(false);
const exportForm = reactive({
  merchant_id: "",
  starts_on: weekAgoIso,
  ends_on: todayIso,
});

function makeOpKey() {
  return `ops-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function money(cents: number | null | undefined) {
  return `¥${((cents || 0) / 100).toFixed(2)}`;
}

async function loadMeta() {
  if (!session.hasOpsPerm("view_merchants")) return;
  try {
    const res = await api<{ merchants: any[]; stalls: any[] }>("/ops/stalls");
    merchants.value = res.merchants || [];
    stalls.value = res.stalls || [];
    if (!exportForm.merchant_id && merchants.value.length) {
      exportForm.merchant_id = String(merchants.value[0].id);
    }
  } catch {
    /* Optional filter options */
  }
}

async function loadOrders(append = false) {
  if (!session.hasOpsPerm("view_orders")) {
    loading.value = false;
    return;
  }
  if (append) {
    if (!nextCursor.value || loadingMore.value) return;
    loadingMore.value = true;
  } else {
    loading.value = true;
    error.value = "";
  }
  try {
    const params = new URLSearchParams({
      filter: filterTab.value,
      mode: modeFilter.value,
      page_size: "20",
    });
    if (stallFilter.value) params.set("stall", stallFilter.value);
    if (keyword.value.trim()) params.set("q", keyword.value.trim());
    if (append && nextCursor.value) params.set("cursor", nextCursor.value);

    const res = await api<{
      results: any[];
      next: string | null;
      counts: Record<string, number>;
    }>(`/ops/orders?${params.toString()}`);

    counts.value = res.counts || {};
    nextCursor.value = res.next || null;
    orders.value = append
      ? [...orders.value, ...(res.results || [])]
      : res.results || [];
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

watch([filterTab, modeFilter, stallFilter], () => {
  void loadOrders(false);
});

async function toggleDetail(order: any) {
  finError.value = "";
  if (expandedOrderId.value === order.id) {
    expandedOrderId.value = null;
    return;
  }
  expandedOrderId.value = order.id;
  detailLoading.value = true;
  try {
    const res = await api(`/ops/orders/${order.id}`);
    detailMap[order.id] = res;
    finForm.operation = "verify";
    finForm.payment_id = res.payments?.[0]?.id || "";
    finForm.refund_id =
      res.refunds?.find((r: any) => r.status === "closed" && !r.resolved_at)
        ?.id || "";
    finForm.out_refund_no = "";
    finForm.expected_amount_cents = res.payments?.[0]?.amount_cents || 0;
    finForm.reason = "";
    finForm.confirm_refund = false;
    finForm.operation_key = makeOpKey();
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    detailLoading.value = false;
  }
}

async function submitResolution(orderId: string) {
  if (finBusy.value) return;
  finBusy.value = true;
  finError.value = "";
  try {
    const payload: Record<string, any> = {
      operation: finForm.operation,
      reason: finForm.reason.trim(),
    };
    if (finForm.operation !== "verify") {
      payload.payment_id = finForm.payment_id || null;
    }
    if (finForm.operation === "external") {
      payload.out_refund_no = finForm.out_refund_no.trim();
    }
    if (finForm.operation === "retry") {
      payload.refund_id = finForm.refund_id || null;
    }
    if (finForm.operation === "retry" || finForm.operation === "compensation") {
      payload.confirm_refund = finForm.confirm_refund;
      payload.expected_amount_cents = Number(finForm.expected_amount_cents);
      payload.operation_key = finForm.operation_key || makeOpKey();
    }
    const updated = await api(`/ops/orders/${orderId}/financial-resolution`, {
      method: "POST",
      body: payload,
    });
    detailMap[orderId] = updated;
    finForm.operation_key = makeOpKey();
    notify("资金渠道核验指令已执行并记录存证", "success");
    await loadOrders(false);
    emit("changed");
  } catch (e) {
    finError.value = (e as Error).message;
  } finally {
    finBusy.value = false;
  }
}

async function exportCsv() {
  if (exportBusy.value) return;
  if (!exportForm.merchant_id) {
    notify("请先选择要导出对账单的经营主体。", "error");
    return;
  }
  exportBusy.value = true;
  try {
    const params = new URLSearchParams({
      merchant_id: String(exportForm.merchant_id),
      starts_on: exportForm.starts_on,
      ends_on: exportForm.ends_on,
    });
    const res = await fetch(
      `/api/v1/ops/reconciliation/export?${params.toString()}`,
      {
        credentials: "include",
        headers: {
          Accept: "application/json, */*;q=0.8",
        },
      },
    );
    if (!res.ok) {
      let msg = "导出对账单失败，请检查日期范围后重试。";
      try {
        const errData = await res.json();
        if (errData?.detail) msg = errData.detail;
      } catch {
        /* Fallback message */
      }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `reconciliation-merchant-${exportForm.merchant_id}-${exportForm.starts_on}_${exportForm.ends_on}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    notify("对账单 CSV 已生成下载", "success");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    exportBusy.value = false;
  }
}

onMounted(() => {
  void loadMeta();
  void loadOrders(false);
});
</script>

<template>
  <div class="stack" style="gap: 18px">
    <section
      v-if="session.hasOpsPerm('export_reconciliation')"
      class="o-card"
    >
      <div class="o-card-header">
        <div>
          <h2>商户资金对账单导出（CSV）</h2>
          <p>
            按经营主体导出最长 31 天的正式收款、渠道退款、到摊收款及待复核差异明细（含防公式注入保护）。
          </p>
        </div>
      </div>

      <form class="o-toolbar" @submit.prevent="exportCsv">
        <div class="o-filters">
          <select
            v-model="exportForm.merchant_id"
            class="o-select"
            aria-label="选择导出商户"
            required
          >
            <option value="">选择经营主体</option>
            <option v-for="m in merchants" :key="m.id" :value="String(m.id)">
              #{{ m.id }} {{ m.business_name }}（{{ m.username }}）
            </option>
          </select>
          <input
            v-model="exportForm.starts_on"
            type="date"
            class="o-input"
            aria-label="开始日期"
            required
          />
          <input
            v-model="exportForm.ends_on"
            type="date"
            class="o-input"
            aria-label="结束日期"
            required
          />
        </div>
        <button
          type="submit"
          class="o-btn o-btn-pine"
          :disabled="exportBusy"
        >
          <Download :size="15" />
          {{ exportBusy ? "正在导出…" : "导出 CSV 对账单" }}
        </button>
      </form>
    </section>

    <section class="o-card">
      <div class="o-card-header">
        <div>
          <h2>全站订单监督与异常资金核验</h2>
          <p>
            治理端严守职责边界：接单、出餐、送达与收货仅由商家与学生各自执行；管理员只读巡检履约全貌，并依据支付渠道真实回执处置资金异常。
          </p>
        </div>
        <button
          type="button"
          class="o-btn"
          :disabled="loading"
          @click="loadOrders(false)"
        >
          <RefreshCw :size="15" /> 刷新
        </button>
      </div>

      <div class="o-toolbar" style="margin-bottom: 12px">
        <div class="o-tabs" role="tablist" aria-label="订单状态范围">
          <button
            v-for="tab in [
              { id: 'attention', label: '需重点关注' },
              { id: 'active', label: '进行中' },
              { id: 'followup', label: '待跟进' },
              { id: 'all', label: '全部订单' },
              { id: 'completed', label: '已完成' },
              { id: 'cancelled', label: '取消/拒单' },
            ]"
            :key="tab.id"
            type="button"
            class="o-tab"
            :class="{ active: filterTab === tab.id }"
            @click="filterTab = tab.id"
          >
            {{ tab.label }} ({{ counts[tab.id] ?? 0 }})
          </button>
        </div>
      </div>

      <div class="o-toolbar">
        <div class="o-filters">
          <select
            v-model="modeFilter"
            class="o-select"
            aria-label="按服务模式筛选"
          >
            <option value="all">全部模式</option>
            <option value="live">正式订单（live）</option>
            <option value="simulation">演练体验（simulation）</option>
          </select>
          <select
            v-if="stalls.length"
            v-model="stallFilter"
            class="o-select"
            aria-label="按摊位筛选"
          >
            <option value="">全部摊位</option>
            <option v-for="s in stalls" :key="s.id" :value="String(s.id)">
              {{ s.name }}
            </option>
          </select>
        </div>

        <form class="o-filters" @submit.prevent="loadOrders(false)">
          <input
            v-model="keyword"
            class="o-input"
            placeholder="搜索订单号、顾客账号或摊位名"
            aria-label="搜索订单"
          />
          <button type="submit" class="o-btn">
            <Search :size="15" /> 搜索
          </button>
        </form>
      </div>
    </section>

    <p v-if="error" class="o-alert" role="alert">{{ error }}</p>
    <div v-else-if="loading" class="o-card o-empty">正在读取订单列表…</div>
    <div v-else-if="!orders.length" class="o-card o-empty">
      当前筛选下暂无订单记录（如默认「需重点关注」为空，可切换至「全部订单」查看）。
    </div>

    <div v-else class="o-list">
      <article v-for="ord in orders" :key="ord.id" class="o-item">
        <div class="o-item-top">
          <div class="o-item-title">
            <h3>{{ ord.number }}</h3>
            <span class="o-badge pine">{{ statusText(ord.status, ord.fulfillment_type) }}</span>
            <span
              class="o-badge"
              :class="
                ord.payment_review_required
                  ? 'danger'
                  : ord.payment_status === 'paid'
                    ? 'ok'
                    : 'warn'
              "
            >
              {{
                ord.payment_review_required
                  ? "需人工核验资金"
                  : ord.payment_status === "paid"
                    ? "已付款"
                    : ord.payment_status === "refunding"
                      ? "退款处理中"
                      : ord.payment_status === "refunded"
                        ? "已退款"
                        : "待付款/到摊付"
              }}
            </span>
            <span v-if="ord.mode === 'simulation'" class="o-badge"
              >演练单</span
            >
          </div>

          <div class="o-actions-row">
            <strong>{{ money(ord.total_cents) }}</strong>
            <button
              type="button"
              class="o-btn"
              @click="toggleDetail(ord)"
            >
              {{ expandedOrderId === ord.id ? "收起详情" : "详情与核验" }}
              <ChevronUp v-if="expandedOrderId === ord.id" :size="15" />
              <ChevronDown v-else :size="15" />
            </button>
          </div>
        </div>

        <div class="o-meta-row">
          <span>摊位：{{ ord.stall_name }}（#{{ ord.stall_id }}）</span>
          <span>顾客：{{ ord.buyer_display_name }}（{{ ord.buyer_username }}）</span>
          <span
            >方式：{{
              ord.fulfillment_type === "delivery" ? "定点短距配送" : "到摊自取"
            }}</span
          >
          <span>下单：{{ new Date(ord.created_at).toLocaleString("zh-CN") }}</span>
        </div>

        <p v-if="ord.financial_hold_reason" class="o-callout warn" style="margin: 0">
          资金冻结提示：{{ ord.financial_hold_reason }}
        </p>

        <div v-if="expandedOrderId === ord.id" class="o-drawer">
          <div v-if="detailLoading && !detailMap[ord.id]" class="muted">
            正在加载订单与资金存证…
          </div>
          <template v-else-if="detailMap[ord.id]">
            <div class="o-callout">
              <ShieldCheck :size="15" style="vertical-align: -3px" />
              隐私与防冒领保护：顾客取餐码、微信待支付二维码与商家收款码已在治理台自动脱敏。
            </div>

            <div class="o-meta-row">
              <span
                v-for="it in detailMap[ord.id].order.items"
                :key="it.product_id"
              >
                {{ it.name }} × {{ it.quantity }}（{{
                  money(it.unit_price_cents)
                }}）
              </span>
            </div>

            <div v-if="detailMap[ord.id].payments?.length" class="stack" style="gap: 8px">
              <strong style="font-size: 13px">渠道付款记录</strong>
              <div
                v-for="p in detailMap[ord.id].payments"
                :key="p.id"
                class="o-meta-row o-code-line"
              >
                <span>状态：{{ p.status }}</span>
                <span>金额：{{ money(p.amount_cents) }}</span>
                <span>商户单号：{{ p.out_trade_no }}</span>
                <span v-if="p.transaction_id">微信单号：{{ p.transaction_id }}</span>
              </div>
            </div>

            <div v-if="detailMap[ord.id].refunds?.length" class="stack" style="gap: 8px">
              <strong style="font-size: 13px">渠道退款记录</strong>
              <div
                v-for="r in detailMap[ord.id].refunds"
                :key="r.id"
                class="o-meta-row o-code-line"
              >
                <span>状态：{{ r.status }}</span>
                <span>金额：{{ money(r.amount_cents) }}</span>
                <span>退款单号：{{ r.out_refund_no }}</span>
                <span v-if="r.error_message">说明：{{ r.error_message }}</span>
              </div>
            </div>

            <div v-if="detailMap[ord.id].evidence?.length" class="stack" style="gap: 8px">
              <strong style="font-size: 13px">历史资金处置存证</strong>
              <div
                v-for="ev in detailMap[ord.id].evidence"
                :key="ev.id"
                class="o-meta-row"
              >
                <span>[{{ ev.operation }} → {{ ev.outcome }}]</span>
                <span>操作人：{{ ev.actor_username || "系统" }}</span>
                <span>原因：{{ ev.reason }}</span>
              </div>
            </div>

            <form
              v-if="
                session.hasOpsPerm('resolve_payments') && ord.mode === 'live'
              "
              class="o-card o-form-grid"
              style="background: #fcfaf6"
              @submit.prevent="submitResolution(ord.id)"
            >
              <div class="span-2">
                <strong style="font-size: 14px"
                  >发起渠道核验与资金处置（需真实支付渠道回执验证）</strong
                >
              </div>
              <label class="o-field">
                处置动作
                <select v-model="finForm.operation" class="o-select">
                  <option value="verify">
                    查询渠道真实状态并同步结案（verify）
                  </option>
                  <option value="uncreated">
                    核实渠道未建单并关闭本地创建中记录（uncreated）
                  </option>
                  <option value="external">
                    核实外部商户退款单号并结案（external）
                  </option>
                  <option value="retry">
                    对已关闭退款重新发起全额退款（retry）
                  </option>
                  <option value="compensation">
                    对多笔重复实收发起补偿全额退款（compensation）
                  </option>
                </select>
              </label>

              <label v-if="finForm.operation !== 'verify'" class="o-field">
                目标原付款记录
                <select v-model="finForm.payment_id" class="o-select" required>
                  <option value="">请选择付款记录</option>
                  <option
                    v-for="p in detailMap[ord.id].payments"
                    :key="p.id"
                    :value="p.id"
                  >
                    {{ p.out_trade_no }}（{{ p.status }} ·
                    {{ money(p.amount_cents) }}）
                  </option>
                </select>
              </label>

              <label v-if="finForm.operation === 'external'" class="o-field">
                外部商户退款单号 *
                <input
                  v-model="finForm.out_refund_no"
                  class="o-input"
                  required
                />
              </label>

              <label v-if="finForm.operation === 'retry'" class="o-field">
                原关闭退款记录 *
                <select v-model="finForm.refund_id" class="o-select" required>
                  <option value="">请选择关闭未结案退款</option>
                  <option
                    v-for="r in detailMap[ord.id].refunds"
                    :key="r.id"
                    :value="r.id"
                  >
                    {{ r.out_refund_no }}（{{ r.status }}）
                  </option>
                </select>
              </label>

              <template
                v-if="
                  finForm.operation === 'retry' ||
                  finForm.operation === 'compensation'
                "
              >
                <label class="o-field">
                  确认退款金额（分）*
                  <input
                    v-model.number="finForm.expected_amount_cents"
                    type="number"
                    min="1"
                    class="o-input"
                    required
                  />
                </label>
                <label class="o-check" style="align-self: end">
                  <input v-model="finForm.confirm_refund" type="checkbox" />
                  我已核对原付款状态与全额退款金额
                </label>
              </template>

              <label class="o-field span-2">
                核验与处置原因说明 *
                <input
                  v-model="finForm.reason"
                  class="o-input"
                  placeholder="将永久写入资金核验存证与审计日志"
                  required
                />
              </label>

              <div class="span-2 o-actions-row">
                <p
                  v-if="finError"
                  class="o-alert"
                  role="alert"
                  style="width: 100%"
                >
                  {{ finError }}
                </p>
                <button
                  type="submit"
                  class="o-btn o-btn-primary"
                  :disabled="finBusy"
                >
                  {{ finBusy ? "正在向渠道核验…" : "执行渠道核验" }}
                </button>
              </div>
            </form>
          </template>
        </div>
      </article>

      <div v-if="nextCursor" style="text-align: center; margin-top: 8px">
        <button
          type="button"
          class="o-btn"
          :disabled="loadingMore"
          @click="loadOrders(true)"
        >
          {{ loadingMore ? "正在加载…" : "加载更多订单" }}
        </button>
      </div>
    </div>
  </div>
</template>
