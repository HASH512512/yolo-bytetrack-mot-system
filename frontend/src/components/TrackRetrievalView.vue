<template>
  <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
    <!-- Left Column: Filter Controls + Track Summary List (4 cols) -->
    <div class="lg:col-span-4 space-y-4">
      <div class="card space-y-3">
        <div class="flex items-center justify-between gap-2 border-b border-slate-800 pb-2.5">
          <div>
            <h2 class="text-base font-bold text-slate-100 flex items-center gap-1.5">
              <span>&#x1F50D;</span>
              <span>历史轨迹检索</span>
            </h2>
            <p class="text-[11px] text-slate-400 mt-0.5">
              <span>SQLite WAL 复合索引按任务 / ID / 类别过滤</span>
            </p>
          </div>
          <span class="badge badge-blue text-[11px]">
            <span>{{ displaySummaries.length }} 条轨迹</span>
          </span>
        </div>

        <form @submit.prevent="handleQuery" class="space-y-2.5 text-xs">
          <div>
            <label class="block text-slate-400 mb-1">监控任务 (Task)</label>
            <select
              v-model.number="selectedTaskId"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              <option :value="0" disabled>-- 请选择任务 --</option>
              <option v-for="t in store.tasks" :key="t.id" :value="t.id">
                #{{ t.id }} {{ t.name }} ({{ t.status }})
              </option>
            </select>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-slate-400 mb-1">Track ID (可选)</label>
              <input
                v-model="trackIdInput"
                type="number"
                min="1"
                placeholder="全部 ID (All)"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label class="block text-slate-400 mb-1">目标类别 (Class)</label>
              <select
                v-model="classFilter"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                <option value="all">全部类别 (all)</option>
                <option value="car">汽车 (car)</option>
                <option value="person">行人 (person)</option>
                <option value="motorcycle">摩托车 (motorcycle)</option>
                <option value="bicycle">自行车 (bicycle)</option>
                <option value="vehicle">车辆 (vehicle)</option>
                <option value="pedestrian">行人 (pedestrian)</option>
              </select>
            </div>
          </div>

          <div class="flex items-center gap-2 pt-1">
            <button
              type="submit"
              :disabled="!selectedTaskId || store.tracksLoading"
              class="btn btn-primary text-xs py-2 px-3 flex-1"
            >
              <span>{{ store.tracksLoading ? '检索中...' : '\u{1F50D} 查询轨迹' }}</span>
            </button>
            <button
              type="button"
              @click="handleResetFilters"
              :disabled="!selectedTaskId || store.tracksLoading"
              class="btn btn-secondary text-xs py-2 px-3"
            >
              <span>重置</span>
            </button>
          </div>
        </form>
      </div>

      <!-- Track Summary List -->
      <div class="card space-y-2.5">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <span class="text-sm font-bold text-slate-100">轨迹摘要列表</span>
          <span class="text-[11px] text-slate-400">点击卡片回放单目标</span>
        </div>

        <div class="space-y-2 max-h-[540px] overflow-y-auto pr-1">
          <div
            v-for="summary in displaySummaries"
            :key="summary.track_id"
            @click="selectTrackSummary(summary)"
            class="p-3 rounded-lg border transition-all cursor-pointer space-y-1.5"
            :class="
              selectedTrackId === summary.track_id
                ? 'bg-indigo-950/45 border-indigo-500 shadow-md shadow-indigo-500/10'
                : 'bg-slate-800/40 border-slate-700/60 hover:bg-slate-800/80'
            "
          >
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <span class="font-mono font-bold text-sm text-cyan-300">Track #{{ summary.track_id }}</span>
                <span class="badge badge-blue text-[10px]">{{ summary.class_name }}</span>
              </div>
              <span v-if="summary.violation_count > 0" class="badge badge-red text-[10px]">
                <span>&#x1F6A8; {{ summary.violation_count }} 次违章</span>
              </span>
              <span v-else class="badge badge-gray text-[10px]">
                <span>0 违章</span>
              </span>
            </div>

            <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
              <span>帧: F{{ summary.first_frame }} &rarr; F{{ summary.last_frame }}</span>
              <span class="text-right">点数: {{ summary.point_count }} 点</span>
              <span>均速: {{ summary.avg_speed.toFixed(1) }} km/h</span>
              <span class="text-right text-amber-300">峰值: {{ summary.max_speed.toFixed(1) }} km/h</span>
            </div>

            <div class="text-[10px] text-slate-500 flex justify-between pt-1 border-t border-slate-700/40">
              <span>起: {{ formatTimestamp(summary.start_ts) }}</span>
              <span>止: {{ formatTimestamp(summary.end_ts) }}</span>
            </div>
          </div>

          <div
            v-if="displaySummaries.length === 0"
            class="text-center py-10 text-slate-500 text-xs space-y-1.5"
          >
            <div class="text-2xl">&#x1F4ED;</div>
            <div>暂无持久化轨迹记录</div>
            <div class="text-[11px] text-slate-600">
              <span>请先在「实时监控」开启任务运行数秒后再检索</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Right Column: Aligned Replay Stage + Points Table (8 cols) -->
    <div class="lg:col-span-8 space-y-4">
      <div class="card space-y-3 bg-slate-950/90">
        <!-- Replay Header & Telemetry -->
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-sm font-bold text-slate-100">2D 轨迹时空回放舞台</span>
            <span v-if="isSingleTrackMode && activeTrackDisplayId !== null" class="badge badge-green text-[11px]">
              <span>正在回放 Track #{{ activeTrackDisplayId }}</span>
            </span>
            <span v-else-if="replayPoints.length > 0" class="badge badge-amber text-[11px]">
              <span>多目标预览 (点击左侧卡片单目标连线回放)</span>
            </span>
            <span class="badge badge-gray text-[10px] font-mono">
              <span>{{ frameDims.width }} &times; {{ frameDims.height }}</span>
            </span>
            <label class="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer ml-1">
              <input type="checkbox" v-model="showReplayZones" class="accent-indigo-500 rounded" />
              <span>叠加ROI区</span>
            </label>
          </div>

          <div v-if="currentPoint" class="flex flex-wrap items-center gap-3 text-xs font-mono text-slate-300">
            <span>F{{ currentPoint.frame_id }}</span>
            <span class="text-cyan-300">({{ currentPoint.cx.toFixed(0) }}, {{ currentPoint.cy.toFixed(0) }})</span>
            <span class="text-amber-300">{{ currentPoint.speed.toFixed(1) }} km/h</span>
            <span class="text-emerald-300">{{ (currentPoint.confidence * 100).toFixed(0) }}%</span>
          </div>
        </div>

        <!-- Empty / Missing Frame Status Banner -->
        <div
          v-if="replayPoints.length === 0"
          class="px-3 py-2 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-400 flex items-center gap-2"
        >
          <span>&#x2139;&#xFE0F;</span>
          <span>当前暂无回放点位数据，请在左侧选择任务并点击「查询轨迹」或点击轨迹摘要卡片。</span>
        </div>
        <div
          v-else-if="frameStatus === 'missing'"
          class="px-3 py-2 rounded-lg bg-amber-950/40 border border-amber-800/60 text-xs text-amber-200 flex items-center gap-2"
        >
          <span>&#x26A0;&#xFE0F;</span>
          <span>当前帧图像未命中回放缓存或已不可寻址，已自动切换至纯矢量轨迹网格回放模式。</span>
        </div>

        <!-- Exact Aspect-Ratio Stage Container (Zero Letterbox Offset) -->
        <div class="w-full rounded-lg border border-slate-800 bg-slate-950 p-2 flex items-center justify-center min-h-[260px]">
          <div class="relative overflow-hidden rounded" :style="stageBoxStyle">
            <img
              v-if="frameBlobUrl && frameStatus === 'loaded'"
              :src="frameBlobUrl"
              alt="Historical Replay Video Frame"
              class="w-full h-full object-contain block select-none"
              @load="onReplayImgLoad"
              @error="onReplayImgError"
            />
            <canvas
              ref="replayCanvasRef"
              :width="frameDims.width"
              :height="frameDims.height"
              class="absolute inset-0 w-full h-full pointer-events-none block"
            ></canvas>
          </div>
        </div>

        <!-- Replay Playback Controls (Play/Pause/Prev/Next/FPS + Scrubber) -->
        <div class="flex flex-wrap items-center gap-2.5 pt-1">
          <div class="flex flex-wrap items-center gap-1.5">
            <button
              @click="stepPrev"
              :disabled="replayPoints.length === 0 || replayIndex <= 0"
              class="btn btn-secondary text-xs py-1.5 px-2.5"
              title="上一帧 (Prev)"
            >
              <span>&#x23EE; 上一帧</span>
            </button>
            <button
              @click="togglePlay"
              :disabled="replayPoints.length <= 1"
              class="btn btn-primary text-xs py-1.5 px-3"
            >
              <span>{{ isPlaying ? '\u23F8 暂停' : '\u25B6 播放' }}</span>
            </button>
            <button
              @click="stepNext"
              :disabled="replayPoints.length === 0 || replayIndex >= replayPoints.length - 1"
              class="btn btn-secondary text-xs py-1.5 px-2.5"
              title="下一帧 (Next)"
            >
              <span>下一帧 &#x23ED;</span>
            </button>
            <button
              @click="resetReplay"
              :disabled="replayPoints.length === 0"
              class="btn btn-secondary text-xs py-1.5 px-2.5"
              title="重置到起点 (Reset)"
            >
              <span>&#x21BA; 重置</span>
            </button>
            <select
              v-model.number="replayFps"
              class="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              title="回放速率 (FPS)"
            >
              <option :value="5">5 FPS</option>
              <option :value="10">10 FPS</option>
              <option :value="15">15 FPS</option>
              <option :value="25">25 FPS</option>
              <option :value="30">30 FPS</option>
            </select>
          </div>

          <div class="flex-1 flex items-center gap-3 min-w-[180px]">
            <input
              type="range"
              min="0"
              :max="Math.max(0, replayPoints.length - 1)"
              v-model.number="replayIndex"
              @input="pauseReplay"
              :disabled="replayPoints.length === 0"
              class="flex-1 accent-indigo-500 cursor-pointer"
            />
            <span class="text-xs font-mono text-slate-400 whitespace-nowrap">
              {{ replayPoints.length > 0 ? replayIndex + 1 : 0 }} / {{ replayPoints.length }}
            </span>
          </div>
        </div>
      </div>

      <!-- Tabular Trajectory Points List -->
      <div class="card space-y-2">
        <div class="flex items-center justify-between">
          <span class="text-sm font-bold text-slate-100">轨迹点位明细表</span>
          <span class="text-[11px] text-slate-400">共 {{ replayPoints.length }} 点，点击行跳转对应帧</span>
        </div>

        <div class="max-h-[260px] overflow-y-auto overflow-x-auto border border-slate-800 rounded-lg">
          <table class="w-full text-xs text-left">
            <thead class="text-slate-400 bg-slate-800/90 sticky top-0 border-b border-slate-700">
              <tr>
                <th class="py-2 px-3">#</th>
                <th class="py-2 px-3">Frame ID</th>
                <th class="py-2 px-3">时间戳</th>
                <th class="py-2 px-3">中心坐标 (cx, cy)</th>
                <th class="py-2 px-3">尺寸 (w &times; h)</th>
                <th class="py-2 px-3">类别</th>
                <th class="py-2 px-3">置信度</th>
                <th class="py-2 px-3">速度 (km/h)</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-800/80">
              <tr
                v-for="(pt, idx) in replayPoints"
                :key="`${pt.frame_id}-${pt.timestamp}-${idx}`"
                @click="jumpToPoint(idx)"
                class="cursor-pointer transition-colors"
                :class="
                  idx === replayIndex
                    ? 'bg-indigo-600/25 text-white font-medium'
                    : 'hover:bg-slate-800/60 text-slate-300'
                "
              >
                <td class="py-1.5 px-3 font-mono text-slate-500">{{ idx + 1 }}</td>
                <td class="py-1.5 px-3 font-mono text-cyan-300">{{ pt.frame_id }}</td>
                <td class="py-1.5 px-3 font-mono text-slate-400">{{ formatTimestamp(pt.timestamp) }}</td>
                <td class="py-1.5 px-3 font-mono">({{ pt.cx.toFixed(1) }}, {{ pt.cy.toFixed(1) }})</td>
                <td class="py-1.5 px-3 font-mono">{{ pt.w.toFixed(0) }} &times; {{ pt.h.toFixed(0) }}</td>
                <td class="py-1.5 px-3">
                  <span class="badge badge-blue text-[10px]">{{ pt.class_name }}</span>
                </td>
                <td class="py-1.5 px-3 font-mono text-emerald-400">{{ (pt.confidence * 100).toFixed(0) }}%</td>
                <td class="py-1.5 px-3 font-mono text-amber-300">{{ pt.speed.toFixed(1) }}</td>
              </tr>
              <tr v-if="replayPoints.length === 0">
                <td colspan="8" class="py-8 text-center text-slate-500">
                  <span>当前筛选条件下无轨迹点数据</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useMotStore } from '../stores/motStore';
import type { TrackPoint, TrackSummary, Zone } from '../types';

const store = useMotStore();

const selectedTaskId = ref<number>(0);
const trackIdInput = ref<string>('');
const classFilter = ref<string>('all');
const selectedTrackId = ref<number | null>(null);
const cachedSummaries = ref<TrackSummary[]>([]);

const replayCanvasRef = ref<HTMLCanvasElement | null>(null);
const replayIndex = ref<number>(0);
const isPlaying = ref<boolean>(false);
const replayFps = ref<number>(10);
const showReplayZones = ref<boolean>(false);

const frameBlobUrl = ref<string | null>(null);
const frameStatus = ref<'idle' | 'loading' | 'loaded' | 'missing'>('idle');
const probedDims = ref<{ width: number; height: number } | null>(null);

let playTimer: number | null = null;
let frameAbortController: AbortController | null = null;

watch(
  () => [store.activeTaskId, store.tasks] as const,
  ([activeId, tasks]) => {
    if (!selectedTaskId.value && tasks.length > 0) {
      selectedTaskId.value = activeId || tasks[0].id;
    }
  },
  { immediate: true }
);

const displaySummaries = computed<TrackSummary[]>(() => {
  return cachedSummaries.value.length > 0 ? cachedSummaries.value : store.trackSummaries;
});

const replayPoints = computed<TrackPoint[]>(() => store.trackPoints);

const currentPoint = computed<TrackPoint | null>(() => {
  if (replayPoints.value.length === 0) return null;
  const idx = Math.min(Math.max(0, replayIndex.value), replayPoints.value.length - 1);
  return replayPoints.value[idx];
});

const activeTrackDisplayId = computed<number | null>(() => {
  if (selectedTrackId.value !== null) return selectedTrackId.value;
  const parsed = parseInt(trackIdInput.value, 10);
  if (!Number.isNaN(parsed) && parsed > 0) return parsed;
  if (store.trackSummaries.length === 1) return store.trackSummaries[0].track_id;
  return null;
});

const isSingleTrackMode = computed<boolean>(() => activeTrackDisplayId.value !== null);

const taskZones = computed<Zone[]>(() => {
  if (!selectedTaskId.value) return store.zones;
  return store.zonesByTask[selectedTaskId.value] || [];
});

// Exact frameWidth / frameHeight from X-Frame-Width/Height headers, active stream, or track max bbox fallback
const frameDims = computed(() => {
  if (probedDims.value && probedDims.value.width > 0 && probedDims.value.height > 0) {
    return probedDims.value;
  }
  const activeRunFrame = store.framesByTask[selectedTaskId.value];
  if (activeRunFrame?.frame_width && activeRunFrame?.frame_height) {
    return { width: activeRunFrame.frame_width, height: activeRunFrame.frame_height };
  }
  let maxW = 0;
  let maxH = 0;
  for (const pt of replayPoints.value) {
    const right = pt.cx + pt.w * 0.5;
    const bottom = pt.cy + pt.h * 0.5;
    if (right > maxW) maxW = right;
    if (bottom > maxH) maxH = bottom;
  }
  if (maxW > 0 && maxH > 0) {
    return {
      width: Math.max(640, Math.ceil(maxW + 40)),
      height: Math.max(360, Math.ceil(maxH + 40)),
    };
  }
  return { width: 1280, height: 720 };
});

// Constrain both max-width and max-height while preserving exact frameWidth / frameHeight aspect ratio
const stageBoxStyle = computed(() => {
  const w = Math.max(1, frameDims.value.width);
  const h = Math.max(1, frameDims.value.height);
  const ratio = w / h;
  return {
    aspectRatio: `${w} / ${h}`,
    width: `min(100%, calc(500px * ${ratio.toFixed(6)}))`,
  };
});

function revokeFrameBlob() {
  if (frameBlobUrl.value) {
    URL.revokeObjectURL(frameBlobUrl.value);
    frameBlobUrl.value = null;
  }
}

async function loadReplayFrameImage(taskId: number, frameId: number) {
  if (frameAbortController) {
    frameAbortController.abort();
    frameAbortController = null;
  }
  const controller = new AbortController();
  frameAbortController = controller;
  frameStatus.value = 'loading';

  try {
    const res = await fetch(`/api/tasks/${taskId}/frame/${frameId}`, {
      signal: controller.signal,
    });
    if (controller.signal.aborted) return;

    const hdrW = Number(res.headers.get('X-Frame-Width') || 0);
    const hdrH = Number(res.headers.get('X-Frame-Height') || 0);
    if (hdrW > 0 && hdrH > 0) {
      probedDims.value = { width: hdrW, height: hdrH };
    }

    if (!res.ok) {
      revokeFrameBlob();
      frameStatus.value = 'missing';
      await nextTick();
      drawReplayCanvas();
      return;
    }

    const blob = await res.blob();
    if (controller.signal.aborted) return;
    revokeFrameBlob();
    frameBlobUrl.value = URL.createObjectURL(blob);
    frameStatus.value = 'loaded';
    await nextTick();
    drawReplayCanvas();
  } catch (err: any) {
    if (err?.name === 'AbortError') return;
    revokeFrameBlob();
    frameStatus.value = 'missing';
    await nextTick();
    drawReplayCanvas();
  }
}

function onReplayImgLoad(e: Event) {
  const img = e.target as HTMLImageElement;
  if ((!probedDims.value || probedDims.value.width <= 0) && img.naturalWidth > 0 && img.naturalHeight > 0) {
    probedDims.value = { width: img.naturalWidth, height: img.naturalHeight };
  }
  frameStatus.value = 'loaded';
  drawReplayCanvas();
}

function onReplayImgError() {
  revokeFrameBlob();
  frameStatus.value = 'missing';
  drawReplayCanvas();
}

watch(selectedTaskId, () => {
  probedDims.value = null;
  revokeFrameBlob();
  frameStatus.value = 'idle';
});

watch(
  () => [selectedTaskId.value, currentPoint.value?.frame_id] as const,
  ([tid, fid]) => {
    if (tid && fid !== undefined && fid !== null) {
      loadReplayFrameImage(tid, fid);
    } else {
      revokeFrameBlob();
      frameStatus.value = 'idle';
    }
  },
  { immediate: true }
);

function formatTimestamp(ts: number): string {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

async function handleQuery() {
  if (!selectedTaskId.value) return;
  pauseReplay();
  const parsedTrackId = trackIdInput.value.trim() ? parseInt(trackIdInput.value.trim(), 10) : null;
  selectedTrackId.value = parsedTrackId && !Number.isNaN(parsedTrackId) ? parsedTrackId : null;

  await Promise.all([
    store.fetchZonesForTask(selectedTaskId.value),
    store.fetchTracks({
      task_id: selectedTaskId.value,
      track_id: selectedTrackId.value,
      class_name: classFilter.value,
      limit: 1500,
    }),
  ]);
  cachedSummaries.value = [...store.trackSummaries];
  replayIndex.value = 0;
  await nextTick();
  drawReplayCanvas();
}

async function handleResetFilters() {
  trackIdInput.value = '';
  classFilter.value = 'all';
  selectedTrackId.value = null;
  await handleQuery();
}

async function selectTrackSummary(summary: TrackSummary) {
  if (!selectedTaskId.value) return;
  pauseReplay();
  selectedTrackId.value = summary.track_id;
  trackIdInput.value = String(summary.track_id);
  await store.fetchTracks({
    task_id: selectedTaskId.value,
    track_id: summary.track_id,
    class_name: classFilter.value,
    limit: 2000,
  });
  replayIndex.value = 0;
  await nextTick();
  drawReplayCanvas();
  if (replayPoints.value.length > 1) {
    startPlayback();
  }
}

function startPlayback() {
  if (replayPoints.value.length <= 1) return;
  stopTimer();
  isPlaying.value = true;
  if (replayIndex.value >= replayPoints.value.length - 1) {
    replayIndex.value = 0;
  }
  const intervalMs = Math.max(30, Math.round(1000 / Math.max(1, replayFps.value)));
  playTimer = window.setInterval(() => {
    if (replayIndex.value < replayPoints.value.length - 1) {
      replayIndex.value += 1;
    } else {
      pauseReplay();
    }
  }, intervalMs);
}

function pauseReplay() {
  isPlaying.value = false;
  stopTimer();
}

function togglePlay() {
  if (isPlaying.value) {
    pauseReplay();
  } else {
    startPlayback();
  }
}

function stepPrev() {
  pauseReplay();
  if (replayIndex.value > 0) {
    replayIndex.value -= 1;
  }
}

function stepNext() {
  pauseReplay();
  if (replayIndex.value < replayPoints.value.length - 1) {
    replayIndex.value += 1;
  }
}

function resetReplay() {
  pauseReplay();
  replayIndex.value = 0;
  drawReplayCanvas();
}

function jumpToPoint(idx: number) {
  pauseReplay();
  replayIndex.value = idx;
  drawReplayCanvas();
}

function stopTimer() {
  if (playTimer !== null) {
    clearInterval(playTimer);
    playTimer = null;
  }
}

watch(replayFps, () => {
  if (isPlaying.value) {
    startPlayback();
  }
});

watch([replayIndex, replayPoints, taskZones, frameDims, showReplayZones, frameStatus], () => {
  drawReplayCanvas();
});

function drawReplayCanvas() {
  const canvas = replayCanvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;
  const uiScale = Math.max(1.0, w / 960);

  if (frameBlobUrl.value && frameStatus.value === 'loaded') {
    ctx.clearRect(0, 0, w, h);
  } else {
    ctx.fillStyle = '#090f1d';
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = 'rgba(51, 65, 85, 0.30)';
    ctx.lineWidth = 1;
    const gridStep = Math.max(60, Math.round(80 * uiScale));
    for (let gx = 0; gx <= w; gx += gridStep) {
      ctx.beginPath();
      ctx.moveTo(gx, 0);
      ctx.lineTo(gx, h);
      ctx.stroke();
    }
    for (let gy = 0; gy <= h; gy += gridStep) {
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(w, gy);
      ctx.stroke();
    }
  }

  if (showReplayZones.value) {
    for (const z of taskZones.value) {
      if (!z.polygon || z.polygon.length < 3) continue;
      ctx.save();
      ctx.beginPath();
      z.polygon.forEach((pt, idx) => {
        if (idx === 0) ctx.moveTo(pt[0], pt[1]);
        else ctx.lineTo(pt[0], pt[1]);
      });
      ctx.closePath();

      if (z.zone_type === 'intrusion') {
        ctx.strokeStyle = 'rgba(239, 68, 68, 0.75)';
        ctx.fillStyle = 'rgba(239, 68, 68, 0.12)';
      } else if (z.zone_type === 'lane') {
        ctx.strokeStyle = 'rgba(16, 185, 129, 0.65)';
        ctx.fillStyle = 'rgba(16, 185, 129, 0.08)';
      } else {
        ctx.strokeStyle = 'rgba(245, 158, 11, 0.70)';
        ctx.fillStyle = 'rgba(245, 158, 11, 0.10)';
      }
      ctx.lineWidth = Math.max(2, Math.round(2 * uiScale));
      ctx.fill();
      ctx.stroke();

      const p0 = z.polygon[0];
      ctx.font = `bold ${Math.round(14 * uiScale)}px sans-serif`;
      ctx.fillStyle = 'rgba(226, 232, 240, 0.9)';
      ctx.fillText(z.name, p0[0] + 6, p0[1] + Math.round(18 * uiScale));
      ctx.restore();
    }
  }

  const pts = replayPoints.value;
  if (pts.length === 0) {
    ctx.fillStyle = '#64748b';
    ctx.font = `${Math.round(15 * uiScale)}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText('暂无可回放轨迹点 — 请在左侧选择任务查询或点击轨迹卡片', w / 2, h / 2);
    ctx.textAlign = 'left';
    return;
  }

  const curIdx = Math.min(Math.max(0, replayIndex.value), pts.length - 1);

  if (isSingleTrackMode.value) {
    ctx.save();
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(56, 189, 248, 0.28)';
    ctx.lineWidth = Math.max(2, Math.round(2 * uiScale));
    ctx.setLineDash([6, 4]);
    pts.forEach((pt, idx) => {
      if (idx === 0) ctx.moveTo(pt.cx, pt.cy);
      else ctx.lineTo(pt.cx, pt.cy);
    });
    ctx.stroke();
    ctx.restore();

    if (curIdx > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.strokeStyle = '#22d3ee';
      ctx.lineWidth = Math.max(3, Math.round(3 * uiScale));
      for (let i = 0; i <= curIdx; i++) {
        if (i === 0) ctx.moveTo(pts[i].cx, pts[i].cy);
        else ctx.lineTo(pts[i].cx, pts[i].cy);
      }
      ctx.stroke();
      ctx.restore();
    }

    const pStart = pts[0];
    const pEnd = pts[pts.length - 1];
    const dotR = Math.max(5, Math.round(5 * uiScale));
    ctx.fillStyle = '#10b981';
    ctx.beginPath();
    ctx.arc(pStart.cx, pStart.cy, dotR, 0, Math.PI * 2);
    ctx.fill();
    ctx.font = `bold ${Math.round(12 * uiScale)}px monospace`;
    ctx.fillText('START', pStart.cx + dotR + 4, pStart.cy - 6);

    ctx.fillStyle = '#f43f5e';
    ctx.beginPath();
    ctx.arc(pEnd.cx, pEnd.cy, dotR, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillText('END', pEnd.cx + dotR + 4, pEnd.cy + 14);
  } else {
    for (let i = 0; i <= curIdx; i++) {
      const pt = pts[i];
      ctx.fillStyle = i === curIdx ? '#f59e0b' : 'rgba(56, 189, 248, 0.35)';
      ctx.beginPath();
      ctx.arc(pt.cx, pt.cy, (i === curIdx ? 5 : 2.5) * uiScale, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  const cur = pts[curIdx];
  const bx = cur.cx - cur.w * 0.5;
  const by = cur.cy - cur.h * 0.5;

  ctx.save();
  ctx.strokeStyle = '#f59e0b';
  ctx.fillStyle = 'rgba(245, 158, 11, 0.18)';
  ctx.lineWidth = Math.max(2.5, Math.round(3 * uiScale));
  ctx.fillRect(bx, by, cur.w, cur.h);
  ctx.strokeRect(bx, by, cur.w, cur.h);

  ctx.fillStyle = '#fbbf24';
  ctx.beginPath();
  ctx.arc(cur.cx, cur.cy, Math.max(6, Math.round(6 * uiScale)), 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2;
  ctx.stroke();

  const tag = `${activeTrackDisplayId.value ? `Track #${activeTrackDisplayId.value} ` : ''}${cur.class_name} | F${cur.frame_id} | ${cur.speed.toFixed(1)}km/h`;
  const fontSize = Math.round(15 * uiScale);
  ctx.font = `bold ${fontSize}px monospace`;
  const tw = ctx.measureText(tag).width;
  const pillH = Math.round(fontSize * 1.45);
  const padX = Math.round(8 * uiScale);
  const pillW = tw + padX * 2;
  const tx = Math.max(0, Math.min(bx, w - pillW));
  const ty = Math.max(4, by - pillH - 4);
  ctx.fillStyle = 'rgba(15, 23, 42, 0.92)';
  ctx.fillRect(tx, ty, pillW, pillH);
  ctx.fillStyle = '#fde68a';
  ctx.fillText(tag, tx + padX, ty + fontSize);
  ctx.restore();
}

onMounted(async () => {
  if (store.tasks.length === 0) {
    await store.fetchTasks();
  }
  if (!selectedTaskId.value && store.tasks.length > 0) {
    selectedTaskId.value = store.activeTaskId || store.tasks[0].id;
  }
  if (selectedTaskId.value) {
    await handleQuery();
  } else {
    drawReplayCanvas();
  }
});

onUnmounted(() => {
  stopTimer();
  if (frameAbortController) {
    frameAbortController.abort();
    frameAbortController = null;
  }
  revokeFrameBlob();
});
</script>
