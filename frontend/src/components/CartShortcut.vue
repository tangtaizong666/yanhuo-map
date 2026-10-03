<script setup lang="ts">
import { computed } from "vue";
import { ShoppingBag } from "lucide-vue-next";
import { useCart } from "../stores/cart";
import { useRoute } from "vue-router";

withDefaults(defineProps<{ compact?: boolean }>(), { compact: false });
const cart = useCart();
const route = useRoute();
const count = computed(() =>
  Object.values(cart.carts).reduce(
    (total, rows) => total + rows.reduce((sum, row) => sum + row.quantity, 0),
    0,
  ),
);
</script>

<template>
  <RouterLink
    v-if="count > 0 || !route.path.startsWith('/orders/')"
    to="/cart"
    class="cart-shortcut"
    :class="{ 'cart-shortcut-compact': compact, 'has-items': count > 0 }"
    :aria-label="
      count ? `我的餐袋，已选 ${count} 件餐点` : '我的餐袋，暂未选餐点'
    "
  >
    <ShoppingBag :size="20" aria-hidden="true" />
    <span class="cart-shortcut-label">餐袋</span>
    <span v-if="count" class="cart-shortcut-count" aria-hidden="true">{{
      count > 99 ? "99+" : count
    }}</span>
  </RouterLink>
</template>

<style scoped>
.cart-shortcut {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 44px;
  min-width: 44px;
  padding: 0 13px;
  border: 1px solid #eddfce;
  border-radius: 14px;
  background: #fffaf2;
  color: #68513e;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
  transition:
    background 160ms ease,
    border-color 160ms ease;
}
.cart-shortcut:hover,
.cart-shortcut.router-link-active {
  background: #fff0df;
  border-color: #e9b284;
}
.cart-shortcut.has-items {
  color: #a84818;
}
.cart-shortcut-count {
  min-width: 21px;
  height: 21px;
  padding: 0 5px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  color: #fff;
  background: #c8531c;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.cart-shortcut-compact {
  padding: 0 10px;
  gap: 5px;
}
.cart-shortcut-compact .cart-shortcut-label {
  display: none;
}
@media (prefers-reduced-motion: reduce) {
  .cart-shortcut {
    transition: none;
  }
}
</style>
