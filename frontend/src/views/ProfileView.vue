<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Heart,
  LogOut,
  MapPin,
  MessageSquare,
  Settings2,
  ShieldCheck,
  ShoppingBag,
  Star,
  Store,
  Trash2,
  UserRound,
} from "lucide-vue-next";
import { api, statusText } from "../lib/api";
import { useSession } from "../stores/session";
import { notify } from "../lib/notify";
import { useFollowState } from "../lib/discovery";
import { orderPage, type OrderCounts } from "../lib/orderPages";
import type { StallSummary as Stall, DiscoveryPage } from "../lib/types";

const router = useRouter();
const session = useSession();
const section = ref("follows");
const follows = ref<Stall[]>([]);
const nextFollows = ref<string | null>(null);
const moreLoading = ref(false);
async function loadMoreFollows() {
  if (!nextFollows.value || moreLoading.value) return;
  const read = followState.snapshot();
  moreLoading.value = true;
  try {
    const result = await api<DiscoveryPage<Stall>>(
      `/follows?cursor=${encodeURIComponent(nextFollows.value)}`,
    );
    if (followState.isCurrent(read)) {
      follows.value = [
        ...new Map(
          [
            ...follows.value,
            ...result.results.map((row) => followState.reconcile(row, read)),
          ].map((row) => [row.id, row]),
        ).values(),
      ];
      nextFollows.value = result.next;
    }
  } catch (cause) {
    if (followState.isCurrent(read)) error.value = (cause as Error).message;
  } finally {
    moreLoading.value = false;
  }
}
const followState = useFollowState((id, followed) => {
  if (!followed) follows.value = follows.value.filter((item) => item.id !== id);
});
const { pendingFollows } = followState;
watch(
  () => session.user?.id,
  () => {
    follows.value = [];
  },
);
const orderCounts = ref<OrderCounts>({ all: 0, reviewed: 0 });
const loading = ref(true);
const error = ref("");
const busy = ref(false);
const displayName = ref("");
const oldPassword = ref("");
const newPassword = ref("");
const passwordConfirm = ref("");
const feedback = ref("");
const feedbackContact = ref("");
const deletionOpen = ref(false);
const deletionConfirmation = ref("");
const deletionPassword = ref("");
const reviews = computed(() => orderCounts.value.reviewed || 0);
const sections = [
  { id: "follows", title: "我的关注", icon: Heart },
  { id: "settings", title: "账号设置", icon: Settings2 },
  { id: "feedback", title: "意见反馈", icon: MessageSquare },
  { id: "privacy", title: "隐私与账号", icon: ShieldCheck },
];
onMounted(async () => {
  let read = followState.snapshot();
  try {
    await session.load();
    read = followState.snapshot();
    if (!followState.isCurrent(read)) return;
    if (!session.user) {
      await router.replace({ path: "/login", query: { returnTo: "/me" } });
      return;
    }
    displayName.value = session.user.display_name;
    const results = await Promise.all([
      api<DiscoveryPage<Stall>>("/follows"),
      orderPage("/orders", "all", null, undefined, 1),
    ]);
    if (followState.isCurrent(read)) {
      nextFollows.value = results[0].next;
      follows.value = results[0].results
        .map((stall) => followState.reconcile(stall, read))
        .filter((stall) => stall.is_followed);
      orderCounts.value = results[1].counts;
    }
  } catch (e) {
    if (followState.isCurrent(read)) error.value = (e as Error).message;
  } finally {
    if (followState.isCurrent(read)) loading.value = false;
  }
});
async function unfollow(stall: Stall) {
  await followState.follow(stall, false);
}
async function saveProfile() {
  busy.value = true;
  try {
    await api("/auth/profile", {
      method: "PATCH",
      body: { display_name: displayName.value.trim() },
    });
    await session.refreshUser();
    notify("昵称已更新", "success");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    busy.value = false;
  }
}
async function changePassword() {
  if (newPassword.value !== passwordConfirm.value) {
    notify("两次输入的新密码不一致", "error");
    return;
  }
  busy.value = true;
  try {
    await api("/auth/password", {
      method: "POST",
      body: {
        old_password: oldPassword.value,
        new_password: newPassword.value,
      },
    });
    oldPassword.value = "";
    newPassword.value = "";
    passwordConfirm.value = "";
    notify("密码已更新", "success");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    busy.value = false;
  }
}
async function sendFeedback() {
  busy.value = true;
  try {
    await api("/feedback", {
      method: "POST",
      body: {
        content: feedback.value.trim(),
        contact: feedbackContact.value.trim(),
      },
    });
    feedback.value = "";
    feedbackContact.value = "";
    notify("反馈已提交，感谢你帮助校园小摊变得更好", "success");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    busy.value = false;
  }
}
async function logout() {
  try {
    await session.logout();
    notify("已退出登录", "info");
    await router.push("/");
  } catch (e) {
    notify((e as Error).message, "error");
  }
}
async function deleteAccount() {
  if (deletionConfirmation.value !== session.user?.username) return;
  busy.value = true;
  try {
    await api("/auth/account", {
      method: "DELETE",
      body: { password: deletionPassword.value },
    });
    await session.refreshUser();
    notify("账号已注销", "info");
    await router.replace("/");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="page profile-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">YOUR CORNER OF CAMPUS</span>
        <h1>我的烟火日常<span class="heading-dot">.</span></h1>
        <p class="muted">记住喜欢的味道，也记住生活的小美好。</p>
      </div>
    </div>
    <p v-if="error" class="error-message">{{ error }}</p>
    <div v-if="loading" class="empty-state"><span class="spinner"></span></div>
    <div v-else-if="session.user" class="profile-layout">
      <aside class="profile-sidebar">
        <section class="card identity-card">
          <div class="avatar"><UserRound :size="35" /></div>
          <h2>{{ session.user.display_name || session.user.username }}</h2>
          <p>@{{ session.user.username }}</p>
          <span class="identity-caption"
            >校园寻味家 <Star :size="11" fill="currentColor"
          /></span>
          <div class="profile-stats">
            <button @click="section = 'follows'">
              <strong>{{ follows.length }}</strong
              ><span>关注</span></button
            ><RouterLink to="/orders"
              ><strong>{{ orderCounts.all }}</strong
              ><span>订单</span></RouterLink
            ><RouterLink to="/orders"
              ><strong>{{ reviews }}</strong
              ><span>评价</span></RouterLink
            >
          </div>
        </section>
        <nav class="card profile-menu" aria-label="个人中心">
          <RouterLink to="/orders"
            ><ShoppingBag :size="18" /> 我的订单
            <ChevronRight :size="15" /></RouterLink
          ><button
            v-for="item in sections"
            :key="item.id"
            :class="{ active: section === item.id }"
            @click="section = item.id"
          >
            <component :is="item.icon" :size="18" /> {{ item.title }}
            <ChevronRight :size="15" /></button
          ><RouterLink
            v-if="session.user.is_merchant || session.user.is_staff"
            to="/merchant"
            ><Store :size="18" /> 商家工作台
            <ChevronRight :size="15" /></RouterLink
          ><RouterLink v-else to="/merchant/apply"
            ><Store :size="18" />{{
              session.user.merchant_application_status
                ? "我的入驻申请"
                : "我是商家，申请入驻"
            }}<ChevronRight :size="15" /></RouterLink
          ><button class="logout-item" @click="logout">
            <LogOut :size="18" /> 退出登录
          </button>
        </nav>
        <p class="sidebar-note">
          烟火就在转角处。<br /><span>SEE YOU AROUND THE CORNER.</span>
        </p>
      </aside>
      <section class="profile-content">
        <template v-if="section === 'follows'"
          ><div class="section-heading">
            <div>
              <span class="eyebrow">MY FAVORITE PLACES</span>
              <h2>总想再吃一口的摊位</h2>
            </div>
            <span class="muted count-label">{{ follows.length }} 个关注</span>
          </div>
          <div v-if="!follows.length" class="empty-state card">
            <Heart :size="40" />
            <h3>把喜欢的小摊，留在这里</h3>
            <p>在摊位详情点一下关注，下次就能轻松找到。</p>
            <RouterLink to="/" class="btn btn-primary"
              >发现校园好味道 <ArrowUpRight :size="16"
            /></RouterLink>
          </div>
          <div v-else class="follow-grid">
            <article
              v-for="stall in follows"
              :key="stall.id"
              class="card followed-stall"
            >
              <RouterLink :to="`/stalls/${stall.id}`" class="follow-image"
                ><img :src="stall.image" :alt="stall.name" /><span
                  class="stall-status"
                  :class="stall.status"
                  >{{ statusText(stall.status) }}</span
                ></RouterLink
              >
              <div class="followed-copy">
                <div class="follow-title">
                  <RouterLink :to="`/stalls/${stall.id}`"
                    ><h3>{{ stall.name }}</h3></RouterLink
                  ><button
                    :aria-label="`取消关注${stall.name}`"
                    @click="unfollow(stall)"
                    :disabled="pendingFollows.has(stall.id)"
                    :aria-busy="pendingFollows.has(stall.id)"
                  >
                    <Heart :size="18" fill="currentColor" />
                  </button>
                </div>
                <p><MapPin :size="13" /> {{ stall.address }}</p>
                <RouterLink :to="`/stalls/${stall.id}`" class="follow-visit"
                  >今天吃点什么 <ArrowUpRight :size="15"
                /></RouterLink>
              </div>
            </article>
          </div>
          <button
            v-if="nextFollows"
            class="btn btn-secondary"
            :disabled="moreLoading"
            @click="loadMoreFollows"
          >
            {{ moreLoading ? "正在加载" : "加载更多关注" }}
          </button>
        </template>
        <template v-else-if="section === 'settings'"
          ><div class="section-heading">
            <div>
              <span class="eyebrow">MAKE YOURSELF AT HOME</span>
              <h2>账号设置</h2>
            </div>
          </div>
          <section class="card settings-card">
            <h3>个人资料</h3>
            <form @submit.prevent="saveProfile" class="stack">
              <label class="field"
                >昵称<input
                  class="input"
                  v-model="displayName"
                  maxlength="30"
                  required
              /></label>
              <div>
                <button class="btn btn-primary" :disabled="busy">
                  保存昵称 <CheckCircle2 :size="16" />
                </button>
              </div>
            </form>
          </section>
          <section class="card settings-card">
            <h3>修改密码</h3>
            <p class="muted">修改密码后，请重新生成并保存账号恢复码。</p>
            <form @submit.prevent="changePassword" class="stack">
              <label class="field"
                >当前密码<input
                  type="password"
                  class="input"
                  v-model="oldPassword"
                  autocomplete="current-password"
                  required /></label
              ><label class="field"
                >新密码<input
                  type="password"
                  class="input"
                  v-model="newPassword"
                  autocomplete="new-password"
                  minlength="8"
                  maxlength="128"
                  required
                  placeholder="至少 8 位字符" /></label
              ><label class="field"
                >再次输入新密码<input
                  type="password"
                  class="input"
                  v-model="passwordConfirm"
                  autocomplete="new-password"
                  minlength="8"
                  required
              /></label>
              <div>
                <button class="btn btn-secondary" :disabled="busy">
                  更新密码
                </button>
              </div>
            </form>
          </section>
          <section class="card settings-card">
            <h3>账号找回</h3>
            <p class="muted">
              先保存一次性恢复码，忘记密码时可用它设置新密码。
            </p>
            <RouterLink class="btn btn-secondary" to="/account-security"
              >管理恢复码</RouterLink
            >
          </section></template
        >
        <template v-else-if="section === 'feedback'"
          ><div class="section-heading">
            <div>
              <span class="eyebrow">WE ARE ALL EARS</span>
              <h2>让烟火地图更好一点</h2>
            </div>
          </div>
          <section class="card settings-card">
            <p class="muted section-description">
              找摊时遇到的问题、使用中的不便，或一个小小的好点子，我们都想听听。
            </p>
            <form @submit.prevent="sendFeedback" class="stack">
              <label class="field"
                >你的建议<textarea
                  class="input"
                  v-model="feedback"
                  rows="7"
                  minlength="5"
                  maxlength="1000"
                  required
                  placeholder="请尽量描述具体的场景，帮助我们了解问题…"
                ></textarea
                ><span class="field-counter"
                  >{{ feedback.length }}/1000</span
                ></label
              ><label class="field"
                >联系方式 <span class="muted">选填</span
                ><input
                  class="input"
                  v-model="feedbackContact"
                  maxlength="100"
                  placeholder="邮箱或手机号，方便我们进一步了解"
              /></label>
              <div>
                <button class="btn btn-primary" :disabled="busy">
                  {{ busy ? "正在提交…" : "发送反馈" }}
                  <ArrowUpRight :size="16" />
                </button>
              </div>
            </form></section
        ></template>
        <template v-else
          ><div class="section-heading">
            <div>
              <span class="eyebrow">YOUR TRUST MATTERS</span>
              <h2>隐私与账号</h2>
            </div>
          </div>
          <section class="card settings-card privacy-copy">
            <h3><ShieldCheck :size="19" /> 只在需要的时候，使用必要的信息</h3>
            <p>
              账号和昵称用于登录、保存关注与订单。浏览小摊不需要填写学号或提交学生证。
            </p>
            <p>
              点击定位后，浏览器才会申请位置权限；你也可以手动选择校园区域。拒绝定位不会影响浏览和搜索。
            </p>
            <p>
              自取订单可选填手机号；配送订单需要收餐人称呼、联系号码和校园交接点，供所属商家完成本单配送。无需填写宿舍房间号或学号。订单中的商品价格、配送费和交接位置保存下单时的记录，方便之后核对。
            </p>
            <p>请勿在公开评价和意见反馈中填写密码、身份证号等敏感信息。</p>
          </section>
          <section class="card settings-card deletion-card">
            <h3>注销账号</h3>
            <p class="muted section-description">
              注销后账号将停用，个人识别信息将匿名化，必要的交易记录会保留。请先完成或取消进行中的订单；商家与运营账号不支持在此注销。
            </p>
            <button
              v-if="!deletionOpen"
              class="delete-toggle"
              @click="deletionOpen = true"
            >
              <Trash2 :size="15" /> 我想注销账号
            </button>
            <form v-else @submit.prevent="deleteAccount" class="stack">
              <label class="field"
                >输入账号 {{ session.user.username }} 确认<input
                  class="input"
                  v-model="deletionConfirmation"
                  autocomplete="off"
                  required /></label
              ><label class="field"
                >当前密码<input
                  class="input"
                  v-model="deletionPassword"
                  type="password"
                  autocomplete="current-password"
                  required
              /></label>
              <div class="deletion-buttons">
                <button
                  type="button"
                  class="btn btn-secondary"
                  @click="deletionOpen = false"
                >
                  保留账号</button
                ><button
                  class="btn btn-secondary delete-toggle"
                  :disabled="
                    busy || deletionConfirmation !== session.user.username
                  "
                >
                  {{ busy ? "正在处理…" : "确认注销" }}
                </button>
              </div>
            </form>
          </section></template
        >
      </section>
    </div>
  </div>
</template>

<style scoped>
.profile-page {
  padding-top: 40px;
}
.heading-dot {
  color: #e78145;
}
.profile-layout {
  display: grid;
  grid-template-columns: 270px minmax(0, 1fr);
  gap: 40px;
  align-items: start;
}
.profile-sidebar {
  display: grid;
  gap: 17px;
}
.identity-card {
  text-align: center;
  padding: 28px 22px 0;
  overflow: hidden;
}
.avatar {
  width: 74px;
  height: 74px;
  border-radius: 50%;
  background: #e9dfc7;
  color: #998165;
  display: grid;
  place-items: center;
  margin: 0 auto 16px;
  border: 4px solid #f6efdf;
}
.identity-card h2 {
  font-size: 20px;
  margin: 0 0 6px;
}
.identity-card > p {
  font-size: 11px;
  color: #a59a88;
  margin: 0;
}
.identity-caption {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #b99564;
  font-size: 10px;
  margin-top: 12px;
}
.profile-stats {
  display: flex;
  margin-top: 24px;
  padding: 18px 0;
  border-top: 1px solid #eee4d7;
}
.profile-stats > * {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 7px;
  border: 0;
  background: none;
  color: #39382e;
  padding: 0;
}
.profile-stats > * + * {
  border-left: 1px solid #ece3d5;
}
.profile-stats strong {
  font-size: 19px;
  font-weight: 600;
}
.profile-stats span {
  font-size: 10px;
  color: #9a8e7b;
}
.profile-menu {
  padding: 10px;
}
.profile-menu > * {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 48px;
  padding: 0 13px;
  border-radius: 9px;
  font-size: 12px;
  width: 100%;
  border: 0;
  background: none;
  color: #847864;
  text-align: left;
}
.profile-menu > * > svg:last-child:not(:first-child) {
  margin-left: auto;
  color: #bbad99;
}
.profile-menu > .active {
  background: #faecd9;
  color: #c97737;
}
.profile-menu > .logout-item {
  border-top: 1px solid #f0e7d9;
  border-radius: 0;
  margin-top: 7px;
  color: #ab947a;
}
.sidebar-note {
  text-align: center;
  font-family: serif;
  font-size: 17px;
  color: #a69982;
  line-height: 1.9;
  margin: 12px 0;
}
.sidebar-note span {
  font-family: Arial, sans-serif;
  font-size: 8px;
  letter-spacing: 1.3px;
}
.profile-content {
  min-width: 0;
}
.profile-content > .section-heading {
  min-height: 70px;
  margin: 0 0 20px;
}
.profile-content .section-heading h2 {
  font-size: 22px;
  margin: 10px 0 0;
}
.profile-content .section-heading .eyebrow {
  font-size: 9px;
  letter-spacing: 1.6px;
}
.count-label {
  font-size: 11px;
  white-space: nowrap;
}
.follow-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}
.followed-stall {
  overflow: hidden;
  padding: 0;
}
.follow-image {
  display: block;
  position: relative;
  height: 165px;
  overflow: hidden;
}
.follow-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s;
}
.followed-stall:hover .follow-image img {
  transform: scale(1.04);
}
.stall-status {
  position: absolute;
  left: 13px;
  top: 13px;
  padding: 5px 8px;
  border-radius: 5px;
  background: #fff9;
  font-size: 10px;
  color: #766d5f;
  backdrop-filter: blur(8px);
}
.stall-status.open {
  background: #fff6e7;
  color: #98702c;
}
.followed-copy {
  padding: 16px 18px;
}
.follow-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
.follow-title h3 {
  font-size: 15px;
  margin: 0;
}
.follow-title button {
  border: 0;
  background: none;
  display: grid;
  place-items: center;
  color: #df8e5e;
  min-width: 44px;
  min-height: 44px;
  margin: -10px;
}
.followed-copy > p {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #a0927c;
  font-size: 11px;
  line-height: 1.7;
  margin: 9px 0 17px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.followed-copy > p svg {
  flex: none;
}
.follow-visit {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid #eee5d8;
  padding-top: 14px;
  font-size: 11px;
  color: #b5814e;
}
.settings-card {
  padding: 28px;
  margin-bottom: 22px;
}
.settings-card h3 {
  font-size: 16px;
  margin: 0 0 23px;
}
.settings-card .stack {
  max-width: 490px;
  gap: 19px;
}
.settings-card .field > .muted {
  font-size: 11px;
  font-weight: 400;
}
.section-description {
  font-size: 13px;
  line-height: 1.9;
  margin: 0 0 24px;
}
.field-counter {
  text-align: right;
  font-size: 10px;
  color: #ad9d87;
  font-weight: 400;
}
.privacy-copy h3 {
  display: flex;
  gap: 9px;
  align-items: center;
}
.privacy-copy p {
  font-size: 13px;
  color: #8f8270;
  line-height: 2;
}
.delete-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: #b17358;
  font-size: 12px;
  min-height: 44px;
  background: none;
  border: 0;
  padding: 0;
}
.deletion-buttons {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.deletion-buttons .delete-toggle {
  border: 1px solid #dbb09a;
  padding: 10px 18px;
}
.deletion-card .field {
  font-size: 12px;
}
@media (max-width: 900px) {
  .profile-layout {
    grid-template-columns: 225px minmax(0, 1fr);
    gap: 23px;
  }
  .follow-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 650px) {
  .profile-page {
    padding-top: 23px;
  }
  .profile-layout {
    display: block;
  }
  .profile-sidebar {
    margin-bottom: 25px;
    gap: 15px;
  }
  .identity-card {
    padding-top: 22px;
  }
  .profile-menu {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 3px;
  }
  .profile-menu > * {
    font-size: 11px;
    padding: 0 11px;
    gap: 8px;
  }
  .profile-menu > * > svg:last-child:not(:first-child) {
    width: 12px;
  }
  .profile-menu > .logout-item {
    border: 0;
    margin: 0;
  }
  .sidebar-note {
    display: none;
  }
  .profile-content .section-heading h2 {
    font-size: 20px;
  }
  .follow-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .follow-image {
    height: 125px;
  }
  .followed-copy {
    padding: 13px;
  }
  .follow-title h3 {
    font-size: 13px;
  }
  .followed-copy > p {
    font-size: 10px;
  }
  .follow-visit {
    font-size: 10px;
  }
  .settings-card {
    padding: 23px 20px;
  }
  .identity-card .avatar {
    width: 64px;
    height: 64px;
  }
  .count-label {
    font-size: 10px;
  }
}
</style>
