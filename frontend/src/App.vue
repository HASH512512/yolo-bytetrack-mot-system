<template>
  <div class="min-h-screen flex flex-col bg-slate-950 text-slate-100">
    <!-- Top Header -->
    <header class="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-40 px-4 py-3">
      <div class="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
        <!-- Logo & Title -->
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-lg bg-gradient-to-tr from-indigo-600 to-cyan-400 flex items-center justify-center shadow-lg shadow-indigo-500/20 text-lg">
            <span>&#x1F3AF;</span>
          </div>
          <div>
            <h1 class="text-sm md:text-base font-bold text-slate-100 flex items-center gap-2">
              <span>基于YOLO与ByteTrack的可扩展多目标跟踪系统</span>
              <span class="badge badge-indigo text-[10px] hidden sm:inline-flex bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                <span>v1.1.0</span>
              </span>
            </h1>
            <p class="text-[11px] text-slate-400 hidden md:block">
              <span>FastAPI + SQLite WAL + Vue 3 + ByteTrack (Class-Aware / NSA / DIoU+VDC+ORU / Lazy HSV)</span>
            </p>
          </div>
        </div>

        <!-- Navigation Tabs -->
        <nav class="flex flex-wrap items-center gap-1 bg-slate-800/80 p-1 rounded-xl border border-slate-700/60 text-xs">
          <button
            @click="store.activeTab = 'monitoring'"
            class="px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5"
            :class="
              store.activeTab === 'monitoring'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            "
          >
            <span>&#x1F3A5;</span>
            <span>实时监控与画布</span>
          </button>
          <button
            @click="store.activeTab = 'tracks'"
            class="px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5"
            :class="
              store.activeTab === 'tracks'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            "
          >
            <span>&#x1F50D;</span>
            <span>轨迹检索与回放</span>
          </button>
          <button
            @click="store.activeTab = 'violations'"
            class="px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 relative"
            :class="
              store.activeTab === 'violations'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            "
          >
            <span>&#x26A0;&#xFE0F;</span>
            <span>违章预警与任务</span>
            <span
              v-if="store.unacknowledgedViolations.length > 0"
              class="w-2 h-2 rounded-full bg-rose-500 animate-pulse"
            ></span>
          </button>
          <button
            @click="store.activeTab = 'ablation'"
            class="px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5"
            :class="
              store.activeTab === 'ablation'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            "
          >
            <span>&#x1F4CA;</span>
            <span>算法消融评测</span>
          </button>
        </nav>

        <!-- Right Quick Controls -->
        <div class="flex items-center gap-2">
          <!-- Task Selector Dropdown -->
          <select
            v-if="store.tasks.length > 0"
            :value="store.activeTaskId || ''"
            @change="handleSelectTask"
            class="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 max-w-[200px]"
          >
            <option v-for="t in store.tasks" :key="t.id" :value="t.id">
              {{ t.name }} ({{ t.status }})
            </option>
          </select>

          <!-- WS Status -->
          <div
            class="hidden lg:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs"
            :class="store.wsConnected ? 'bg-emerald-950/40 text-emerald-300 border border-emerald-800/40' : 'bg-slate-800 text-slate-400'"
          >
            <span
              class="w-2 h-2 rounded-full"
              :class="store.wsConnected ? 'bg-emerald-400 animate-ping' : 'bg-slate-500'"
            ></span>
            <span>{{ store.wsConnected ? 'WS 已连接' : 'WS 未连接' }}</span>
          </div>
        </div>
      </div>
    </header>

    <!-- Main Content Area -->
    <main class="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6">
      <transition name="fade" mode="out-in">
        <LiveMonitoringView v-if="store.activeTab === 'monitoring'" />
        <TrackRetrievalView v-else-if="store.activeTab === 'tracks'" />
        <TaskViolationView v-else-if="store.activeTab === 'violations'" />
        <AblationMetricsView v-else-if="store.activeTab === 'ablation'" />
      </transition>
    </main>

    <!-- Footer -->
    <footer class="border-t border-slate-800/80 bg-slate-900/60 py-3 text-center text-xs text-slate-500">
      <div class="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-2">
        <span>泉州信息工程学院 毕业设计工程实践原型系统</span>
        <span>课题: 基于YOLO与ByteTrack的可扩展多目标跟踪系统设计与实现</span>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue';
import AblationMetricsView from './components/AblationMetricsView.vue';
import LiveMonitoringView from './components/LiveMonitoringView.vue';
import TaskViolationView from './components/TaskViolationView.vue';
import TrackRetrievalView from './components/TrackRetrievalView.vue';
import { useMotStore } from './stores/motStore';

const store = useMotStore();

function handleSelectTask(e: Event) {
  const target = e.target as HTMLSelectElement;
  const id = parseInt(target.value, 10);
  if (!isNaN(id)) {
    store.setActiveTask(id);
    store.fetchViolations();
  }
}

onMounted(async () => {
  await store.fetchTasks();
  await Promise.all([
    store.fetchViolations(),
    store.fetchAblation(),
    store.fetchMetrics(),
    store.fetchLocalAssets(),
    store.fetchLockedBenchmarks(),
  ]);
});
</script>

<style>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
