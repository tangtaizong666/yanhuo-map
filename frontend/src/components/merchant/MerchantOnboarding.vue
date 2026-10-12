<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { ClipboardCheck, Store, ArrowRight, RefreshCw } from "lucide-vue-next";
import { api } from "../../lib/api";
import { useSession } from "../../stores/session";
const session = useSession();
const emit = defineEmits<{ approved: [] }>();
const application = ref<any>(null),
  loading = ref(false),
  readReady = ref(false),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
const form = reactive({
  business_name: "",
  stall_name: "",
  contact_phone: "",
  area_id: "",
  category: "",
  address_note: "",
  description: "",
});
let sequence = 0,
  disposed = false,
  controller: AbortController | undefined;
const editable = computed(
  () =>
    !application.value ||
    ["draft", "needs_changes", "rejected"].includes(application.value.status),
);
const labels: Record<string, string> = {
  draft: "资料准备中",
  submitted: "已提交，等待团队核验",
  needs_changes: "请补充资料",
  approved: "入驻已通过",
  rejected: "本次申请未通过",
};
function fill(value: any) {
  application.value = value;
  for (const key of Object.keys(form) as (keyof typeof form)[])
    form[key] = value?.[key] == null ? "" : String(value[key]);
}
async function load() {
  if (!session.user || busy.value) return;
  const request = ++sequence,
    owner = session.user.id;
  loading.value = true;
  error.value = "";
  try {
    const result = await api<{ application: any }>("/merchant/application");
    if (disposed || request !== sequence || session.user?.id !== owner) return;
    fill(result.application);
    readReady.value = true;
  } catch (e) {
    if (!disposed && request === sequence) error.value = (e as Error).message;
  } finally {
    if (!disposed && request === sequence) loading.value = false;
  }
}
async function save(submit = false) {
  if (busy.value || !editable.value || !session.user) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  const owner = session.user.id,
    request = ++sequence;
  controller?.abort();
  controller = new AbortController();
  const valid = () =>
    !disposed && request === sequence && session.user?.id === owner;
  try {
    const body = {
      ...form,
      area_id: form.area_id ? Number(form.area_id) : null,
    };
    const saved = await api<{ application: any }>("/merchant/application", {
      method: "PATCH",
      body,
      signal: controller.signal,
    });
    if (!valid()) return;
    fill(saved.application);
    if (submit) {
      const result = await api<{ application: any }>(
        "/merchant/application/submit",
        { method: "POST", signal: controller.signal },
      );
      if (!valid()) return;
      fill(result.application);
      notice.value = "资料已提交。核验结果会显示在这里。";
    } else notice.value = "草稿已保存，可以稍后继续填写。";
  } catch (e) {
    if (valid()) error.value = (e as Error).message;
  } finally {
    if (valid()) busy.value = false;
  }
}
async function enter() {
  try {
    await session.refreshUser();
    if (session.isMerchant) emit("approved");
    else error.value = "资料已通过，团队还需完成摊位绑定。请稍后刷新进度。";
  } catch (e) {
    error.value = (e as Error).message;
  }
}
watch(
  () => session.user?.id,
  () => {
    controller?.abort();
    sequence++;
    fill(null);
    readReady.value = false;
    notice.value = "";
    error.value = "";
    busy.value = false;
    void load();
  },
  { immediate: true, flush: "sync" },
);
onBeforeUnmount(() => {
  disposed = true;
  sequence++;
  controller?.abort();
});
</script>
<template>
  <section class="onboarding m-panel">
    <div class="intro">
      <span class="intro-icon"><Store :size="28" /></span>
      <div>
        <span class="m-eyebrow">先让同学找得到你</span>
        <h1>把小摊带到校园地图上</h1>
        <p>
          自己填写，或请项目团队协助录入。核验通过后，就能准备菜单和出摊位置。
        </p>
      </div>
    </div>
    <template v-if="!session.user"
      ><p>登录后可以保存资料、查看入驻进度。无需提供学号。</p>
      <RouterLink class="btn btn-primary" to="/login?returnTo=/merchant/apply"
        >登录或注册后申请 <ArrowRight :size="17" /></RouterLink
    ></template>
    <template v-else>
      <p v-if="loading" role="status">正在读取入驻资料…</p>
      <p v-if="error" class="m-alert" role="alert">{{ error }}</p>
      <p v-if="notice" class="m-info-banner" role="status">{{ notice }}</p>
      <template v-if="!loading && readReady">
        <div v-if="application" class="application-status">
          <ClipboardCheck :size="23" />
          <div>
            <strong>{{ labels[application.status] || "入驻资料" }}</strong>
            <p>
              {{
                application.source === "assisted"
                  ? editable
                    ? "团队协助录入，请核对后确认提交。"
                    : "资料由团队协助录入"
                  : "商家自助申请"
              }}
            </p>
            <p v-if="application.review_note">
              团队反馈：{{ application.review_note }}
            </p>
          </div>
        </div>
        <template v-if="editable">
          <p class="helper">
            先填清楚叫什么、在哪里、怎样联系你。照片与菜单可以通过后慢慢完善；提交不等于开通线上接单。
          </p>
          <form class="m-form" @submit.prevent="save(true)">
            <fieldset :disabled="busy">
              <div class="fields">
                <label
                  >经营主体名称<input
                    v-model="form.business_name"
                    maxlength="120"
                    required
                    placeholder="个体户、企业或经营者名称" /></label
                ><label
                  >摊位名称<input
                    v-model="form.stall_name"
                    maxlength="80"
                    required
                    placeholder="同学们熟悉的小摊名字" /></label
                ><label
                  >联系电话<input
                    v-model="form.contact_phone"
                    type="tel"
                    maxlength="30"
                    required /></label
                ><label
                  >所在校园区域<select v-model="form.area_id" required>
                    <option value="">请选择校园区域</option>
                    <option
                      v-for="area in session.config?.areas || []"
                      :key="area.id"
                      :value="String(area.id)"
                    >
                      {{ area.name }}
                    </option>
                  </select></label
                ><label
                  >主要经营品类<input
                    v-model="form.category"
                    maxlength="30"
                    placeholder="例如：煎饼、饮品"
                    required /></label
                ><label
                  >通常在哪里出摊<input
                    v-model="form.address_note"
                    maxlength="200"
                    placeholder="例如：学府路夜市入口左边"
                    required
                /></label>
              </div>
              <label
                >补充说明（选填）<textarea
                  v-model="form.description"
                  maxlength="1000"
                  rows="3"
                  placeholder="经营时间、需要团队帮助的地方等"
                />
              </label>
            </fieldset>
            <div class="actions">
              <button
                class="btn btn-secondary"
                type="button"
                :disabled="busy"
                @click="save(false)"
              >
                保存草稿</button
              ><button class="btn btn-primary" :disabled="busy">
                {{
                  busy
                    ? "正在保存…"
                    : application?.source === "assisted"
                      ? "确认资料并提交"
                      : "提交入驻资料"
                }}
                <ArrowRight :size="17" />
              </button>
            </div>
          </form>
        </template>
        <template v-else
          ><dl class="summary">
            <div>
              <dt>摊位</dt>
              <dd>{{ application.stall_name }}</dd>
            </div>
            <div>
              <dt>位置说明</dt>
              <dd>{{ application.address_note }}</dd>
            </div>
            <div>
              <dt>联系电话</dt>
              <dd>{{ application.contact_phone }}</dd>
            </div>
          </dl>
          <p class="helper">
            {{
              application.status === "approved"
                ? "接下来添加菜单、核对取餐位置。在线接单仍以经营核验结果为准。"
                : "团队正在核验资料；需要补充时，会在这里说明。"
            }}
          </p>
          <button
            v-if="application.status === 'approved'"
            class="btn btn-primary"
            @click="enter"
          >
            进入开摊准备 <ArrowRight :size="17" /></button
        ></template>
        <details class="help">
          <summary>需要团队帮忙录入？</summary>
          <p>
            可以请项目团队在现场协助填写，并告知你当前账号。代录资料会显示在此页，由你核对后提交；团队不会代替你确认资料。
          </p>
        </details>
        <button
          class="btn btn-secondary refresh"
          :disabled="busy || loading"
          @click="load"
        >
          <RefreshCw :size="16" /> 刷新入驻进度
        </button>
      </template>
      <button
        v-if="!loading && !readReady"
        class="btn btn-secondary"
        @click="load"
      >
        重新读取入驻资料
      </button>
    </template>
  </section>
</template>
<style scoped>
.onboarding {
  max-width: 860px;
  margin: 24px auto;
  padding: clamp(22px, 4vw, 42px);
}
.intro {
  display: flex;
  gap: 18px;
  align-items: flex-start;
  margin-bottom: 24px;
}
.intro-icon {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  flex: none;
  background: #fff0df;
  border-radius: 18px;
  color: #df6b21;
}
.intro h1 {
  font-size: clamp(24px, 4vw, 32px);
  margin: 8px 0 12px;
  line-height: 1.4;
}
.intro p,
.helper,
.help p {
  color: #775e4b;
  line-height: 1.8;
  font-size: 14px;
}
.fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.fields label {
  min-width: 0;
}
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
fieldset > label {
  margin-top: 18px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.application-status {
  display: flex;
  gap: 12px;
  background: #fff3e4;
  padding: 18px;
  border-radius: 16px;
  margin-bottom: 16px;
  color: #744820;
}
.application-status p {
  margin: 6px 0 0;
  font-size: 14px;
  line-height: 1.7;
}
.summary {
  display: grid;
  gap: 16px;
}
.summary dt {
  font-size: 12px;
  color: #806851;
}
.summary dd {
  margin: 5px 0;
  overflow-wrap: anywhere;
}
.help {
  border-top: 1px solid #ecdfcf;
  margin-top: 24px;
}
.help summary {
  min-height: 48px;
  cursor: pointer;
  display: flex;
  align-items: center;
  font-size: 14px;
}
.refresh {
  margin-top: 12px;
}
.onboarding :is(button, input, select) {
  min-height: 44px;
}
.onboarding :is(input, select, textarea) {
  max-width: 100%;
  box-sizing: border-box;
}
.onboarding select {
  width: 100%;
  border: 1px solid #e4d2bc;
  background: #fffcf7;
  border-radius: 10px;
  padding: 11px 12px;
  color: #5c442f;
  font: inherit;
}
@media (max-width: 600px) {
  .onboarding {
    margin: 12px 0;
  }
  .fields {
    grid-template-columns: 1fr;
  }
  .intro-icon {
    display: none;
  }
  .actions > * {
    flex: 1;
    min-width: 120px;
  }
}
</style>
