<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  ArrowUpRight,
  ClipboardCheck,
  FileSpreadsheet,
  LockKeyhole,
  MapPinCheck,
  ShieldCheck,
} from "lucide-vue-next";
import { api } from "../lib/api";
import { loginDestination, safeReturnTo } from "../lib/identity";
import { notify } from "../lib/notify";
import type { User } from "../lib/types";
import { useSession } from "../stores/session";
import "../ops.css";

const route = useRoute();
const router = useRouter();
const session = useSession();

const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");

function fillDemoAdmin() {
  error.value = "";
  username.value = "admin";
  password.value = "demo12345";
}

async function submit() {
  if (busy.value) return;
  error.value = "";
  const trimmed = username.value.trim();
  if (!trimmed || !password.value) {
    error.value = "请输入管理员账号与登录密码。";
    return;
  }
  busy.value = true;
  session.beginIdentityChange();
  try {
    await api<User>("/ops/auth/login", {
      method: "POST",
      body: {
        username: trimmed,
        password: password.value,
      },
    });
    await session.refreshUser();
    session.setConsumerPreview(false);
    notify("欢迎进入烟火地图平台管理台", "success");
    await router.replace(
      loginDestination(session.user, safeReturnTo(route.query.returnTo)),
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="ops-login-page">
    <div class="ops-login-topbar">
      <RouterLink
        to="/"
        class="ops-login-back"
        @click="session.setConsumerPreview(true)"
      >
        <ArrowLeft :size="17" /> 返回校园首页
      </RouterLink>
      <RouterLink to="/login" class="ops-login-back">
        学生 / 商家登录 <ArrowUpRight :size="16" />
      </RouterLink>
    </div>

    <div class="ops-login-layout">
      <section class="ops-login-story">
        <div>
          <div class="ops-story-badge">
            <span><ShieldCheck :size="23" /></span>
            烟火地图 <small>治理台</small>
          </div>
        </div>

        <div>
          <span class="ops-story-eyebrow">CAMPUS GOVERNANCE CONSOLE</span>
          <h1>守护校园烟火秩序，<br />让每一笔信任有据可查。</h1>
          <p>
            连接学生寻味与摊主经营的治理中枢：集中完成入驻初审、证照与准入分级、校园取餐区划、异常资金核验与现场位置纠偏。
          </p>
        </div>

        <div class="ops-story-pillars">
          <div>
            <ClipboardCheck :size="18" />
            <div>
              <strong>分层准入与证照核验</strong>
              <span>流动摊展示与持证门店线上交易严格隔离，杜绝越权开通。</span>
            </div>
          </div>
          <div>
            <MapPinCheck :size="18" />
            <div>
              <strong>现场位置核实与过期停单</strong>
              <span>结合现场巡查结论核验位置纠错，维护校园真实点位指标。</span>
            </div>
          </div>
          <div>
            <FileSpreadsheet :size="18" />
            <div>
              <strong>只读履约监督与渠道对账</strong>
              <span>治理台默认只读监督履约，关键治理操作全程留痕，支持责任追溯。</span>
            </div>
          </div>
        </div>

        <div class="ops-story-foot">
          <span>三端协同 · 学生端 / 商家工作台 / 平台管理台</span>
          <LockKeyhole :size="16" />
        </div>
      </section>

      <section class="ops-login-card">
        <span class="eyebrow">OPERATOR AUTHENTICATION</span>
        <h2>管理员登录</h2>
        <p class="ops-login-sub">
          仅限经授权的校园运营与平台治理人员登录。管理员账号不开放公开自助注册。
        </p>

        <p v-if="session.user && !session.isAdmin" class="o-callout warn">
          当前浏览器已登录普通账号
          <strong>{{ session.user.username }}</strong
          >，提交管理员凭证后将自动切换为治理会话。
        </p>

        <form class="stack" @submit.prevent="submit">
          <label class="o-field">
            管理员账号
            <input
              v-model="username"
              class="o-input"
              name="username"
              autocomplete="username"
              placeholder="请输入管理员用户名"
              required
              maxlength="150"
            />
          </label>
          <label class="o-field">
            登录密码
            <input
              v-model="password"
              class="o-input"
              type="password"
              name="password"
              autocomplete="current-password"
              placeholder="请输入管理员密码"
              required
              maxlength="128"
            />
          </label>

          <p v-if="error" class="o-alert" role="alert">{{ error }}</p>

          <button
            type="submit"
            class="o-btn o-btn-pine"
            style="width: 100%; margin-top: 4px"
            :disabled="busy"
          >
            {{ busy ? "正在核验身份…" : "进入平台管理台" }}
            <ArrowUpRight :size="17" />
          </button>
        </form>

        <div class="ops-login-notice">
          <ShieldCheck :size="16" style="flex-shrink: 0; margin-top: 2px" />
          <span>
            登录与管理操作受速率限制、会话身份绑定及操作审计保护；若需重置管理员凭证，请联系系统超级管理员处理。
          </span>
        </div>

        <div v-if="session.config?.demo_mode" class="ops-demo-box">
          <span>演示环境 · 平台治理体验账号</span>
          <div>
            <button type="button" @click="fillDemoAdmin">
              填入管理员账号（admin）
            </button>
          </div>
          <small>演示密码为 demo12345，可完整体验入驻审核、准入分级、对账导出与现场核实。</small>
        </div>
      </section>
    </div>
  </div>
</template>
