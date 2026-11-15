<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  ArrowUpRight,
  Flame,
  Heart,
  MapPin,
  RefreshCw,
  ShieldCheck,
  Store,
  ClipboardList,
  ChartNoAxesCombined,
  GraduationCap,
} from "lucide-vue-next";
import { ApiError, api } from "../lib/api";
import { useSession } from "../stores/session";
import { notify } from "../lib/notify";
import { completePendingFollow } from "../lib/discovery";
import {
  isMerchantPath,
  loginDestination,
  safeReturnTo,
} from "../lib/identity";

const route = useRoute();
const router = useRouter();
const session = useSession();
const mode = ref<"login" | "register">("login");
const username = ref("");
const password = ref("");
const confirmation = ref("");
const displayName = ref("");
const phone = ref("");
const captchaId = ref("");
const captchaSvg = ref("");
const captchaInput = ref("");
const demoCaptchaCode = ref("");
const captchaLoading = ref(false);
const verificationCode = ref("");
const demoSmsCode = ref("");
const sendingCode = ref(false);
const smsCooldown = ref(0);
const busy = ref(false);
const error = ref("");
let cooldownTimer: ReturnType<typeof setInterval> | null = null;

function clearCooldown() {
  if (cooldownTimer) {
    clearInterval(cooldownTimer);
    cooldownTimer = null;
  }
}
function startCooldown(seconds: number) {
  clearCooldown();
  smsCooldown.value = Math.max(0, Math.floor(seconds));
  if (smsCooldown.value <= 0) return;
  cooldownTimer = setInterval(() => {
    if (smsCooldown.value <= 1) {
      smsCooldown.value = 0;
      clearCooldown();
    } else {
      smsCooldown.value -= 1;
    }
  }, 1000);
}
onUnmounted(clearCooldown);

function cleanedPhone() {
  return phone.value.replace(/[\s-]/g, "").replace(/^\+86/, "");
}

async function loadCaptcha() {
  if (captchaLoading.value) return;
  captchaLoading.value = true;
  try {
    const res = await api<{
      captcha_id: string;
      svg: string;
      demo_captcha_code?: string;
    }>("/auth/captcha");
    captchaId.value = res.captcha_id || "";
    captchaSvg.value = res.svg || "";
    demoCaptchaCode.value = res.demo_captcha_code || "";
    captchaInput.value = "";
  } catch {
    captchaId.value = "";
    captchaSvg.value = "";
    demoCaptchaCode.value = "";
  } finally {
    captchaLoading.value = false;
  }
}

watch(mode, (next) => {
  if (next === "register" && !captchaSvg.value) {
    void loadCaptcha();
  }
});

async function requestSmsCode() {
  if (sendingCode.value || busy.value || smsCooldown.value > 0) return;
  error.value = "";
  const normalizedPhone = cleanedPhone();
  if (!/^1[3-9]\d{9}$/.test(normalizedPhone)) {
    error.value = "请先填写有效的 11 位中国大陆手机号。";
    return;
  }
  const codeText = captchaInput.value.trim().toUpperCase();
  if (!captchaId.value || !/^[A-Z0-9]{4}$/.test(codeText)) {
    error.value = "请先填写 4 位图形校验码。";
    if (!captchaId.value) void loadCaptcha();
    return;
  }
  sendingCode.value = true;
  try {
    const res = await api<{
      sent: boolean;
      masked_phone: string;
      cooldown_seconds: number;
      demo_code?: string;
    }>("/auth/register/code", {
      method: "POST",
      body: {
        phone: normalizedPhone,
        captcha_id: captchaId.value,
        captcha_code: codeText,
      },
    });
    startCooldown(res.cooldown_seconds || 60);
    demoSmsCode.value = res.demo_code || "";
    notify(
      res.demo_code
        ? `已生成 ${res.masked_phone} 的演示验证码，可点下方一键填入`
        : `验证码已发送至 ${res.masked_phone}`,
      "success",
    );
  } catch (e) {
    error.value = (e as Error).message;
    if (!(e instanceof ApiError && e.status === 429 && e.code === "sms_cooldown")) {
      void loadCaptcha();
    }
  } finally {
    sendingCode.value = false;
  }
}

const merchantLogin = computed(
  () =>
    route.query.role === "merchant" ||
    isMerchantPath(safeReturnTo(route.query.returnTo)),
);
async function switchIdentity(merchant: boolean) {
  mode.value = "login";
  error.value = "";
  const currentTarget = safeReturnTo(route.query.returnTo);
  const target =
    isMerchantPath(currentTarget) === merchant
      ? currentTarget
      : merchant
        ? "/merchant"
        : "/";
  await router.replace({
    path: "/login",
    query: merchant
      ? { role: "merchant", returnTo: target }
      : target === "/"
        ? {}
        : { returnTo: target },
  });
}
async function fillDemo(merchant: boolean) {
  await switchIdentity(merchant);
  username.value = merchant ? "vendor" : "student";
  password.value = "demo12345";
}
function fillDemoVerification() {
  if (!/^1[3-9]\d{9}$/.test(cleanedPhone())) {
    const suffix = String(Math.floor(10000000 + Math.random() * 89999999));
    phone.value = `138${suffix}`;
  }
  if (demoCaptchaCode.value) {
    captchaInput.value = demoCaptchaCode.value;
  }
}
async function submit() {
  if (busy.value) return;
  error.value = "";
  if (mode.value === "register" && password.value !== confirmation.value) {
    error.value = "两次输入的密码不一致，请重新确认。";
    return;
  }
  const normalizedPhone = cleanedPhone();
  if (mode.value === "register") {
    if (!/^1[3-9]\d{9}$/.test(normalizedPhone)) {
      error.value = "请填写有效的 11 位中国大陆手机号完成验证。";
      return;
    }
    if (!/^\d{6}$/.test(verificationCode.value.trim())) {
      error.value = "请先通过图形校验获取并填写 6 位手机验证码。";
      return;
    }
  }
  busy.value = true;
  session.beginIdentityChange();
  try {
    await api(`/auth/${mode.value}`, {
      method: "POST",
      body:
        mode.value === "register"
          ? {
              username: username.value.trim(),
              password: password.value,
              display_name: displayName.value.trim(),
              phone: normalizedPhone,
              verification_code: verificationCode.value.trim(),
            }
          : {
              username: username.value.trim(),
              password: password.value,
            },
    });
    await session.refreshUser();
    session.setConsumerPreview(false);
    if (session.user)
      await completePendingFollow(
        session.user.id,
        route.query.returnTo,
        route.query.followIntent,
      );
    notify(
      mode.value === "register"
        ? merchantLogin.value
          ? "账号已创建，继续填写入驻资料"
          : "欢迎加入，去发现校园里的好味道吧"
        : session.isAdmin
          ? "已进入平台管理台"
          : session.isMerchant
            ? "欢迎回来，开始今天的好生意"
            : merchantLogin.value
              ? "已登录，可以继续填写入驻申请"
              : "欢迎回来",
      "success",
    );
    await router.replace(
      merchantLogin.value && !session.isMerchant && !session.isAdmin
        ? "/merchant/apply"
        : loginDestination(session.user, route.query.returnTo),
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div :class="['page', 'auth-page', { 'merchant-auth-page': merchantLogin }]">
    <RouterLink to="/" class="back-link"
      ><ArrowLeft :size="18" /> 返回逛逛</RouterLink
    >
    <div class="auth-layout">
      <section class="auth-story">
        <div class="story-mark">
          <Flame :size="29" /> 烟火地图
          <small v-if="merchantLogin">商家版</small>
        </div>
        <span class="eyebrow">GOOD FOOD. CLOSE BY.</span>
        <h1 v-if="merchantLogin">
          好手艺，<br />值得被更多人看见<span>。</span>
        </h1>
        <h1 v-else>爱上的那一口，<br />就在校园转角<span>。</span></h1>
        <p v-if="merchantLogin">
          轻松接单，从容出餐。<br />让每一份用心，都成为回头的理由。
        </p>
        <p v-else>
          收藏熟悉的味道，发现还没尝过的小摊。<br />下课后的幸福，从这里开始。
        </p>
        <div v-if="merchantLogin" class="story-lines">
          <span><ClipboardList :size="17" /> 接单、出餐、核销，一处处理</span>
          <span><ChartNoAxesCombined :size="17" /> 看清每天的订单与实收</span>
          <span><Store :size="17" /> 把摊位打理好，把顾客照顾好</span>
        </div>
        <div v-else class="story-lines">
          <span><MapPin :size="17" /> 找到正在出摊的好味道</span
          ><span><Heart :size="17" /> 记住你喜欢的那一家</span
          ><span><ShieldCheck :size="17" /> 到摊自取，现场付款</span>
        </div>
        <div class="story-footer">
          烟火就在转角处 <ArrowUpRight :size="25" />
        </div>
      </section>
      <section class="card auth-form">
        <div class="identity-switch" role="group" aria-label="选择登录身份">
          <button
            type="button"
            :class="{ active: !merchantLogin }"
            :aria-pressed="!merchantLogin"
            :disabled="busy"
            @click="switchIdentity(false)"
          >
            <GraduationCap :size="17" />学生端
          </button>
          <button
            type="button"
            :class="{ active: merchantLogin }"
            :aria-pressed="merchantLogin"
            :disabled="busy"
            @click="switchIdentity(true)"
          >
            <Store :size="17" />商家工作台
          </button>
        </div>
        <span class="eyebrow">{{
          merchantLogin ? "YOUR DAILY WORKSPACE" : "NICE TO MEET YOU"
        }}</span>
        <h2>
          {{
            merchantLogin
              ? mode === "login"
                ? "登录商家工作台"
                : "创建账号，申请开摊"
              : mode === "login"
                ? "回来吃点好的"
                : "认识一下，新朋友"
          }}
        </h2>
        <p class="muted">
          {{
            merchantLogin
              ? "已有账号直接登录；新商家创建账号后即可提交入驻申请。"
              : mode === "login"
                ? "登录后，继续你的校园寻味之旅。"
                : "创建账号，保存关注与每一份订单。"
          }}
        </p>
        <div class="tabs auth-tabs" aria-label="账号操作">
          <button
            type="button"
            :class="{ active: mode === 'login' }"
            @click="
              mode = 'login';
              error = '';
            "
          >
            登录</button
          ><button
            type="button"
            :class="{ active: mode === 'register' }"
            @click="
              mode = 'register';
              error = '';
            "
          >
            {{ merchantLogin ? "注册并申请" : "注册" }}
          </button>
        </div>
        <form @submit.prevent="submit" class="stack">
          <label class="field"
            >账号<input
              class="input"
              v-model="username"
              autocomplete="username"
              placeholder="输入你的账号"
              required
              minlength="3"
              maxlength="30"
              pattern="[A-Za-z0-9_]+"
              title="使用 3–30 位字母、数字或下划线"
          /></label>
          <label v-if="mode === 'register'" class="field"
            >昵称<input
              class="input"
              v-model="displayName"
              autocomplete="nickname"
              placeholder="怎么称呼你？"
              maxlength="30"
              required
          /></label>
          <template v-if="mode === 'register'">
            <label class="field"
              >手机号<input
                class="input"
                type="tel"
                inputmode="numeric"
                v-model="phone"
                autocomplete="tel"
                placeholder="输入 11 位中国大陆手机号"
                maxlength="11"
            /></label>
            <div class="field verify-field">
              <span class="verify-label">图形校验码</span>
              <div class="verify-row">
                <input
                  class="input verify-input"
                  v-model="captchaInput"
                  aria-label="图形校验码"
                  placeholder="输入右侧 4 位字符"
                  maxlength="4"
                  autocapitalize="characters"
                  autocomplete="off"
                />
                <button
                  type="button"
                  class="captcha-box"
                  :disabled="captchaLoading"
                  aria-label="刷新图形校验码"
                  title="点击换一张"
                  @click="loadCaptcha"
                >
                  <img
                    v-if="captchaSvg"
                    :src="captchaSvg"
                    alt="图形校验码图片"
                    width="132"
                    height="44"
                  />
                  <span v-else class="captcha-fallback">
                    <RefreshCw :size="14" />点此加载
                  </span>
                </button>
              </div>
            </div>
            <div class="field verify-field">
              <span class="verify-label">手机验证码</span>
              <div class="verify-row">
                <input
                  class="input verify-input"
                  inputmode="numeric"
                  v-model="verificationCode"
                  aria-label="手机验证码"
                  placeholder="输入 6 位验证码"
                  maxlength="6"
                  autocomplete="one-time-code"
                />
                <button
                  type="button"
                  class="btn btn-secondary send-code-btn"
                  :disabled="sendingCode || smsCooldown > 0 || busy"
                  @click="requestSmsCode"
                >
                  {{
                    sendingCode
                      ? "发送中…"
                      : smsCooldown > 0
                        ? `${smsCooldown}s 后重发`
                        : "获取验证码"
                  }}
                </button>
              </div>
              <div
                v-if="demoSmsCode"
                class="demo-sms-banner"
                role="status"
              >
                <span>演示环境验证码：<strong>{{ demoSmsCode }}</strong></span>
                <button
                  type="button"
                  @click="verificationCode = demoSmsCode"
                >
                  一键填入
                </button>
              </div>
            </div>
          </template>
          <label class="field"
            >密码<input
              class="input"
              type="password"
              v-model="password"
              :autocomplete="
                mode === 'login' ? 'current-password' : 'new-password'
              "
              placeholder="至少 8 位字符"
              :minlength="mode === 'register' ? 8 : undefined"
              required
              maxlength="128"
          /></label>
          <label v-if="mode === 'register'" class="field"
            >确认密码<input
              class="input"
              type="password"
              v-model="confirmation"
              autocomplete="new-password"
              placeholder="再输入一次密码"
              required
              minlength="8"
              maxlength="128"
          /></label>
          <p v-if="error" class="error-message" role="alert">{{ error }}</p>
          <button class="btn btn-primary submit-button" :disabled="busy">
            {{
              busy
                ? "正在处理…"
                : mode === "login"
                  ? merchantLogin
                    ? "登录商家工作台"
                    : "登录，继续探索"
                  : "创建账号"
            }}<ArrowUpRight :size="18" />
          </button>
        </form>
        <RouterLink
          v-if="mode === 'login'"
          class="recovery-link"
          :to="{
            path: '/recover',
            query: {
              ...(merchantLogin ? { role: 'merchant' } : {}),
              ...(typeof route.query.returnTo === 'string' &&
              safeReturnTo(route.query.returnTo) !== '/'
                ? { returnTo: safeReturnTo(route.query.returnTo) }
                : {}),
            },
          }"
          >忘记密码？使用恢复码找回</RouterLink
        >
        <p class="auth-note">
          <ShieldCheck :size="16" />
          {{
            merchantLogin
              ? "申请审核、公开展示与交易资格分别核验，注册不会自动开通接单。"
              : "注册需完成图形校验与手机号验证（一人一号防刷，不收集学号或宿舍号）；游客也可直接浏览摊位。"
          }}
        </p>
        <div v-if="session.config?.demo_mode" class="demo-accounts">
          <span>{{
            mode === "register"
              ? "演示环境 · 注册验证辅助"
              : "演示环境 · 体验账号"
          }}</span>
          <div v-if="mode === 'login'">
            <button
              v-if="!merchantLogin"
              type="button"
              @click="fillDemo(false)"
            >
              填入学生账号</button
            ><button type="button" @click="fillDemo(true)">填入商家账号</button>
          </div>
          <div v-else>
            <button
              v-if="demoCaptchaCode"
              type="button"
              @click="fillDemoVerification"
            >
              填入测试手机与校验码</button
            ><button type="button" @click="fillDemo(false)">改用现成学生账号</button>
          </div>
          <small>{{
            mode === "register"
              ? "演示环境未接入付费短信网关，通过图形校验后将直接生成可核验的 6 位验证码"
              : "两个演示账号的密码均为 demo12345"
          }}</small>
        </div>
        <RouterLink to="/ops/login" class="ops-entry-link">
          <ShieldCheck :size="15" /> 平台运营人员？前往管理员登录
          <ArrowUpRight :size="14" />
        </RouterLink>
      </section>
    </div>
  </div>
</template>

<style scoped>
.recovery-link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  margin-top: 8px;
  color: #995124;
  font-size: 13px;
}
.auth-page {
  padding-top: 30px;
}
.merchant-auth-page {
  min-height: 100dvh;
}
.identity-switch {
  display: flex;
  padding: 4px;
  gap: 4px;
  border: 1px solid #e9e0d4;
  background: #f8f4ed;
  border-radius: 14px;
  margin-bottom: 28px;
}
.identity-switch button {
  flex: 1;
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #777166;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
.identity-switch button.active {
  color: #b94a18;
  background: #fff;
  box-shadow: 0 2px 8px #5439120c;
}
.identity-switch button:focus-visible {
  outline: 2px solid #ed7136;
  outline-offset: 2px;
}
.story-mark small {
  border: 1px solid #d8c2a0;
  border-radius: 6px;
  padding: 4px 7px;
  font-size: 11px;
  color: #886843;
}
.merchant-auth-page .auth-story {
  background: linear-gradient(145deg, #f6ead4, #ebe0c9);
}
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: 14px;
  color: var(--muted, #7b766e);
  margin-bottom: 24px;
}
.auth-layout {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  gap: 28px;
  max-width: 1060px;
  margin: 0 auto;
}
.auth-story {
  background: #eee8da;
  border-radius: 26px;
  padding: 44px;
  display: flex;
  flex-direction: column;
  min-height: 600px;
  position: relative;
  overflow: hidden;
}
.auth-story::after {
  content: "";
  position: absolute;
  width: 310px;
  height: 310px;
  border: 1px solid #d4b783;
  border-radius: 50%;
  bottom: -170px;
  right: -60px;
  box-shadow:
    0 0 0 40px #e8dfcb,
    0 0 0 41px #d4b783;
}
.story-mark {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 20px;
  font-weight: 800;
  margin-bottom: 78px;
}
.story-mark svg {
  color: #eb6b30;
}
.auth-story .eyebrow {
  font-size: 11px;
  letter-spacing: 2px;
  color: #827458;
}
.auth-story h1 {
  font-size: clamp(30px, 3vw, 43px);
  line-height: 1.45;
  letter-spacing: -1.2px;
  margin: 16px 0;
}
.auth-story h1 span {
  color: #ef753c;
}
.auth-story p {
  font-size: 14px;
  line-height: 1.9;
  color: #746c5d;
}
.story-lines {
  display: grid;
  gap: 13px;
  margin-top: 25px;
}
.story-lines span {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
  color: #6f644f;
}
.story-footer {
  margin-top: auto;
  padding-top: 55px;
  display: flex;
  align-items: center;
  gap: 17px;
  font-family: serif;
  font-size: 20px;
  z-index: 1;
}
.auth-form {
  padding: 45px 42px;
  align-self: center;
}
.auth-form h2 {
  font-size: 28px;
  letter-spacing: -1px;
  margin: 12px 0;
}
.auth-form > .muted {
  font-size: 14px;
  margin-bottom: 26px;
}
.auth-tabs {
  margin-bottom: 24px;
}
.auth-tabs button {
  flex: 1;
}
.submit-button {
  width: 100%;
  justify-content: center;
  margin-top: 6px;
}
.auth-note {
  display: flex;
  gap: 7px;
  align-items: flex-start;
  color: #8d877e;
  font-size: 12px;
  line-height: 1.7;
  margin: 24px 0 0;
}
.auth-form .stack {
  gap: 15px;
}
.verify-field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.verify-label {
  font-size: 13px;
  font-weight: 600;
  color: inherit;
}
.verify-row {
  display: flex;
  align-items: stretch;
  gap: 8px;
}
.verify-input {
  flex: 1;
  min-width: 0;
}
.captcha-box {
  width: 132px;
  min-height: 44px;
  padding: 0;
  border-radius: 10px;
  border: 1px solid #e5d2ba;
  background: #fff6ea;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  cursor: pointer;
}
.captcha-box img {
  display: block;
  width: 132px;
  height: 44px;
}
.captcha-fallback {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #8c6a46;
}
.send-code-btn {
  flex-shrink: 0;
  min-height: 44px;
  min-width: 116px;
  padding: 0 14px;
  font-size: 13px;
  white-space: nowrap;
}
.demo-sms-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 10px;
  background: #fdf4e5;
  border: 1px solid #ebd5b3;
  font-size: 12px;
  color: #7c5834;
}
.demo-sms-banner strong {
  font-size: 14px;
  letter-spacing: 1.5px;
  color: #a94717;
  font-variant-numeric: tabular-nums;
}
.demo-sms-banner button {
  min-height: 36px;
  padding: 4px 10px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #dfc39d;
  color: #9b4819;
  font-size: 12px;
  font-weight: 600;
}
@media (max-width: 760px) {
  .auth-page {
    padding-top: 18px;
  }
  .auth-layout {
    display: block;
  }
  .auth-story {
    display: none;
  }
  .auth-form {
    padding: 28px 23px;
    max-width: 460px;
    margin: 0 auto;
  }
  .back-link {
    margin-bottom: 16px;
  }
  .auth-form h2 {
    font-size: 26px;
  }
}
.demo-accounts {
  margin-top: 22px;
  border-top: 1px dashed #ddd2be;
  padding-top: 18px;
  color: #a38d6e;
  font-size: 11px;
}
.demo-accounts > div {
  display: flex;
  gap: 8px;
  margin: 10px 0;
}
.demo-accounts button {
  border: 1px solid #e7d7bd;
  border-radius: 7px;
  padding: 8px 12px;
  min-height: 44px;
  color: #a37f50;
  background: #fcf6eb;
  font-size: 11px;
}
.demo-accounts small {
  font-size: 10px;
  color: #b2a58e;
}
.ops-entry-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  margin-top: 14px;
  color: #5b6961;
  font-size: 12px;
  font-weight: 600;
}
.ops-entry-link:hover {
  color: #24372f;
}
</style>
