<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import {
  Check,
  ChevronDown,
  ImagePlus,
  Package,
  Pencil,
  Plus,
  Search,
  SlidersHorizontal,
  Upload,
  X,
} from "lucide-vue-next";
import { api, ApiError, money } from "../../lib/api";
import { notify } from "../../lib/notify";
import MerchantRestock from "./MerchantRestock.vue";
import MerchantInventoryCorrection from "./MerchantInventoryCorrection.vue";
import { readStorage, writeStorage, removeStorage } from "../../lib/storage";
import { newRequestKey } from "../../lib/engagement";
import { useSession } from "../../stores/session";
const session = useSession();

const props = defineProps<{ stall: any }>();
const emit = defineEmits<{ refresh: [] }>();
type Product = {
  id: number;
  name: string;
  description: string;
  image: string;
  category?: string;
  price_cents: number;
  stock: number;
  is_active: boolean;
  sale_paused?: boolean;
  stock_version?: number;
  taste_options?: { name: string; choices: string[] }[];
};
type Draft = {
  name: string;
  description: string;
  image: string;
  category: string;
  price: string;
  stock: string;
  is_active: boolean;
  taste_options: { name: string; choices: string }[];
};
const search = ref("");
const category = ref("all");
const status = ref("all");
const busy = reactive<Record<number, boolean>>({});
const quickEdits = reactive<
  Record<
    number,
    {
      price: string;
      basePrice: number;
      priceDirty: boolean;
    }
  >
>({});
const products = computed<Product[]>(() => props.stall?.products || []);
const categories = computed(() => [
  ...new Set(products.value.map((p) => p.category || "未分类")),
]);
const counts = computed(() => ({
  all: products.value.length,
  active: products.value.filter(
    (p) => p.is_active && !p.sale_paused && p.stock > 0,
  ).length,
  soldout: products.value.filter(
    (p) => p.is_active && !p.sale_paused && p.stock === 0,
  ).length,
  paused: products.value.filter((p) => p.is_active && p.sale_paused).length,
  inactive: products.value.filter((p) => !p.is_active).length,
}));
const filters = computed(() => [
  { key: "all", label: "全部商品", count: counts.value.all },
  { key: "active", label: "销售中", count: counts.value.active },
  { key: "soldout", label: "已售罄", count: counts.value.soldout },
  { key: "paused", label: "暂停供应", count: counts.value.paused },
  { key: "inactive", label: "已下架", count: counts.value.inactive },
]);
const shown = computed(() =>
  products.value.filter((p) => {
    const matchesSearch = `${p.name} ${p.description || ""}`
      .toLowerCase()
      .includes(search.value.trim().toLowerCase());
    const matchesCategory =
      category.value === "all" || (p.category || "未分类") === category.value;
    const matchesStatus =
      status.value === "all" ||
      (status.value === "active" &&
        p.is_active &&
        !p.sale_paused &&
        p.stock > 0) ||
      (status.value === "soldout" &&
        p.is_active &&
        !p.sale_paused &&
        p.stock === 0) ||
      (status.value === "paused" && p.is_active && p.sale_paused) ||
      (status.value === "inactive" && !p.is_active);
    return matchesSearch && matchesCategory && matchesStatus;
  }),
);
watch(
  products,
  (list) => {
    for (const p of list) {
      const draft = quickEdits[p.id];
      if (!draft)
        quickEdits[p.id] = {
          price: (p.price_cents / 100).toFixed(2),
          basePrice: p.price_cents,
          priceDirty: false,
        };
      else {
        if (!draft.priceDirty) {
          draft.price = (p.price_cents / 100).toFixed(2);
          draft.basePrice = p.price_cents;
        }
      }
    }
  },
  { immediate: true },
);
const modal = ref(false);
const editingId = ref<number | null>(null);
const saving = ref(false);
const uploading = ref(false);
const formError = ref("");
const modalElement = ref<HTMLElement>();
const nameInput = ref<HTMLInputElement>();
let returnFocus: HTMLElement | null = null;
let previousOverflow = "";
const blank = (): Draft => ({
  name: "",
  description: "",
  image: "",
  category: "",
  price: "",
  stock: "",
  is_active: true,
  taste_options: [],
});
type Creation = { body: Record<string, any>; draft: Draft };
const creationKey = `merchant-product-create:${session.user?.id}:${props.stall.id}`;
const pendingCreation = ref<Creation | null>(null);
try {
  const saved = JSON.parse(readStorage(creationKey, "session") || "null");
  if (saved?.body?.idempotency_key && saved?.draft?.name)
    pendingCreation.value = saved;
} catch {
  /* Ignore corrupt non-business drafts. */
}
const form = reactive<Draft>(blank());
let original = blank();
watch(
  () => props.stall?.id,
  () => {
    search.value = "";
    category.value = "all";
    status.value = "all";
    closeEditor(true);
  },
);
function startEditor(product?: Product) {
  editingId.value = product?.id ?? null;
  Object.assign(
    form,
    product
      ? {
          name: product.name,
          description: product.description || "",
          image: product.image || "",
          category: product.category || "",
          price: (product.price_cents / 100).toFixed(2),
          stock: String(product.stock),
          is_active: product.is_active,
          taste_options: (product.taste_options || []).map((group) => ({
            name: group.name,
            choices: group.choices.join("、"),
          })),
        }
      : pendingCreation.value?.draft || blank(),
  );
  original = JSON.parse(JSON.stringify(form));
  formError.value = "";
  returnFocus = document.activeElement as HTMLElement;
  previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  modal.value = true;
  nextTick(() => nameInput.value?.focus());
}
function closeEditor(force = false) {
  if (!modal.value || (!force && (saving.value || uploading.value))) return;
  modal.value = false;
  document.body.style.overflow = previousOverflow;
  nextTick(() => returnFocus?.focus());
}
onBeforeUnmount(() => {
  if (modal.value) document.body.style.overflow = previousOverflow;
});
function modalKeys(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeEditor();
  }
  if (event.key !== "Tab") return;
  const nodes = Array.from(
    modalElement.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
    ) || [],
  ).filter((el) => el.getClientRects().length);
  const first = nodes[0],
    last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
function priceCents(value: string | number) {
  if (!/^\d+(\.\d{1,2})?$/.test(String(value).trim()))
    throw new Error("售价请填写大于 0 的金额，最多两位小数。");
  const cents = Math.round(Number(value) * 100);
  if (!Number.isSafeInteger(cents) || cents < 1 || cents > 1000000)
    throw new Error("售价须在 0.01 元至 10,000 元之间。");
  return cents;
}
function stockValue(value: string | number) {
  if (!/^\d+$/.test(String(value)) || Number(value) > 100000)
    throw new Error("线上剩余可卖份数请填写 0 至 100,000 的整数。");
  return Number(value);
}
async function patchProduct(
  product: Product,
  data: Record<string, unknown>,
  message: string,
) {
  if (busy[product.id]) return;
  busy[product.id] = true;
  try {
    await api(`/merchant/products/${product.id}`, {
      method: "PATCH",
      body: data,
    });
    notify(message, "success");
    emit("refresh");
  } catch (error) {
    notify((error as Error).message, "error");
  } finally {
    busy[product.id] = false;
  }
}
async function saveQuick(product: Product) {
  const draft = quickEdits[product.id];
  if (!draft || busy[product.id]) return;
  try {
    const data: Record<string, unknown> = {};
    if (draft.priceDirty && priceCents(draft.price) !== draft.basePrice)
      data.price_cents = priceCents(draft.price);
    if (!Object.keys(data).length) {
      draft.priceDirty = false;
      emit("refresh");
      return;
    }
    busy[product.id] = true;
    await api(`/merchant/products/${product.id}`, {
      method: "PATCH",
      body: data,
    });
    draft.priceDirty = false;
    notify("商品已保存", "success");
    emit("refresh");
  } catch (error) {
    notify((error as Error).message, "error");
  } finally {
    busy[product.id] = false;
  }
}
async function uploadImage(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  if (
    !["image/jpeg", "image/png", "image/webp"].includes(file.type) ||
    file.size > 5 * 1024 * 1024
  ) {
    formError.value = "请选择不超过 5 MB 的 JPG、PNG 或 WebP 图片。";
    input.value = "";
    return;
  }
  uploading.value = true;
  formError.value = "";
  const selectedStall = props.stall.id;
  try {
    const data = new FormData();
    data.append("file", file);
    const result = await api<{ url: string }>(
      `/merchant/stalls/${selectedStall}/image`,
      { method: "POST", body: data },
    );
    if (modal.value && selectedStall === props.stall.id)
      form.image = result.url;
  } catch (error) {
    formError.value = (error as Error).message;
  } finally {
    uploading.value = false;
    input.value = "";
  }
}
async function saveEditor(continueAdding = false) {
  if (saving.value || uploading.value) return;
  formError.value = "";
  const recovering = editingId.value == null && !!pendingCreation.value;
  try {
    if (!form.name.trim()) throw new Error("请填写商品名称。");
    const tasteOptions = tasteValues(form.taste_options);
    const values = {
      name: form.name.trim(),
      category: form.category.trim(),
      description: form.description.trim(),
      image: form.image.trim(),
      price_cents: priceCents(form.price),
      ...(editingId.value == null ? { stock: stockValue(form.stock) } : {}),
      is_active: form.is_active,
      taste_options: tasteOptions,
    };
    const baseline = {
      name: original.name,
      category: original.category,
      description: original.description,
      image: original.image,
      price_cents: original.price ? priceCents(original.price) : 0,
      is_active: original.is_active,
      taste_options: tasteValues(original.taste_options),
    };
    const data =
      editingId.value == null
        ? values
        : Object.fromEntries(
            Object.entries(values).filter(
              ([key, value]) =>
                JSON.stringify(value) !==
                JSON.stringify(baseline[key as keyof typeof baseline]),
            ),
          );
    if (!Object.keys(data).length) {
      closeEditor();
      return;
    }
    if (editingId.value == null && !pendingCreation.value) {
      const record = {
        body: { ...data, idempotency_key: newRequestKey("product") },
        draft: JSON.parse(JSON.stringify(form)),
      };
      if (!writeStorage(creationKey, JSON.stringify(record), "session"))
        throw new Error(
          "浏览器无法保存新增重试记录，暂未提交。请允许本站保存数据后重试。",
        );
      pendingCreation.value = record;
    }
    saving.value = true;
    const result = await api<Product>(
      editingId.value == null
        ? `/merchant/stalls/${props.stall.id}/products`
        : `/merchant/products/${editingId.value}`,
      {
        method: editingId.value == null ? "POST" : "PATCH",
        body: editingId.value == null ? pendingCreation.value!.body : data,
      },
    );
    if (
      editingId.value == null &&
      (!result ||
        !Number.isSafeInteger(result.id) ||
        result.name !== pendingCreation.value!.body.name ||
        result.price_cents !== pendingCreation.value!.body.price_cents)
    )
      throw new Error("新增响应无法核对，请重试确认原结果。");
    if (editingId.value == null) {
      pendingCreation.value = null;
      removeStorage(creationKey, "session");
    }
    notify(
      editingId.value != null
        ? "商品资料已保存，线上可卖份数与供应状态未改变"
        : !values.is_active
          ? "商品已保存为下架，顾客暂不可购买"
          : values.stock === 0
            ? "商品已保存，当前 0 份；补货后才可购买"
            : "商品已保存为可售，仍以摊位营业与接单条件为准",
      "success",
    );
    emit("refresh");
    if (continueAdding && editingId.value == null) {
      Object.assign(form, blank());
      original = blank();
      await nextTick();
      nameInput.value?.focus();
    } else closeEditor(true);
  } catch (error) {
    if (
      !recovering &&
      error instanceof ApiError &&
      (error.data?.submitted === false ||
        [400, 403, 404, 422].includes(error.status))
    ) {
      pendingCreation.value = null;
      removeStorage(creationKey, "session");
    }
    formError.value = pendingCreation.value
      ? "新增结果尚未确认。请确认原请求结果；将使用相同标识和内容重试，不会重复新增。"
      : (error as Error).message;
  } finally {
    saving.value = false;
  }
}
function tasteValues(groups: Draft["taste_options"]) {
  if (groups.length > 3) throw new Error("口味最多设置 3 组。");
  const parsed = groups.map((group) => ({
    name: group.name.trim(),
    choices: group.choices
      .split(/[、，,\n]/)
      .map((choice) => choice.trim())
      .filter(Boolean),
  }));
  if (
    parsed.some(
      (group) =>
        !group.name ||
        group.name.length > 20 ||
        !group.choices.length ||
        group.choices.length > 8 ||
        group.choices.some((choice) => choice.length > 20),
    )
  )
    throw new Error(
      "每组填写 1 至 20 字的名称、1 至 8 个选项，每个选项不超过 20 字。",
    );
  if (
    new Set(parsed.map((group) => group.name)).size !== parsed.length ||
    parsed.some((group) => new Set(group.choices).size !== group.choices.length)
  )
    throw new Error("组名与同组内选项不能重复。");
  return parsed;
}
</script>

<template>
  <section class="catalog-panel">
    <div class="catalog-heading">
      <div>
        <span class="eyebrow">YOUR MENU</span>
        <h2>每一份好味，都在这里</h2>
        <p>管理菜单与线上可卖份数，缺料时暂停供应，补齐后再手动恢复。</p>
      </div>
      <div class="catalog-top-actions">
        <RouterLink
          class="catalog-outline"
          :to="`/stalls/${stall.id}`"
          @click="session.setConsumerPreview(true)"
          >预览菜单</RouterLink
        ><button class="catalog-primary" @click="startEditor()">
          <Plus :size="18" />添加商品
        </button>
      </div>
    </div>
    <p v-if="pendingCreation" class="m-info-banner" role="status">
      上一笔新增商品结果待确认。<button
        class="btn btn-secondary"
        @click="startEditor()"
      >
        确认上一笔新增
      </button>
    </p>
    <MerchantRestock
      v-if="products.length"
      :key="`${session.user?.id}:${stall.id}`"
      :stall="stall"
      @refresh="emit('refresh')"
    />
    <div class="catalog-tools">
      <label class="catalog-search"
        ><Search :size="18" /><input
          v-model="search"
          type="search"
          placeholder="搜索商品名称"
          aria-label="搜索商品名称" /></label
      ><label class="category-select"
        ><SlidersHorizontal :size="16" /><select
          v-model="category"
          aria-label="商品分类筛选"
        >
          <option value="all">全部分类</option>
          <option v-for="item in categories" :key="item" :value="item">
            {{ item }}
          </option></select
        ><ChevronDown :size="14"
      /></label>
    </div>
    <div class="catalog-filters" role="group" aria-label="商品状态筛选">
      <button
        v-for="filter in filters"
        :key="filter.key"
        :class="{ selected: status === filter.key }"
        :aria-pressed="status === filter.key"
        @click="status = filter.key"
      >
        {{ filter.label }}<span>{{ filter.count }}</span>
      </button>
    </div>
    <div v-if="shown.length" class="catalog-grid">
      <article
        v-for="product in shown"
        :key="product.id"
        class="merchant-product product-card"
      >
        <div class="product-top">
          <div class="product-photo">
            <img
              v-if="product.image"
              :src="product.image"
              :alt="product.name"
              loading="lazy"
            /><Package v-else :size="30" />
          </div>
          <div class="product-info">
            <span class="product-category">{{
              product.category || "未分类"
            }}</span>
            <h3>{{ product.name }}</h3>
            <p>{{ product.description || "还没有商品描述" }}</p>
            <strong>¥{{ money(product.price_cents) }}</strong>
          </div>
          <span
            class="product-status"
            :class="
              !product.is_active
                ? 'off'
                : product.sale_paused || product.stock === 0
                  ? 'sold'
                  : 'on'
            "
            >{{
              !product.is_active
                ? "已下架"
                : product.sale_paused
                  ? "暂停供应"
                  : product.stock === 0
                    ? "已售罄"
                    : "销售中"
            }}</span
          >
        </div>
        <div v-if="quickEdits[product.id]" class="quick-fields">
          <label
            >单价（元）<input
              v-model="quickEdits[product.id]!.price"
              type="number"
              min="0.01"
              max="10000"
              step="0.01"
              inputmode="decimal"
              :aria-label="`${product.name}售价`"
              :disabled="busy[product.id]"
              @input="quickEdits[product.id]!.priceDirty = true"
          /></label>
          <div class="stock-readout">
            <span>线上剩余可卖</span><strong>{{ product.stock }} 份</strong>
          </div>
          <button
            class="quick-save"
            :disabled="busy[product.id] || !quickEdits[product.id]!.priceDirty"
            @click="saveQuick(product)"
          >
            <Check :size="15" />保存
          </button>
        </div>
        <div class="product-actions">
          <button :disabled="busy[product.id]" @click="startEditor(product)">
            <Pencil :size="15" />编辑商品
          </button>
          <div>
            <button
              v-if="product.is_active"
              :disabled="busy[product.id]"
              @click="
                patchProduct(
                  product,
                  { sale_paused: !product.sale_paused },
                  product.sale_paused
                    ? '已恢复供应，仍需有线上可卖份数并满足接单条件'
                    : '已暂停供应，补货和订单取消不会自动恢复供应',
                )
              "
            >
              {{ product.sale_paused ? "恢复供应" : "暂停供应" }}</button
            ><button
              :class="{ 'action-orange': !product.is_active }"
              :disabled="busy[product.id]"
              @click="
                patchProduct(
                  product,
                  { is_active: !product.is_active },
                  product.is_active
                    ? '商品已下架'
                    : product.stock === 0
                      ? '已显示在菜单中，当前售罄；请先补货'
                      : '商品已上架',
                )
              "
            >
              {{ product.is_active ? "下架" : "上架" }}
            </button>
          </div>
        </div>
        <MerchantInventoryCorrection
          :key="`${session.user?.id}:${stall.id}:${product.id}`"
          :product="product"
          :stall-id="stall.id"
          @refresh="emit('refresh')"
        />
      </article>
    </div>
    <div v-else class="catalog-empty">
      <div><Package :size="30" /></div>
      <h3>
        {{ products.length ? "没有符合条件的商品" : "把你的招牌放进菜单" }}
      </h3>
      <p>
        {{
          products.length
            ? "试试其他关键词、分类或销售状态。"
            : "先填菜名、价格和线上剩余可卖份数；照片与介绍可以稍后完善。"
        }}
      </p>
      <button
        v-if="!products.length"
        class="catalog-primary"
        @click="startEditor()"
      >
        <Plus :size="16" />添加第一件商品</button
      ><button
        v-else
        class="catalog-outline"
        @click="
          search = '';
          category = 'all';
          status = 'all';
        "
      >
        清除筛选
      </button>
    </div>
  </section>
  <Teleport to="body"
    ><div
      v-if="modal"
      class="product-modal-backdrop"
      @mousedown.self="closeEditor()"
    >
      <section
        ref="modalElement"
        class="product-editor-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="product-editor-title"
        @keydown="modalKeys"
      >
        <header>
          <div>
            <span class="eyebrow">MENU EDITOR</span>
            <h2 id="product-editor-title">
              {{ editingId == null ? "添加新商品" : "编辑商品" }}
            </h2>
          </div>
          <button
            class="modal-close"
            aria-label="关闭商品编辑"
            :disabled="saving || uploading"
            @click="closeEditor()"
          >
            <X :size="22" />
          </button>
        </header>
        <form class="product-editor" @submit.prevent="saveEditor(false)">
          <p v-if="pendingCreation && editingId == null" class="m-info-banner">
            原请求已保留，请先确认新增结果，再修改商品资料。
          </p>
          <fieldset
            class="editor-body"
            :disabled="saving || (editingId == null && !!pendingCreation)"
          >
            <label class="editor-field"
              >商品名称 <span>*</span
              ><input
                ref="nameInput"
                v-model="form.name"
                required
                maxlength="80"
                placeholder="例如：招牌烤冷面"
            /></label>
            <div class="editor-twocol">
              <label class="editor-field"
                >单价（元） <span>*</span
                ><input
                  v-model="form.price"
                  type="number"
                  required
                  min="0.01"
                  max="10000"
                  step="0.01"
                  inputmode="decimal"
                  placeholder="0.00" /></label
              ><label v-if="editingId == null" class="editor-field"
                >线上剩余可卖份数 <span>*</span
                ><input
                  v-model="form.stock"
                  type="number"
                  required
                  min="0"
                  max="100000"
                  step="1"
                  inputmode="numeric"
                  aria-label="线上剩余可卖份数"
                  placeholder="填写实际可售份数"
              /></label>
            </div>
            <p v-if="editingId == null" class="stock-help">
              {{
                form.stock !== "" && Number(form.stock) === 0
                  ? "当前填了 0 份：可以保存，但顾客暂时不能购买。"
                  : "填写留给线上新订单的份数，不包含已被订单预留的部分，也不是线下实物总数。"
              }}
            </p>
            <p v-else class="stock-help">
              这里仅编辑商品资料。新增线上份数使用“今天补货”，盘点调整使用商品卡片中的“更正线上可卖份数”。
            </p>
            <details class="editor-extras">
              <summary>照片、分类与介绍（选填）</summary>
              <div class="editor-photo-row">
                <div class="editor-photo">
                  <img
                    v-if="form.image"
                    :src="form.image"
                    alt="商品图片预览"
                  /><ImagePlus v-else :size="34" />
                </div>
                <div>
                  <label
                    class="upload-button"
                    :class="{ disabled: uploading || saving }"
                    ><Upload :size="16" />{{
                      uploading ? "正在上传…" : "上传商品照片"
                    }}<input
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      :disabled="uploading || saving"
                      @change="uploadImage"
                  /></label>
                  <p>
                    JPG、PNG、WebP，最大 5 MB<br />使用真实照片，让好味道被看见。
                  </p>
                  <button
                    v-if="form.image"
                    type="button"
                    class="remove-photo"
                    :disabled="uploading || saving"
                    @click="form.image = ''"
                  >
                    移除照片
                  </button>
                </div>
              </div>
              <label class="editor-field"
                >商品分类<input
                  v-model="form.category"
                  list="merchant-product-categories"
                  maxlength="30"
                  placeholder="例如：招牌小吃、清爽饮品" /><datalist
                  id="merchant-product-categories"
                >
                  <option
                    v-for="item in categories.filter((c) => c !== '未分类')"
                    :key="item"
                    :value="item"
                  /></datalist
              ></label>
              <label class="editor-field"
                >商品描述<textarea
                  v-model="form.description"
                  maxlength="200"
                  rows="3"
                  placeholder="介绍配料、口味或份量，帮助顾客挑选"
                /><small>{{ form.description.length }} / 200</small></label
              >
            </details>
            <details class="editor-extras taste-editor">
              <summary>
                口味选择（选填、免费）<span v-if="form.taste_options.length">
                  · {{ form.taste_options.length }} 组</span
                >
              </summary>
              <p class="stock-help">
                例如辣度、甜度。每组选择一种，不改变价格；加料或不同售价请单独建立商品。
              </p>
              <div
                v-for="(group, index) in form.taste_options"
                :key="index"
                class="taste-group"
              >
                <label class="editor-field"
                  >第 {{ index + 1 }} 组名称<input
                    v-model="group.name"
                    maxlength="20"
                    placeholder="例如：辣度"
                    :disabled="saving"
                /></label>
                <label class="editor-field"
                  >选项，用顿号隔开<textarea
                    v-model="group.choices"
                    rows="2"
                    :aria-label="`第 ${index + 1} 组选项`"
                    placeholder="例如：不辣、微辣、中辣"
                    :disabled="saving"
                  />
                </label>
                <button
                  type="button"
                  class="remove-photo"
                  :disabled="saving"
                  @click="form.taste_options.splice(index, 1)"
                >
                  移除第 {{ index + 1 }} 组
                </button>
              </div>
              <button
                type="button"
                class="catalog-outline"
                :disabled="saving || form.taste_options.length >= 3"
                @click="form.taste_options.push({ name: '', choices: '' })"
              >
                <Plus :size="16" />添加一组口味
                {{ form.taste_options.length }}/3
              </button>
            </details>
            <label class="editor-toggle"
              ><div>
                <strong>显示在菜单中</strong
                ><small
                  >下架后隐藏；购买还需未暂停供应、有线上可卖份数并满足接单条件</small
                >
              </div>
              <input
                v-model="form.is_active"
                type="checkbox"
                role="switch"
                aria-label="上架销售" /><span class="switch-track"
                ><span /></span
            ></label>
          </fieldset>
          <p v-if="formError" class="editor-error" role="alert">
            {{ formError }}
          </p>
          <footer>
            <button
              type="button"
              class="catalog-outline"
              :disabled="saving || uploading"
              @click="closeEditor()"
            >
              取消</button
            ><button
              v-if="editingId == null && !pendingCreation"
              type="button"
              class="catalog-outline"
              :disabled="saving || uploading"
              @click="saveEditor(true)"
            >
              保存并继续添加</button
            ><button
              type="submit"
              class="catalog-primary"
              :disabled="saving || uploading"
            >
              <Check :size="17" />{{
                saving
                  ? "保存中…"
                  : pendingCreation && editingId == null
                    ? "确认原新增结果"
                    : editingId == null
                      ? "添加商品"
                      : "保存修改"
              }}
            </button>
          </footer>
        </form>
      </section>
    </div></Teleport
  >
</template>

<style scoped>
.stock-readout {
  display: grid;
  gap: 7px;
  font-size: 12px;
  color: #7c6650;
}
.stock-readout strong {
  display: flex;
  align-items: center;
  min-height: 44px;
  font-size: 17px;
  color: #58432f;
}
.catalog-top-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.catalog-top-actions a {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}
.stock-help {
  font-size: 13px;
  line-height: 1.7;
  color: #795431;
  margin: 0;
}
.taste-group {
  border-top: 1px solid #e7d5c0;
  margin-top: 16px;
  padding-top: 16px;
  display: grid;
  gap: 12px;
}
.taste-editor > p {
  margin: 12px 0;
}
.taste-editor > button {
  margin-top: 14px;
}
.editor-extras {
  background: #fff6eb;
  border: 1px solid #edddc9;
  border-radius: 12px;
  padding: 0 14px;
}
.editor-extras > summary {
  min-height: 48px;
  cursor: pointer;
  display: flex;
  align-items: center;
  color: #775035;
  font-size: 14px;
}
.editor-extras[open] {
  padding-bottom: 16px;
}
.editor-extras .editor-field {
  margin-top: 16px;
}
.product-editor footer {
  flex-wrap: wrap;
}
.catalog-panel {
  min-width: 0;
}
.eyebrow {
  display: block;
  font-size: 10px;
  letter-spacing: 2px;
  color: #a47653;
  font-weight: 700;
  margin-bottom: 9px;
}
.catalog-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 26px;
}
.catalog-heading h2 {
  font-size: 25px;
  color: #392e24;
}
.catalog-heading p {
  color: #867867;
  font-size: 13px;
  margin-top: 7px;
}
.catalog-primary,
.catalog-outline {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  border-radius: 12px;
  padding: 0 18px;
  white-space: nowrap;
  font-weight: 600;
  font-size: 13px;
}
.catalog-primary {
  background: #ed742f;
  color: white;
}
.catalog-primary:hover:not(:disabled) {
  background: #d75e1c;
}
.catalog-outline {
  background: #fff;
  border: 1px solid #e8dfd3;
}
.catalog-tools {
  display: flex;
  gap: 12px;
  margin-bottom: 15px;
}
.catalog-search {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  background: #fff;
  border: 1px solid #e9e1d5;
  border-radius: 12px;
  padding: 0 14px;
  color: #8e7b68;
}
.catalog-search input {
  min-height: 46px;
  border: 0;
  background: none;
  width: 100%;
  font-size: 13px;
}
.category-select {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 13px;
  background: white;
  border: 1px solid #e9e1d5;
  border-radius: 12px;
  color: #65574a;
}
.category-select select {
  min-height: 46px;
  border: 0;
  background: none;
  appearance: none;
  padding: 0 5px;
  max-width: 150px;
  font-size: 13px;
}
.catalog-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 20px;
}
.catalog-filters button {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  min-height: 42px;
  border-radius: 10px;
  font-size: 13px;
  color: #807260;
}
.catalog-filters button span {
  font-size: 11px;
  border-radius: 5px;
  background: #eee8df;
  padding: 2px 5px;
}
.catalog-filters button.selected {
  background: #ffebdc;
  color: #b64e17;
  font-weight: 700;
}
.catalog-filters button.selected span {
  background: #ffdac1;
}
.catalog-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 17px;
}
.product-card {
  padding: 20px;
  border: 1px solid #eae2d7;
  border-radius: 18px;
  background: #fff;
}
.product-top {
  display: flex;
  gap: 14px;
  position: relative;
  min-height: 102px;
}
.product-photo {
  width: 88px;
  height: 88px;
  border-radius: 13px;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: #f7eee2;
  color: #b99d80;
}
.product-photo img {
  height: 100%;
  width: 100%;
  object-fit: cover;
}
.product-info {
  min-width: 0;
  flex: 1;
  padding-top: 1px;
}
.product-category {
  color: #907e69;
  font-size: 10px;
  display: block;
  min-height: 17px;
  padding-right: 50px;
}
.product-info h3 {
  font-size: 16px;
  color: #382e24;
  margin-bottom: 4px;
  overflow-wrap: anywhere;
}
.product-info p {
  font-size: 11px;
  line-height: 1.6;
  color: #8b8073;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.product-info strong {
  display: block;
  color: #cf5f22;
  font-size: 19px;
  margin-top: 6px;
}
.product-status {
  position: absolute;
  top: 0;
  right: 0;
  font-size: 10px;
  border-radius: 5px;
  padding: 4px 6px;
  line-height: 1.2;
}
.product-status.on {
  background: #eaf3eb;
  color: #4e7650;
}
.product-status.sold {
  background: #fff0df;
  color: #b47728;
}
.product-status.off {
  background: #f0eded;
  color: #847974;
}
.quick-fields {
  display: flex;
  align-items: end;
  gap: 9px;
  background: #fcfaf6;
  border: 1px solid #f2ede6;
  border-radius: 12px;
  padding: 11px;
  margin-top: 14px;
}
.quick-fields label {
  flex: 1;
  min-width: 0;
  font-size: 10px;
  color: #817361;
}
.quick-fields input {
  display: block;
  width: 100%;
  min-height: 38px;
  background: #fff;
  border: 1px solid #e9e1d5;
  border-radius: 7px;
  margin-top: 6px;
  padding: 0 8px;
  color: #483b2f;
  font-size: 13px;
}
.quick-save {
  display: flex;
  align-items: center;
  gap: 3px;
  min-height: 38px;
  padding: 0 9px;
  font-size: 12px;
  background: #f6e4d5;
  color: #a74f1e;
  border-radius: 7px;
}
.quick-save:disabled {
  opacity: 0.45;
}
.product-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid #f0eae2;
  margin-top: 15px;
  padding-top: 6px;
  gap: 6px;
}
.product-actions > button {
  display: flex;
  align-items: center;
  gap: 5px;
}
.product-actions button {
  min-height: 38px;
  padding: 0 5px;
  font-size: 11px;
  color: #817362;
}
.product-actions > div {
  display: flex;
  gap: 10px;
}
.product-actions .action-orange {
  color: #d36323;
  font-weight: 700;
}
.catalog-empty {
  text-align: center;
  background: #fff;
  border: 1px dashed #e3d5c5;
  border-radius: 18px;
  padding: 60px 20px;
}
.catalog-empty > div {
  height: 64px;
  width: 64px;
  border-radius: 20px;
  background: #fff0e0;
  color: #c28755;
  display: grid;
  place-items: center;
  margin: 0 auto 20px;
}
.catalog-empty h3 {
  font-size: 18px;
}
.catalog-empty p {
  font-size: 13px;
  color: #958370;
  margin: 10px 0 20px;
}
.product-modal-backdrop {
  position: fixed;
  inset: 0;
  background: #25180f66;
  backdrop-filter: blur(4px);
  display: flex;
  justify-content: flex-end;
  z-index: 180;
  padding: 16px;
}
.product-editor-panel {
  background: #fffcf7;
  border: 1px solid #fff8f0;
  border-radius: 24px;
  width: 560px;
  max-width: 100%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  box-shadow: -20px 0 80px #2f1c121f;
}
.product-editor-panel > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 25px 28px 21px;
  border-bottom: 1px solid #eee4d9;
  gap: 12px;
}
.product-editor-panel h2 {
  font-size: 23px;
}
.product-editor-panel .eyebrow {
  margin-bottom: 5px;
}
.modal-close {
  display: grid;
  place-items: center;
  border-radius: 50%;
  width: 44px;
  height: 44px;
  background: #f6eee4;
  flex-shrink: 0;
}
.product-editor-panel form {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}
.editor-body {
  padding: 24px 28px;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.editor-photo-row {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 25px;
}
.editor-photo {
  width: 122px;
  height: 122px;
  border: 1px dashed #d7c7b3;
  border-radius: 15px;
  background: #faf0e4;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #ac8969;
  flex-shrink: 0;
}
.editor-photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.upload-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  padding: 0 13px;
  background: #fff;
  border: 1px solid #e4d5c4;
  border-radius: 9px;
  font-size: 12px;
  cursor: pointer;
  position: relative;
}
.upload-button input {
  position: absolute;
  width: 100%;
  height: 100%;
  opacity: 0;
  left: 0;
  top: 0;
  cursor: pointer;
}
.upload-button:focus-within {
  outline: 3px solid #e86a2790;
  outline-offset: 3px;
}
.upload-button.disabled {
  opacity: 0.5;
}
.editor-photo-row p {
  font-size: 10px;
  color: #9b8772;
  line-height: 1.8;
  margin-top: 8px;
}
.remove-photo {
  font-size: 11px;
  color: #b76034;
  min-height: 30px;
  padding: 0;
}
.editor-field {
  display: block;
  font-size: 12px;
  color: #76634f;
  margin-bottom: 18px;
  font-weight: 500;
  position: relative;
}
.editor-field > span {
  color: #dd703a;
}
.editor-field input,
.editor-field textarea {
  width: 100%;
  display: block;
  border: 1px solid #e7dccf;
  border-radius: 10px;
  background: #fff;
  padding: 12px 13px;
  margin-top: 8px;
  font-size: 14px;
  color: #473b30;
  line-height: 1.5;
}
.editor-field textarea {
  resize: vertical;
  min-height: 90px;
}
.editor-field small {
  display: block;
  text-align: right;
  color: #a69583;
  font-size: 10px;
  margin-top: 5px;
}
.editor-twocol {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.editor-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: relative;
  gap: 15px;
  background: #faf0e4;
  border-radius: 12px;
  padding: 15px;
}
.editor-toggle strong {
  font-size: 13px;
  display: block;
}
.editor-toggle small {
  display: block;
  color: #8c7760;
  font-size: 10px;
  margin-top: 5px;
}
.editor-toggle input {
  position: absolute;
  width: 45px;
  height: 28px;
  right: 15px;
  opacity: 0;
  cursor: pointer;
  z-index: 1;
}
.switch-track {
  height: 26px;
  width: 45px;
  border-radius: 15px;
  background: #cbbbaa;
  padding: 3px;
  display: block;
  flex-shrink: 0;
  transition: background 0.15s;
}
.switch-track span {
  display: block;
  height: 20px;
  width: 20px;
  border-radius: 50%;
  background: white;
  transition: transform 0.15s;
}
.editor-toggle input:checked + .switch-track {
  background: #e66c2a;
}
.editor-toggle input:checked + .switch-track span {
  transform: translateX(19px);
}
.editor-toggle input:focus-visible + .switch-track {
  outline: 3px solid #e86a2790;
  outline-offset: 3px;
}
.editor-error {
  font-size: 12px;
  color: #b3412a;
  background: #fff0e8;
  border: 1px solid #f3cebc;
  padding: 12px;
  border-radius: 10px;
  margin-top: 16px;
}
.product-editor-panel footer {
  padding: 18px 28px;
  border-top: 1px solid #eee4d9;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  background: #fffcf7;
  border-radius: 0 0 24px 24px;
}
.product-editor-panel footer .catalog-primary {
  min-width: 150px;
}
@media (max-width: 1000px) {
  .catalog-grid {
    grid-template-columns: 1fr;
  }
  .product-photo {
    width: 95px;
    height: 95px;
  }
}
@media (max-width: 600px) {
  .catalog-heading {
    gap: 10px;
    margin-bottom: 21px;
    align-items: flex-start;
  }
  .catalog-heading h2 {
    font-size: 20px;
  }
  .catalog-heading p {
    font-size: 11px;
    max-width: 215px;
  }
  .catalog-heading > .catalog-primary {
    font-size: 11px;
    padding: 0 11px;
    margin-top: 21px;
    min-height: 40px;
    gap: 3px;
  }
  .eyebrow {
    font-size: 9px;
  }
  .catalog-tools {
    gap: 8px;
  }
  .category-select {
    padding: 0 9px;
    gap: 5px;
  }
  .category-select > svg:first-child {
    display: none;
  }
  .category-select select {
    font-size: 12px;
    max-width: 100px;
  }
  .catalog-search {
    padding: 0 10px;
    gap: 7px;
  }
  .catalog-search input {
    font-size: 12px;
  }
  .catalog-filters {
    gap: 1px;
    justify-content: space-between;
  }
  .catalog-filters button {
    padding: 0 8px;
    font-size: 11px;
    gap: 4px;
  }
  .catalog-filters button span {
    font-size: 9px;
  }
  .product-card {
    padding: 16px;
    border-radius: 15px;
  }
  .product-photo {
    width: 83px;
    height: 83px;
  }
  .product-info h3 {
    font-size: 15px;
  }
  .product-top {
    gap: 12px;
  }
  .product-actions button {
    min-height: 42px;
  }
  .product-modal-backdrop {
    padding: 0;
    align-items: flex-end;
  }
  .product-editor-panel {
    width: 100%;
    height: 95dvh;
    border-radius: 22px 22px 0 0;
  }
  .product-editor-panel > header {
    padding: 20px;
  }
  .editor-body {
    padding: 20px;
  }
  .editor-photo-row {
    gap: 15px;
  }
  .editor-photo {
    height: 100px;
    width: 100px;
  }
  .editor-photo-row p {
    font-size: 9px;
  }
  .product-editor-panel footer {
    padding: 15px 20px max(15px, env(safe-area-inset-bottom));
    border-radius: 0;
  }
  .product-editor-panel footer .catalog-primary {
    flex: 1;
  }
  .product-editor-panel footer .catalog-outline {
    flex: 1;
  }
  .editor-field input,
  .editor-field textarea {
    font-size: 16px;
  }
  .quick-fields input {
    font-size: 16px;
  }
  .catalog-grid {
    gap: 13px;
  }
}
</style>

<style scoped>
fieldset.editor-body {
  border: 0;
  margin: 0;
  min-width: 0;
}
</style>
