<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import {
  Check,
  ChevronDown,
  MessageCircle,
  MessageSquareText,
  RefreshCw,
  Star,
} from "lucide-vue-next";
import { api, formatTime } from "../../lib/api";
import { notify } from "../../lib/notify";
const props = defineProps<{ stall: any }>();
type Review = {
  id: number;
  rating: number;
  content: string;
  display_name: string;
  created_at: string;
  merchant_reply: string;
  replied_at: string | null;
};
const reviews = ref<Review[]>([]);
const loading = ref(true);
const error = ref("");
const filter = ref("all");
const sort = ref("newest");
const editing = ref<number | null>(null);
const withdrawing = ref<number | null>(null);
const drafts = reactive<Record<number, string>>({});
const saving = ref<number | null>(null);
let loadSequence = 0;
const average = computed(() =>
  reviews.value.length
    ? (
        reviews.value.reduce((sum, review) => sum + review.rating, 0) /
        reviews.value.length
      ).toFixed(1)
    : "—",
);
const positiveRate = computed(() =>
  reviews.value.length
    ? Math.round(
        (reviews.value.filter((r) => r.rating >= 4).length /
          reviews.value.length) *
          100,
      )
    : null,
);
const filters = computed(() => [
  { key: "all", label: "全部评价", count: reviews.value.length },
  {
    key: "unreplied",
    label: "待回复",
    count: reviews.value.filter((r) => !r.merchant_reply).length,
  },
  {
    key: "positive",
    label: "好评",
    count: reviews.value.filter((r) => r.rating >= 4).length,
  },
  {
    key: "other",
    label: "中差评",
    count: reviews.value.filter((r) => r.rating <= 3).length,
  },
]);
const distribution = computed(() =>
  [5, 4, 3, 2, 1].map((rating) => ({
    rating,
    count: reviews.value.filter((r) => r.rating === rating).length,
    width: reviews.value.length
      ? (reviews.value.filter((r) => r.rating === rating).length /
          reviews.value.length) *
        100
      : 0,
  })),
);
const shown = computed(() =>
  reviews.value
    .filter(
      (r) =>
        filter.value === "all" ||
        (filter.value === "unreplied" && !r.merchant_reply) ||
        (filter.value === "positive" && r.rating >= 4) ||
        (filter.value === "other" && r.rating <= 3),
    )
    .sort((a, b) =>
      sort.value === "lowest"
        ? a.rating - b.rating ||
          +new Date(b.created_at) - +new Date(a.created_at)
        : +new Date(b.created_at) - +new Date(a.created_at),
    ),
);
watch(
  () => props.stall?.id,
  () => {
    reviews.value = [];
    editing.value = null;
    withdrawing.value = null;
    filter.value = "all";
    void load();
  },
  { immediate: true },
);
async function load() {
  const sequence = ++loadSequence;
  if (!props.stall?.id) {
    loading.value = false;
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    const result = await api<Review[]>(
      `/merchant/reviews?stall=${props.stall.id}`,
    );
    if (sequence === loadSequence) reviews.value = result;
  } catch (e) {
    if (sequence === loadSequence) error.value = (e as Error).message;
  } finally {
    if (sequence === loadSequence) loading.value = false;
  }
}
function startReply(review: Review) {
  withdrawing.value = null;
  editing.value = review.id;
  drafts[review.id] = review.merchant_reply || "";
}
async function submitReply(review: Review) {
  const content = drafts[review.id]?.trim();
  if (!content || saving.value != null) return;
  saving.value = review.id;
  try {
    await api(`/merchant/reviews/${review.id}/reply`, {
      method: "POST",
      body: { content },
    });
    editing.value = null;
    notify("回复已发布", "success");
    await load();
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    saving.value = null;
  }
}
async function withdrawReply(review: Review) {
  if (saving.value != null) return;
  saving.value = review.id;
  try {
    await api(`/merchant/reviews/${review.id}/reply`, {
      method: "POST",
      body: { content: "" },
    });
    withdrawing.value = null;
    notify("商家回复已撤回", "success");
    await load();
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    saving.value = null;
  }
}
</script>

<template>
  <section class="reviews-panel">
    <div class="reviews-heading">
      <div>
        <span class="eyebrow">WORDS FROM YOUR GUESTS</span>
        <h2>听见每一份真实反馈</h2>
        <p>认真回应，让一次光顾成为下一次期待。</p>
      </div>
      <button class="reviews-refresh" :disabled="loading" @click="load">
        <RefreshCw :size="16" :class="{ spinning: loading }" />刷新评价
      </button>
    </div>
    <div v-if="!error" class="review-overview">
      <div class="average-score">
        <span>顾客综合评分</span
        ><strong>{{ average }}<small v-if="reviews.length"> / 5</small></strong>
        <div class="score-stars" aria-hidden="true">
          <Star
            v-for="star in 5"
            :key="star"
            :size="17"
            :class="{
              filled: reviews.length && star <= Math.round(Number(average)),
            }"
          />
        </div>
        <p>
          {{
            reviews.length
              ? `来自 ${reviews.length} 条真实订单评价`
              : "完成订单后，顾客可以留下评价"
          }}
        </p>
      </div>
      <div class="rating-distribution">
        <div v-for="row in distribution" :key="row.rating">
          <span>{{ row.rating }}<Star :size="10" /></span>
          <div class="rating-track">
            <div :style="{ width: `${row.width}%` }" />
          </div>
          <small>{{ row.count }}</small>
        </div>
      </div>
      <div class="review-summary">
        <div>
          <span>好评率</span
          ><strong>{{ positiveRate == null ? "—" : `${positiveRate}%` }}</strong
          ><small>4 星及以上</small>
        </div>
        <div>
          <span>等待回复</span><strong>{{ filters[1]!.count }}</strong
          ><small>一句回应，一份用心</small>
        </div>
      </div>
    </div>
    <div class="review-controls">
      <div class="review-filters" role="group" aria-label="评价筛选">
        <button
          v-for="item in filters"
          :key="item.key"
          :class="{ selected: filter === item.key }"
          :aria-pressed="filter === item.key"
          @click="filter = item.key"
        >
          {{ item.label }}<span>{{ item.count }}</span>
        </button>
      </div>
      <label class="review-sort"
        ><select v-model="sort" aria-label="评价排序">
          <option value="newest">最新评价优先</option>
          <option value="lowest">较低评分优先</option></select
        ><ChevronDown :size="14"
      /></label>
    </div>
    <div v-if="error" class="review-empty" role="alert">
      <MessageSquareText :size="32" />
      <h3>评价暂时没有加载成功</h3>
      <p>{{ error }}</p>
      <button @click="load">重新加载</button>
    </div>
    <div
      v-else-if="loading && !reviews.length"
      class="review-empty"
      aria-live="polite"
    >
      <RefreshCw :size="28" class="spinning" />
      <p>正在读取顾客评价…</p>
    </div>
    <div v-else-if="!shown.length" class="review-empty">
      <MessageSquareText :size="35" />
      <h3>
        {{
          !reviews.length
            ? "期待第一位顾客的分享"
            : filter === "unreplied"
              ? "每一份反馈，都已认真回应"
              : "暂无这类评价"
        }}
      </h3>
      <p>
        {{
          !reviews.length
            ? "顾客完成取餐并评价后，反馈会显示在这里。"
            : "你可以切换筛选，查看其他顾客的反馈。"
        }}
      </p>
    </div>
    <div v-else class="review-list">
      <article v-for="review in shown" :key="review.id" class="review-card">
        <header>
          <div class="review-avatar">
            {{ (review.display_name || "顾客").slice(0, 1) }}
          </div>
          <div class="review-customer">
            <strong>{{ review.display_name || "顾客" }}</strong>
            <div class="customer-stars" :aria-label="`${review.rating} 星评价`">
              <Star
                v-for="star in 5"
                :key="star"
                :size="13"
                :class="{ filled: star <= review.rating }"
              />
            </div>
          </div>
          <time>{{ formatTime(review.created_at) }}</time>
        </header>
        <p class="review-content">
          {{ review.content || "顾客未填写文字评价" }}
        </p>
        <div
          v-if="review.merchant_reply && editing !== review.id"
          class="merchant-reply"
        >
          <div>
            <MessageCircle :size="14" /><strong>商家回复</strong
            ><time>{{ formatTime(review.replied_at) }}</time>
          </div>
          <p>{{ review.merchant_reply }}</p>
        </div>
        <div v-if="editing === review.id" class="reply-editor">
          <label :for="`reply-${review.id}`"
            >回复顾客<textarea
              :id="`reply-${review.id}`"
              v-model="drafts[review.id]"
              rows="3"
              maxlength="500"
              placeholder="感谢认可，或认真说明你将如何改进…"
              :disabled="saving === review.id"
            />
          </label>
          <div>
            <small
              >{{ drafts[review.id]?.length || 0 }} / 500 ·
              回复将公开展示</small
            >
            <div>
              <button :disabled="saving === review.id" @click="editing = null">
                取消</button
              ><button
                class="send-reply"
                :disabled="saving === review.id || !drafts[review.id]?.trim()"
                @click="submitReply(review)"
              >
                <Check :size="14" />{{
                  saving === review.id ? "发布中…" : "发布回复"
                }}
              </button>
            </div>
          </div>
        </div>
        <div
          v-else-if="withdrawing === review.id"
          class="withdraw-confirm"
          role="group"
          aria-label="确认撤回商家回复"
        >
          <strong>撤回这条商家回复？</strong>
          <p>顾客端将不再显示这条回复，顾客的评价仍会保留。</p>
          <div>
            <button
              :disabled="saving === review.id"
              @click="withdrawing = null"
            >
              保留回复</button
            ><button
              class="confirm-withdraw"
              :disabled="saving === review.id"
              @click="withdrawReply(review)"
            >
              {{ saving === review.id ? "撤回中…" : "确认撤回" }}
            </button>
          </div>
        </div>
        <footer v-else>
          <span><Check :size="12" />已完成订单评价</span>
          <div class="review-footer-actions">
            <button
              v-if="review.merchant_reply"
              class="withdraw-trigger"
              @click="withdrawing = review.id"
            >
              撤回回复</button
            ><button @click="startReply(review)">
              <MessageCircle :size="14" />{{
                review.merchant_reply ? "修改回复" : "回复评价"
              }}
            </button>
          </div>
        </footer>
      </article>
    </div>
  </section>
</template>

<style scoped>
.reviews-panel {
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
.reviews-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 25px;
}
.reviews-heading h2 {
  font-size: 25px;
  color: #392e24;
}
.reviews-heading p {
  color: #867867;
  font-size: 13px;
  margin-top: 7px;
}
.reviews-refresh {
  display: flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
  min-height: 44px;
  padding: 0 14px;
  background: #fff;
  border: 1px solid #e9e0d5;
  border-radius: 11px;
  font-size: 12px;
  color: #7a6957;
}
.review-overview {
  display: grid;
  grid-template-columns: 1fr 1.35fr 1fr;
  align-items: center;
  gap: 35px;
  border: 1px solid #eaded0;
  border-radius: 20px;
  padding: 29px 30px;
  background: linear-gradient(105deg, #fff8ed, #fffdf9 58%, #fff7ee);
  margin-bottom: 25px;
}
.average-score > span {
  font-size: 12px;
  color: #876f58;
}
.average-score > strong {
  display: block;
  font-size: 49px;
  color: #d96727;
  letter-spacing: -2px;
  font-weight: 650;
  line-height: 1.5;
}
.average-score > strong small {
  font-size: 15px;
  color: #b59677;
  font-weight: 400;
  letter-spacing: 0;
}
.score-stars,
.customer-stars {
  display: flex;
  gap: 3px;
  color: #dccfc1;
}
.filled {
  color: #e77a38;
  fill: #e77a38;
}
.average-score p {
  font-size: 10px;
  color: #9d866d;
  margin-top: 9px;
}
.rating-distribution {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rating-distribution > div {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rating-distribution > div > span {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #927e68;
  width: 25px;
}
.rating-track {
  flex: 1;
  height: 6px;
  background: #eee5d9;
  border-radius: 8px;
  overflow: hidden;
}
.rating-track > div {
  height: 100%;
  border-radius: 8px;
  background: linear-gradient(90deg, #f4a368, #ed7736);
  transition: width 0.3s;
}
.rating-distribution small {
  width: 25px;
  text-align: right;
  color: #a49077;
  font-size: 10px;
}
.review-summary {
  border-left: 1px solid #eadfce;
  padding-left: 30px;
  display: flex;
  gap: 24px;
  flex-direction: column;
}
.review-summary > div {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  align-items: center;
}
.review-summary span {
  font-size: 11px;
  color: #8c765e;
}
.review-summary strong {
  font-size: 23px;
  letter-spacing: -1px;
  color: #5e4934;
}
.review-summary small {
  grid-column: 1/-1;
  font-size: 9px;
  color: #a18a72;
}
.review-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 15px;
  margin-bottom: 18px;
}
.review-filters {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.review-filters button {
  min-height: 43px;
  padding: 0 13px;
  color: #8b7966;
  font-size: 12px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  gap: 7px;
}
.review-filters button span {
  font-size: 10px;
  opacity: 0.8;
}
.review-filters button.selected {
  background: #ffebdb;
  color: #b95823;
  font-weight: 600;
}
.review-sort {
  display: flex;
  align-items: center;
  color: #97816a;
  gap: 5px;
}
.review-sort select {
  min-height: 43px;
  appearance: none;
  border: 0;
  font-size: 11px;
  color: #927b63;
  background: transparent;
  cursor: pointer;
  padding-right: 5px;
}
.review-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}
.review-card {
  background: #fff;
  border: 1px solid #eae1d6;
  border-radius: 17px;
  padding: 22px;
}
.review-card header {
  display: flex;
  align-items: center;
  gap: 11px;
}
.review-avatar {
  height: 40px;
  width: 40px;
  display: grid;
  place-items: center;
  background: #eee5d9;
  border: 3px solid #f9f4ec;
  border-radius: 50%;
  color: #95785b;
  font-size: 15px;
  flex-shrink: 0;
}
.review-customer {
  min-width: 0;
}
.review-customer strong {
  display: block;
  font-size: 13px;
  color: #514032;
  max-width: 200px;
  overflow-wrap: anywhere;
  margin-bottom: 5px;
}
.review-card header > time {
  margin-left: auto;
  align-self: flex-start;
  font-size: 9px;
  color: #a89580;
  padding-top: 3px;
  white-space: nowrap;
}
.customer-stars {
  gap: 2px;
}
.review-content {
  font-size: 13px;
  line-height: 1.85;
  color: #6c5946;
  margin-top: 17px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.merchant-reply {
  background: #faf5ed;
  border-radius: 11px;
  padding: 13px 14px;
  margin-top: 17px;
}
.merchant-reply > div {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #ba814f;
}
.merchant-reply strong {
  font-size: 10px;
}
.merchant-reply time {
  margin-left: auto;
  font-size: 9px;
  color: #b2997f;
}
.merchant-reply p {
  font-size: 11px;
  color: #9a8065;
  margin-top: 7px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.review-card footer {
  border-top: 1px solid #f1eae1;
  margin-top: 17px;
  padding-top: 9px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.review-card footer > span {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  color: #ac987f;
}
.review-card footer button {
  min-height: 35px;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 10px;
  background: #fff4e9;
  border-radius: 8px;
  color: #c97940;
  font-size: 11px;
}
.review-footer-actions {
  display: flex;
  gap: 7px;
  align-items: center;
}
.review-card footer .withdraw-trigger {
  background: transparent;
  color: #a1855d;
  padding: 0 4px;
}
.withdraw-confirm {
  border: 1px solid #efd3bd;
  background: #fff7ec;
  border-radius: 12px;
  padding: 14px;
  margin-top: 17px;
}
.withdraw-confirm strong {
  font-size: 12px;
  color: #8b4f2f;
}
.withdraw-confirm p {
  font-size: 11px;
  line-height: 1.8;
  color: #96765b;
  margin-top: 5px;
}
.withdraw-confirm > div {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.withdraw-confirm button {
  min-height: 42px;
  padding: 0 13px;
  font-size: 11px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #ead7c5;
  color: #947257;
}
.withdraw-confirm .confirm-withdraw {
  background: #d5662a;
  border-color: #d5662a;
  color: #fff;
}
.reply-editor {
  border-top: 1px solid #f1eae1;
  margin-top: 17px;
  padding-top: 17px;
}
.reply-editor label {
  font-size: 11px;
  color: #866d54;
}
.reply-editor textarea {
  display: block;
  width: 100%;
  margin-top: 8px;
  background: #fffcf7;
  border: 1px solid #e8d8c5;
  border-radius: 10px;
  padding: 10px 12px;
  resize: vertical;
  min-height: 85px;
  font-size: 12px;
  color: #64503c;
}
.reply-editor > div {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  margin-top: 8px;
}
.reply-editor small {
  font-size: 9px;
  color: #a38a70;
}
.reply-editor > div > div {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.reply-editor button {
  min-height: 36px;
  padding: 0 10px;
  font-size: 10px;
  border-radius: 8px;
  color: #997955;
  display: flex;
  align-items: center;
  gap: 4px;
}
.reply-editor .send-reply {
  background: #e77835;
  color: #fff;
}
.review-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  border: 1px dashed #e6d7c5;
  background: #fffdfa;
  border-radius: 18px;
  padding: 65px 20px;
  color: #b08a62;
}
.review-empty h3 {
  font-size: 17px;
  color: #6c533a;
  margin-top: 18px;
}
.review-empty p {
  font-size: 12px;
  color: #a18a72;
  margin-top: 10px;
}
.review-empty button {
  margin-top: 15px;
  min-height: 42px;
  padding: 0 20px;
  background: #fbe5d0;
  color: #ae6430;
  border-radius: 10px;
  font-size: 12px;
}
.spinning {
  animation: review-spin 1.4s linear infinite;
}
@keyframes review-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 1000px) {
  .review-overview {
    gap: 20px;
    padding: 25px;
    grid-template-columns: 1fr 1.2fr;
  }
  .review-summary {
    grid-column: 1/-1;
    border-left: 0;
    border-top: 1px solid #eadfce;
    padding: 17px 0 0;
    flex-direction: row;
    gap: 25px;
  }
  .review-summary > div {
    flex: 1;
  }
  .review-list {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .reviews-heading {
    align-items: flex-start;
    gap: 10px;
  }
  .reviews-heading h2 {
    font-size: 20px;
  }
  .reviews-heading p {
    font-size: 11px;
  }
  .eyebrow {
    font-size: 8px;
    letter-spacing: 1.4px;
  }
  .reviews-refresh {
    font-size: 10px;
    padding: 0 10px;
    gap: 4px;
    margin-top: 18px;
  }
  .review-overview {
    padding: 20px 18px;
    gap: 15px;
    border-radius: 16px;
    grid-template-columns: 1fr 1fr;
  }
  .average-score > strong {
    font-size: 43px;
  }
  .average-score > span {
    font-size: 10px;
  }
  .average-score p {
    font-size: 9px;
    max-width: 140px;
  }
  .rating-distribution > div {
    gap: 7px;
  }
  .rating-distribution small {
    width: 18px;
  }
  .review-summary {
    gap: 16px;
  }
  .review-summary strong {
    font-size: 21px;
  }
  .review-summary span {
    font-size: 10px;
  }
  .review-controls {
    flex-wrap: wrap;
    gap: 0;
  }
  .review-filters {
    width: 100%;
    justify-content: space-between;
    gap: 0;
  }
  .review-filters button {
    font-size: 10px;
    padding: 0 9px;
    gap: 4px;
  }
  .review-sort {
    margin-left: auto;
  }
  .review-sort select {
    min-height: 37px;
    font-size: 10px;
  }
  .review-card {
    padding: 18px;
    border-radius: 15px;
  }
  .review-card header > time {
    font-size: 8px;
  }
  .review-content {
    font-size: 12px;
  }
  .review-card footer button {
    min-height: 42px;
  }
  .reply-editor textarea {
    font-size: 16px;
  }
  .reply-editor button {
    min-height: 44px;
  }
  .review-empty {
    padding: 45px 20px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
  .rating-track > div {
    transition: none;
  }
}
</style>
