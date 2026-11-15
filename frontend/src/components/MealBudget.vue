<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { ChevronDown, SlidersHorizontal } from "lucide-vue-next";

const props = defineProps<{
  budget: number;
  sort: string;
  collapsible?: boolean;
}>();
defineEmits<{
  "update:budget": [value: number];
  "update:sort": [value: string];
}>();
const choices = [0, 1000, 1500, 2000];
const mql =
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(max-width: 760px)")
    : null;
const isMobile = ref(mql ? mql.matches : false);
const detailsOpen = ref(props.budget > 0 || props.sort !== "default");

function syncMedia(event: MediaQueryListEvent) {
  isMobile.value = event.matches;
  if (event.matches && (props.budget > 0 || props.sort !== "default")) {
    detailsOpen.value = true;
  }
}

watch(
  () => [props.budget, props.sort] as const,
  ([nextBudget, nextSort]) => {
    if (nextBudget > 0 || nextSort !== "default") {
      detailsOpen.value = true;
    }
  },
);

onMounted(() => mql?.addEventListener("change", syncMedia));
onUnmounted(() => mql?.removeEventListener("change", syncMedia));
</script>
<template>
  <details
    v-if="collapsible && isMobile"
    class="meal-budget meal-budget-collapsible"
    aria-label="餐点预算与排序"
    :open="detailsOpen"
    @toggle="detailsOpen = ($event.target as HTMLDetailsElement).open"
  >
    <summary class="meal-budget-summary">
      <SlidersHorizontal :size="15" />
      <span>餐费预算与排序</span>
      <strong>
        {{ budget ? `${budget / 100} 元以内` : "不限预算"
        }}{{ sort === "price" ? " · 价格从低到高" : "" }}
      </strong>
      <ChevronDown :size="15" class="meal-budget-chevron" />
    </summary>
    <div class="meal-budget-body">
      <div class="budget-options" role="group" aria-label="每份餐费预算">
        <span>每份餐费</span>
        <button
          v-for="value in choices"
          :key="value"
          type="button"
          :aria-pressed="budget === value"
          @click="$emit('update:budget', value)"
        >
          {{ value ? `${value / 100} 元以内` : "不限" }}
        </button>
      </div>
      <label class="meal-sort"
        >餐点排序<select
          aria-label="餐点排序"
          :value="sort"
          @change="
            $emit('update:sort', ($event.target as HTMLSelectElement).value)
          "
        >
          <option value="default">发现好味</option>
          <option value="price">价格从低到高</option>
        </select></label
      >
      <p>仅筛选餐点，不影响摊位列表；价格为每份餐费，不含配送费。</p>
    </div>
  </details>
  <div v-else class="meal-budget" aria-label="餐点预算与排序">
    <div class="budget-options" role="group" aria-label="每份餐费预算">
      <span>每份餐费</span>
      <button
        v-for="value in choices"
        :key="value"
        type="button"
        :aria-pressed="budget === value"
        @click="$emit('update:budget', value)"
      >
        {{ value ? `${value / 100} 元以内` : "不限" }}
      </button>
    </div>
    <label class="meal-sort"
      >餐点排序<select
        aria-label="餐点排序"
        :value="sort"
        @change="
          $emit('update:sort', ($event.target as HTMLSelectElement).value)
        "
      >
        <option value="default">发现好味</option>
        <option value="price">价格从低到高</option>
      </select></label
    >
    <p>仅筛选餐点，不影响摊位列表；价格为每份餐费，不含配送费。</p>
  </div>
</template>
<style scoped>
.meal-budget {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 14px;
  padding: 14px 16px;
  border: 1px solid #ebdfce;
  border-radius: 15px;
  background: #fffaf2;
  margin: 18px 0;
}
.meal-budget-collapsible {
  display: block;
  padding: 0 14px;
  margin: 12px 0 16px;
}
.meal-budget-summary {
  list-style: none;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 46px;
  cursor: pointer;
  color: #6c573f;
  font-size: 13px;
  font-weight: 600;
}
.meal-budget-summary::-webkit-details-marker {
  display: none;
}
.meal-budget-summary strong {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: #a74714;
  background: #fff0de;
  padding: 3px 9px;
  border-radius: 999px;
}
.meal-budget-chevron {
  flex-shrink: 0;
  color: #937b63;
  transition: transform 0.16s ease;
}
.meal-budget-collapsible[open] .meal-budget-chevron {
  transform: rotate(180deg);
}
.meal-budget-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 14px;
  padding: 10px 0 14px;
  border-top: 1px dashed #e7d8c3;
}
.budget-options {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.budget-options > span,
.meal-sort {
  font-size: 12px;
  color: #6c573f;
}
.budget-options button {
  min-height: 44px;
  padding: 7px 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  color: #6c573f;
  background: transparent;
  font-size: 13px;
  cursor: pointer;
}
.budget-options button[aria-pressed="true"] {
  background: #fff0de;
  color: #a74714;
  border-color: #ecc6a4;
  font-weight: 650;
}
.meal-sort {
  display: flex;
  align-items: center;
  gap: 8px;
}
.meal-sort select {
  min-height: 44px;
  max-width: 100%;
  padding: 8px 10px;
  color: #60442e;
  border: 1px solid #e4d3be;
  border-radius: 10px;
  background: #fff;
  font: inherit;
}
.meal-budget p {
  flex-basis: 100%;
  font-size: 11px;
  line-height: 1.6;
  color: #806d59;
  margin: 0;
}
@media (max-width: 500px) {
  .meal-budget {
    padding: 12px;
  }
  .budget-options {
    gap: 2px;
    width: 100%;
  }
  .budget-options > span {
    width: 100%;
    margin: 0 0 3px 4px;
  }
  .budget-options button {
    padding: 7px 10px;
  }
  .meal-sort {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
