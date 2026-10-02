<script setup lang="ts">
import { computed } from "vue";
import type { Portion } from "../lib/types";
import { portionText } from "../lib/portions";
const props = defineProps<{ portions?: Portion[] }>();
const rows = computed(() =>
  (props.portions || [])
    .map((portion, index) => ({ index, text: portionText(portion) }))
    .filter((row) => row.text),
);
</script>
<template>
  <ul v-if="rows.length" class="portion-order-summary">
    <li v-for="row in rows" :key="row.index">
      第 {{ row.index + 1 }} 份 · {{ row.text }}
    </li>
  </ul>
</template>
<style scoped>
.portion-order-summary {
  list-style: none;
  padding: 0;
  margin: 7px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: #755b42;
  overflow-wrap: anywhere;
}
</style>
