<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import { Camera, Check, ImagePlus, RefreshCw, SlidersHorizontal, Sparkles, Upload, X } from "lucide-vue-next";
import { api } from "../../lib/api";
import { prepareDishPhoto } from "../../lib/imagePrepare";
import { ownedImage } from "../../lib/media";

export type AssistDisplayAvailability = "available" | "sold_out" | "paused";
export type AssistDraft = {
  name: string;
  description: string;
  image: string;
  category: string;
  price: string;
  stock: string;
  is_active: boolean;
  display_availability: AssistDisplayAvailability;
  taste_options: { name: string; choices: string }[];
};

type AiDraftResponse = {
  ai_available: boolean;
  fallback_reason: string;
  is_food: boolean;
  name: string;
  name_candidates: string[];
  category: string;
  description: string;
  highlight_tags: string[];
  ask_hint: string;
  notice: string;
};

const props = defineProps<{
  open: boolean;
  stall: any;
  discoveryOnly?: boolean;
  categories: string[];
  suggestedPrices: string[];
  pendingCreationExists: boolean;
  saving: boolean;
  submitError: string;
}>();

const emit = defineEmits<{
  close: [];
  submitDraft: [draft: AssistDraft];
  handoffToEditor: [draft: AssistDraft];
  openPendingRecovery: [];
}>();

const panelEl = ref<HTMLElement>();
const discardEl = ref<HTMLElement>();
const continueBtn = ref<HTMLButtonElement>();
const cameraBtn = ref<HTMLElement>();
const nameInput = ref<HTMLInputElement>();
const priceInput = ref<HTMLInputElement>();

const uploading = ref(false);
const initialAnalyzing = ref(false);
const refining = ref(false);
const discardOpen = ref(false);
const localError = ref("");
const localPreviewUrl = ref("");
const merchantNote = ref("");
const selectedTags = ref<string[]>([]);

let aiAbort: AbortController | null = null;
let closeFocusTarget: HTMLElement | null = null;

const blankDraft = (): AssistDraft => ({
  name: "",
  description: "",
  image: "",
  category: "",
  price: "",
  stock: "",
  is_active: true,
  display_availability: "available",
  taste_options: [],
});

const draft = reactive<AssistDraft>(blankDraft());

const aiState = reactive<AiDraftResponse>({
  ai_available: false,
  fallback_reason: "",
  is_food: true,
  name: "",
  name_candidates: [],
  category: "",
  description: "",
  highlight_tags: ["现点现做", "趁热享用", "份量实在", "招牌推荐"],
  ask_hint: "可点选符合实物的特点，或补一句真实主料后润色文案",
  notice: "",
});

const hasDirtyDraft = computed(() =>
  Boolean(
    draft.image ||
      draft.name.trim() ||
      draft.price.trim() ||
      draft.stock.trim() ||
      draft.description.trim() ||
      merchantNote.value.trim() ||
      selectedTags.value.length,
  ),
);

const combinedError = computed(() => props.submitError || localError.value);
const locked = computed(() => uploading.value || props.saving);

function revokePreview() {
  if (localPreviewUrl.value) {
    URL.revokeObjectURL(localPreviewUrl.value);
    localPreviewUrl.value = "";
  }
}

function resetState() {
  aiAbort?.abort();
  aiAbort = null;
  revokePreview();
  Object.assign(draft, blankDraft());
  merchantNote.value = "";
  selectedTags.value = [];
  localError.value = "";
  uploading.value = false;
  initialAnalyzing.value = false;
  refining.value = false;
  discardOpen.value = false;
  Object.assign(aiState, {
    ai_available: false,
    fallback_reason: "",
    is_food: true,
    name: "",
    name_candidates: [],
    category: "",
    description: "",
    highlight_tags: ["现点现做", "趁热享用", "份量实在", "招牌推荐"],
    ask_hint: "可点选符合实物的特点，或补一句真实主料后润色文案",
    notice: "",
  });
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      resetState();
      nextTick(() => {
        const first = panelEl.value?.querySelector<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), [tabindex="0"]',
        );
        (cameraBtn.value || first)?.focus();
      });
    } else {
      aiAbort?.abort();
      revokePreview();
    }
  },
);

watch(
  () => props.stall?.id,
  () => {
    if (props.open) emit("close");
  },
);

onBeforeUnmount(() => {
  aiAbort?.abort();
  revokePreview();
});

function requestClose(force = false) {
  if (!props.open || (!force && locked.value)) return;
  if (!force && hasDirtyDraft.value && !props.pendingCreationExists) {
    if (!discardOpen.value) {
      closeFocusTarget = panelEl.value?.contains(document.activeElement)
        ? (document.activeElement as HTMLElement)
        : null;
      discardOpen.value = true;
      nextTick(() => continueBtn.value?.focus());
    }
    return;
  }
  aiAbort?.abort();
  discardOpen.value = false;
  emit("close");
}

function cancelDiscard() {
  discardOpen.value = false;
  nextTick(() => closeFocusTarget?.focus());
}

function confirmDiscard() {
  requestClose(true);
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    if (discardOpen.value) cancelDiscard();
    else requestClose();
    return;
  }
  if (event.key !== "Tab") return;
  const scope = discardOpen.value ? discardEl.value : panelEl.value;
  const focusable = Array.from(
    scope?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
    ) || [],
  ).filter((el) => el.getClientRects().length > 0);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}

async function runAiDraft(isInitial: boolean) {
  if (!draft.image) return;
  aiAbort?.abort();
  const controller = new AbortController();
  aiAbort = controller;
  localError.value = "";
  if (isInitial) initialAnalyzing.value = true;
  else refining.value = true;

  const stallId = props.stall.id;
  try {
    const res = await api<AiDraftResponse>(
      `/merchant/stalls/${stallId}/ai-product-draft`,
      {
        method: "POST",
        signal: controller.signal,
        body: {
          image: draft.image,
          merchant_name: isInitial ? "" : draft.name.trim(),
          merchant_note: merchantNote.value.trim(),
          selected_tags: selectedTags.value,
        },
      },
    );
    if (!props.open || stallId !== props.stall.id) return;
    Object.assign(aiState, res);
    if (isInitial) {
      if (res.name) draft.name = res.name;
      if (res.category) draft.category = res.category;
      if (res.description) draft.description = res.description;
    } else {
      if (!draft.name.trim() && res.name) draft.name = res.name;
      if (res.category && !draft.category.trim()) draft.category = res.category;
      if (res.description) draft.description = res.description;
    }
    await nextTick();
    if (isInitial) {
      if (draft.name.trim()) priceInput.value?.focus();
      else nameInput.value?.focus();
    }
  } catch (err) {
    if (controller.signal.aborted) return;
    if (isInitial) {
      Object.assign(aiState, {
        ai_available: false,
        fallback_reason: "network_error",
        is_food: true,
        notice: "照片已上传，智能文案暂未连通；请确认菜名与售价后直接上架。",
      });
      await nextTick();
      nameInput.value?.focus();
    } else {
      localError.value = (err as Error).message || "润色文案失败，可直接手动修改商品介绍。";
    }
  } finally {
    if (aiAbort === controller) aiAbort = null;
    if (isInitial) initialAnalyzing.value = false;
    else refining.value = false;
  }
}

function skipAiWait() {
  aiAbort?.abort();
  aiAbort = null;
  initialAnalyzing.value = false;
  aiState.ai_available = false;
  aiState.notice = "已跳过智能识图等待，照片已保存；请填写菜名与售价后上架。";
  nextTick(() => nameInput.value?.focus());
}

async function onPickFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const rawFile = input.files?.[0];
  input.value = "";
  if (!rawFile || locked.value) return;

  localError.value = "";
  uploading.value = true;
  revokePreview();
  if (typeof URL?.createObjectURL === "function") {
    localPreviewUrl.value = URL.createObjectURL(rawFile);
  }

  const stallId = props.stall.id;
  try {
    const prepared = await prepareDishPhoto(rawFile);
    const formData = new FormData();
    formData.append("file", prepared);
    const uploaded = await api<{ url: string }>(
      `/merchant/stalls/${stallId}/image`,
      { method: "POST", body: formData },
    );
    if (!props.open || stallId !== props.stall.id) return;
    draft.image = uploaded.url;
    selectedTags.value = [];
    uploading.value = false;
    await runAiDraft(true);
  } catch (err) {
    revokePreview();
    localError.value = (err as Error).message || "照片上传失败，请重试。";
  } finally {
    uploading.value = false;
  }
}

function toggleTag(tag: string) {
  const idx = selectedTags.value.indexOf(tag);
  if (idx >= 0) selectedTags.value.splice(idx, 1);
  else if (selectedTags.value.length < 6) selectedTags.value.push(tag);
}

function submit() {
  if (locked.value || initialAnalyzing.value || refining.value) return;
  localError.value = "";
  if (!draft.name.trim()) {
    localError.value = "请先确认这道菜叫什么名字。";
    nameInput.value?.focus();
    return;
  }
  if (!/^\d+(\.\d{1,2})?$/.test(draft.price.trim()) || Number(draft.price) <= 0) {
    localError.value = "请填写大于 0 的单价（元），最多两位小数。";
    priceInput.value?.focus();
    return;
  }
  if (!props.discoveryOnly && (!/^\d+$/.test(draft.stock.trim()) || Number(draft.stock) > 100000)) {
    localError.value = "请填写今日留给线上的可卖份数（0 至 100,000 的整数）。";
    return;
  }
  emit("submitDraft", JSON.parse(JSON.stringify(draft)));
}

function handoff() {
  if (locked.value) return;
  aiAbort?.abort();
  emit("handoffToEditor", JSON.parse(JSON.stringify(draft)));
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="assist-backdrop"
      @mousedown.self.prevent="requestClose()"
    >
      <section
        ref="panelEl"
        class="assist-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="photo-assist-title"
        @keydown="handleKeydown"
      >
        <header :inert="discardOpen">
          <div>
            <span class="assist-eyebrow">拍照辅助上架</span>
            <h2 id="photo-assist-title">拍菜识图上架</h2>
          </div>
          <button
            type="button"
            class="assist-close"
            aria-label="关闭拍照上架"
            :disabled="locked"
            @click="requestClose()"
          >
            <X :size="22" />
          </button>
        </header>

        <div v-if="pendingCreationExists" class="assist-body" :inert="discardOpen">
          <div class="assist-pending-box" role="status">
            <h3>上一笔新增商品还在等待确认</h3>
            <p>
              为防止网络波动造成重复建品，请先核对上一笔新增商品的结果，完成后再拍照上架新菜品。
            </p>
            <button
              type="button"
              class="assist-primary"
              @click="emit('openPendingRecovery')"
            >
              去确认上一笔新增
            </button>
          </div>
        </div>

        <form
          v-else
          class="assist-form"
          :inert="discardOpen"
          @submit.prevent="submit"
        >
          <div class="assist-body">
            <!-- Stage 1: Pick or Snap Photo -->
            <div v-if="!draft.image && !uploading" class="assist-capture-card">
              <div class="assist-capture-icon" aria-hidden="true">
                <Camera :size="30" />
              </div>
              <h3>拍一张本店菜品实拍</h3>
              <p>
                自动压缩大图并由 DeepSeek 识别菜名与撰写介绍；随后只需确认<strong>菜名</strong>与<strong>定价</strong>即可上架。
              </p>
              <div class="assist-capture-actions">
                <label ref="cameraBtn" class="assist-primary assist-file-btn" tabindex="0">
                  <Camera :size="18" />拍照识菜
                  <input
                    type="file"
                    accept="image/*"
                    capture="environment"
                    aria-label="拍照识菜"
                    @change="onPickFile"
                  />
                </label>
                <label class="assist-outline assist-file-btn" tabindex="0">
                  <Upload :size="17" />从相册选图
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    aria-label="从相册选图"
                    @change="onPickFile"
                  />
                </label>
              </div>
              <small class="assist-hint-note">
                手机原图会自动缩放压缩；请拍摄真实餐点，勿上传收款码图片。
              </small>
            </div>

            <!-- Stage 2: Uploading / AI Recognizing -->
            <div
              v-else-if="uploading || initialAnalyzing"
              class="assist-loading-card"
              role="status"
              aria-live="polite"
            >
              <div class="assist-preview-thumb">
                <img
                  v-if="ownedImage(draft.image) || localPreviewUrl"
                  :src="ownedImage(draft.image) || localPreviewUrl"
                  alt="菜品照片预览"
                />
                <ImagePlus v-else :size="28" />
              </div>
              <div class="assist-loading-text">
                <strong>{{
                  uploading
                    ? "正在压缩并上传实拍照片…"
                    : "DeepSeek 正在看图整理菜名与文案…"
                }}</strong>
                <p>
                  {{
                    uploading
                      ? "大图会自动优化，节省手机流量。"
                      : "识别完成后会请你核对菜名和售价，不会擅自开卖。"
                  }}
                </p>
                <button
                  v-if="initialAnalyzing && !uploading"
                  type="button"
                  class="assist-outline assist-skip-btn"
                  @click="skipAiWait"
                >
                  跳过等待，直接手填
                </button>
              </div>
            </div>

            <!-- Stage 3: Concise 3-Question Inquiry & Instant Listing -->
            <template v-else>
              <div class="assist-top-summary">
                <div class="assist-preview-thumb">
                  <img
                    v-if="ownedImage(draft.image)"
                    :src="ownedImage(draft.image)"
                    alt="已上传的菜品实拍"
                  />
                  <ImagePlus v-else :size="28" />
                </div>
                <div class="assist-summary-info">
                  <span
                    v-if="aiState.ai_available && aiState.is_food"
                    class="assist-badge ok"
                  >
                    <Sparkles :size="13" />已根据实拍生成初稿
                  </span>
                  <span v-else-if="!aiState.is_food" class="assist-badge warn">
                    请核对实拍画面
                  </span>
                  <span v-else class="assist-badge neutral">照片已就绪</span>
                  <p v-if="aiState.notice" :role="!aiState.is_food ? 'alert' : undefined" class="assist-notice">
                    {{ aiState.notice }}
                  </p>
                  <p v-else class="assist-notice">
                    核对菜名、填好定价就能上架；需要补充特色可点下方标签润色。
                  </p>
                  <label class="assist-retake" :class="{ disabled: locked || refining }">
                    <RefreshCw :size="13" />重拍 / 换图
                    <input
                      type="file"
                      accept="image/*"
                      :disabled="locked || refining"
                      aria-label="重拍或更换商品照片"
                      @change="onPickFile"
                    />
                  </label>
                </div>
              </div>

              <!-- Question 1: Dish Name -->
              <section class="assist-question-block" aria-labelledby="q-dish-name">
                <div class="assist-q-title" id="q-dish-name">
                  <span>1</span>这道菜叫什么名字？<em>*</em>
                </div>
                <input
                  ref="nameInput"
                  v-model="draft.name"
                  class="assist-input"
                  aria-label="这道菜叫什么名字"
                  required
                  maxlength="80"
                  :disabled="saving"
                  placeholder="例如：招牌鸡蛋烤冷面"
                />
                <div
                  v-if="aiState.name_candidates.length"
                  class="assist-chips"
                  role="group"
                  aria-label="候选菜名"
                >
                  <button
                    v-for="cand in aiState.name_candidates"
                    :key="cand"
                    type="button"
                    class="assist-chip"
                    :class="{ active: draft.name === cand }"
                    :disabled="saving"
                    @click="draft.name = cand"
                  >
                    {{ cand }}
                  </button>
                </div>
              </section>

              <!-- Question 2: Price & Supply -->
              <section class="assist-question-block" aria-labelledby="q-price-supply">
                <div class="assist-q-title" id="q-price-supply">
                  <span>2</span>准备卖多少钱、{{ discoveryOnly ? "今天供应状态" : "留给线上几份" }}？<em>*</em>
                </div>
                <div class="assist-twocol">
                  <div class="assist-sub-field">
                    <label for="assist-price-input">单价（元）</label>
                    <input
                      id="assist-price-input"
                      ref="priceInput"
                      v-model="draft.price"
                      class="assist-input"
                      type="number"
                      required
                      min="0.01"
                      max="10000"
                      step="0.01"
                      inputmode="decimal"
                      :disabled="saving"
                      placeholder="0.00"
                    />
                    <div class="assist-chips compact" role="group" aria-label="常用单价快捷选择">
                      <button
                        v-for="p in suggestedPrices"
                        :key="p"
                        type="button"
                        class="assist-chip"
                        :class="{ active: Number(draft.price) === Number(p) && draft.price !== '' }"
                        :disabled="saving"
                        @click="draft.price = p"
                      >
                        ¥{{ p }}
                      </button>
                    </div>
                  </div>

                  <div v-if="!discoveryOnly" class="assist-sub-field">
                    <label for="assist-stock-input">线上剩余可卖份数</label>
                    <input
                      id="assist-stock-input"
                      v-model="draft.stock"
                      class="assist-input"
                      type="number"
                      required
                      min="0"
                      max="100000"
                      step="1"
                      inputmode="numeric"
                      :disabled="saving"
                      placeholder="填写整数份数"
                    />
                    <div class="assist-chips compact" role="group" aria-label="常用份数快捷选择">
                      <button
                        v-for="qty in ['10', '20', '30', '0']"
                        :key="qty"
                        type="button"
                        class="assist-chip"
                        :class="{ active: draft.stock === qty }"
                        :disabled="saving"
                        @click="draft.stock = qty"
                      >
                        {{ qty === "0" ? "0 份暂售罄" : `${qty} 份` }}
                      </button>
                    </div>
                  </div>

                  <div v-else class="assist-sub-field">
                    <span class="assist-label-text">今日供应展示</span>
                    <div class="assist-supply-Segment" role="group" aria-label="今日供应">
                      <button
                        v-for="opt in ([
                          { value: 'available', label: '今天有' },
                          { value: 'sold_out', label: '卖完了' },
                          { value: 'paused', label: '暂时不卖' },
                        ] as const)"
                        :key="opt.value"
                        type="button"
                        :aria-pressed="draft.display_availability === opt.value"
                        :disabled="saving"
                        @click="draft.display_availability = opt.value"
                      >
                        {{ opt.label }}
                      </button>
                    </div>
                  </div>
                </div>
                <p
                  v-if="!discoveryOnly && draft.stock === '0'"
                  class="assist-zero-note"
                >
                  填 0 份会先保存进菜单，后续补货后顾客才可下单。
                </p>
              </section>

              <!-- Question 3: Optional Highlights & Copywriting -->
              <section class="assist-question-block" aria-labelledby="q-copywriting">
                <div class="assist-q-title" id="q-copywriting">
                  <span>3</span>有什么真实特色想告诉学生？<small>（选填）</small>
                </div>
                <p class="assist-q-sub">{{ aiState.ask_hint }}</p>
                <div
                  v-if="aiState.highlight_tags.length"
                  class="assist-chips"
                  role="group"
                  aria-label="菜品特色标签"
                >
                  <button
                    v-for="tag in aiState.highlight_tags"
                    :key="tag"
                    type="button"
                    class="assist-chip"
                    :class="{ active: selectedTags.includes(tag) }"
                    :aria-pressed="selectedTags.includes(tag)"
                    :disabled="saving || refining"
                    @click="toggleTag(tag)"
                  >
                    {{ tag }}
                  </button>
                </div>
                <div class="assist-refine-row">
                  <input
                    v-model="merchantNote"
                    class="assist-input"
                    maxlength="120"
                    :disabled="saving || refining"
                    aria-label="补充真实主料或做法"
                    placeholder="选填：如猪肉大葱馅、现烙酥皮"
                  />
                  <button
                    type="button"
                    class="assist-outline assist-refine-btn"
                    :disabled="saving || refining"
                    @click="runAiDraft(false)"
                  >
                    <Sparkles :size="15" />{{
                      refining ? "生成中…" : "润色文案"
                    }}
                  </button>
                </div>

                <label class="assist-field-stack">
                  <span>商品文案（可直接修改）</span>
                  <textarea
                    v-model="draft.description"
                    class="assist-textarea"
                    maxlength="200"
                    rows="3"
                    :disabled="saving"
                    placeholder="如实介绍外观与真实配料，勿编造未录入配料、规格或销量"
                  />
                  <small>{{ draft.description.length }} / 200 · 请核对与实物一致</small>
                </label>

                <label class="assist-field-stack">
                  <span>菜单分类</span>
                  <input
                    v-model="draft.category"
                    class="assist-input"
                    list="photo-assist-categories"
                    maxlength="30"
                    :disabled="saving"
                    placeholder="例如：招牌小吃、清爽饮品"
                  />
                  <datalist id="photo-assist-categories">
                    <option
                      v-for="item in categories.filter((c) => c !== '未分类')"
                      :key="item"
                      :value="item"
                    />
                  </datalist>
                </label>
              </section>
            </template>

            <p v-if="combinedError" class="assist-error" role="alert">
              {{ combinedError }}
            </p>
          </div>

          <footer v-if="draft.image && !uploading && !initialAnalyzing">
            <button
              type="button"
              class="assist-outline"
              :disabled="saving || refining"
              @click="handoff"
            >
              <SlidersHorizontal :size="16" />口味与更多
            </button>
            <button
              type="submit"
              class="assist-primary"
              :disabled="saving || refining"
            >
              <Check :size="17" />{{ saving ? "正在上架…" : "确认上架" }}
            </button>
          </footer>
        </form>

        <div v-if="discardOpen" class="assist-discard-backdrop">
          <section
            ref="discardEl"
            class="assist-discard-panel"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="assist-discard-title"
            aria-describedby="assist-discard-desc"
          >
            <h3 id="assist-discard-title">放弃这次拍照上架草稿？</h3>
            <p id="assist-discard-desc">当前上传的照片和已填内容尚未保存到菜单中。</p>
            <div>
              <button type="button" class="assist-outline" @click="confirmDiscard">
                放弃草稿
              </button>
              <button
                ref="continueBtn"
                type="button"
                class="assist-primary"
                @click="cancelDiscard"
              >
                继续填写
              </button>
            </div>
          </section>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.assist-backdrop {
  position: fixed;
  inset: 0;
  background: #25180f66;
  backdrop-filter: blur(4px);
  display: flex;
  justify-content: flex-end;
  z-index: 185;
  padding: 16px;
}
.assist-panel {
  position: relative;
  background: #fffcf7;
  border: 1px solid #fff8f0;
  border-radius: 24px;
  width: 560px;
  max-width: 100%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  box-shadow: -20px 0 80px #2f1c121f;
  box-sizing: border-box;
}
.assist-panel > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 16px;
  border-bottom: 1px solid #eee4d9;
  gap: 12px;
}
.assist-eyebrow {
  display: block;
  font-size: 12px;
  letter-spacing: 1.5px;
  color: #a47653;
  font-weight: 700;
  margin-bottom: 4px;
}
.assist-panel h2 {
  margin: 0;
  font-size: 21px;
  color: #392e24;
}
.assist-close {
  display: grid;
  place-items: center;
  border-radius: 50%;
  width: 44px;
  height: 44px;
  background: #f6eee4;
  color: #473b30;
  flex-shrink: 0;
}
.assist-form {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}
.assist-body {
  padding: 20px 24px;
  overflow-y: auto;
  overscroll-behavior: contain;
  display: grid;
  gap: 16px;
}
.assist-primary,
.assist-outline {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 7px;
  min-height: 46px;
  border-radius: 12px;
  padding: 0 16px;
  white-space: nowrap;
  font-weight: 600;
  font-size: 14px;
  box-sizing: border-box;
  cursor: pointer;
}
.assist-primary {
  background: #ed742f;
  color: #fff;
  border: 1px solid #ed742f;
}
.assist-primary:hover:not(:disabled) {
  background: #d75e1c;
}
.assist-outline {
  background: #fff;
  color: #4d3f32;
  border: 1px solid #e4d8c8;
}
.assist-primary:disabled,
.assist-outline:disabled,
.assist-close:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.assist-capture-card {
  background: #fff7ed;
  border: 1px dashed #e2cbb0;
  border-radius: 18px;
  padding: 28px 20px;
  text-align: center;
}
.assist-capture-icon {
  width: 60px;
  height: 60px;
  border-radius: 18px;
  background: #ffe8d2;
  color: #c95c1e;
  display: grid;
  place-items: center;
  margin: 0 auto 14px;
}
.assist-capture-card h3 {
  margin: 0 0 8px;
  font-size: 18px;
  color: #392e24;
}
.assist-capture-card p {
  margin: 0 auto 20px;
  max-width: 400px;
  font-size: 14px;
  line-height: 1.65;
  color: #6f5b47;
}
.assist-capture-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  max-width: 380px;
  margin: 0 auto 14px;
}
.assist-file-btn {
  position: relative;
  overflow: hidden;
}
.assist-file-btn input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.assist-file-btn:focus-within {
  outline: 3px solid #d38648;
  outline-offset: 2px;
}
.assist-hint-note {
  display: block;
  font-size: 12px;
  color: #8c7761;
  line-height: 1.5;
}
.assist-loading-card,
.assist-top-summary {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 14px;
  background: #fff7ed;
  border: 1px solid #ecdcc8;
  border-radius: 16px;
}
.assist-preview-thumb {
  width: 84px;
  height: 84px;
  border-radius: 14px;
  overflow: hidden;
  background: #f5eadc;
  color: #ad8d6e;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.assist-preview-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.assist-loading-text {
  flex: 1;
  min-width: 0;
}
.assist-loading-text strong {
  display: block;
  font-size: 15px;
  color: #392e24;
  margin-bottom: 6px;
}
.assist-loading-text p {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: #745f4a;
}
.assist-skip-btn {
  min-height: 44px;
  font-size: 13px;
}
.assist-summary-info {
  flex: 1;
  min-width: 0;
}
.assist-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}
.assist-badge.ok {
  background: #e8f3e9;
  color: #3d6840;
}
.assist-badge.warn {
  background: #fdebd8;
  color: #a94918;
}
.assist-badge.neutral {
  background: #efe7dc;
  color: #6c5947;
}
.assist-notice {
  margin: 7px 0 8px;
  font-size: 13px;
  line-height: 1.55;
  color: #65523f;
}
.assist-retake {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 44px;
  padding: 0 12px;
  border-radius: 9px;
  border: 1px solid #e2d3c1;
  background: #fff;
  font-size: 13px;
  font-weight: 600;
  color: #6d553e;
  cursor: pointer;
}
.assist-retake input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.assist-retake.disabled {
  opacity: 0.5;
  pointer-events: none;
}
.assist-question-block {
  background: #fff;
  border: 1px solid #ece2d5;
  border-radius: 16px;
  padding: 15px;
  display: grid;
  gap: 10px;
}
.assist-q-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  font-size: 15px;
  font-weight: 700;
  color: #392e24;
}
.assist-q-title > span {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #ffead8;
  color: #b84f17;
  font-size: 12px;
  display: inline-grid;
  place-items: center;
  flex-shrink: 0;
}
.assist-q-title em {
  font-style: normal;
  color: #d95f25;
}
.assist-q-title small {
  font-weight: 500;
  font-size: 13px;
  color: #8a7662;
}
.assist-q-sub {
  margin: 0;
  font-size: 13px;
  color: #7e6a56;
  line-height: 1.5;
}
.assist-input,
.assist-textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #e4d8c8;
  border-radius: 10px;
  background: #fff;
  padding: 11px 12px;
  font-size: 15px;
  color: #3f3328;
  line-height: 1.5;
}
.assist-textarea {
  resize: vertical;
  min-height: 82px;
}
.assist-twocol {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.assist-sub-field {
  display: grid;
  gap: 7px;
  align-content: start;
}
.assist-sub-field label,
.assist-label-text,
.assist-field-stack > span {
  font-size: 13px;
  font-weight: 600;
  color: #6e5b49;
}
.assist-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.assist-chips.compact {
  gap: 6px;
}
.assist-chip {
  min-height: 44px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid #e6dacb;
  background: #fdf8f2;
  color: #5e4c3b;
  font-size: 13px;
  font-weight: 600;
}
.assist-chip.active {
  background: #ffead8;
  border-color: #df9a6c;
  color: #9f4213;
}
.assist-supply-Segment {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}
.assist-supply-Segment button {
  min-height: 44px;
  padding: 0 6px;
  border-radius: 10px;
  border: 1px solid #e6dacb;
  background: #fff;
  font-size: 13px;
  color: #5e4c3b;
}
.assist-supply-Segment button[aria-pressed="true"] {
  background: #ffead8;
  border-color: #df9a6c;
  color: #9f4213;
  font-weight: 700;
}
.assist-zero-note {
  margin: 0;
  font-size: 13px;
  color: #8b5b2b;
}
.assist-refine-row {
  display: flex;
  gap: 8px;
  align-items: stretch;
}
.assist-refine-row .assist-input {
  flex: 1;
  min-width: 0;
}
.assist-refine-btn {
  flex-shrink: 0;
  white-space: nowrap;
  padding: 0 13px;
}
.assist-field-stack {
  display: grid;
  gap: 6px;
}
.assist-field-stack small {
  font-size: 12px;
  color: #94816d;
  text-align: right;
}
.assist-error {
  margin: 0;
  font-size: 13px;
  color: #b3412a;
  background: #fff0e8;
  border: 1px solid #f3cebc;
  padding: 12px;
  border-radius: 10px;
}
.assist-pending-box {
  background: #fff7ed;
  border: 1px solid #ead5bc;
  border-radius: 16px;
  padding: 22px;
  display: grid;
  gap: 12px;
}
.assist-pending-box h3 {
  margin: 0;
  font-size: 17px;
  color: #392e24;
}
.assist-pending-box p {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: #6b5743;
}
.assist-panel footer {
  padding: 15px 24px max(15px, env(safe-area-inset-bottom));
  border-top: 1px solid #eee4d9;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
  background: #fffcf7;
  border-radius: 0 0 24px 24px;
}
.assist-panel footer .assist-primary {
  flex: 1.2;
  min-width: 140px;
}
.assist-panel footer .assist-outline {
  flex: 1;
}
.assist-discard-backdrop {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 20px;
  border-radius: inherit;
  background: #25180f66;
}
.assist-discard-panel {
  width: 100%;
  max-width: 360px;
  box-sizing: border-box;
  padding: 22px;
  border: 1px solid #e7d7c3;
  border-radius: 18px;
  background: #fffcf7;
  color: #473b30;
}
.assist-discard-panel h3 {
  margin: 0;
  font-size: 18px;
}
.assist-discard-panel p {
  margin: 12px 0 18px;
  font-size: 14px;
  line-height: 1.6;
}
.assist-discard-panel > div {
  display: flex;
  gap: 10px;
}
.assist-discard-panel button {
  flex: 1;
}

@media (max-width: 600px) {
  .assist-backdrop {
    padding: 0;
    align-items: flex-end;
  }
  .assist-panel {
    width: 100%;
    height: 95dvh;
    border-radius: 22px 22px 0 0;
  }
  .assist-panel > header {
    padding: 16px 18px 14px;
  }
  .assist-body {
    padding: 16px 18px;
  }
  .assist-capture-actions,
  .assist-twocol {
    grid-template-columns: 1fr;
  }
  .assist-refine-row {
    flex-direction: column;
  }
  .assist-input,
  .assist-textarea {
    font-size: 16px;
  }
  .assist-panel footer {
    padding: 13px 18px max(13px, env(safe-area-inset-bottom));
    border-radius: 0;
  }
}
</style>
