<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { ArrowUpRight, RefreshCw, Search, ShieldAlert } from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const props = defineProps<{
  refreshTick?: number;
}>();

const session = useSession();
const subTab = ref<"users" | "audit">("users");

const usersLoading = ref(true);
const usersError = ref("");
const roleFilter = ref("all");
const userQuery = ref("");
const userCounts = ref<Record<string, number>>({});
const users = ref<any[]>([]);
const busyUserId = ref<number | null>(null);

const auditLoading = ref(false);
const auditError = ref("");
const auditAction = ref("");
const auditQuery = ref("");
const auditActions = ref<string[]>([]);
const logs = ref<any[]>([]);

async function loadUsers() {
  if (!session.hasOpsPerm("view_users")) {
    usersLoading.value = false;
    return;
  }
  usersLoading.value = true;
  usersError.value = "";
  try {
    const params = new URLSearchParams({ role: roleFilter.value });
    if (userQuery.value.trim()) params.set("q", userQuery.value.trim());
    const res = await api<{ counts: Record<string, number>; results: any[] }>(
      `/ops/users?${params.toString()}`,
    );
    userCounts.value = res.counts || {};
    users.value = res.results || [];
  } catch (e) {
    usersError.value = (e as Error).message;
  } finally {
    usersLoading.value = false;
  }
}

async function loadAudit() {
  if (!session.hasOpsPerm("view_audit")) {
    auditLoading.value = false;
    return;
  }
  auditLoading.value = true;
  auditError.value = "";
  try {
    const params = new URLSearchParams();
    if (auditAction.value) params.set("action", auditAction.value);
    if (auditQuery.value.trim()) params.set("q", auditQuery.value.trim());
    const res = await api<{ actions: string[]; results: any[] }>(
      `/ops/audit-logs?${params.toString()}`,
    );
    auditActions.value = res.actions || [];
    logs.value = res.results || [];
  } catch (e) {
    auditError.value = (e as Error).message;
  } finally {
    auditLoading.value = false;
  }
}

watch(roleFilter, () => {
  void loadUsers();
});

watch(auditAction, () => {
  void loadAudit();
});

watch(subTab, (next) => {
  if (next === "audit") void loadAudit();
  else void loadUsers();
});

watch(
  () => props.refreshTick,
  () => {
    if (subTab.value === "audit") void loadAudit();
    else void loadUsers();
  },
);

async function toggleUserActive(u: any) {
  if (busyUserId.value !== null) return;
  busyUserId.value = u.id;
  try {
    await api(`/ops/users/${u.id}`, {
      method: "PATCH",
      body: { is_active: !u.is_active },
    });
    notify(
      u.is_active ? `已停用账号 ${u.username}` : `已恢复启用账号 ${u.username}`,
      "success",
    );
    await loadUsers();
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    busyUserId.value = null;
  }
}

onMounted(() => {
  if (!session.hasOpsPerm("view_users") && session.hasOpsPerm("view_audit")) {
    subTab.value = "audit";
    void loadAudit();
  } else {
    void loadUsers();
  }
});
</script>

<template>
  <div class="stack" style="gap: 18px">
    <section class="o-card">
      <div class="o-card-header">
        <div>
          <h2>平台账号治理与管理操作审计日志</h2>
          <p>
            含进行中订单或未结清资金记录的账号受安全保护不可停用；关键治理与交易操作均自动记入审计日志以供追溯。
          </p>
        </div>
        <div class="o-actions-row">
          <a
            v-if="session.user?.is_superuser"
            href="/admin/"
            target="_blank"
            rel="noopener"
            class="o-btn"
          >
            底层维护后台 (/admin/) <ArrowUpRight :size="15" />
          </a>
        </div>
      </div>

      <div class="o-tabs" role="tablist" aria-label="账号与审计切换">
        <button
          v-if="session.hasOpsPerm('view_users')"
          type="button"
          class="o-tab"
          :class="{ active: subTab === 'users' }"
          @click="subTab = 'users'"
        >
          平台账号管理 ({{ userCounts.all ?? 0 }})
        </button>
        <button
          v-if="session.hasOpsPerm('view_audit')"
          type="button"
          class="o-tab"
          :class="{ active: subTab === 'audit' }"
          @click="subTab = 'audit'"
        >
          操作审计日志
        </button>
      </div>
    </section>

    <template v-if="subTab === 'users' && session.hasOpsPerm('view_users')">
      <section class="o-card">
        <div class="o-toolbar">
          <div class="o-tabs">
            <button
              v-for="r in [
                { id: 'all', label: '全部' },
                { id: 'staff', label: '管理员' },
                { id: 'merchant', label: '商家' },
                { id: 'student', label: '学生/顾客' },
              ]"
              :key="r.id"
              type="button"
              class="o-tab"
              :class="{ active: roleFilter === r.id }"
              @click="roleFilter = r.id"
            >
              {{ r.label }} ({{ userCounts[r.id] ?? 0 }})
            </button>
          </div>

          <form class="o-filters" @submit.prevent="loadUsers">
            <input
              v-model="userQuery"
              class="o-input"
              placeholder="搜索用户名、昵称或商户名"
              aria-label="搜索平台账号"
            />
            <button type="submit" class="o-btn">
              <Search :size="15" /> 搜索
            </button>
            <button
              type="button"
              class="o-btn"
              aria-label="刷新账号列表"
              :disabled="usersLoading"
              @click="loadUsers"
            >
              <RefreshCw :size="15" />
            </button>
          </form>
        </div>
      </section>

      <p v-if="usersError" class="o-alert" role="alert">{{ usersError }}</p>
      <div v-else-if="usersLoading" class="o-card o-empty">
        正在读取账号列表…
      </div>

      <div v-else class="o-list">
        <article v-for="u in users" :key="u.id" class="o-item">
          <div class="o-item-top">
            <div class="o-item-title">
              <h3>{{ u.display_name }}（{{ u.username }}）</h3>
              <span v-if="u.is_superuser" class="o-badge pine"
                >超级管理员</span
              >
              <span v-else-if="u.is_staff" class="o-badge pine">运营人员</span>
              <span v-if="u.is_merchant" class="o-badge ok">认证商户</span>
              <span
                v-if="!u.is_staff && !u.is_merchant"
                class="o-badge"
                >学生/普通用户</span
              >
              <span v-if="!u.is_active" class="o-badge danger">已停用</span>
            </div>

            <button
              v-if="
                session.hasOpsPerm('manage_users') &&
                !u.is_superuser &&
                (!u.is_staff || session.user?.is_superuser) &&
                u.id !== session.user?.id
              "
              type="button"
              class="o-btn"
              :class="u.is_active ? 'o-btn-danger' : 'o-btn-pine'"
              :disabled="busyUserId !== null"
              @click="toggleUserActive(u)"
            >
              <ShieldAlert v-if="u.is_active" :size="15" />
              {{ u.is_active ? "停用账号" : "恢复启用" }}
            </button>
          </div>

          <div class="o-meta-row">
            <span v-if="u.business_name"
              >关联经营主体：{{ u.business_name }}</span
            >
            <span v-if="u.application_status"
              >入驻申请状态：{{ u.application_status }}</span
            >
            <span v-if="u.date_joined"
              >注册时间：{{
                new Date(u.date_joined).toLocaleDateString("zh-CN")
              }}</span
            >
          </div>
        </article>
      </div>
    </template>

    <template v-else-if="subTab === 'audit' && session.hasOpsPerm('view_audit')">
      <section class="o-card">
        <div class="o-toolbar">
          <div class="o-filters">
            <select
              v-model="auditAction"
              class="o-select"
              aria-label="按审计动作筛选"
            >
              <option value="">全部审计动作</option>
              <option v-for="act in auditActions" :key="act" :value="act">
                {{ act }}
              </option>
            </select>
            <input
              v-model="auditQuery"
              class="o-input"
              placeholder="搜索操作人或目标对象"
              aria-label="搜索审计日志"
              @keyup.enter="loadAudit"
            />
            <button type="button" class="o-btn" @click="loadAudit">
              <Search :size="15" /> 筛选
            </button>
            <button
              type="button"
              class="o-btn"
              aria-label="刷新审计日志"
              :disabled="auditLoading"
              @click="loadAudit"
            >
              <RefreshCw :size="15" /> 刷新
            </button>
          </div>
        </div>
      </section>

      <p v-if="auditError" class="o-alert" role="alert">{{ auditError }}</p>
      <div v-else-if="auditLoading" class="o-card o-empty">
        正在读取操作审计日志…
      </div>
      <div v-else-if="!logs.length" class="o-card o-empty">
        暂无匹配的审计记录。
      </div>

      <div v-else class="o-list">
        <article v-for="log in logs" :key="log.id" class="o-item">
          <div class="o-item-top">
            <div class="o-item-title">
              <span class="o-badge pine">{{ log.action }}</span>
              <strong>目标：{{ log.target }}</strong>
            </div>
            <span class="muted" style="font-size: 12px">
              {{ new Date(log.created_at).toLocaleString("zh-CN") }} · 操作人：{{
                log.actor_username
              }}
            </span>
          </div>
          <div
            v-if="log.details && Object.keys(log.details).length"
            class="o-code-line o-callout"
          >
            {{ JSON.stringify(log.details) }}
          </div>
        </article>
      </div>
    </template>
  </div>
</template>
