<script setup lang="ts">
import { Heart, Star, MapPin, Clock3, ArrowUpRight } from "lucide-vue-next";
import { confirmedText, statusText, money } from "../lib/api";
import type { StallSummary as Stall } from "../lib/types";
import { productAvailable, preparationLabel } from "../lib/availability";
import ReceivingNotice from "./ReceivingNotice.vue";
defineProps<{ stall: Stall; compact?: boolean; followBusy?: boolean }>();
defineEmits<{ follow: [stall: Stall] }>();
</script>
<template>
  <article :class="['stall-card', { compact }]">
    <RouterLink :to="`/stalls/${stall.id}`" class="stall-photo"
      ><img
        :src="stall.image"
        :alt="stall.name + '美食配图'"
        loading="lazy"
        width="640"
        height="420"
      /><span :class="['badge', stall.status]">{{
        statusText(stall.status)
      }}</span></RouterLink
    ><button
      :class="['follow-button', { followed: stall.is_followed }]"
      :disabled="followBusy"
      :aria-busy="!!followBusy"
      :aria-pressed="stall.is_followed"
      :aria-label="
        stall.is_followed ? '取消关注' + stall.name : '关注' + stall.name
      "
      @click="$emit('follow', stall)"
    >
      <Heart :size="17" :fill="stall.is_followed ? 'currentColor' : 'none'" />
    </button>
    <div class="stall-body">
      <div class="stall-head-info">
      <RouterLink :to="`/stalls/${stall.id}`" class="stall-title"
        ><h3>{{ stall.name }}</h3>
        <ArrowUpRight :size="18"
      /></RouterLink>
      <div class="stall-rating">
        <span class="rating"
          ><Star :size="12" fill="currentColor" />{{
            Number(stall.rating) > 0 ? Number(stall.rating).toFixed(1) : "新摊"
          }}</span
        ><span>{{ stall.category }}</span
        ><span v-if="stall.review_count">{{ stall.review_count }} 条评价</span
        ><span v-if="stall.distance_m != null" class="distance">{{
          stall.distance_m >= 1000
            ? (stall.distance_m / 1000).toFixed(1) + "km"
            : Math.round(stall.distance_m) + "m"
        }}</span>
      </div>
      <p class="stall-desc">{{ stall.description }}</p>
      <div class="stall-address">
        <MapPin :size="12" /><span>{{ stall.address }}</span>
      </div>
      <p class="prep-summary">
        <Clock3 :size="12" />{{ preparationLabel(stall.prep_minutes)
        }}<small>接单后更新</small>
      </p>
      </div>
      <div
        v-if="!compact && stall.products.some(productAvailable)"
        class="stall-menu-peek"
        aria-label="看看餐点"
      >
        <RouterLink
          v-for="product in stall.products.filter(productAvailable).slice(0, 2)"
          :key="product.id"
          :to="`/stalls/${stall.id}/products/${product.id}`"
          :aria-label="`查看${product.name}详情`"
          ><span>{{ product.name }}</span
          ><b>¥{{ money(product.price_cents) }}</b></RouterLink
        >
      </div>
      <div class="stall-bottom">
        <span
          :class="['fulfillment', { disabled: !stall.can_order }]"
          :title="stall.order_unavailable_reason"
          >{{
            stall.can_order
              ? "可线上点单"
              : stall.status === "open" && stall.accepting_orders === false
                ? "线上接单暂停"
                : !stall.transaction_enabled
                  ? "到摊选购"
                  : "暂不可点单"
          }}</span
        ><span class="freshness"
          ><Clock3 :size="11" />{{
            confirmedText(stall.last_confirmed_at)
          }}</span
        >
      </div>
      <ReceivingNotice class="stall-receiving" :stall="stall" compact />
    </div>
  </article>
</template>
<style scoped>
.prep-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  font-size: 12px;
  line-height: 1.7;
  color: #725b43;
  margin: 8px 0;
}
.prep-summary small {
  font-size: 10px;
  color: #806c58;
}
.stall-card {
  position: relative;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: #fff;
  overflow: hidden;
  transition:
    transform 0.25s,
    box-shadow 0.25s;
}
.stall-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 12px 34px #48301510;
}
.stall-photo {
  height: 188px;
  display: block;
  position: relative;
  overflow: hidden;
  background: #f3e6d4;
}
.stall-photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.5s;
}
.stall-card:hover .stall-photo img {
  transform: scale(1.035);
}
.stall-photo:after {
  content: "";
  position: absolute;
  inset: 55% 0 0;
  background: linear-gradient(transparent, #241b1622);
}
.stall-photo .badge {
  position: absolute;
  left: 14px;
  top: 14px;
  z-index: 1;
  backdrop-filter: blur(12px);
  background: #fffdf8ec;
  padding: 5px 9px;
  font-size: 11px;
  box-shadow: 0 2px 8px #00000006;
}
.follow-button {
  position: absolute;
  right: 13px;
  top: 12px;
  z-index: 2;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #fffdf8ec;
  color: #756c60;
  backdrop-filter: blur(12px);
}
.follow-button.followed {
  color: #d16a34;
}
.stall-body {
  padding: 19px 18px 15px;
}
.stall-menu-peek {
  display: flex;
  gap: 7px;
  margin-top: 10px;
}
.stall-menu-peek a {
  display: flex;
  align-items: center;
  gap: 5px;
  min-height: 44px;
  min-width: 0;
  padding: 7px 8px;
  border-radius: 7px;
  background: #fcf4e9;
  color: #75553b;
  font-size: 11px;
  flex: 1;
}
.stall-menu-peek a:hover {
  background: #f7e6d2;
}
.stall-menu-peek span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stall-menu-peek b {
  font-weight: 600;
  flex-shrink: 0;
  color: #ab5528;
}
.stall-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.stall-title svg {
  color: #c1b5a5;
}
.stall-rating {
  display: flex;
  gap: 9px;
  align-items: center;
  font-size: 11px;
  color: #968878;
  margin-top: 8px;
}
.stall-rating > span:not(:first-child):not(.distance) {
  border-left: 1px solid #e5dbce;
  padding-left: 9px;
}
.stall-rating .rating {
  display: flex;
  gap: 3px;
  align-items: center;
  color: #be611f;
  font-weight: 700;
  font-size: 12px;
}
.distance {
  margin-left: auto;
}
.stall-desc {
  font-size: 12px;
  color: #8a8278;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 11px;
}
.stall-address {
  font-size: 11px;
  color: #a09484;
  display: flex;
  gap: 5px;
  align-items: center;
  margin-top: 7px;
  min-width: 0;
}
.stall-address span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stall-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px dashed #ede5da;
  padding-top: 12px;
  margin-top: 14px;
  gap: 5px;
}
.fulfillment {
  font-size: 11px;
  border: 1px solid #f1dcc5;
  border-radius: 4px;
  padding: 2px 5px;
  color: #aa713e;
  background: #fffcf7;
  white-space: nowrap;
}
.fulfillment.disabled {
  color: #8c857c;
  border-color: #e7e2da;
  background: #f8f6f2;
}
.freshness {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 11px;
  color: #948a7b;
  white-space: nowrap;
}
.compact {
  display: grid;
  grid-template-columns: 112px 1fr;
  overflow: visible;
  background: #fff;
  border-radius: 14px;
  padding: 13px;
  gap: 13px;
}
.compact .stall-photo {
  height: 112px;
  border-radius: 10px;
}
.compact .stall-body {
  padding: 0;
  min-width: 0;
}
.compact .stall-photo .badge {
  top: 6px;
  left: 6px;
  font-size: 11px;
  padding: 3px 5px;
}
.compact .follow-button {
  top: auto;
  bottom: 13px;
  right: 12px;
  width: 44px;
  height: 44px;
}
.compact .stall-title h3 {
  font-size: 16px;
}
.compact .stall-title > svg {
  display: none;
}
.compact .stall-desc {
  display: none;
}
.compact .stall-bottom {
  padding-top: 8px;
  margin-top: 8px;
  padding-right: 38px;
  flex-wrap: wrap;
  border: 0;
}
.compact .stall-address {
  margin-top: 10px;
}
.compact .freshness {
  font-size: 11px;
}
.compact .fulfillment {
  display: inline-flex;
}
@media (max-width: 767px) {
  .stall-card {
    display: grid;
    grid-template-columns: 112px 1fr;
    padding: 12px;
    gap: 13px;
    border-radius: 15px;
    overflow: visible;
  }
  .stall-photo {
    height: 112px;
    min-height: 0;
    border-radius: 10px;
  }
  .stall-photo .badge {
    top: 6px;
    left: 6px;
    font-size: 12px;
    padding: 4px 5px;
  }
  .stall-body { display: contents; }
  .stall-head-info { min-width: 0; }
  .stall-menu-peek, .stall-bottom, .stall-receiving { grid-column: 1 / -1; }
  .stall-menu-peek { margin-top: 0; }
  .stall-desc { display: none; }
  .prep-summary { font-size: 12px; margin: 7px 0 0; }
  .prep-summary small { display: none; }
  .stall-bottom { margin-top: 0; padding-top: 0; }
  .stall-title h3 {
    font-size: 16px;
  }
  .stall-title > svg {
    display: none;
  }
  .follow-button {
    width: 44px;
    height: 44px;
    top: auto;
    bottom: 11px;
    right: 10px;
    background: none;
    color: #b9aa98;
  }
  .stall-rating {
    margin-top: 7px;
    font-size: 12px;
    gap: 6px;
  }
  .stall-rating > span:not(:first-child):not(.distance) {
    padding-left: 6px;
  }
  .stall-desc {
    margin-top: 7px;
    font-size: 12px;
  }
  .stall-address {
    font-size: 12px;
    margin-top: 5px;
  }
  .stall-bottom {
    flex-wrap: wrap;
    padding-top: 8px;
    margin-top: 8px;
    justify-content: flex-start;
    padding-right: 38px;
    gap: 7px;
    border-top: 0;
  }
  .freshness {
    font-size: 12px;
  }
  .fulfillment {
    font-size: 12px;
    padding: 1px 3px;
  }
  .compact {
    grid-template-columns: 90px 1fr;
  }
  .compact .stall-photo {
    height: 90px;
    min-height: 0;
  }
  .compact .stall-bottom {
    padding-right: 18px;
  }
  .stall-menu-peek {
    gap: 5px;
    margin-top: 6px;
  }
  .stall-menu-peek a {
    padding: 5px;
    font-size: 12px;
  }

}
@media (max-width: 370px) {
  .stall-card {
    grid-template-columns: 98px 1fr;
    gap: 10px;
    padding: 10px;
  }
  .stall-photo {
    height: 98px;
  }
  .stall-rating > span:nth-child(3) {
    display: none;
  }
  .stall-bottom .fulfillment {
    display: none;
  }
}
</style>
