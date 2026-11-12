<script setup lang="ts">
import { SlidersHorizontal } from "lucide-vue-next";
import { useDiscovery } from "../stores/discovery";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
withDefaults(defineProps<{ showSort?: boolean }>(), { showSort: true });
const filters = useDiscovery();
const route = useRoute(),
  router = useRouter();
const followOnly = computed(() => route.query.follow === "1");
function choose(mode: "open" | "all" | "follow") {
  filters.status = mode === "open" ? "open" : "";
  const query = { ...route.query };
  if (mode === "follow") query.follow = "1";
  else delete query.follow;
  router.replace({ path: route.path, query });
}
</script>
<template>
  <div class="discovery-filters">
    <div class="filter-chips" role="group" aria-label="营业状态筛选">
      <button
        :class="{ active: filters.status === 'open' && !followOnly }"
        :aria-pressed="filters.status === 'open' && !followOnly"
        @click="choose('open')"
      >
        <span class="open-dot"></span>正在出摊
      </button>
      <button
        :class="{ active: !filters.status && !followOnly }"
        :aria-pressed="!filters.status && !followOnly"
        @click="choose('all')"
      >
        全部摊位
      </button>
      <button
        :class="{ active: followOnly }"
        :aria-pressed="followOnly"
        @click="choose('follow')"
      >
        我的关注
      </button>
    </div>
    <label v-if="showSort" class="sort-control"
      ><SlidersHorizontal :size="13" /><select
        v-model="filters.sort"
        aria-label="摊位排序"
      >
        <option value="freshness">最近确认</option>
        <option value="rating">评分优先</option>
        <option v-if="filters.position" value="distance">距离最近</option>
      </select></label
    >
  </div>
</template>
<style scoped>
.discovery-filters {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 21px;
}
.filter-chips {
  min-width: 0;
  gap: 6px;
}
.filter-chips button {
  display: flex;
  align-items: center;
  gap: 5px;
  min-height: 44px;
  font-size: 12px;
  padding: 8px 12px;
}
.open-dot {
  width: 5px;
  height: 5px;
  background: #56865f;
  border-radius: 50%;
}
.sort-control {
  display: flex;
  align-items: center;
  gap: 5px;
  color: #806d57;
  white-space: nowrap;
}
.sort-control select {
  border: 0;
  background: none;
  font-size: 12px;
  color: #796651;
  min-height: 44px;
  cursor: pointer;
}
@media (max-width: 767px) {
  .discovery-filters {
    gap: 7px;
    margin-bottom: 16px;
  }
  .filter-chips {
    gap: 5px;
  }
  .filter-chips button {
    font-size: 12px;
    padding: 8px;
    gap: 4px;
  }
  .sort-control svg {
    display: none;
  }
  .sort-control select {
    font-size: 12px;
    max-width: 73px;
  }
}
</style>
