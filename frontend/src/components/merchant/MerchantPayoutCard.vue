<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { CheckCircle2, CreditCard, Edit3, Lock, Save, Wallet } from "lucide-vue-next";
import { api, ApiError, formatTime } from "../../lib/api";
import { notify } from "../../lib/notify";

const props = defineProps<{
  stall: any;
  discoveryOnly?: boolean;
  embedded?: boolean;
}>();

const emit = defineEmits<{
  saved: [stall: any];
}>();

const editing = ref(false);
const saving = ref(false);
const formError = ref("");

const payout = computed(() => {
  const raw = props.stall?.payout_account;
  const feeBps = raw?.effective_fee_bps ?? props.stall?.effective_fee_bps ?? 500;
  return {
    channel: (raw?.channel === "bank" ? "bank" : "wechat") as "wechat" | "bank",
    holder_name: raw?.holder_name || "",
    wechat_id: raw?.wechat_id || "",
    bank_name: raw?.bank_name || "",
    bank_last4: raw?.bank_last4 || "",
    updated_at: raw?.updated_at || null,
    registered: Boolean(raw?.registered),
    platform_bound: Boolean(raw?.platform_bound),
    mode: raw?.mode || (props.stall?.services?.mode === "simulation" ? "simulation" : "live"),
    effective_fee_bps: feeBps,
    merchant_share_bps: Math.max(0, 10000 - feeBps),
  };
});

const feePercent = computed(() =>
  (payout.value.effective_fee_bps / 100).toFixed(1).replace(/\.0$/, ""),
);
const sharePercent = computed(() =>
  (payout.value.merchant_share_bps / 100).toFixed(1).replace(/\.0$/, ""),
);

const draft = reactive({
  channel: "wechat" as "wechat" | "bank",
  holder_name: "",
  wechat_id: "",
  bank_name: "",
  bank_last4: "",
});

function syncDraftFromStall() {
  draft.channel = payout.value.channel;
  draft.holder_name = payout.value.holder_name;
  draft.wechat_id = payout.value.wechat_id;
  draft.bank_name = payout.value.bank_name;
  draft.bank_last4 = payout.value.bank_last4;
}

watch(
  () => [props.stall?.id, props.stall?.payout_account?.updated_at],
  () => {
    if (!editing.value) syncDraftFromStall();
  },
  { immediate: true },
);

function startEdit() {
  formError.value = "";
  syncDraftFromStall();
  editing.value = true;
}

function cancelEdit() {
  formError.value = "";
  syncDraftFromStall();
  editing.value = false;
}

async function submitPayout() {
  if (saving.value) return;
  formError.value = "";
  const holder = draft.holder_name.trim();
  if (holder.length < 2) {
    formError.value = "请填写不少于 2 个字的收款人真实姓名。";
    return;
  }
  if (draft.channel === "wechat" && draft.wechat_id.trim().length < 3) {
    formError.value = "请填写收款微信号或绑定手机号。";
    return;
  }
  if (draft.channel === "bank") {
    if (draft.bank_name.trim().length < 2) {
      formError.value = "请填写结算开户银行名称（如“中国工商银行”）。";
      return;
    }
    if (!/^\d{4}$/.test(draft.bank_last4.trim())) {
      formError.value = "为保护资金安全，请只填写银行卡号后 4 位数字，切勿输入完整卡号。";
      return;
    }
  }
  saving.value = true;
  try {
    const updated = await api<any>(
      `/merchant/stalls/${props.stall.id}/payout-account`,
      {
        method: "PATCH",
        body: {
          channel: draft.channel,
          holder_name: holder,
          wechat_id: draft.channel === "wechat" ? draft.wechat_id.trim() : "",
          bank_name: draft.channel === "bank" ? draft.bank_name.trim() : "",
          bank_last4: draft.channel === "bank" ? draft.bank_last4.trim() : "",
        },
      },
    );
    editing.value = false;
    emit("saved", updated);
    notify("收款账户资料已保存", "success");
  } catch (err) {
    if (err instanceof ApiError && err.data?.errors) {
      const firstFieldErrors = Object.values(err.data.errors).flat();
      formError.value = String(firstFieldErrors[0] || err.message);
    } else {
      formError.value = (err as Error).message || "保存失败，请稍后重试。";
    }
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section
    id="payout"
    :class="[embedded ? 'payout-embedded' : 'm-panel', 'm-payout-card']"
    aria-label="收款账户与锁价说明"
  >
    <div class="m-payout-top">
      <div class="m-payout-title-row">
        <span class="m-payout-badge-icon"><Wallet :size="20" /></span>
        <div>
          <h3>收款账户与自动锁价</h3>
          <p class="m-payout-summary">
            <template v-if="discoveryOnly">
              当前摊位为找摊展示模式，暂未开放线上收款；如需开通线上点单收款，请联系校园运营核验。
            </template>
            <template v-else>
              系统按菜价<strong>自动锁死顾客付款金额</strong>（无需上传个人收款码，少一分钱都付不了）；每单完结自动把
              <strong>{{ sharePercent }}%</strong> 打入你的收款账户。
            </template>
          </p>
        </div>
      </div>
      <span
        v-if="!discoveryOnly"
        :class="['m-status', payout.registered ? 'open' : 'pending']"
      >
        {{
          payout.mode === "simulation"
            ? payout.registered
              ? "已登记 · 模拟体验"
              : "待登记 · 模拟体验"
            : payout.platform_bound
              ? "微信分账已接通"
              : payout.registered
                ? "已登记收款号"
                : "待登记收款号"
        }}
      </span>
    </div>

    <div v-if="!discoveryOnly" class="m-payout-account-box">
      <div class="m-payout-account-info">
        <template v-if="payout.registered">
          <strong>
            <CheckCircle2 :size="16" class="m-payout-ok" />
            {{
              payout.channel === "wechat"
                ? `微信零钱收款：${payout.holder_name}（${payout.wechat_id}）`
                : `银行卡结算：${payout.holder_name}（${payout.bank_name} · 尾号 ${payout.bank_last4}）`
            }}
          </strong>
          <small>
            到手比例 {{ sharePercent }}%（平台技术服务费 {{ feePercent }}%）
            <template v-if="payout.updated_at">
              · 最近更新 {{ formatTime(payout.updated_at) }}
            </template>
          </small>
        </template>
        <template v-else>
          <strong>尚未登记收款账户（钱打到哪去）</strong>
          <small>
            请先点右侧按钮登记你的收款微信或银行卡信息，校园运营会据此协助完成微信小微/特约收款绑定。
          </small>
        </template>
      </div>
      <button
        v-if="!editing"
        type="button"
        class="btn btn-secondary m-payout-edit-btn"
        @click="startEdit"
      >
        <Edit3 :size="15" />
        {{ payout.registered ? "修改收款账户" : "登记收款账户" }}
      </button>
    </div>

    <form
      v-if="!discoveryOnly && editing"
      class="m-form m-payout-form"
      @submit.prevent="submitPayout"
    >
      <div class="m-payout-channel-tabs" role="group" aria-label="选择到账方式">
        <button
          type="button"
          :class="{ active: draft.channel === 'wechat' }"
          :aria-pressed="draft.channel === 'wechat'"
          @click="draft.channel = 'wechat'"
        >
          <Wallet :size="16" /> 微信零钱收款（推荐）
        </button>
        <button
          type="button"
          :class="{ active: draft.channel === 'bank' }"
          :aria-pressed="draft.channel === 'bank'"
          @click="draft.channel = 'bank'"
        >
          <CreditCard :size="16" /> 银行卡结算
        </button>
      </div>

      <div class="m-field-row">
        <label>
          收款人真实姓名
          <input
            v-model="draft.holder_name"
            required
            maxlength="60"
            placeholder="填写本人真实姓名"
          />
        </label>
        <label v-if="draft.channel === 'wechat'">
          收款微信号或绑定手机号
          <input
            v-model="draft.wechat_id"
            required
            maxlength="60"
            placeholder="例如：laoli_188xxxx"
          />
        </label>
        <label v-else>
          结算开户银行
          <input
            v-model="draft.bank_name"
            required
            maxlength="80"
            placeholder="例如：中国工商银行"
          />
        </label>
      </div>

      <label v-if="draft.channel === 'bank'">
        结算银行卡后 4 位数字（勿填完整卡号）
        <input
          v-model="draft.bank_last4"
          required
          maxlength="4"
          inputmode="numeric"
          pattern="\d{4}"
          placeholder="只填卡号最后 4 位，如 8891"
        />
        <small class="m-muted">
          为保护资金安全，工作台只记录尾号供对账；完整卡号仅在微信官方安全页签约时本人填写。
        </small>
      </label>

      <p v-if="formError" role="alert" class="m-alert">{{ formError }}</p>

      <div class="m-payout-actions">
        <button type="submit" class="btn btn-primary" :disabled="saving">
          <Save :size="16" /> {{ saving ? "保存中…" : "保存收款账户" }}
        </button>
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="saving"
          @click="cancelEdit"
        >
          取消
        </button>
      </div>
    </form>

    <details class="m-detail-toggle">
      <summary>
        <Lock :size="14" />
        详细说明：为什么不用传个人收款码？怎么锁死金额并到账？
      </summary>
      <ul>
        <li>
          <strong>怎么限制顾客付多少钱：</strong>个人静态收款码是顾客自己手输金额，忙时容易出现只付
          0.01 元或假截图逃单。本系统按购物车菜价直接生成<strong>固定金额微信账单</strong>，没有改金额输入框，少一分钱都付不了。
        </li>
        <li>
          <strong>不用手动点收款、不怕忙忘点：</strong>顾客付完由微信自动核验，订单直接进「制作中」；交餐后点「完成」（或输
          4 位取餐码），就算忙忘点，顾客确认已取餐或出餐满 2 小时无争议也会自动完结结算。
        </li>
        <li>
          <strong>怎么开通真实打款：</strong>先在上方登记收款人微信或银行卡尾号，校园运营团队会协助你在微信官方完成小微/特约商户绑定（个人身份证即可办小微收款），每单自动分账
          {{ sharePercent }}% 到你的账户（平台仅收 {{ feePercent }}% 服务费，全额退款单不收服务费）。
        </li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.m-payout-card {
  display: grid;
  gap: 12px;
  min-width: 0;
}
.payout-embedded {
  padding: 4px 0 2px;
}
.m-payout-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.m-payout-title-row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  flex: 1 1 240px;
}
.m-payout-badge-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: #fdebd8;
  color: #b55523;
  display: grid;
  place-items: center;
}
.m-payout-title-row h3 {
  margin: 0 0 4px;
  font-size: 16px;
  color: #3a2b1f;
}
.m-payout-summary {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.65;
  color: #66503c;
}
.m-payout-summary strong {
  color: #8f3e14;
}
.m-payout-account-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 13px 15px;
  border-radius: 12px;
  background: #fff9f0;
  border: 1px solid #ead9c3;
}
.m-payout-account-info {
  display: grid;
  gap: 4px;
  flex: 1 1 220px;
  min-width: 0;
}
.m-payout-account-info strong {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #3a2c20;
  overflow-wrap: anywhere;
}
.m-payout-ok {
  color: #4f7e53;
  flex: none;
}
.m-payout-account-info small {
  font-size: 12.5px;
  color: #7b6652;
  line-height: 1.55;
}
.m-payout-edit-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.m-payout-form {
  padding: 14px;
  border-radius: 12px;
  background: #fffdf9;
  border: 1px solid #e6d5bf;
  display: grid;
  gap: 12px;
}
.m-payout-channel-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.m-payout-channel-tabs button {
  min-height: 44px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid #e2d1bc;
  background: #faf5ed;
  color: #6f5843;
  font-size: 13px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}
.m-payout-channel-tabs button.active {
  background: #fff0df;
  border-color: #d97538;
  color: #9b4517;
}
.m-payout-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
@media (max-width: 520px) {
  .m-payout-channel-tabs {
    grid-template-columns: 1fr;
  }
  .m-payout-edit-btn {
    width: 100%;
    justify-content: center;
  }
}
</style>
