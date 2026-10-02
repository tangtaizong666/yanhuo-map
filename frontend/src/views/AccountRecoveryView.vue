<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ArrowLeft, KeyRound, ShieldCheck, Copy, Check } from "lucide-vue-next";
import { api, ApiError } from "../lib/api";
import { copyText } from "../lib/engagement";
import { useSession } from "../stores/session";
const route = useRoute(),
  session = useSession();
const managing = computed(() => route.path === "/account-security");
const enabled = ref<boolean | null>(null),
  code = ref(""),
  username = ref(""),
  password = ref(""),
  confirmation = ref("");
const busy = ref(false),
  error = ref(""),
  message = ref(""),
  finished = ref(false),
  generated = ref("");
let controller: AbortController | undefined,
  revision = 0,
  disposed = false;
const back = computed(() => (session.isMerchant ? "/merchant" : "/me"));
async function loadStatus() {
  if (!managing.value || !session.user || busy.value) return;
  const current = ++revision;
  controller?.abort();
  controller = new AbortController();
  try {
    const data = await api<{ enabled: boolean }>("/auth/recovery", {
      signal: controller.signal,
    });
    if (current === revision) enabled.value = data.enabled;
  } catch (e) {
    if (current === revision) error.value = (e as Error).message;
  }
}
watch(
  () => [session.user?.id, managing.value],
  () => {
    revision++;
    controller?.abort();
    generated.value = "";
    code.value = "";
    password.value = "";
    confirmation.value = "";
    enabled.value = null;
    error.value = "";
    message.value = "";
    busy.value = false;
    finished.value = false;
    void loadStatus();
  },
  { immediate: true, flush: "sync" },
);
onUnmounted(() => {
  disposed = true;
  revision++;
  controller?.abort();
  generated.value = "";
});
async function submit(revoke = false) {
  if (busy.value) return;
  if (!managing.value && password.value !== confirmation.value) {
    error.value = "两次新密码不一致。";
    return;
  }
  if (managing.value && !password.value) {
    error.value = "请先输入当前密码。";
    return;
  }
  const current = ++revision;
  controller?.abort();
  controller = new AbortController();
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    const data = await api(
      managing.value ? "/auth/recovery" : "/auth/recovery/reset",
      {
        method: revoke ? "DELETE" : "POST",
        signal: controller.signal,
        body: managing.value
          ? { password: password.value }
          : {
              username: username.value.trim(),
              recovery_code: code.value,
              new_password: password.value,
            },
      },
    );
    if (current !== revision) return;
    password.value = "";
    confirmation.value = "";
    if (managing.value) {
      enabled.value = data.enabled;
      generated.value = data.recovery_code || "";
      message.value = revoke ? "恢复码已停用。" : data.detail;
    } else {
      code.value = "";
      try {
        await session.refreshUser();
      } catch {
        /* Password reset is already confirmed by the server. */
      }
      if (!disposed) {
        finished.value = true;
        message.value = data.detail;
      }
    }
  } catch (e) {
    if (current !== revision) return;
    error.value =
      e instanceof ApiError && (e.status === 0 || e.status >= 500)
        ? managing.value
          ? "操作结果尚未确认。可刷新状态；重新生成会使之前的恢复码失效。"
          : "重设结果尚未确认。请先尝试用新密码登录；成功后原恢复码已失效。"
        : (e as Error).message;
  } finally {
    if (current === revision || finished.value) busy.value = false;
  }
}
async function copyCode() {
  message.value = (await copyText(generated.value))
    ? "恢复码已复制，请保存到安全位置。"
    : "此浏览器无法自动复制，请长按恢复码全选复制。";
}
</script>
<template>
  <main class="recovery-page">
    <RouterLink
      class="back"
      :to="
        managing
          ? back
          : {
              path: '/login',
              query:
                route.query.role === 'merchant' ? { role: 'merchant' } : {},
            }
      "
      ><ArrowLeft :size="18" />{{ managing ? "返回" : "返回登录" }}</RouterLink
    >
    <section class="card recovery-card">
      <span class="recovery-icon"><KeyRound :size="28" /></span>
      <p class="eyebrow">烟火地图 · 账号安全</p>
      <h1>{{ managing ? "给账号留一把备用钥匙" : "使用恢复码找回账号" }}</h1>
      <p class="intro">
        {{
          managing
            ? "忘记密码时，用账号和事先保存的恢复码设置新密码。无需短信或邮件。"
            : "输入之前保存的恢复码。没有设置过或已经遗失，请联系项目运营核验身份；系统无法替你找回旧码。"
        }}
      </p>
      <template v-if="managing && !session.user"
        ><p>登录后才能管理恢复码。</p>
        <RouterLink
          class="btn btn-primary"
          to="/login?returnTo=/account-security"
          >登录账号</RouterLink
        ></template
      >
      <template v-else-if="finished"
        ><p class="success"><Check :size="20" />{{ message }}</p>
        <RouterLink class="btn btn-primary" to="/login"
          >使用新密码登录</RouterLink
        ></template
      >
      <template v-else>
        <p v-if="managing" class="recovery-status">
          {{
            enabled === null
              ? "恢复码状态尚未读取"
              : enabled
                ? "已设置恢复码"
                : "尚未设置有效恢复码"
          }}<button type="button" :disabled="busy" @click="loadStatus">
            刷新状态
          </button>
        </p>
        <form v-if="!generated" class="stack" @submit.prevent="submit()">
          <label v-if="!managing" class="field"
            >账号<input
              class="input"
              v-model="username"
              required
              maxlength="150"
              autocomplete="username"
              :disabled="busy"
          /></label>
          <label v-if="!managing" class="field"
            >恢复码<input
              class="input recovery-input"
              v-model="code"
              required
              maxlength="80"
              autocomplete="off"
              spellcheck="false"
              autocapitalize="characters"
              :disabled="busy"
          /></label>
          <label class="field"
            >{{ managing ? "当前密码" : "新密码"
            }}<input
              class="input"
              type="password"
              v-model="password"
              required
              :minlength="managing ? undefined : 8"
              maxlength="128"
              :autocomplete="managing ? 'current-password' : 'new-password'"
              :disabled="busy"
          /></label>
          <label v-if="!managing" class="field"
            >再次输入新密码<input
              class="input"
              type="password"
              v-model="confirmation"
              required
              minlength="8"
              maxlength="128"
              autocomplete="new-password"
              :disabled="busy"
          /></label>
          <button
            class="btn btn-primary"
            :disabled="busy || (managing && enabled === null)"
          >
            {{
              busy
                ? "正在处理…"
                : managing
                  ? enabled
                    ? "生成新码，替换旧码"
                    : "生成恢复码"
                  : "重设密码"
            }}
          </button>
          <button
            v-if="managing && enabled"
            class="btn btn-secondary"
            type="button"
            :disabled="busy"
            @click="submit(true)"
          >
            停用恢复码
          </button>
        </form>
        <div v-else class="saved-code">
          <strong>仅本次显示，请先保存</strong
          ><textarea
            :value="generated"
            readonly
            aria-label="新恢复码"
            rows="2"
            @focus="($event.target as HTMLTextAreaElement).select()"
          />
          <button class="btn btn-secondary" @click="copyCode">
            <Copy :size="16" />复制恢复码
          </button>
          <button
            class="btn btn-primary"
            @click="
              generated = '';
              message = '请妥善保管，之后不会再次显示这个恢复码。';
            "
          >
            <Check :size="16" />我已安全保存
          </button>
        </div>
        <p v-if="error" class="error-message" role="alert">{{ error }}</p>
        <p v-if="message" class="success" role="status">{{ message }}</p>
      </template>
      <p class="recovery-foot">
        <ShieldCheck
          :size="18"
        />恢复码可重设密码，请像密码一样保管。使用、停用、重新生成或修改密码后，旧码都会失效。找回后所有设备需重新登录。
      </p>
    </section>
  </main>
</template>
<style scoped>
.recovery-page {
  max-width: 580px;
  margin: 0 auto;
  padding: 28px 18px 80px;
}
.back {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  margin-bottom: 14px;
}
.recovery-card {
  padding: 32px;
}
.recovery-icon {
  display: inline-flex;
  background: #fff0dc;
  color: #bd4b0b;
  padding: 16px;
  border-radius: 18px;
  margin-bottom: 18px;
}
h1 {
  font-size: 26px;
  line-height: 1.4;
  margin: 8px 0 14px;
}
.intro,
.recovery-foot {
  line-height: 1.8;
  color: #796b5a;
}
.intro {
  margin-bottom: 24px;
}
.recovery-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: #faf3e8;
  padding: 12px;
  border-radius: 12px;
  margin: 20px 0;
  font-size: 14px;
}
.recovery-status button {
  min-height: 44px;
  color: #9a410e;
  white-space: nowrap;
}
.recovery-input {
  font-family: monospace;
}
.saved-code {
  display: grid;
  gap: 14px;
}
.saved-code textarea {
  padding: 14px;
  font: 18px/1.6 monospace;
  background: #fff7e9;
  border: 1px solid #ecdac4;
  border-radius: 12px;
  resize: none;
  width: 100%;
  box-sizing: border-box;
}
.success {
  color: #366346;
  line-height: 1.7;
}
.recovery-foot {
  font-size: 12px;
  margin: 26px 0 0;
  padding-top: 20px;
  border-top: 1px solid #eee5d8;
}
.recovery-foot svg {
  vertical-align: middle;
  margin-right: 5px;
}
.stack .btn {
  width: 100%;
  min-height: 46px;
}
@media (max-width: 500px) {
  .recovery-card {
    padding: 22px;
  }
  h1 {
    font-size: 23px;
  }
  .recovery-page {
    padding-top: 16px;
  }
}
</style>
