<script setup lang="ts">
import { computed } from "vue";
import { money } from "../../lib/api";
const props = defineProps<{ series: any[] }>();
const max = computed(() =>
  Math.max(100, ...props.series.map((d) => d.revenue_cents)),
);
const points = computed(() =>
  props.series
    .map(
      (d, i) =>
        `${40 + (i * 580) / Math.max(1, props.series.length - 1)},${162 - (d.revenue_cents / max.value) * 130}`,
    )
    .join(" "),
);
const labels = computed(() =>
  props.series.filter(
    (_, i) =>
      props.series.length <= 7 || i % 5 === 0 || i === props.series.length - 1,
  ),
);
</script>
<template>
  <div class="m-trend">
    <svg
      v-if="series.length"
      viewBox="0 0 660 205"
      role="img"
      aria-label="按付款确认日期统计的收款总额趋势，退款另计，具体数值见每日明细"
    >
      <defs>
        <linearGradient id="income-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#f78543" stop-opacity=".23" />
          <stop offset="100%" stop-color="#f78543" stop-opacity=".01" />
        </linearGradient>
      </defs>
      <g v-for="n in [0, 1, 2]" :key="n">
        <line
          x1="40"
          x2="620"
          :y1="32 + n * 65"
          :y2="32 + n * 65"
          stroke="#eee7dd"
          stroke-dasharray="4 5"
        />
        <text x="3" :y="36 + n * 65" fill="#88796a" font-size="11">
          {{ money((max * (2 - n)) / 2) }}
        </text>
      </g>
      <polygon :points="`40,162 ${points} 620,162`" fill="url(#income-fill)" />
      <polyline
        :points="points"
        fill="none"
        stroke="#e9712e"
        stroke-width="3"
        stroke-linejoin="round"
        stroke-linecap="round"
      />
      <circle
        v-for="(d, i) in series"
        :key="d.date"
        :cx="40 + (i * 580) / Math.max(1, series.length - 1)"
        :cy="162 - (d.revenue_cents / max) * 130"
        r="3.5"
        fill="#fff"
        stroke="#e9712e"
        stroke-width="2"
      >
        <title>{{ d.date }}：¥{{ money(d.revenue_cents) }}</title>
      </circle>
      <text
        v-for="d in labels"
        :key="d.date"
        :x="40 + (series.indexOf(d) * 580) / Math.max(1, series.length - 1)"
        y="190"
        text-anchor="middle"
        fill="#88796a"
        font-size="12"
      >
        {{ d.date.slice(5).replace("-", "/") }}
      </text>
    </svg>
    <p v-else class="muted">暂无收款记录，首笔款项确认后会显示在这里。</p>
  </div>
</template>
<style scoped>
.m-trend {
  width: 100%;
  min-width: 0;
}
.m-trend svg {
  width: 100%;
  height: auto;
  display: block;
}
.m-trend p {
  padding: 50px 12px;
  text-align: center;
}
</style>
