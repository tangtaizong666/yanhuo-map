<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowDownToLine,
  BarChart3,
  CircleHelp,
  ClipboardCheck,
  ReceiptText,
  ShoppingBag,
  Users,
  Wallet,
} from "lucide-vue-next";
import { formatTime, money } from "../../lib/api";
import MerchantPayoutCard from "./MerchantPayoutCard.vue";
import MerchantTrend from "./MerchantTrend.vue";

const props = defineProps<{
  stall?: any;
  discoveryOnly?: boolean;
  metrics: any;
  days: number;
  error: string;
  loading: boolean;
  mode?: "live" | "simulation";
  canSwitchMode?: boolean;
}>();

const emit = defineEmits<{
  period: [days: number];
  mode: [value: "live" | "simulation"];
  openOrder: [];
  stallUpdated: [stall: any];
}>();

const activeMode = computed(
  () => props.mode || (props.metrics?.mode === "simulation" ? "simulation" : "live"),
);
const simulated = computed(() => (props.metrics?.mode || activeMode.value) === "simulation");

const sourceLabels: Record<string, string> = {
  stall_qr: "摊位二维码",
  share_link: "分享链接",
  search: "搜索",
  map: "地图",
  follow: "关注列表",
  homepage: "首页",
  home: "首页",
  home_recent: "首页常买",
  recent_order: "最近订单",
  stall_detail: "摊位详情",
  merchant_preview: "商家预览",
  direct: "直接访问",
  unknown: "未标记来源",
};

const eventLabels: Record<string, string> = {
  qr_open: "二维码链接打开",
  share_open: "分享链接打开",
  share_click: "点击分享",
  navigation_click: "点击导航",
  route_click: "路线规划",
  follow: "新增关注",
  stall_view: "摊位页面浏览",
  browse: "浏览发现",
  reorder: "再来一单",
  checkout_view: "进入结算",
};

const stats = computed(() => [
  {
    label: simulated.value ? "模拟到手净收入" : "到手分账净收入",
    value: props.metrics
      ? `¥${money(props.metrics.merchant_net_cents ?? props.metrics.revenue_cents)}`
      : "—",
    hint: "扣平台服务费后的商户净额",
    icon: Wallet,
  },
  {
    label: simulated.value ? "模拟顾客实付" : "顾客实付总额",
    value: props.metrics ? `¥${money(props.metrics.revenue_cents)}` : "—",
    hint: "所选时间内已付款总额",
    icon: ReceiptText,
  },
  {
    label: "下单量",
    value: props.metrics?.orders_created ?? "—",
    hint: "所选时间内创建订单数",
    icon: ShoppingBag,
  },
  {
    label: "关注人数",
    value: props.metrics?.followers ?? "—",
    hint: "当前累计收藏关注同学",
    icon: Users,
  },
]);

const maxSales = computed(() =>
  Math.max(
    1,
    ...(props.metrics?.top_products || []).map((p: any) => p.quantity),
  ),
);

const hasPaymentBreakdown = computed(
  () =>
    typeof props.metrics?.merchant_net_cents === "number" ||
    typeof props.metrics?.online_revenue_cents === "number",
);

function paymentRecordLabel(record: any) {
  const method =
    ((record.mode || props.metrics?.mode) === "simulation" ? "模拟 · " : "") +
    (record.payment_method === "wechat" ? "微信锁价预付" : "历史线下确认");
  if (record.payment_status === "refunded") return `${method} · 已原路退款`;
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
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `烟火地图-${simulated.value ? "模拟数据-" : ""}近${props.days}天经营日报.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
</script>

<template>
  <div class="m-analytics">
    <p v-if="simulated" class="m-info-banner">
      模拟经营数据 · 本页金额和导出日报用于流程体验，不代表真实收款、退款或收入。
    </p>

    <div class="m-page-brief">
      <p>
        <strong>本页用途：</strong>上方先核对你的<strong>收款账户与到手净收入</strong>，中间查看<strong>每一笔收款流水</strong>，下方按需浏览热销菜品与客流统计。
      </p>
      <details v-if="canSwitchMode" class="m-detail-toggle">
        <summary>
          当前展示：{{ simulated ? "模拟体验账本" : "正式经营账本" }} · 点此切换数据源
        </summary>
        <div class="m-mode-switch-box">
          <p>正式记录统计真实订单；模拟演练仅统计练习订单，两者互不混算。</p>
          <div class="m-segmented" role="group" aria-label="经营记录模式">
            <button
              type="button"
              :class="{ active: activeMode === 'live' }"
              :aria-pressed="activeMode === 'live'"
              :disabled="loading"
              @click="emit('mode', 'live')"
            >
              正式记录
            </button>
            <button
              type="button"
              :class="{ active: activeMode === 'simulation' }"
              :aria-pressed="activeMode === 'simulation'"
              :disabled="loading"
              @click="emit('mode', 'simulation')"
            >
              模拟演练
            </button>
          </div>
        </div>
      </details>
    </div>

    <p v-if="error" role="alert" class="m-alert">{{ error }}</p>
    <p v-if="loading" class="m-muted" role="status">正在更新经营数据…</p>

    <!-- 分类一：收款账户与到手收入 -->
    <section class="m-cat-group" aria-label="收款账户与到手收入">
      <div class="m-cat-head">
        <h2>收款账户与到手收入</h2>
        <p>登记钱打到哪个微信/银行卡，查看所选时间实赚净额</p>
      </div>

      <MerchantPayoutCard
        v-if="stall"
        :stall="stall"
        :discovery-only="discoveryOnly"
        @saved="emit('stallUpdated', $event)"
      />

      <div class="m-section-toolbar">
        <div class="m-segmented" role="group" aria-label="数据时间范围">
          <button
            v-for="period in [1, 7, 30]"
            :key="period"
            type="button"
            :class="{ active: days === period }"
            :aria-pressed="days === period"
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

      <div class="m-kpis">
        <article v-for="s in stats" :key="s.label" class="m-panel m-kpi">
          <span class="m-kpi-icon orange"><component :is="s.icon" :size="20" /></span>
          <span class="m-kpi-label">{{ s.label }}</span>
          <strong>{{ s.value }}</strong>
          <small>{{ s.hint }}</small>
        </article>
      </div>

      <section
        v-if="hasPaymentBreakdown"
        class="m-panel m-payment-breakdown-card"
        aria-label="分账结算明细"
      >
        <div class="m-payment-breakdown">
          <div>
            <span>商户分账净额</span>
            <strong>¥{{ money(metrics.merchant_net_cents ?? metrics.online_revenue_cents) }}</strong>
            <small>
              已完结到账 ¥{{ money(metrics.settled_net_cents || 0) }} · 制作/待取托管中 ¥{{
                money(metrics.escrow_net_cents || 0)
              }}
            </small>
          </div>
          <div>
            <span>平台技术服务费</span>
            <strong>¥{{ money(metrics.platform_fee_cents || 0) }}</strong>
            <small>
              当前费率
              {{
                ((metrics.effective_fee_bps ?? 500) / 100)
                  .toFixed(1)
                  .replace(/\.0$/, "")
              }}% · 订单完结自动分账
            </small>
          </div>
          <div v-if="typeof metrics.refund_cents === 'number'">
            <span>本期原路退款</span>
            <strong>¥{{ money(metrics.refund_cents) }}</strong>
            <small>全额退款单自动退回服务费，不扣商家钱</small>
          </div>
        </div>
        <details class="m-detail-toggle">
          <summary>查看账单计算口径与到账时间详细说明</summary>
          <div>
            按北京时间自然日统计。顾客下单付款后资金先由微信安全托管（显示在“制作/待取托管中”），当订单交餐完结后自动分账到你的收款账户（显示在“已完结到账”）。收款总额按付款确认时间统计；若后续发生退款，按退款完成日期单独统计并冲正服务费。
          </div>
        </details>
      </section>
    </section>

    <!-- 分类二：收款流水与每日趋势 -->
    <section class="m-cat-group" aria-label="收款流水与每日趋势">
      <div class="m-cat-head">
        <h2>收款流水与每日趋势</h2>
        <p>核对每一笔已付款订单，查看近期生意走势</p>
      </div>

      <section class="m-panel">
        <div class="m-panel-head">
          <div>
            <h2>最近收款记录</h2>
            <p class="m-muted">顾客完成微信预付的订单会自动列在这里，退款单会注明状态。</p>
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
              <strong>订单 #{{ r.number.slice(-8) }}</strong>
              <small>{{ formatTime(r.paid_at) }} · {{ paymentRecordLabel(r) }}</small>
            </div>
            <b>+ ¥{{ money(r.total_cents) }}</b>
          </div>
        </div>
      </section>

      <section class="m-panel">
        <div class="m-panel-head">
          <h2>营业收款趋势</h2>
          <span class="m-muted">每日收款总额（元）</span>
        </div>
        <MerchantTrend :series="metrics?.series || []" />
        <details v-if="metrics" class="m-detail-toggle">
          <summary>查看每日订单与收款明细表</summary>
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
    </section>

    <!-- 分类三：菜品销量、经营提醒与客流统计 -->
    <section class="m-cat-group" aria-label="菜品销量与客流分析">
      <div class="m-cat-head">
        <h2>菜品销量与客流分析</h2>
        <p>看看哪道菜最好卖、有没有漏取订单、同学从哪个入口找到你</p>
      </div>

      <div class="m-dashboard-columns">
        <section class="m-panel">
          <div class="m-panel-head">
            <h2>人气商品 <small>TOP 5</small></h2>
            <BarChart3 :size="18" />
          </div>
          <div v-if="!metrics?.top_products?.length" class="m-empty">
            <BarChart3 :size="30" />
            <p>已收款的订单会自动汇总成小摊人气榜。</p>
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
              <span>接单超时取消</span>
              <strong>{{ metrics?.expired_orders ?? "—" }} <small>单</small></strong>
            </div>
            <div>
              <span>出餐超 1 小时未取</span>
              <strong>{{ metrics?.uncollected_orders ?? "—" }} <small>单</small></strong>
            </div>
            <div>
              <span>在售商品 / 已售罄</span>
              <strong>
                {{ metrics?.products_active ?? "—" }} / {{ metrics?.products_sold_out ?? "—" }}
              </strong>
            </div>
            <div>
              <span>本期创建订单完成率</span>
              <strong>{{
                metrics?.order_completion_rate == null
                  ? "—"
                  : `${Math.round(metrics.order_completion_rate * 100)}%`
              }}</strong>
            </div>
          </div>
          <p class="m-muted">
            超过 1 小时未取建议联系同学确认；出餐满 2 小时无争议将自动完结分账。
          </p>
        </section>
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
          <span class="m-muted">所选期间 · {{ simulated ? "模拟记录" : "正式记录" }}</span>
        </div>
        <div class="returning-stats">
          <div>
            <strong>{{ metrics.returning_customer_count }}</strong>
            <span>再次完成订单的同学</span>
          </div>
          <div>
            <strong>{{ metrics.completed_customer_count }}</strong>
            <span>本期完成订单的同学</span>
          </div>
          <div>
            <strong>{{
              metrics.returning_customer_rate == null
                ? "—"
                : `${Math.round(metrics.returning_customer_rate * 100)}%`
            }}</strong>
            <span>本期完成顾客中的回头客占比</span>
          </div>
        </div>
        <details class="m-detail-toggle">
          <summary>查看回头客统计口径说明</summary>
          <div>
            {{
              metrics.metric_definitions?.returning_customers ||
              "本期完成订单的顾客中，曾在本摊位完成同模式订单的人数，按人去重；不代表整体留存率。"
            }}
          </div>
        </details>
      </section>

      <section v-if="Array.isArray(metrics?.source_counts)" class="m-panel merchant-sources">
        <div class="m-panel-head">
          <h2>同学从哪里找到你</h2>
          <span class="m-muted">扫码、地图与搜索等入口次数</span>
        </div>
        <div v-if="metrics.source_counts.length" class="source-counts">
          <div v-for="row in metrics.source_counts" :key="`${row.source}:${row.event_type}`">
            <span>
              {{ sourceLabels[row.source] || "其他来源" }}
              <small>{{ eventLabels[row.event_type] || "页面操作" }}</small>
            </span>
            <strong>{{ row.count }} <small>次</small></strong>
          </div>
        </div>
        <p v-else class="m-muted">所选期间还没有来源记录。</p>
        <p class="m-source-note">
          来源由客户端标记，次数不代表独立人数或真实扫码量；包含同一摊位的浏览记录。
        </p>
      </section>

      <details
        v-if="metrics?.pilot_evidence"
        class="m-panel m-more-details pilot-evidence"
      >
        <summary>
          <span class="pilot-summary-title">
            <ClipboardCheck :size="18" /> 试点经营与接单效率记录
          </span>
          <span>{{ simulated ? "模拟演练 · 点击展开" : "服务端核验记录 · 点击展开" }}</span>
        </summary>
        <section aria-label="试点经营记录">
          <div class="returning-stats">
            <div>
              <strong>{{ metrics.pilot_evidence.active_merchant_days }}</strong>
              <span>商户开摊天数</span>
            </div>
            <div>
              <strong>{{ metrics.pilot_evidence.completed_orders }}</strong>
              <span>实际完成订单</span>
            </div>
            <div>
              <strong>{{ metrics.pilot_evidence.paying_customers }}</strong>
              <span>已付款完单人数</span>
            </div>
            <div>
              <strong>{{ metrics.pilot_evidence.accept_p50_seconds ?? "—" }}</strong>
              <span>常见接单用时（中位数，秒）</span>
            </div>
            <div>
              <strong>{{ metrics.pilot_evidence.accept_p95_seconds ?? "—" }}</strong>
              <span>忙时接单用时（95% 订单内，秒）</span>
            </div>
            <div>
              <strong>
                {{ metrics.pilot_evidence.location_reports_confirmed }} /
                {{ metrics.pilot_evidence.location_reports_received }}
              </strong>
              <span>核实属实 / 收到的位置反馈</span>
            </div>
          </div>
          <p class="m-muted" style="margin-top: 12px">
            {{ metrics.pilot_evidence.definitions }} 接单样本
            {{ metrics.pilot_evidence.accept_sample_count }} 笔。
          </p>
        </section>
      </details>
    </section>
  </div>
</template>

<style scoped>
.m-mode-switch-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}
.m-mode-switch-box p {
  margin: 0;
  flex: 1 1 200px;
}
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
  font-size: 12.5px;
  line-height: 1.7;
  color: #7d6853;
  margin-bottom: 0;
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
.pilot-summary-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px !important;
  font-weight: 700 !important;
  color: #45372c !important;
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
.m-payment-breakdown-card {
  display: grid;
  gap: 8px;
}
.m-payment-breakdown {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 18px;
}
.m-payment-breakdown > div {
  display: flex;
  flex-direction: column;
  gap: 6px;
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
