<script setup lang="ts">
import { ref, watch } from "vue";
import { ArrowUpRight, Store, Utensils, Clock3 } from "lucide-vue-next";
import { money, statusText } from "../lib/api";
import type { Product, StallMap as Stall } from "../lib/types";
import { preparationLabel } from "../lib/availability";
const props = defineProps<{ product: Product; stall: Stall }>();
const imageFailed = ref(false);
watch(
  () => props.product.image,
  () => {
    imageFailed.value = false;
  },
);
</script>

<template>
  <RouterLink
    :to="`/stalls/${stall.id}/products/${product.id}`"
    class="dish-card"
    :aria-label="`查看${product.name}详情，${stall.name}`"
  >
    <div class="dish-card-photo">
      <img
        v-if="product.image && !imageFailed"
        :src="product.image"
        :alt="product.name"
        loading="lazy"
        width="560"
        height="440"
        @error="imageFailed = true"
      />
      <div v-else class="dish-photo-placeholder">
        <Utensils :size="32" :stroke-width="1.2" /><span>暂无餐点照片</span>
      </div>
      <span v-if="product.sale_paused" class="dish-availability">暂停供应</span>
      <span
        v-else-if="product.availability === 'sold_out'"
        class="dish-availability"
        >{{ product.display_only ? "今天卖完了" : "线上售罄" }}</span
      >
      <span v-else-if="stall.status !== 'open'" class="dish-availability">{{
        statusText(stall.status)
      }}</span>
      <span v-else-if="!stall.can_order" class="dish-availability">{{
        stall.accepting_orders === false
          ? "到摊选购 · 线上暂停"
          : stall.transaction_enabled
            ? "暂不可点单"
            : "到摊选购"
      }}</span>
      <span v-else class="dish-availability available">可以点单 · 自取</span>
      <span class="dish-open" aria-hidden="true"
        ><ArrowUpRight :size="19"
      /></span>
    </div>
    <div class="dish-card-body">
      <h3>{{ product.name }}</h3>
      <div class="dish-card-bottom">
        <strong>¥{{ money(product.price_cents) }}</strong>
      </div>
      <p class="dish-stall">
        <Store :size="13" /><span>{{ stall.name }}</span>
      </p>
      <p v-if="product.description?.trim()" class="dish-description">
        {{ product.description }}
      </p>
      <p class="dish-preparation">
        <Clock3 :size="12" /><span
          ><span class="dish-preparation-duration">{{
            preparationLabel(stall.prep_minutes)
          }}</span
          ><small>接单后更新</small></span
        >
      </p>
    </div>
  </RouterLink>
</template>

<style scoped>
.dish-preparation {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  font-size: 12px;
  line-height: 1.6;
  color: #796047;
  margin: 8px 0 0;
}
.dish-preparation svg {
  flex: none;
  margin-top: 3px;
}
.dish-preparation > span {
  display: flex;
  flex-wrap: wrap;
  gap: 0 6px;
  min-width: 0;
}
.dish-preparation-duration {
  white-space: nowrap;
}
.dish-preparation small {
  font: inherit;
  color: inherit;
  white-space: nowrap;
}
.dish-card {
  display: block;
  min-width: 0;
  min-height: 44px;
  background: #fffdf9;
  border: 1px solid #eae0d3;
  border-radius: 18px;
  overflow: hidden;
  transition:
    transform 0.2s,
    box-shadow 0.2s;
}
.dish-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 12px 30px #51331910;
}
.dish-card:focus-visible {
  outline: 3px solid #ad552b;
  outline-offset: 3px;
}
.dish-card-photo {
  position: relative;
  aspect-ratio: 1.3;
  background: #eee4d8;
  overflow: hidden;
}
.dish-card-photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.35s;
}
.dish-photo-placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #f4e8d7;
  color: #927457;
  font-size: 12px;
}
.dish-card:hover img {
  transform: scale(1.045);
}
.dish-availability {
  position: absolute;
  top: 12px;
  left: 12px;
  border-radius: 7px;
  color: #5e5347;
  background: #fffaf0ed;
  padding: 5px 9px;
  font-size: 12px;
  font-weight: 500;
  max-width: calc(100% - 24px);
}
.dish-availability.available {
  color: #355e41;
}
.dish-open {
  position: absolute;
  bottom: 12px;
  right: 12px;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #754722;
  background: #fffdf2ed;
}
.dish-card-body {
  padding: 17px;
}
.dish-stall {
  display: flex;
  align-items: center;
  gap: 5px;
  color: #80705f;
  font-size: 12px;
  margin: 8px 0 0;
}
.dish-stall svg {
  flex: none;
}
.dish-stall span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dish-card h3 {
  margin: 0;
  font-size: 18px;
  color: #3e3025;
  line-height: 1.4;
  min-height: 2.8em;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.dish-description {
  font-size: 12px;
  margin-top: 6px;
  color: #7c7063;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dish-card-bottom {
  display: flex;
  align-items: center;
  margin-top: 6px;
}
.dish-card-bottom strong {
  font-size: 20px;
  color: #b95422;
  font-variant-numeric: tabular-nums;
}
@media (max-width: 767px) {
  .dish-card {
    border-radius: 13px;
  }
  .dish-card-body {
    padding: 12px;
  }
  .dish-card h3 {
    font-size: 16px;
    line-height: 1.4;
    overflow-wrap: anywhere;
  }
  .dish-card-photo {
    aspect-ratio: 1.4;
  }
  .dish-availability {
    top: 8px;
    left: 8px;
    padding: 4px 6px;
    font-size: 12px;
    max-width: calc(100% - 16px);
  }
  .dish-card-bottom strong {
    font-size: 18px;
  }
  .dish-stall,
  .dish-description {
    font-size: 12px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .dish-card,
  .dish-card-photo img {
    transition: none;
  }
  .dish-card:hover,
  .dish-card:hover img {
    transform: none;
  }
}
</style>
