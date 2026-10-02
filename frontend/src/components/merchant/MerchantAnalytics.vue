<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowDownToLine,
  Wallet,
  ShoppingBag,
  Users,
  ReceiptText,
  BarChart3,
  CircleHelp,
} from "lucide-vue-next";
import { formatTime, money } from "../../lib/api";
import MerchantTrend from "./MerchantTrend.vue";
const props = defineProps<{
  metrics: any;
  days: number;
  error: string;
  loading: boolean;
}>();
const emit = defineEmits<{ period: [days: number]; openOrder: [] }>();
const simulated = computed(() => props.metrics?.mode === "simulation");
const sourceLabels: Record<string, string> = {
  stall_qr: "摊位二维码",
  share_link: "分享链接",
  search: "搜索",
  map: "地图",
  follow: "关注列表",
  homepage: "首页",
  home: "首页",
  direct: "直接访问",
  unknown: "未标记来源",
};
const eventLabels: Record<string, string> = {
  qr_open: "二维码链接打开",
  share_open: "分享链接打开",
  share_click: "点击分享",
  navigation_click: "点击导航",
  follow: "新增关注",
  stall_view: "摊位页面浏览",
};
const stats = computed(() => [
  {
    label: simulated.value ? "模拟收款总额" : "收款总额",
    value: props.metrics ? `¥${money(props.metrics.revenue_cents)}` : "—",
    icon: Wallet,
  },
  {
    label: "下单量",
    value: props.metrics?.orders_created ?? "—",
    icon: ShoppingBag,
  },
  {
    label: simulated.value ? "模拟收款客单价" : "收款客单价",
    value: props.metrics ? `¥${money(props.metrics.average_order_cents)}` : "—",
    icon: ReceiptText,
  },
  { label: "关注人数", value: props.metrics?.followers ?? "—", icon: Users },
]);
const maxSales = computed(() =>
  Math.max(
    1,
    ...(props.metrics?.top_products || []).map((p: any) => p.quantity),
  ),
);
const hasPaymentBreakdown = computed(
  () =>
    typeof props.metrics?.offline_revenue_cents === "number" &&
    typeof props.metrics?.online_revenue_cents === "number",
);
function paymentRecordLabel(record: any) {
  const method =
    ((record.mode || props.metrics?.mode) === "simulation" ? "模拟 · " : "") +
    (record.payment_method === "wechat" ? "微信支付确认" : "到摊收款确认");
  if (record.payment_status === "refunded") return `${method} · 后续已退款`;
  if (record.payment_status === "refunding") return `${method} · 退款处理中`;
  return method;
}
function exportCsv() {
  if (!props.metrics) return;
  const rows = [
    [
      "日期",
      "下单量",
      "完成量",
      simulated.value
        ? "模拟收款总额（元，无真实资金）"
        : "收款总额（元，退款另计）",
    ],
    ...props.metrics.series.map((d: any) => [
      d.date,
      d.orders_created,
      d.orders_completed,
      (d.revenue_cents / 100).toFixed(2),
    ]),
  ];
  const blob = new Blob(
    ["\ufeff" + rows.map((r) => r.join(",")).join("\r\n")],
    { type: "text/csv;charset=utf-8;" },
  );
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = `烟火地图-${simulated.value ? "模拟数据-" : ""}近${props.days}天经营日报.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
</script>
<template>
  <div class="m-analytics">
    <p v-if="simulated" class="m-info-banner">
      模拟经营数据 ·
      本页金额和导出日报用于流程体验，不代表真实收款、退款或收入。
    </p>
    <div class="m-section-toolbar">
      <div class="m-segmented" aria-label="数据时间范围">
        <button
          v-for="period in [1, 7, 30]"
          :key="period"
          :class="{ active: days === period }"
          :disabled="loading"
          @click="emit('period', period)"
        >
          {{ period === 1 ? "今天" : `近 ${period} 天` }}
        </button>
      </div>
      <button
        class="btn btn-secondary"
        :disabled="!metrics || loading"
        @click="exportCsv"
      >
        <ArrowDownToLine :size="16" /> 导出日报
      </button>
    </div>
    <section
      v-if="
        typeof metrics?.completed_customer_count === 'number' &&
        typeof metrics?.returning_customer_count === 'number'
      "
      class="m-panel merchant-returning"
    >
      <div class="m-panel-head">
        <h2><Users :size="20" /> 回头客</h2>
        <span class="m-muted"
          >所选期间 · {{ simulated ? "模拟记录" : "正式记录" }}</span
        >
      </div>
      <div class="returning-stats">
        <div>
          <strong>{{ metrics.returning_customer_count }}</strong
          ><span>再次完成订单的同学</span>
        </div>
        <div>
          <strong>{{ metrics.completed_customer_count }}</strong
          ><span>本期完成订单的同学</span>
        </div>
        <div>
          <strong>{{
            metrics.returning_customer_rate == null
              ? "—"
              : `${Math.round(metrics.returning_customer_rate * 100)}%`
          }}</strong
          ><span>本期完成顾客中的回头客占比</span>
        </div>
      </div>
      <p>
        {{
          metrics.metric_definitions?.returning_customers ||
          "本期完成订单的顾客中，曾在本摊位完成同模式订单的人数，按人去重；不代表留存率。"
        }}
      </p>
    </section>
    <section
      v-if="Array.isArray(metrics?.source_counts)"
      class="m-panel merchant-sources"
    >
      <div class="m-panel-head">
        <h2>同学从哪里找到你</h2>
        <span class="m-muted">所选期间的页面事件</span>
      </div>
      <div v-if="metrics.source_counts.length" class="source-counts">
        <div
          v-for="row in metrics.source_counts"
          :key="`${row.source}:${row.event_type}`"
        >
          <span
            >{{ sourceLabels[row.source] || "其他来源"
            }}<small>{{
              eventLabels[row.event_type] || "页面操作"
            }}</small></span
          ><strong>{{ row.count }} <small>次</small></strong>
        </div>
      </div>
      <p v-else class="m-muted">所选期间还没有来源记录。</p>
      <p>
        来源由客户端标记，次数不代表独立人数或真实扫码量；此项包含同一摊位的浏览记录，不按正式或模拟订单区分。
      </p>
    </section>
    <p v-if="error" role="alert" class="m-alert">{{ error }}</p>
    <p v-if="loading" class="m-muted" role="status">正在更新经营数据…</p>
    <div class="m-kpis">
      <article v-for="s in stats" :key="s.label" class="m-panel m-kpi">
        <span class="m-kpi-icon orange"
          ><component :is="s.icon" :size="20" /></span
        ><span class="m-kpi-label">{{ s.label }}</span
        ><strong>{{ s.value }}</strong
        ><small>{{
          s.label === "关注人数"
            ? "当前累计关注"
            : s.label === "收款客单价"
              ? "收款总额 ÷ 收款订单数"
              : "所选自然日期范围"
        }}</small>
      </article>
    </div>
    <section
      v-if="hasPaymentBreakdown"
      class="m-panel m-payment-breakdown"
      aria-label="款项分类"
    >
      <div>
        <span>到摊收款</span
        ><strong>¥{{ money(metrics.offline_revenue_cents) }}</strong
        ><small>由商家确认收到</small>
      </div>
      <div>
        <span>微信付款</span
        ><strong>¥{{ money(metrics.online_revenue_cents) }}</strong
        ><small>{{
          simulated ? "模拟支付成功，不涉及真实资金" : "由微信确认支付成功"
        }}</small>
      </div>
      <div v-if="typeof metrics.refund_cents === 'number'">
        <span>本期原路退款</span
        ><strong>¥{{ money(metrics.refund_cents) }}</strong
        ><small>按退款完成日期，单独统计</small>
      </div>
    </section>
    <section class="m-panel">
      <div class="m-panel-head">
        <h2>营业收款趋势</h2>
        <span class="m-muted">收款总额，退款另计 · 元</span>
      </div>
      <MerchantTrend :series="metrics?.series || []" />
      <details v-if="metrics">
        <summary>查看每日明细</summary>
        <div class="m-data-table">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>下单</th>
                <th>完成</th>
                <th>收款总额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in metrics.series" :key="row.date">
                <td>{{ row.date }}</td>
                <td>{{ row.orders_created }}</td>
                <td>{{ row.orders_completed }}</td>
                <td>¥{{ money(row.revenue_cents) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </section>
    <div class="m-dashboard-columns">
      <section class="m-panel">
        <div class="m-panel-head">
          <h2>人气商品 <small>TOP 5</small></h2>
          <BarChart3 :size="18" />
        </div>
        <div v-if="!metrics?.top_products?.length" class="m-empty">
          <BarChart3 :size="30" />
          <p>已收款的订单会汇成小摊的人气榜。</p>
        </div>
        <div v-else class="m-ranking">
          <div v-for="(p, i) in metrics.top_products.slice(0, 5)" :key="p.name">
            <span :class="{ first: i === 0 }">{{ Number(i) + 1 }}</span>
            <div>
              <strong>{{ p.name }}</strong>
              <div class="m-rank-track">
                <i :style="{ width: `${(p.quantity / maxSales) * 100}%` }" />
              </div>
            </div>
            <b>{{ p.quantity }} <small>份</small></b>
          </div>
        </div>
      </section>
      <section class="m-panel">
        <div class="m-panel-head">
          <h2>经营提醒</h2>
          <CircleHelp :size="18" />
        </div>
        <div class="m-health-list">
          <div>
            <span>接单超时取消</span
            ><strong
              >{{ metrics?.expired_orders ?? "—" }} <small>单</small></strong
            >
          </div>
          <div>
            <span>出餐超 1 小时未取</span
            ><strong
              >{{ metrics?.uncollected_orders ?? "—" }}
              <small>单</small></strong
            >
          </div>
          <div>
            <span>在售商品 / 已售罄</span
            ><strong
              >{{ metrics?.products_active ?? "—" }} /
              {{ metrics?.products_sold_out ?? "—" }}</strong
            >
          </div>
          <div>
            <span>本期创建订单完成率</span
            ><strong>{{
              metrics?.order_completion_rate == null
                ? "—"
                : `${Math.round(metrics.order_completion_rate * 100)}%`
            }}</strong>
          </div>
        </div>
        <p class="m-muted">未取餐订单请联系顾客；系统不会自动完成取餐。</p>
      </section>
    </div>
    <section class="m-panel">
      <div class="m-panel-head">
        <div>
          <h2>收款记录</h2>
          <p class="m-muted">
            区分到摊确认与微信付款；线上款项结算以支付服务商账单为准。
          </p>
        </div>
        <Wallet :size="20" />
      </div>
      <div v-if="!metrics?.recent_payments?.length" class="m-empty">
        <ReceiptText :size="30" />
        <p>所选时间内还没有收款记录。</p>
      </div>
      <div v-else class="m-ledger">
        <div v-for="r in metrics.recent_payments" :key="r.id">
          <span class="m-ledger-icon"><ReceiptText :size="18" /></span>
          <div>
            <strong>订单 #{{ r.number.slice(-8) }}</strong
            ><small
              >{{ formatTime(r.paid_at) }} · {{ paymentRecordLabel(r) }}</small
            >
          </div>
          <b>+ ¥{{ money(r.total_cents) }}</b>
        </div>
      </div>
    </section>
    <p class="m-page-footnote">
      按北京时间自然日统计。收款总额按付款确认时间统计，包含后续退款订单；退款按退款完成时间单独统计，不能将收款总额当作净收入或可提现余额。完成量按核销时间，完成率按本期创建订单计算。
    </p>
  </div>
</template>

<style scoped>
.returning-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.returning-stats > div {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.returning-stats strong {
  font-size: 28px;
  color: #734725;
}
.returning-stats span,
.merchant-returning p,
.merchant-sources > p {
  font-size: 13px;
  line-height: 1.8;
  color: #7d6853;
}
.source-counts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.source-counts > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  background: #fff5e7;
  border-radius: 12px;
}
.source-counts span > small {
  display: block;
  margin-top: 6px;
}
.source-counts small {
  font-size: 12px;
  font-weight: 400;
  color: #7d6853;
}
@media (max-width: 600px) {
  .returning-stats,
  .source-counts {
    grid-template-columns: 1fr;
  }
  .returning-stats > div {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
  .returning-stats strong {
    font-size: 24px;
  }
}
.m-payment-breakdown {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 18px;
  margin: 20px 0;
}
.m-payment-breakdown > div {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.m-payment-breakdown span {
  font-size: 13px;
  color: #61574c;
}
.m-payment-breakdown strong {
  font-size: 24px;
  color: #3b3028;
}
.m-payment-breakdown small {
  color: #726455;
  font-size: 12px;
}
</style>
