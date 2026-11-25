<template>
  <div class="workspace">
    <aside class="workspace-sidebar">
      <button type="button" class="workspace-brand" @click="store.activeTab = 'monitoring'">
        <span class="workspace-brand-mark" aria-hidden="true"></span>
        <span>目标跟踪</span>
      </button>

      <nav class="workspace-nav" aria-label="工作台导航">
        <button
          v-for="item in navigation"
          :key="item.id"
          type="button"
          class="workspace-nav-item"
          :aria-current="store.activeTab === item.id ? 'page' : undefined"
          @click="store.activeTab = item.id"
        >
          <span class="workspace-nav-icon" aria-hidden="true">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <p class="workspace-sidebar-note">YOLO / ByteTrack</p>
    </aside>

    <main class="workspace-main" id="main-content">
      <div v-if="store.activeTab !== 'monitoring'" class="workspace-heading">
        <h1>{{ activePage.label }}</h1>
        <p>{{ activePage.description }}</p>
      </div>

      <LiveMonitoringView v-if="store.activeTab === 'monitoring'" />
      <TrackRetrievalView v-else-if="store.activeTab === 'tracks'" />
      <TaskViolationView v-else-if="store.activeTab === 'tasks'" key="tasks" view="tasks" />
      <TaskViolationView v-else-if="store.activeTab === 'violations'" key="violations" view="violations" />
      <AblationMetricsView v-else-if="store.activeTab === 'ablation'" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue';
import AblationMetricsView from './components/AblationMetricsView.vue';
import LiveMonitoringView from './components/LiveMonitoringView.vue';
import TaskViolationView from './components/TaskViolationView.vue';
import TrackRetrievalView from './components/TrackRetrievalView.vue';
import { useMotStore } from './stores/motStore';
import type { ActiveTab } from './types';

const store = useMotStore();
const navigation: { id: ActiveTab; label: string; icon: string; description?: string }[] = [
  { id: 'monitoring', label: '实时监控', icon: '▣' },
  { id: 'tracks', label: '历史轨迹', icon: '⌁', description: '按任务、目标编号或类别查询轨迹，查看目标经过的位置。' },
  { id: 'violations', label: '异常事件', icon: '◇', description: '查看区域规则检测到的异常，并标记已处理的事件。' },
  { id: 'tasks', label: '任务管理', icon: '☷', description: '管理已保存的视频源、模型和任务。' },
  { id: 'ablation', label: '算法评测', icon: '▥', description: '对比不同跟踪配置的准确度、速度和耗时。' },
];
const activePage = computed(() => navigation.find((item) => item.id === store.activeTab) ?? navigation[0]);

watch(
  () => store.activeTab,
  (tab) => {
    window.scrollTo(0, 0);
    if (tab === 'violations') void store.fetchViolations();
    if (tab === 'ablation') {
      void store.fetchAblation();
      void store.fetchLockedBenchmarks();
    }
  },
);

onMounted(async () => {
  await Promise.all([store.fetchTasks(), store.fetchLocalAssets()]);
  await store.fetchViolations();
});
</script>
