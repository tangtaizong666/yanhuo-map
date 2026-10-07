<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { Moon, MapPin, ArrowUpRight, RefreshCw } from "lucide-vue-next";
import { api, statusText } from "../lib/api";
import type { StallSummary as Stall, DiscoveryPage } from "../lib/types";
import { useSession } from "../stores/session";
const props = defineProps<{
  area: number;
  category: string;
  query: string;
  status: string;
  following: boolean;
  syncedAt?: Date | null;
}>();
const emit = defineEmits<{ clear: []; allAreas: [] }>();
const session = useSession(),
  rows = ref<Stall[]>([]),
  loading = ref(true),
  error = ref("");
let controller: AbortController | undefined,
  revision = 0;
const relevant = computed(() =>
  props.following ? rows.value.filter((s) => s.is_followed) : rows.value,
);
const hasFilters = computed(() => !!props.category || !!props.query);
const title = computed(() => {
  if (loading.value) return "正在看看附近的出摊情况";
  if (error.value) return "暂时无法确认附近的出摊情况";
  if (!rows.value.length)
    return props.area ? "这个区域还没有收录摊位" : "这里还没有收录摊位";
  if (props.following && !relevant.value.length)
    return props.area ? "当前区域还没有关注的摊位" : "还没有关注的摊位";
  if (hasFilters.value || relevant.value.some((s) => s.status === "open"))
    return "暂时没有符合筛选条件的摊位";
  if (relevant.value.some((s) => s.status === "stale"))
    return "出摊位置正在等待商家确认";
  if (relevant.value.some((s) => s.status === "paused"))
    return "附近的小摊正在暂歇";
  return "小摊们暂时收摊了";
});
const description = computed(() =>
  error.value
    ? "当前没有读到完整信息，请重新加载；不能据此判断商家已收摊。"
    : !rows.value.length
      ? "可以换个校园区域看看。这里还没有资料，不代表现场一定没有摊位。"
      : hasFilters.value
        ? "试试其他品类或清空关键词，已收摊的小摊也可以先了解和关注。"
        : props.following && !relevant.value.length
          ? "先看看全部摊位，关注喜欢的小摊，下次就能直接查看它是否营业。"
          : "以下是已收录的摊位。计划时段仅供参考，出发前请确认当天出摊状态。",
);
async function load() {
  controller?.abort();
  controller = new AbortController();
  const current = ++revision;
  loading.value = true;
  error.value = "";
  rows.value = [];
  try {
    const data = await api<DiscoveryPage<Stall>>(
      `/stalls?sort=freshness${props.area ? `&area=${props.area}` : ""}${props.following ? "&follow=1" : ""}`,
      { signal: controller.signal },
    );
    if (current === revision) rows.value = data.results;
  } catch (e) {
    if (current === revision) error.value = (e as Error).message;
  } finally {
    if (current === revision) loading.value = false;
  }
}
watch(
  () => [
    props.area,
    props.category,
    props.query,
    props.status,
    props.following,
    props.syncedAt,
    session.user?.id,
  ],
  load,
  { immediate: true },
);
onUnmounted(() => {
  revision++;
  controller?.abort();
});
</script>
<template>
  <section class="card discovery-empty" aria-label="附近出摊提示">
    <div class="empty-intro">
      <span class="empty-symbol"><Moon :size="27" /></span>
      <div>
        <h3>{{ title }}</h3>
        <p>{{ loading ? "请稍等…" : description }}</p>
      </div>
    </div>
    <div v-if="!loading && !error && relevant.length" class="resting-stalls">
      <RouterLink
        v-for="stall in relevant.slice(0, 3)"
        :key="stall.id"
        :to="`/stalls/${stall.id}`"
      >
        <div>
          <strong>{{ stall.name }}</strong
          ><span>{{ statusText(stall.status) }}</span>
        </div>
        <p><MapPin :size="13" />{{ stall.address || stall.area_name }}</p>
        <p v-if="stall.usual_hours">通常 {{ stall.usual_hours }} · 商家计划</p>
        <ArrowUpRight class="resting-arrow" :size="18" aria-hidden="true" />
      </RouterLink>
    </div>
    <div class="empty-actions">
      <button v-if="error" class="btn btn-secondary" @click="load">
        <RefreshCw :size="16" />重新加载出摊信息</button
      ><button v-else class="btn btn-secondary" @click="emit('clear')">
        看看全部摊位</button
      ><button v-if="area" class="btn btn-ghost" @click="emit('allAreas')">
        换到全校周边
      </button>
    </div>
  </section>
</template>
<style scoped>
.discovery-empty {
  padding: 26px;
}
.empty-intro {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}
.empty-symbol {
  background: #f7edda;
  padding: 14px;
  border-radius: 18px;
  color: #ac772a;
}
.empty-intro h3 {
  font-size: 20px;
  margin: 3px 0 10px;
}
.empty-intro p,
.resting-stalls p {
  font-size: 13px;
  line-height: 1.8;
  color: #776651;
  margin: 0;
}
.empty-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 22px;
}
.resting-stalls {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 22px;
}
.resting-stalls a {
  position: relative;
  padding: 16px 38px 16px 16px;
  border: 1px solid #ede1d0;
  border-radius: 14px;
  background: #fffaf1;
  min-width: 0;
}
.resting-stalls a > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}
.resting-stalls strong {
  font-size: 15px;
}
.resting-stalls span {
  font-size: 11px;
  white-space: nowrap;
  color: #846849;
}
.resting-stalls p {
  margin-top: 8px;
  overflow-wrap: anywhere;
}
.resting-arrow {
  position: absolute;
  right: 12px;
  top: 18px;
  color: #a9531d;
}
.resting-stalls svg {
  vertical-align: middle;
}
@media (max-width: 700px) {
  .discovery-empty {
    padding: 18px;
  }
  .empty-intro {
    gap: 12px;
  }
  .empty-symbol {
    padding: 7px;
  }
  .empty-symbol svg {
    width: 21px;
    height: 21px;
  }
  .empty-intro h3 {
    font-size: 18px;
  }
  .resting-stalls {
    grid-template-columns: 1fr;
    gap: 0;
    margin-top: 14px;
  }
  .resting-stalls a {
    padding: 14px 26px 14px 0;
    border: 0;
    border-top: 1px solid var(--line);
    border-radius: 0;
    background: none;
  }
  .resting-stalls strong {
    font-size: 16px;
  }
  .resting-stalls p {
    margin-top: 4px;
    font-size: 14px;
  }
  .resting-arrow {
    right: 0;
    top: 16px;
  }
  .empty-actions {
    margin-top: 12px;
  }
  .empty-actions .btn {
    flex: 1;
    white-space: nowrap;
  }
}
</style>
