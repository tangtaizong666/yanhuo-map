<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from "vue";
import {
  Bike,
  Check,
  Clock3,
  MapPin,
  RefreshCw,
  Save,
  ShieldCheck,
} from "lucide-vue-next";
import { api, money } from "../../lib/api";
import { notify } from "../../lib/notify";

const props = withDefaults(
  defineProps<{
    stall: any;
    embedded?: boolean;
    settings?: any;
    disabled?: boolean;
  }>(),
  { embedded: false },
);
const emit = defineEmits<{ refresh: []; saving: [value: boolean] }>();
const config = ref<any>(null);
const busy = ref(false);
const loading = ref(false);
const error = ref("");
const syncMessage = ref("");
const form = reactive({
  enabled: false,
  fee: "0",
  minimum: "0",
  eta_min_minutes: 20,
  eta_max_minutes: 40,
  starts_at: "11:00",
  ends_at: "14:00",
  capacity: 5,
  point_ids: [] as number[],
});
type Field = keyof typeof form;
const baseline = reactive({ ...form, point_ids: [] as number[] });
const fieldNames: Record<Field, string> = {
  enabled: "配送开关",
  fee: "配送费",
  minimum: "起送金额",
  eta_min_minutes: "预计送达下限",
  eta_max_minutes: "预计送达上限",
  starts_at: "开始接单时间",
  ends_at: "结束接单时间",
  capacity: "接单上限",
  point_ids: "送餐地点",
};
const fields = Object.keys(form) as Field[];
function fieldValue(field: Field, value: unknown) {
  if (field === "fee" || field === "minimum") return `¥${value}`;
  if (field === "point_ids") return `${(value as number[]).length} 个交接点`;
  if (field === "capacity") return `${value} 单`;
  if (field.startsWith("eta_")) return `${value} 分钟`;
  return String(value);
}
function same(field: Field, a: unknown, b: unknown) {
  if (field === "point_ids")
    return (
      JSON.stringify([...(a as number[])].sort((x, y) => x - y)) ===
      JSON.stringify([...(b as number[])].sort((x, y) => x - y))
    );
  if (field === "fee" || field === "minimum") {
    const normalize = (value: unknown) =>
      /^\d+(?:\.\d{1,2})?$/.test(String(value).trim()) ? Number(value) : value;
    return normalize(a) === normalize(b);
  }
  return a === b;
}
const dirtyFields = computed(() =>
  config.value
    ? fields.filter(
        (field) =>
          !(props.embedded && field === "enabled") &&
          !same(field, form[field], baseline[field]),
      )
    : [],
);
let generation = 0;
let controller: AbortController | undefined;
const selectedPoints = computed(
  () =>
    config.value?.available_points?.filter((point: any) =>
      form.point_ids.includes(point.id),
    ) || [],
);
const unavailablePointIds = computed(() =>
  form.point_ids.filter(
    (id) =>
      !config.value?.available_points?.some((point: any) => point.id === id),
  ),
);
const readiness = computed(() => config.value || props.stall.delivery);
function fill(value: any, replace = false) {
  const initial = !config.value;
  config.value = value;
  const incoming = {
    enabled: value.enabled,
    fee: money(value.fee_cents),
    minimum: money(value.min_order_cents),
    eta_min_minutes: value.eta_min_minutes,
    eta_max_minutes: value.eta_max_minutes,
    starts_at: value.starts_at,
    ends_at: value.ends_at,
    capacity: value.capacity,
    point_ids: [...value.point_ids],
  };
  const conflicts: string[] = [];
  let changed = false;
  for (const field of fields) {
    const edited = !same(field, form[field], baseline[field]);
    const remoteChanged = !same(field, incoming[field], baseline[field]);
    if (
      !initial &&
      !replace &&
      edited &&
      remoteChanged &&
      !same(field, form[field], incoming[field]) &&
      field !== "enabled"
    )
      conflicts.push(
        `${fieldNames[field]}（现为 ${fieldValue(field, incoming[field])}）`,
      );
    if (
      initial ||
      replace ||
      !edited ||
      (props.embedded && field === "enabled")
    )
      (form as any)[field] = incoming[field];
    (baseline as any)[field] = Array.isArray(incoming[field])
      ? [...incoming[field]]
      : incoming[field];
    changed ||= remoteChanged;
  }
  if (replace) syncMessage.value = "";
  else if (!initial && conflicts.length)
    syncMessage.value = `其他设备更新了${conflicts.join("、")}。已保留你的未保存修改，请核对后保存。`;
  else if (!initial && changed)
    syncMessage.value = dirtyFields.value.length
      ? "已同步其他设备的设置，你尚未保存的修改已保留。"
      : "已同步最新配送设置。";
}
async function load() {
  const current = ++generation;
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  const active = controller;
  const timeout = window.setTimeout(() => active.abort(), 20000);
  try {
    const value = await api(`/merchant/stalls/${props.stall.id}/delivery`, {
      signal: controller.signal,
    });
    if (current === generation) fill(value);
  } catch (cause) {
    if (current === generation)
      error.value = active.signal.aborted
        ? "读取设置超时，请重试；未保存的修改仍保留。"
        : (cause as Error).message;
  } finally {
    window.clearTimeout(timeout);
    if (current === generation) loading.value = false;
  }
}
function cents(value: string, label: string) {
  if (!/^\d+(?:\.\d{1,2})?$/.test(value.trim()))
    throw new Error(`${label}请填写非负金额，最多两位小数。`);
  return Math.round(Number(value) * 100);
}
async function save() {
  if (busy.value || props.disabled || loading.value || !config.value) return;
  error.value = "";
  if (!dirtyFields.value.length) {
    notify("没有需要保存的配送修改", "info");
    return;
  }
  let body;
  try {
    if (
      !/^\d{2}:\d{2}$/.test(form.starts_at) ||
      !/^\d{2}:\d{2}$/.test(form.ends_at)
    )
      throw new Error("请填写有效的配送时段。");
    if (
      ![form.capacity, form.eta_min_minutes, form.eta_max_minutes].every(
        Number.isInteger,
      ) ||
      form.capacity < 1 ||
      form.capacity > 100 ||
      form.eta_min_minutes < 5 ||
      form.eta_max_minutes > 180
    )
      throw new Error(
        "请在更多配送设置中核对接单上限（1–100 单）和送达范围（5–180 分钟）。",
      );
    if (form.starts_at >= form.ends_at)
      throw new Error("结束时间应晚于开始时间，首版支持同日配送时段。");
    if (form.eta_min_minutes > form.eta_max_minutes)
      throw new Error("预计送达上限不能小于下限。");
    if (form.enabled && !form.point_ids.length)
      throw new Error("请至少选择一个运营已开放的校园交接点。");
    const values = {
      enabled: form.enabled,
      fee_cents: cents(form.fee, "配送费"),
      min_order_cents: cents(form.minimum, "起送金额"),
      eta_min_minutes: form.eta_min_minutes,
      eta_max_minutes: form.eta_max_minutes,
      starts_at: form.starts_at,
      ends_at: form.ends_at,
      capacity: form.capacity,
      point_ids: [...form.point_ids],
    };
    const names: Record<Field, keyof typeof values> = {
      enabled: "enabled",
      fee: "fee_cents",
      minimum: "min_order_cents",
      eta_min_minutes: "eta_min_minutes",
      eta_max_minutes: "eta_max_minutes",
      starts_at: "starts_at",
      ends_at: "ends_at",
      capacity: "capacity",
      point_ids: "point_ids",
    };
    body = Object.fromEntries(
      dirtyFields.value.map((field) => [names[field], values[names[field]]]),
    );
  } catch (cause) {
    error.value = (cause as Error).message;
    return;
  }
  const current = generation;
  busy.value = true;
  emit("saving", true);
  const saveController = new AbortController();
  controller = saveController;
  const timeout = window.setTimeout(() => saveController.abort(), 20000);
  try {
    const value = await api<any>(
      `/merchant/stalls/${props.stall.id}/delivery`,
      { method: "PATCH", body, signal: saveController.signal },
    );
    if (current !== generation) return;
    fill(value, true);
    emit("refresh");
    notify(
      value.available
        ? "配送设置已保存，学生可在开放时段选择配送"
        : "配送设置已保存，开放条件仍以页面提示为准",
      "success",
    );
  } catch (cause) {
    if (current === generation)
      error.value = saveController.signal.aborted
        ? "保存结果尚未确认，请重新读取设置核对后再操作。"
        : (cause as Error).message;
  } finally {
    window.clearTimeout(timeout);
    if (current === generation) {
      busy.value = false;
      emit("saving", false);
    }
  }
}
watch(
  () => props.stall.id,
  () => {
    generation++;
    controller?.abort();
    config.value = null;
    busy.value = false;
    syncMessage.value = "";
    if (props.settings?.available_points) fill(props.settings, true);
    else void load();
  },
  { immediate: true },
);
watch(
  () => props.settings,
  (value) => {
    if (value?.available_points && !busy.value && !loading.value) fill(value);
  },
);
onUnmounted(() => {
  generation++;
  controller?.abort();
});
</script>

<template>
  <section
    class="merchant-delivery-settings"
    :class="{ 'm-panel': !embedded, embedded }"
    aria-labelledby="merchant-delivery-title"
  >
    <div class="m-panel-head">
      <div>
        <h2 id="merchant-delivery-title">
          <Bike :size="20" /> {{ embedded ? "外卖基础设置" : "校园定点配送" }}
        </h2>
        <p class="m-muted">
          {{
            config?.mode === "simulation"
              ? "示例设置已备好，可直接试用，也可按需要调整。"
              : "核对配送费和送餐地点，即可保存。"
          }}
        </p>
      </div>
      <span
        class="m-status"
        :class="readiness?.available ? 'open' : 'paused'"
        >{{ readiness?.available ? "配送可下单" : "暂未开放配送" }}</span
      >
    </div>
    <div v-if="!embedded" class="delivery-readiness">
      <ShieldCheck :size="20" />
      <div>
        <strong>{{
          readiness?.approved ? "运营已批准配送" : "等待运营核验配送条件"
        }}</strong>
        <p>
          {{
            readiness?.reason ||
            "需完成运营核验、微信支付配置和配送设置后开放。"
          }}
        </p>
        <small>配送需先微信付款；到摊自取仍保留原有付款方式。</small>
      </div>
    </div>
    <p v-if="error" role="alert" class="m-alert">{{ error }}</p>
    <div v-if="config" class="delivery-sync" role="status">
      <strong>{{
        dirtyFields.length ? "有未保存的修改" : "当前设置已保存"
      }}</strong>
      <span v-if="dirtyFields.length">保存后才会影响新订单。</span>
      <p v-if="syncMessage">{{ syncMessage }}</p>
    </div>
    <p v-if="loading" role="status" class="m-muted">正在读取配送设置…</p>
    <button v-if="!config && !loading" class="btn btn-secondary" @click="load">
      <RefreshCw :size="16" /> 重新读取设置
    </button>
    <form
      v-if="config"
      class="m-form delivery-form"
      novalidate
      @submit.prevent="save"
    >
      <label v-if="!embedded" class="delivery-switch"
        ><span
          ><strong>接受配送订单</strong
          ><small>保存开关不会绕过运营审核、支付或营业状态检查。</small></span
        ><input
          v-model="form.enabled"
          type="checkbox"
          :disabled="busy || disabled"
      /></label>
      <fieldset :disabled="busy || disabled">
        <legend>每单配送费</legend>
        <div class="delivery-fee-row">
          <label
            >配送费（元）<input
              v-model="form.fee"
              inputmode="decimal"
              maxlength="8"
              required
          /></label>
          <p class="m-muted">
            学生付款前会看到餐费与配送费。更改只影响新订单。
          </p>
        </div>
      </fieldset>
      <fieldset :disabled="busy || disabled" class="delivery-points">
        <legend><MapPin :size="16" /> 可送达的校园交接点</legend>
        <label
          v-for="point in config.available_points"
          :key="point.id"
          class="delivery-point"
          :class="{ selected: form.point_ids.includes(point.id) }"
          ><input
            v-model="form.point_ids"
            type="checkbox"
            :value="point.id" /><span
            ><strong>{{ point.name }}</strong
            ><small>{{ point.address }}</small></span
          ><Check v-if="form.point_ids.includes(point.id)" :size="17"
        /></label>
        <label
          v-for="id in unavailablePointIds"
          :key="`unavailable-${id}`"
          class="delivery-point"
          ><input v-model="form.point_ids" type="checkbox" :value="id" /><span
            ><strong>交接点 #{{ id }} 已不可用</strong
            ><small
              >该点已暂停或不在当前区域。请取消勾选后保存；已有订单的地点不会改变。</small
            ></span
          ></label
        >
        <p v-if="!config.available_points?.length" class="delivery-no-points">
          本区域暂无运营已开放的交接点。请联系运营确认校园交接安排后添加，不能自行填写送寝地址。
        </p>
        <p v-else class="m-muted">
          已选
          {{ selectedPoints.length }}
          个交接点。交接点地址由运营维护，历史订单保留下单时的地点。
        </p>
      </fieldset>
      <details class="delivery-advanced">
        <summary>
          <Clock3 :size="17" /><span
            >更多配送设置<small
              >{{ form.starts_at }}–{{ form.ends_at }} · 起送 ¥{{
                form.minimum
              }}
              · 同时 {{ form.capacity }} 单 · {{ form.eta_min_minutes }}–{{
                form.eta_max_minutes
              }}
              分钟</small
            ></span
          >
        </summary>
        <fieldset :disabled="busy || disabled">
          <legend>费用与接单能力</legend>
          <div class="delivery-fields two">
            <label
              >餐品起送金额（元）<input
                v-model="form.minimum"
                inputmode="decimal"
                maxlength="9"
                required
            /></label>
            <label
              >同时配送容量（单）<input
                v-model.number="form.capacity"
                type="number"
                min="1"
                max="100"
                required
            /></label>
          </div>
          <p class="m-muted">
            起送金额不含配送费。未完成的配送订单达到上限时会暂停接新单。
          </p>
        </fieldset>
        <fieldset :disabled="busy || disabled">
          <legend>每日接单时段与预计送达</legend>
          <div class="delivery-fields two">
            <label
              >开始接单<input v-model="form.starts_at" type="time" required
            /></label>
            <label
              >结束接单<input v-model="form.ends_at" type="time" required
            /></label>
            <label
              >预计送达下限（分钟）<input
                v-model.number="form.eta_min_minutes"
                type="number"
                min="5"
                max="180"
                required
            /></label>
            <label
              >预计送达上限（分钟）<input
                v-model.number="form.eta_max_minutes"
                type="number"
                min="5"
                max="180"
                required
            /></label>
          </div>
          <p class="m-muted">
            北京时间，支持同一天内的接单时段。送达时间包含备餐与路程，为预估范围。
          </p>
        </fieldset>
        <button
          class="btn btn-secondary"
          :disabled="busy || disabled || loading"
          type="button"
          @click="load"
        >
          <RefreshCw :size="16" />重新读取设置
        </button>
      </details>
      <div class="delivery-save">
        <button
          class="btn btn-primary"
          :disabled="busy || disabled || loading"
          type="submit"
        >
          <Save :size="16" />{{ busy ? "正在保存…" : "保存配送设置" }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.delivery-sync {
  padding: 12px 14px;
  margin: 0 0 18px;
  border: 1px solid #e9dec9;
  border-radius: 10px;
  background: #fffaf1;
  color: #785d3e;
  font-size: 12px;
  line-height: 1.8;
}
.delivery-sync strong {
  margin-right: 10px;
  font-weight: 600;
}
.delivery-sync p {
  margin: 5px 0 0;
}
.merchant-delivery-settings.embedded {
  margin-top: 25px;
  padding-top: 22px;
  border-top: 1px solid #eadfcc;
}
.delivery-advanced {
  border: 1px solid #e8ddce;
  border-radius: 12px;
  background: #fffaf2;
  padding: 0 16px;
}
.delivery-advanced summary {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 62px;
  cursor: pointer;
  font-size: 14px;
  color: #68523b;
  list-style: none;
}
.delivery-advanced summary::after {
  content: "+";
  margin-left: auto;
  font-size: 21px;
}
.delivery-advanced[open] summary::after {
  content: "−";
}
.delivery-advanced summary small {
  display: block;
  margin-top: 5px;
  font-size: 12px;
  font-weight: 400;
  color: #816e5b;
}
.delivery-advanced fieldset {
  margin: 12px 0 22px !important;
}
.delivery-advanced > button {
  margin-bottom: 18px;
}
.delivery-fee-row {
  display: grid;
  grid-template-columns: minmax(120px, 180px) 1fr;
  gap: 18px;
  align-items: end;
}
.delivery-fee-row .m-muted {
  margin: 0 0 10px;
}
.delivery-advanced summary:focus-visible {
  outline: 3px solid #cd884f;
  outline-offset: 3px;
}
@media (max-width: 500px) {
  .delivery-fee-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }
}
.merchant-delivery-settings {
  min-width: 0;
  scroll-margin-top: 84px;
}
.delivery-readiness {
  display: flex;
  gap: 12px;
  padding: 17px;
  border: 1px solid #e5d8be;
  border-radius: 14px;
  background: #faf2df;
  color: #745827;
  margin: 8px 0 22px;
}
.delivery-readiness > svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.delivery-readiness strong {
  font-size: 14px;
}
.delivery-readiness p {
  margin: 7px 0;
  font-size: 13px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}
.delivery-readiness small {
  font-size: 12px;
  line-height: 1.7;
}
.delivery-form {
  gap: 23px;
}
.delivery-switch {
  display: flex !important;
  flex-direction: row !important;
  justify-content: space-between;
  align-items: center;
  gap: 18px !important;
  padding: 17px 0;
  border-bottom: 1px solid #ece0cd;
}
.delivery-switch strong,
.delivery-switch small {
  display: block;
}
.delivery-switch strong {
  font-size: 14px;
  color: #513b27;
}
.delivery-switch small {
  font-size: 12px;
  font-weight: 400;
  line-height: 1.7;
  color: #7d6853;
  margin-top: 6px;
}
.delivery-switch input {
  width: 22px !important;
  height: 22px;
  min-height: 22px !important;
  flex-shrink: 0;
  accent-color: #cc703d;
}
.delivery-form fieldset {
  min-width: 0;
  padding: 0;
  border: 0;
  margin: 0;
}
.delivery-form legend {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #57412c;
  font-weight: 600;
  margin-bottom: 15px;
  padding: 0;
}
.delivery-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.delivery-fields.two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.delivery-fields label {
  min-width: 0;
}
.delivery-fields input {
  width: 100%;
  min-width: 0;
  min-height: 46px;
}
.delivery-point {
  display: flex !important;
  align-items: center;
  flex-direction: row !important;
  gap: 13px !important;
  min-height: 72px;
  padding: 14px;
  border: 1px solid #e5d6c0;
  border-radius: 12px;
  margin-bottom: 10px;
  background: #fffcf6;
  cursor: pointer;
}
.delivery-point.selected {
  border-color: #ca8650;
  background: #fbefdf;
}
.delivery-point input {
  width: 18px !important;
  min-height: 18px !important;
  height: 18px;
  flex-shrink: 0;
  accent-color: #cb743c;
}
.delivery-point > span {
  min-width: 0;
  flex: 1;
}
.delivery-point strong,
.delivery-point small {
  display: block;
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.delivery-point strong {
  font-size: 13px;
  color: #674930;
}
.delivery-point small {
  font-size: 12px;
  color: #7f6b59;
  font-weight: 400;
  margin-top: 4px;
}
.delivery-point > svg {
  flex-shrink: 0;
  color: #aa6639;
}
.delivery-no-points {
  border: 1px dashed #dcc9ad;
  border-radius: 12px;
  padding: 20px;
  color: #826849;
  line-height: 1.8;
  font-size: 13px;
  background: #fcf7ef;
}
.delivery-save {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.delivery-save button {
  min-height: 46px;
}
.delivery-form :is(input, button):focus-visible {
  outline: 3px solid #cd884f;
  outline-offset: 3px;
}
@media (max-width: 700px) {
  .merchant-delivery-settings .m-panel-head {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .merchant-delivery-settings .m-panel-head .m-status {
    margin-top: 4px;
  }
  .delivery-fields {
    grid-template-columns: 1fr;
  }
  .delivery-fields.two {
    gap: 12px;
  }
  .delivery-fields label {
    font-size: 12px;
  }
  .delivery-readiness {
    padding: 14px;
  }
  .delivery-save button {
    flex: 1;
    white-space: normal;
  }
  .delivery-point {
    padding: 12px;
  }
}
</style>
