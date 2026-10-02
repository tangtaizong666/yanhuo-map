<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { SlidersHorizontal, X } from "lucide-vue-next";
import type { Portion, Product } from "../lib/types";
import {
  invalidPortions,
  normalizePortions,
  portionText,
} from "../lib/portions";

const props = defineProps<{
  product: Product;
  quantity: number;
  modelValue?: Portion[];
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: Portion[]] }>();
const dialog = ref<HTMLDialogElement>();
const draft = ref<Portion[]>([]);
const rows = computed(() =>
  normalizePortions(props.modelValue, props.quantity),
);
const specified = computed(() =>
  rows.value
    .map((row, index) => ({ text: portionText(row), index }))
    .filter((row) => row.text),
);
const invalid = computed(() =>
  invalidPortions(props.product, props.modelValue, props.quantity),
);
const draftInvalid = computed(() =>
  invalidPortions(props.product, draft.value, props.quantity),
);
function open() {
  if (props.disabled) return;
  draft.value = normalizePortions(props.modelValue, props.quantity);
  dialog.value?.showModal();
}
function removed(row: Portion) {
  return Object.entries(row.options).filter(
    ([name, choice]) =>
      !props.product.taste_options
        ?.find((group) => group.name === name)
        ?.choices.includes(choice),
  );
}
function save() {
  if (props.disabled || draftInvalid.value) return;
  emit("update:modelValue", normalizePortions(draft.value, props.quantity));
  dialog.value?.close();
}
watch(
  () => [props.quantity, props.product.id, props.disabled],
  () => dialog.value?.close(),
);
</script>

<template>
  <div class="portion-editor">
    <button
      type="button"
      class="portion-open"
      :disabled="disabled"
      :aria-label="`设置${product.name}每份口味与备注`"
      @click="open"
    >
      <SlidersHorizontal :size="16" />{{
        specified.length ? "修改每份口味与备注" : "每份口味与备注"
      }}<span>选填</span>
    </button>
    <p v-if="invalid" class="portion-invalid" role="alert">
      口味选项已变更，请重新选择后再结算。
    </p>
    <ul v-if="specified.length" class="portion-summary">
      <li v-for="row in specified" :key="row.index">
        第 {{ row.index + 1 }} 份 · {{ row.text }}
      </li>
    </ul>
    <dialog
      ref="dialog"
      class="portion-dialog"
      :aria-label="`${product.name}每份口味与备注`"
      @click.self="dialog?.close()"
    >
      <div class="portion-shell">
        <header>
          <div>
            <span class="eyebrow">按你的口味来</span>
            <h2>{{ product.name }}</h2>
          </div>
          <button
            type="button"
            aria-label="关闭口味设置"
            @click="dialog?.close()"
          >
            <X :size="22" />
          </button>
        </header>
        <p class="portion-hint">
          每份可以不同。不选口味按商家默认制作；特殊要求请先与商家确认，所选口味不另收费。
        </p>
        <div class="portion-fields">
          <fieldset
            v-for="(row, index) in draft"
            :key="index"
            :aria-label="`第${index + 1}份`"
          >
            <legend>第 {{ index + 1 }} 份</legend>
            <div
              v-for="[name, choice] in removed(row)"
              :key="name"
              class="portion-removed"
            >
              <span>原选「{{ name }}：{{ choice }}」已不可选</span
              ><button type="button" @click="delete row.options[name]">
                移除原选
              </button>
            </div>
            <label
              v-for="group in product.taste_options || []"
              :key="group.name"
            >
              <span>{{ group.name }}</span
              ><select
                :value="row.options[group.name] || ''"
                @change="
                  row.options[group.name] = (
                    $event.target as HTMLSelectElement
                  ).value
                "
              >
                <option value="">默认口味</option>
                <option
                  v-for="choice in group.choices"
                  :key="choice"
                  :value="choice"
                >
                  {{ choice }}
                </option>
              </select>
            </label>
            <label
              ><span
                >这份备注 <small>选填 · {{ row.note.length }}/100</small></span
              ><textarea
                v-model="row.note"
                maxlength="100"
                rows="2"
                placeholder="例如：不要香菜"
              />
            </label>
          </fieldset>
        </div>
        <footer>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="disabled || draftInvalid"
            @click="save"
          >
            保存口味与备注
          </button>
        </footer>
      </div>
    </dialog>
  </div>
</template>

<style scoped>
.portion-editor {
  min-width: 0;
  grid-column: 1/-1;
}
.portion-open {
  display: flex;
  gap: 8px;
  align-items: center;
  color: #a44923;
  min-height: 44px;
  font-size: 12px;
  text-align: left;
}
.portion-open span {
  color: #81715e;
  font-size: 11px;
}
.portion-summary {
  list-style: none;
  margin: 0;
  padding: 0 0 7px;
  font-size: 12px;
  line-height: 1.8;
  color: #735c47;
  overflow-wrap: anywhere;
}
.portion-invalid {
  font-size: 12px;
  color: #a03e27;
  margin: 0 0 7px;
}
.portion-dialog {
  border: 1px solid #eedfce;
  border-radius: 22px;
  background: #fffaf3;
  color: #362b23;
  padding: 0;
  width: min(520px, calc(100% - 24px));
  max-height: calc(100dvh - 32px);
  margin: auto;
  box-shadow: 0 20px 90px #35250c33;
}
.portion-dialog::backdrop {
  background: #38271470;
}
.portion-shell {
  display: flex;
  flex-direction: column;
  max-height: calc(100dvh - 36px);
}
header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 22px 22px 10px;
}
h2 {
  font-size: 21px;
  overflow-wrap: anywhere;
  margin: 7px 0 0;
}
header button {
  min-width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.portion-hint {
  padding: 0 22px 14px;
  color: #76624d;
  font-size: 12px;
  line-height: 1.7;
}
.portion-fields {
  overflow: auto;
  overscroll-behavior: contain;
  padding: 0 22px 8px;
  min-height: 0;
}
fieldset {
  border: 1px solid #eadac7;
  border-radius: 14px;
  padding: 12px 14px 14px;
  margin: 0 0 15px;
  min-width: 0;
  background: #fff;
}
legend {
  padding: 0 6px;
  color: #a54b25;
  font-size: 13px;
  font-weight: 600;
}
label {
  display: block;
  font-size: 13px;
  margin-top: 8px;
}
label > span {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
  gap: 6px;
}
small {
  font-size: 11px;
  color: #89745c;
}
select,
textarea {
  display: block;
  width: 100%;
  min-height: 44px;
  border: 1px solid #dfd1c1;
  border-radius: 9px;
  padding: 10px;
  font: inherit;
  background: #fffdf9;
  color: #362b23;
  box-sizing: border-box;
}
textarea {
  resize: vertical;
  max-height: 160px;
}
.portion-removed {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #a03e27;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.portion-removed button {
  min-height: 44px;
  min-width: 64px;
  font-weight: 600;
  text-decoration: underline;
  flex-shrink: 0;
}
footer {
  padding: 12px 22px max(18px, env(safe-area-inset-bottom));
  border-top: 1px solid #efdfcf;
  background: #fffaf3;
}
footer button {
  width: 100%;
  min-height: 46px;
}
@media (max-width: 400px) {
  header {
    padding: 18px 16px 8px;
  }
  .portion-hint {
    padding-left: 16px;
    padding-right: 16px;
  }
  .portion-fields {
    padding-left: 16px;
    padding-right: 16px;
  }
  footer {
    padding-left: 16px;
    padding-right: 16px;
  }
}
</style>
