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
        <Utensils :size="32" :stroke-width="1.2" /><span>等待美味亮相</span>
      </div>
      <span v-if="product.sale_paused" class="dish-availability">暂停供应</span>
      <span
        v-else-if="product.availability === 'sold_out'"
        class="dish-availability"
        >线上售罄</span
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
      <p class="dish-stall">
        <Store :size="13" /><span>{{ stall.name }}</span>
      </p>
      <h3>{{ product.name }}</h3>
      <p class="dish-description">
        {{ product.description || "点开看看这份好味道" }}
      </p>
      <div class="dish-card-bottom">
        <strong>¥{{ money(product.price_cents) }}</strong
        ><span>查看餐点 <ArrowUpRight :size="13" /></span>
      </div>
      <p class="dish-preparation">
        <Clock3 :size="12" /><span
          >{{ preparationLabel(stall.prep_minutes)
          }}<small>接单后更新</small></span
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
  font-size: 11px;
  line-height: 1.6;
  color: #796047;
  margin: 12px 0 0;
}
.dish-preparation svg {
  flex: none;
  margin-top: 3px;
}
.dish-preparation small {
  display: block;
  font-size: 10px;
  color: #816e59;
}
.dish-card {
  display: block;
  min-width: 0;
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
  font-size: 11px;
  font-weight: 500;
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
}
.dish-stall span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dish-card h3 {
  margin-top: 6px;
  font-size: 18px;
  color: #3e3025;
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
  justify-content: space-between;
  gap: 5px;
  margin-top: 16px;
}
.dish-card-bottom strong {
  font-size: 20px;
  color: #b95422;
  font-variant-numeric: tabular-nums;
}
.dish-card-bottom > span {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: #83674f;
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
    font-size: 10px;
  }
  .dish-card-bottom > span {
    font-size: 10px;
  }
  .dish-card-bottom strong {
    font-size: 18px;
  }
  .dish-stall,
  .dish-description {
    font-size: 12px;
  }
  .dish-card-bottom { margin-top: 12px; }
  .dish-preparation { margin-top: 8px; font-size: 12px; }
  .dish-preparation small { font-size: 11px; }
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
