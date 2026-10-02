<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import {
  Bike,
  CreditCard,
  FlaskConical,
  RefreshCw,
  Settings2,
} from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import MerchantDeliverySettings from "./MerchantDeliverySettings.vue";

const props = defineProps<{ stall: any }>();
const emit = defineEmits<{ refresh: [] }>();
const services = ref<any>(null),
  busy = ref(""),
  error = ref("");
const deliveryBusy = ref(false);
let generation = 0,
  controller: AbortController | undefined;
const simulated = computed(() => services.value?.mode === "simulation");
async function request(changes?: Record<string, boolean>) {
  if (busy.value || deliveryBusy.value) return;
  const current = ++generation;
  controller?.abort();
  const active = new AbortController();
  controller = active;
  busy.value = changes ? Object.keys(changes)[0]! : "loading";
  error.value = "";
  const timeout = window.setTimeout(() => active.abort(), 20000);
  try {
    const liveDelivery =
      changes && !simulated.value && "delivery_enabled" in changes;
    if (liveDelivery)
      await api(`/merchant/stalls/${props.stall.id}/delivery`, {
        method: "PATCH",
        body: { enabled: changes.delivery_enabled },
        signal: active.signal,
      });
    if (current !== generation) return;
    const value = await api<any>(
      `/merchant/stalls/${props.stall.id}/services`,
      {
        method: changes && !liveDelivery ? "PATCH" : "GET",
        body: liveDelivery ? undefined : changes,
        signal: active.signal,
      },
    );
    if (current !== generation) return;
    services.value = value;
    if (changes) {
      emit("refresh");
      notify("经营服务已保存", "success");
    }
  } catch (cause) {
    if (current === generation)
      error.value = active.signal.aborted
        ? "设置结果尚未确认，请重新读取后再操作。"
        : (cause as Error).message;
  } finally {
    window.clearTimeout(timeout);
    if (current === generation) busy.value = "";
  }
}
function deliverySaving(value: boolean) {
  deliveryBusy.value = value;
  // A settings response started before this save must not overwrite its result.
  if (value && busy.value === "loading") {
    generation++;
    controller?.abort();
    busy.value = "";
  }
  if (!value) void request();
}
watch(
  () => props.stall.id,
  () => {
    generation++;
    controller?.abort();
    busy.value = "";
    deliveryBusy.value = false;
    services.value = null;
    void request();
  },
  { immediate: true },
);
watch(
  () => [props.stall.status, props.stall.last_confirmed_at],
  () => {
    if (services.value && !busy.value) void request();
  },
);
onUnmounted(() => {
  generation++;
  controller?.abort();
});
</script>

<template>
  <section
    class="m-panel merchant-services"
    aria-labelledby="merchant-services-title"
  >
    <div class="m-panel-head">
      <div>
        <h2 id="merchant-services-title"><Settings2 :size="20" /> 经营服务</h2>
        <p class="m-muted">想做哪种生意，打开对应开关就好。开关会自动保存。</p>
      </div>
      <span v-if="simulated" class="service-mode"
        ><FlaskConical :size="15" /> 模拟体验</span
      >
    </div>
    <p v-if="simulated" class="service-simulation-note">
      现在可以练习收款和送餐流程，不会真实扣款，也不会安排真实配送。
    </p>
    <p v-if="error" class="m-alert" role="alert">{{ error }}</p>
    <p v-if="busy === 'loading'" class="m-muted" role="status">
      正在读取经营服务…
    </p>
    <div v-if="services" class="service-grid">
      <article class="service-card payment-service">
        <span class="service-icon"><CreditCard :size="24" /></span>
        <div class="service-copy">
          <h3>线上支付</h3>
          <p>
            {{
              simulated
                ? "微信模拟支付 · 学生可以练习付款"
                : "微信支付 · 收款配置由平台协助开通"
            }}
          </p>
        </div>
        <button
          class="service-toggle"
          role="switch"
          aria-label="线上支付"
          :aria-checked="services.online_payment_enabled"
          :disabled="!!busy || deliveryBusy || !simulated"
          @click="
            request({
              online_payment_enabled: !services.online_payment_enabled,
            })
          "
        >
          <span aria-hidden="true" />
        </button>
        <p class="service-state">
          {{
            services.online_payment_enabled
              ? simulated
                ? "已开启模拟支付"
                : "已开启线上支付"
              : "未开启"
          }}
        </p>
        <p v-if="!simulated" class="service-help">
          商户核验、微信商户号与安全配置由管理员办理。你无需填写密钥。
        </p>
      </article>
      <article class="service-card delivery-service">
        <span class="service-icon"><Bike :size="24" /></span>
        <div class="service-copy">
          <h3>外卖配送</h3>
          <p>
            {{
              simulated
                ? "模拟送到校园交接点，熟悉接单到收餐"
                : "由商家送到校园交接点"
            }}
          </p>
        </div>
        <button
          class="service-toggle"
          role="switch"
          aria-label="外卖配送"
          :aria-checked="services.delivery_enabled"
          :disabled="
            !!busy ||
            deliveryBusy ||
            (!simulated &&
              !services.delivery_enabled &&
              !services.delivery?.approved)
          "
          @click="request({ delivery_enabled: !services.delivery_enabled })"
        >
          <span aria-hidden="true" />
        </button>
        <p class="service-state">
          {{
            services.delivery_enabled
              ? simulated
                ? "已开启模拟配送"
                : "已开启配送"
              : "未开启"
          }}
        </p>
        <p v-if="!services.delivery_enabled && simulated" class="service-help">
          第一次开启会备好示例交接点和常用设置；随后只需核对配送费。
        </p>
        <p
          v-if="!simulated && !services.delivery?.approved"
          class="service-help"
        >
          请联系管理员确认配送条件。核验通过后即可用此开关接单或暂停。
        </p>
      </article>
    </div>
    <p
      v-if="services?.delivery_enabled && !services.delivery?.available"
      class="service-availability"
    >
      当前不能接配送单：{{
        services.delivery?.reason || "请核对营业状态与线上支付设置"
      }}
    </p>
    <MerchantDeliverySettings
      v-if="services"
      v-show="services.delivery_enabled || !simulated"
      :key="`${stall.id}:${services.mode}`"
      :stall="stall"
      :settings="services.delivery"
      :disabled="!!busy && busy !== 'loading'"
      embedded
      @saving="deliverySaving"
      @refresh="
        emit('refresh');
        request();
      "
    />
    <button
      v-if="error"
      type="button"
      class="btn btn-secondary"
      :disabled="!!busy"
      @click="request()"
    >
      <RefreshCw :size="16" /> 重新读取经营服务
    </button>
  </section>
</template>

<style scoped>
.merchant-services {
  scroll-margin-top: 82px;
  min-width: 0;
}
.merchant-services .m-panel-head {
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 12px;
}
.service-mode {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: 8px;
  background: #fff0d8;
  color: #916021;
  font-size: 12px;
  white-space: nowrap;
}
.service-simulation-note {
  color: #796341;
  background: #fff8e8;
  padding: 13px 15px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.8;
  margin: 0 0 18px;
}
.service-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.service-card {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) 52px;
  align-items: center;
  gap: 10px 12px;
  padding: 20px;
  border: 1px solid #eadfcc;
  border-radius: 15px;
  background: #fffdf8;
}
.service-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 13px;
  background: #eaf1e6;
  color: #527347;
}
.delivery-service .service-icon {
  background: #fff0de;
  color: #b7753e;
}
.service-copy h3 {
  margin: 0 0 7px;
  font-size: 16px;
}
.service-copy p,
.service-help {
  font-size: 12px;
  line-height: 1.8;
  color: #7f6955;
  margin: 0;
  overflow-wrap: anywhere;
}
.service-toggle {
  display: grid;
  align-items: center;
  width: 52px;
  min-height: 44px;
  padding: 0 4px;
  border: 0;
  border-radius: 12px;
  background: none;
  cursor: pointer;
  position: relative;
}
.service-toggle::before {
  content: "";
  position: absolute;
  left: 2px;
  right: 2px;
  top: 9px;
  bottom: 9px;
  background: #cec6b9;
  border-radius: 20px;
}
.service-toggle span {
  display: block;
  height: 22px;
  width: 22px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 4px #49331722;
  z-index: 1;
  transition: transform 160ms;
}
.service-toggle[aria-checked="true"]::before {
  background: #64825b;
}
.service-toggle[aria-checked="true"] span {
  transform: translateX(22px);
}
.service-toggle:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.service-toggle:focus-visible {
  outline: 3px solid #c78c57;
  outline-offset: 3px;
}
.service-state {
  grid-column: 2 / -1;
  color: #536743;
  margin: 0;
  font-size: 12px;
}
.service-help {
  grid-column: 1 / -1;
  border-top: 1px solid #efe7db;
  padding-top: 12px;
}
.service-availability {
  font-size: 13px;
  color: #936437;
  line-height: 1.8;
  margin: 18px 0 0;
}
@media (max-width: 1050px) {
  .service-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .service-card {
    padding: 16px;
    gap: 10px;
    grid-template-columns: 38px minmax(0, 1fr) 52px;
  }
  .service-icon {
    width: 38px;
    height: 38px;
  }
  .service-copy h3 {
    font-size: 15px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .service-toggle span {
    transition: none;
  }
}
</style>
