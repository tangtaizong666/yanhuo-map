<script setup lang="ts">
import { Bike, Store, MapPin, Clock3, Info } from "lucide-vue-next";
import { computed } from "vue";
import { money } from "../lib/api";
import type { DeliverySettings } from "../lib/types";
const props = defineProps<{
  modelValue: "pickup" | "delivery";
  delivery?: DeliverySettings;
  pointId: number | null;
  subtotal: number;
  busy: boolean;
}>();
const pickupOnly = computed(
  () =>
    props.modelValue === "pickup" &&
    !props.delivery?.available &&
    props.delivery?.mode !== "simulation",
);
defineEmits<{
  "update:modelValue": [value: "pickup" | "delivery"];
  "update:pointId": [value: number | null];
}>();
</script>

<template>
  <section class="card fulfillment-card" aria-labelledby="fulfillment-heading">
    <header>
      <h2 id="fulfillment-heading">
        {{ pickupOnly ? "到摊自取" : "这份好味道，怎么收？" }}
      </h2>
    </header>
    <div
      v-if="!pickupOnly"
      class="fulfillment-options"
      role="group"
      aria-label="选择取餐方式"
    >
      <button
        type="button"
        :aria-pressed="modelValue === 'pickup'"
        :class="{ selected: modelValue === 'pickup' }"
        :disabled="busy"
        @click="$emit('update:modelValue', 'pickup')"
      >
        <Store :size="22" /><span
          ><strong>到摊自取</strong><small>免配送费 · 出餐后付款</small></span
        >
      </button>
      <button
        type="button"
        :aria-pressed="modelValue === 'delivery'"
        :class="{ selected: modelValue === 'delivery' }"
        :disabled="busy"
        @click="$emit('update:modelValue', 'delivery')"
      >
        <Bike :size="22" /><span
          ><strong
            >商家配送{{
              delivery?.mode === "simulation" ? " · 模拟" : ""
            }}</strong
          ><small>{{
            delivery?.available
              ? delivery.mode === "simulation"
                ? "演练送达流程 · 不会实际送货"
                : "送至校园指定交接点"
              : "暂未开放 · 查看说明"
          }}</small></span
        >
      </button>
    </div>
    <slot />
    <details v-if="pickupOnly" class="pickup-delivery-explanation">
      <summary>配送暂未开放 · 查看原因</summary>
      <p>{{ delivery?.reason || "商家尚未开放配送，可继续选择到摊自取。" }}</p>
    </details>
    <template v-if="modelValue === 'delivery'">
      <div
        v-if="!delivery?.available"
        class="delivery-unavailable"
        role="status"
      >
        <Info :size="19" />
        <div>
          <strong>这家小摊暂时不能配送</strong>
          <p>
            {{ delivery?.reason || "商家尚未开放配送，可继续选择到摊自取。" }}
          </p>
          <details>
            <summary>查看配送说明</summary>
            <p>
              {{
                delivery?.mode === "simulation"
                  ? "请商家在经营服务中同时开启模拟线上支付与配送。"
                  : "配送需要先用微信付款，开通后才能提交配送订单。"
              }}
            </p>
          </details>
        </div>
      </div>
      <template v-if="delivery?.enabled">
        <div class="delivery-facts">
          <span
            >配送费 <b>¥{{ money(delivery.fee_cents) }}</b></span
          ><span
            >餐费满 <b>¥{{ money(delivery.min_order_cents) }}</b> 起送</span
          ><span
            ><Clock3 :size="14" />{{ delivery.starts_at }}—{{
              delivery.ends_at
            }}</span
          >
        </div>
        <label class="field"
          >校园交接点<select
            class="input"
            :value="pointId ?? ''"
            :disabled="busy"
            @change="
              $emit(
                'update:pointId',
                Number(($event.target as HTMLSelectElement).value) || null,
              )
            "
          >
            <option value="" disabled>请选择交接点</option>
            <option
              v-for="point in delivery.points"
              :key="point.id"
              :value="point.id"
            >
              {{ point.name }} · {{ point.address }}
            </option>
          </select></label
        >
        <p v-if="!delivery.points.length" class="fulfillment-note">
          该商家还没有可用的校园交接点。
        </p>
        <p class="fulfillment-note">
          <MapPin :size="15" />仅送至所选交接点，请到点收餐。预计下单后
          {{ delivery.eta_min_minutes }}—{{ delivery.eta_max_minutes }}
          分钟送达，付款或接单延迟可能顺延，以实际进度为准。
        </p>
        <p
          v-if="subtotal < delivery.min_order_cents"
          class="minimum-notice"
          role="status"
        >
          餐费还差 ¥{{ money(delivery.min_order_cents - subtotal) }}
          达到起送金额。
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.fulfillment-card {
  padding: 22px 25px;
}
.pickup-delivery-explanation {
  color: #78634c;
  font-size: 12px;
}
.pickup-delivery-explanation summary {
  display: flex;
  align-items: center;
  min-height: 44px;
  cursor: pointer;
  text-decoration: underline;
}
.pickup-delivery-explanation p {
  margin: 0;
  line-height: 1.8;
}
.eyebrow {
  font-size: 10px;
  letter-spacing: 1.5px;
  color: #956d46;
}
h2 {
  font-size: 19px;
  margin: 0 0 16px;
}
.fulfillment-options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.fulfillment-options button {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px;
  background: #fffdf8;
  border: 1px solid #e5ded2;
  border-radius: 13px;
  color: #756c5f;
  text-align: left;
  min-height: 64px;
}
.fulfillment-options button.selected {
  border-color: #d96f32;
  color: #b55720;
  background: #fff2e3;
  box-shadow: inset 0 0 0 1px #d96f32;
}
.fulfillment-options svg {
  flex: none;
}
.fulfillment-options strong {
  display: block;
  font-size: 14px;
}
.fulfillment-options small {
  display: block;
  font-size: 11px;
  margin-top: 6px;
  line-height: 1.6;
}
.delivery-unavailable {
  display: flex;
  gap: 10px;
  padding: 17px;
  background: #f7f1e6;
  border-radius: 12px;
  margin-top: 18px;
  color: #765938;
}
.delivery-unavailable svg {
  flex: none;
  margin-top: 2px;
}
.delivery-unavailable strong {
  font-size: 13px;
}
.delivery-unavailable p {
  font-size: 12px;
  margin: 7px 0 0;
  line-height: 1.8;
}
.delivery-unavailable summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  font-size: 12px;
  cursor: pointer;
  text-decoration: underline;
}
.delivery-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  font-size: 12px;
  color: #746955;
  padding: 20px 0 4px;
}
.delivery-facts span {
  display: flex;
  gap: 5px;
  align-items: center;
}
.field {
  margin-top: 18px;
}
.fulfillment-note {
  display: flex;
  gap: 6px;
  font-size: 12px;
  color: #746955;
  line-height: 1.8;
}
.fulfillment-note svg {
  flex: none;
  margin-top: 3px;
}
.minimum-notice {
  color: #a94f21;
  font-size: 12px;
  margin-bottom: 0;
}
@media (max-width: 600px) {
  .fulfillment-card {
    padding: 18px;
  }
  .fulfillment-options {
    gap: 9px;
  }
  .fulfillment-options button {
    flex-direction: column;
    align-items: flex-start;
    padding: 10px 12px;
    gap: 6px;
  }
  h2 {
    font-size: 18px;
  }
}
</style>
