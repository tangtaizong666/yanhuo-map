<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowUpRight,
  ArrowRight,
  Wallet,
  ShoppingBag,
  PackageCheck,
  Star,
  Utensils,
  MapPin,
  MessageSquare,
  Clock3,
  ChefHat,
  CircleCheck,
  Store,
  Bike,
  ClipboardCheck,
} from "lucide-vue-next";
import {
  acceptReady,
  cancellableDeliveryStatuses,
  fulfillmentLabel,
} from "./delivery";
import { money, formatTime, statusText } from "../../lib/api";
import MerchantTrend from "./MerchantTrend.vue";
import { needsMerchantFollowUp } from "../../lib/orderFollowUp";
const props = defineProps<{
  stall: any;
  orders: any[];
  metrics: any;
  metricsError: string;
  ordersReady: boolean;
  syncError?: string;
}>();
const emit = defineEmits<{ navigate: [section: string, filter?: string] }>();
const pending = computed(() => props.orders.filter(acceptReady));
const followUp = computed(() => props.orders.filter(needsMerchantFollowUp));
const stats = computed(() => [
  {
    label:
      props.metrics?.mode === "simulation" ? "今日模拟收款" : "今日收款总额",
    value: props.metrics ? `¥${money(props.metrics.today.revenue_cents)}` : "—",
    note:
      props.metrics?.mode === "simulation"
        ? "练习金额，不涉及真实收款"
        : "到摊与微信确认，退款另计",
    icon: Wallet,
    color: "orange",
  },
  {
    label: "今日订单",
    value: props.metrics?.today.orders_created ?? "—",
    note: "按下单日期统计",
    icon: ShoppingBag,
    color: "green",
  },
  {
    label: "今日完成",
    value: props.metrics?.today.orders_completed ?? "—",
    note: "已付款且顾客已取餐 / 收餐",
    icon: PackageCheck,
    color: "blue",
  },
  {
    label: "顾客评分",
    value: props.stall.rating ?? "暂无",
    note: `${props.stall.review_count} 条订单评价`,
    icon: Star,
    color: "gold",
  },
]);
const queues = computed(() => [
  {
    label: "待接单",
    key: "pending",
    icon: ShoppingBag,
    count: pending.value.length,
  },
  {
    label: "制作中",
    key: "preparing",
    icon: ChefHat,
    count: props.orders.filter((o) => o.status === "preparing").length,
  },
  {
    label: "已出餐",
    key: "ready",
    icon: PackageCheck,
    count: props.orders.filter((o) => o.status === "ready").length,
  },
  {
    label: "配送中",
    key: "delivering",
    icon: Bike,
    count: props.orders.filter((o) => o.status === "delivering").length,
  },
  {
    label: "待收餐",
    key: "arrived",
    icon: MapPin,
    count: props.orders.filter((o) => o.status === "arrived").length,
  },
  {
    label: "取消申请",
    key: "cancellation",
    icon: Clock3,
    count: props.orders.filter(
      (o) =>
        o.cancel_requested && cancellableDeliveryStatuses.includes(o.status),
    ).length,
  },
]);
const recent = computed(() => props.orders.slice(0, 4));
</script>
<template>
  <div class="m-dashboard">
    <p v-if="metrics?.mode === 'simulation'" class="m-info-banner">
      模拟经营中 · 收款与送餐均为练习，可在「店铺」开启线上支付和外卖。
    </p>
    <section class="m-welcome">
      <div>
        <span class="m-eyebrow">每一份认真，都有回响</span>
        <h2>好生意，从今天这摊开始<span>。</span></h2>
        <p>守好一方烟火，照顾每一份期待。</p>
        <button @click="emit('navigate', 'store')">
          管理出摊状态 <ArrowUpRight :size="17" />
        </button>
      </div>
      <div class="m-welcome-art">
        <img :src="stall.image" :alt="stall.name + '美食照片'" /><span
          ><Store :size="14" /> 小摊有烟火，好味有人情</span
        >
      </div>
    </section>
    <p v-if="metricsError" class="m-alert" role="alert">
      经营数据暂时未更新：{{ metricsError }}
    </p>
    <div class="m-kpis">
      <article v-for="stat in stats" :key="stat.label" class="m-panel m-kpi">
        <span :class="['m-kpi-icon', stat.color]"
          ><component :is="stat.icon" :size="20" /></span
        ><span class="m-kpi-label">{{ stat.label }}</span
        ><strong>{{ stat.value }}</strong
        ><small>{{ stat.note }}</small>
      </article>
    </div>
    <section class="m-panel m-workqueue">
      <div class="m-panel-head">
        <h2>待办事项 <span class="m-live-dot"></span></h2>
        <span class="m-muted">每 10 秒同步最新订单</span>
      </div>
      <div class="m-queue-grid">
        <button
          v-for="item in queues"
          :key="item.key"
          @click="emit('navigate', 'orders', item.key)"
        >
          <component :is="item.icon" :size="22" /><span>{{ item.label }}</span
          ><strong :class="{ highlight: item.count > 0 }">{{
            item.count
          }}</strong
          ><ArrowRight :size="16" />
        </button>
      </div>
      <div class="m-followup-queue" data-testid="merchant-followup-queue">
        <div class="m-followup-copy">
          <span class="m-followup-icon"><ClipboardCheck :size="23" /></span>
          <div>
            <h3>
              售后跟进 <span>{{ ordersReady ? followUp.length : "—" }}</span>
            </h3>
            <p>
              {{
                !ordersReady
                  ? "尚未同步订单，请先刷新工作台。"
                  : syncError
                    ? "当前为上次同步结果，联网后请刷新确认。"
                    : followUp.length
                      ? "退款与异常单集中在这里，已取消、已完成的订单也不会遗漏。"
                      : "目前没有待跟进事项。新的退款或异常会出现在这里。"
              }}
            </p>
          </div>
        </div>
        <button
          class="btn btn-secondary"
          :disabled="!ordersReady"
          @click="emit('navigate', 'orders', 'followup')"
        >
          查看售后 <ArrowRight :size="16" />
        </button>
      </div>
    </section>
    <div class="m-dashboard-columns">
      <section class="m-panel">
        <div class="m-panel-head">
          <div>
            <h2>收款趋势</h2>
            <p class="m-muted">近 7 天 · 收款总额，退款另计 · 元</p>
          </div>
          <button class="m-text-link" @click="emit('navigate', 'analytics')">
            数据详情 <ArrowUpRight :size="15" />
          </button>
        </div>
        <MerchantTrend :series="metrics?.series || []" />
      </section>
      <section class="m-panel">
        <div class="m-panel-head">
          <h2>常用功能</h2>
          <span class="m-overline">好生意，更顺手</span>
        </div>
        <div class="m-shortcuts">
          <button @click="emit('navigate', 'products')">
            <span><Utensils :size="22" /></span><b>商品管理</b
            ><small>菜单与库存</small></button
          ><button @click="emit('navigate', 'store')">
            <span><MapPin :size="22" /></span><b>经营服务</b
            ><small>支付、外卖与营业</small></button
          ><button @click="emit('navigate', 'reviews')">
            <span><MessageSquare :size="22" /></span><b>顾客评价</b
            ><small>听听大家说</small></button
          ><RouterLink :to="`/stalls/${stall.id}`"
            ><span><Store :size="22" /></span><b>预览摊位</b
            ><small>看看学生视角</small></RouterLink
          >
        </div>
      </section>
    </div>
    <section class="m-panel">
      <div class="m-panel-head">
        <h2>
          最近订单 <span class="m-count">{{ orders.length }}</span>
        </h2>
        <button class="m-text-link" @click="emit('navigate', 'orders')">
          全部订单 <ArrowUpRight :size="15" />
        </button>
      </div>
      <div v-if="!recent.length" class="m-empty">
        <ShoppingBag :size="32" />
        <h3>小摊已准备好，等待第一份期待</h3>
        <p>学生提交订单后，会自动出现在这里。配送订单需付款后接单。</p>
      </div>
      <div v-else class="m-recent-list">
        <button
          v-for="order in recent"
          :key="order.id"
          @click="emit('navigate', 'orders', 'all')"
        >
          <img :src="order.items[0]?.image || stall.image" alt="" />
          <div>
            <strong>{{ order.items.map((i: any) => i.name).join("、") }}</strong
            ><small
              >{{ fulfillmentLabel(order) }} · #{{ order.number.slice(-8) }} ·
              {{ formatTime(order.created_at) }}</small
            >
          </div>
          <span :class="['m-status', order.status]">{{
            statusText(order.status, order.fulfillment_type)
          }}</span
          ><b>¥{{ money(order.total_cents) }}</b
          ><ArrowUpRight :size="16" />
        </button>
      </div>
    </section>
    <p class="m-page-footnote">
      <CircleCheck :size="14" />
      收款来自到摊确认和微信支付结果；退款单独统计。微信付款成功不代表已结算到银行卡。
    </p>
    <p
      v-if="
        typeof metrics?.today.offline_revenue_cents === 'number' &&
        typeof metrics?.today.online_revenue_cents === 'number'
      "
      class="m-page-footnote"
    >
      今日到摊收款 ¥{{ money(metrics.today.offline_revenue_cents) }} · 微信付款
      ¥{{ money(metrics.today.online_revenue_cents) }}
      <template v-if="typeof metrics.today.refund_cents === 'number'">
        · 今日退款 ¥{{ money(metrics.today.refund_cents) }}
      </template>
    </p>
  </div>
</template>
<style scoped>
.m-dashboard {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.m-dashboard > .m-workqueue {
  order: -1;
}
.m-dashboard > .m-welcome {
  display: none;
}
.m-followup-queue {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 22px;
  padding-top: 22px;
  border-top: 1px solid #e9dece;
}
.m-followup-copy {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.m-followup-icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: #fbefd9;
  color: #ad682f;
}
.m-followup-copy h3 {
  margin: 0;
  font-size: 15px;
  line-height: 1.7;
}
.m-followup-copy h3 span {
  margin-left: 7px;
  color: #9f552c;
  font-variant-numeric: tabular-nums;
}
.m-followup-copy p {
  margin: 5px 0 0;
  color: #7b6652;
  font-size: 12px;
  line-height: 1.8;
}
.m-followup-queue > button {
  flex-shrink: 0;
  min-height: 44px;
}
.m-queue-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.m-queue-grid > button {
  min-height: 44px;
}
.m-queue-grid > button:nth-child(3n) {
  border-right: 0;
}
@media (max-width: 700px) {
  .m-followup-queue {
    flex-wrap: wrap;
    gap: 14px;
  }
  .m-followup-queue > button {
    width: 100%;
  }
  .m-queue-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 16px;
  }
  .m-queue-grid > button {
    min-height: 62px;
  }
  .m-queue-grid > button:nth-child(odd) {
    border-right: 1px solid var(--m-border);
  }
  .m-queue-grid > button:nth-child(even) {
    border-right: 0;
  }
  .m-queue-grid > button > span {
    font-size: 12px;
  }
}
</style>
