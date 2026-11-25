<template>
  <div class="space-y-4">
    <!-- Top Query & Filter Bar -->
    <div class="card space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-base font-bold text-slate-100 flex items-center gap-2">
            查询条件
          </h2>
        </div>
        <span class="badge badge-blue text-xs">
          <span>{{ displaySummaries.length }} 条轨迹摘要 &middot; {{ replayPoints.length }} 个轨迹点</span>
        </span>
      </div>

      <form @submit.prevent="handleQuery" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 gap-3 text-xs pt-1">
        <div class="lg:col-span-4">
          <label class="block text-slate-400 mb-1">监控任务</label>
          <select
            v-model.number="selectedTaskId"
            class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
          >
            <option :value="0" disabled>-- 请选择任务 --</option>
            <option v-for="t in store.tasks" :key="t.id" :value="t.id">
              #{{ t.id }} {{ t.name }} ({{ t.status }})
            </option>
          </select>
        </div>

        <div class="lg:col-span-2">
          <label class="block text-slate-400 mb-1">目标编号（可选）</label>
          <input
            v-model="trackIdInput"
            type="number"
            min="1"
            placeholder="全部目标"
            class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
          />
        </div>

        <div class="lg:col-span-3">
          <label class="block text-slate-400 mb-1">目标类别</label>
          <select
            v-model="classFilter"
            class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
          >
            <option value="all">全部类别 (all)</option>
            <option value="car">汽车 (car)</option>
            <option value="person">行人 (person)</option>
            <option value="motorcycle">摩托车/电动车 (motorcycle)</option>
            <option value="bicycle">自行车 (bicycle)</option>
            <option value="vehicle">车辆 (vehicle)</option>
            <option value="pedestrian">行人 (pedestrian)</option>
          </select>
        </div>

        <div class="lg:col-span-3 flex items-end gap-2">
          <button
            type="submit"
            :disabled="!selectedTaskId || store.tracksLoading"
            class="btn btn-primary text-xs py-2 px-4 flex-1"
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

    <!-- Main Two-Column Content -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
      <!-- Left Column: Track Summary Cards (4 cols) -->
      <div class="lg:col-span-4 card space-y-3 flex flex-col">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <span class="text-sm font-bold text-slate-100">轨迹摘要列表</span>
          <span class="text-[11px] text-slate-400">点击卡片加载并回放</span>
        </div>

        <div class="space-y-2 max-h-[680px] overflow-y-auto pr-1 flex-1">
          <div
            v-for="summary in displaySummaries"
            :key="summary.track_id"
            @click="selectTrackSummary(summary)"
            class="p-3 rounded-lg border transition-all cursor-pointer space-y-2"
            :class="
              selectedTrackId === summary.track_id
                ? 'bg-indigo-950/40 border-indigo-500 shadow-md shadow-indigo-500/10'
                : 'bg-slate-800/40 border-slate-700/60 hover:bg-slate-800/80'
            "
          >
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <span class="font-mono font-bold text-sm text-cyan-300">Track #{{ summary.track_id }}</span>
                <span class="badge badge-blue text-[10px]">{{ summary.class_name }}</span>
              </div>
              <span
                v-if="summary.violation_count > 0"
                class="badge badge-red text-[10px]"
              >
                <span>&#x1F6A8; {{ summary.violation_count }} 次违章</span>
              </span>
              <span v-else class="badge badge-gray text-[10px]">
                <span>0 违章</span>
              </span>
            </div>

            <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-300">
              <span>帧范围: F{{ summary.first_frame }} &rarr; F{{ summary.last_frame }}</span>
              <span class="text-right">点位数: {{ summary.point_count }} 点</span>
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
            class="text-center py-12 text-slate-500 text-xs space-y-2"
          >
            <div class="text-2xl">&#x1F4ED;</div>
            <div>暂无持久化轨迹记录</div>
            <div class="text-[11px] text-slate-600">
              <span>请先在「实时监控」中启动任务运行数秒以写入轨迹数据</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Right Column: 2D Trajectory Replay Canvas + Points Table (8 cols) -->
      <div class="lg:col-span-8 space-y-4">
        <!-- Interactive 2D Replay Canvas -->
        <div class="card space-y-3 bg-slate-950/90">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2">
              <span class="text-sm font-bold text-slate-100">2D 轨迹时空回放画布</span>
              <span v-if="isSingleTrackMode && activeTrackDisplayId !== null" class="badge badge-green text-[11px]">
                <span>正在回放 Track #{{ activeTrackDisplayId }}</span>
              </span>
              <span v-else-if="replayPoints.length > 0" class="badge badge-amber text-[11px]">
                <span>多目标点位预览 (点击左侧卡片可单目标连线回放)</span>
              </span>
              <label class="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer ml-2">
                <input type="checkbox" v-model="showReplayZones" class="accent-indigo-500 rounded" />
                <span>叠加ROI区</span>
              </label>
            </div>

            <div v-if="currentPoint" class="flex flex-wrap items-center gap-3 text-xs font-mono text-slate-300">
              <span>Frame #{{ currentPoint.frame_id }}</span>
              <span class="text-cyan-300">
                ({{ currentPoint.cx.toFixed(0) }}, {{ currentPoint.cy.toFixed(0) }})
              </span>
              <span class="text-amber-300">{{ currentPoint.speed.toFixed(1) }} km/h</span>
              <span class="text-emerald-300">{{ (currentPoint.confidence * 100).toFixed(0) }}%</span>
            </div>
          </div>

          <!-- Video Frame Underlay + Interactive Vector Canvas Overlay -->
          <div class="relative w-full aspect-video rounded-lg overflow-hidden border border-slate-800 bg-slate-950 flex items-center justify-center">
            <img
              v-if="currentFrameUrl && !replayImgErrored"
              :src="currentFrameUrl"
              alt="Historical Replay Video Frame"
              class="w-full h-full object-contain block select-none"
              @load="onReplayImgLoad"
              @error="onReplayImgError"
            />
            <canvas
              ref="replayCanvasRef"
              :width="canvasDims.width"
              :height="canvasDims.height"
              class="absolute inset-0 w-full h-full object-contain pointer-events-none block"
            ></canvas>
          </div>

          <!-- Scrubber & Playback Controls -->
          <div class="flex flex-wrap items-center gap-3 pt-1">
            <div class="flex items-center gap-1.5">
              <button
                @click="togglePlay"
                :disabled="replayPoints.length === 0"
                class="btn btn-primary text-xs py-1.5 px-3"
              >
                <span>{{ isPlaying ? '\u23F8 暂停' : '\u25B6 播放' }}</span>
              </button>
              <button
                @click="resetReplay"
                :disabled="replayPoints.length === 0"
                class="btn btn-secondary text-xs py-1.5 px-2.5"
              >
                <span>&#x23EE; 重置</span>
              </button>
              <select
                v-model.number="playbackSpeed"
                class="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                <option :value="1">1x</option>
                <option :value="2">2x</option>
                <option :value="4">4x</option>
              </select>
            </div>

            <div class="flex-1 flex items-center gap-3 min-w-[200px]">
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
            <span class="text-[11px] text-slate-400">点击任意行跳转至对应帧位</span>
          </div>

          <div class="max-h-[280px] overflow-y-auto overflow-x-auto border border-slate-800 rounded-lg">
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
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
const playbackSpeed = ref<number>(1);
const showReplayZones = ref<boolean>(false);
const replayImgLoaded = ref<boolean>(false);
const replayImgErrored = ref<boolean>(false);
const probedDims = ref<{ width: number; height: number } | null>(null);
let playTimer: number | null = null;

// Initialize task selection from store
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
  if (!selectedTaskId.value || store.tracksTaskId !== selectedTaskId.value) return [];
  return cachedSummaries.value.length > 0 ? cachedSummaries.value : store.trackSummaries;
});

const replayPoints = computed<TrackPoint[]>(() => store.tracksTaskId === selectedTaskId.value ? store.trackPoints : []);

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
  return store.multiZones[selectedTaskId.value] || (selectedTaskId.value === store.activeTaskId ? store.zones : []);
});

const currentFrameUrl = computed<string | null>(() => {
  if (!selectedTaskId.value || !currentPoint.value) return null;
  return `/api/tasks/${selectedTaskId.value}/frame/${currentPoint.value.frame_id}`;
});

function onReplayImgLoad() {
  replayImgLoaded.value = true;
  replayImgErrored.value = false;
  drawReplayCanvas();
}

function onReplayImgError() {
  replayImgLoaded.value = false;
  replayImgErrored.value = true;
  drawReplayCanvas();
}

async function probeTaskFrameDims(taskId: number, frameId: number) {
  try {
    const res = await fetch(`/api/tasks/${taskId}/frame/${frameId}`);
    if (res.ok) {
      const fw = Number(res.headers.get('X-Frame-Width') || 0);
      const fh = Number(res.headers.get('X-Frame-Height') || 0);
      if (fw > 0 && fh > 0) {
        probedDims.value = { width: fw, height: fh };
      }
      replayImgErrored.value = false;
    }
  } catch (_) {}
}

watch(selectedTaskId, () => {
  probedDims.value = null;
  replayImgLoaded.value = false;
  replayImgErrored.value = false;
});

watch(
  currentFrameUrl,
  (url) => {
    if (url && !probedDims.value && selectedTaskId.value && currentPoint.value) {
      probeTaskFrameDims(selectedTaskId.value, currentPoint.value.frame_id);
    }
  },
  { immediate: true }
);

const canvasDims = computed(() => {
  if (probedDims.value && probedDims.value.width > 0 && probedDims.value.height > 0) {
    return probedDims.value;
  }
  const activeRunFrame =
    store.multiFrames[selectedTaskId.value] ||
    (selectedTaskId.value === store.activeTaskId ? store.currentFrame : null);
  if (activeRunFrame?.frame_width && activeRunFrame?.frame_height) {
    return { width: activeRunFrame.frame_width, height: activeRunFrame.frame_height };
  }
  let maxW = 1280;
  let maxH = 720;
  for (const z of taskZones.value) {
    for (const pt of z.polygon || []) {
      if (pt[0] > maxW) maxW = Math.ceil(pt[0]);
      if (pt[1] > maxH) maxH = Math.ceil(pt[1]);
    }
  }
  for (const pt of replayPoints.value) {
    if (pt.cx + pt.w * 0.5 + 40 > maxW) maxW = Math.ceil(pt.cx + pt.w * 0.5 + 40);
    if (pt.cy + pt.h * 0.5 + 40 > maxH) maxH = Math.ceil(pt.cy + pt.h * 0.5 + 40);
  }
  return { width: maxW, height: maxH };
});

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
  const intervalMs = Math.max(40, Math.round(160 / playbackSpeed.value));
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

watch(playbackSpeed, () => {
  if (isPlaying.value) {
    startPlayback();
  }
});

watch([replayIndex, replayPoints, taskZones, canvasDims, showReplayZones], () => {
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

  if (currentFrameUrl.value && !replayImgErrored.value) {
    ctx.clearRect(0, 0, w, h);
  } else {
    // Background & spatial reference grid when no video frame is loaded
    ctx.fillStyle = '#090f1d';
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = 'rgba(51, 65, 85, 0.28)';
    ctx.lineWidth = 1;
    const gridStep = 80;
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

  // Draw configured ROI zones only when explicitly toggled on
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
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('暂无可回放轨迹点 — 请选择任务并点击查询，或在左侧选择轨迹卡片', w / 2, h / 2);
    ctx.textAlign = 'left';
    return;
  }

  const curIdx = Math.min(Math.max(0, replayIndex.value), pts.length - 1);

  if (isSingleTrackMode.value) {
    // Full future trajectory polyline (subtle dashed)
    ctx.save();
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(56, 189, 248, 0.25)';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    pts.forEach((pt, idx) => {
      if (idx === 0) ctx.moveTo(pt.cx, pt.cy);
      else ctx.lineTo(pt.cx, pt.cy);
    });
    ctx.stroke();
    ctx.restore();

    // Traversed polyline up to curIdx
    if (curIdx > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.strokeStyle = '#22d3ee';
      ctx.lineWidth = 3;
      for (let i = 0; i <= curIdx; i++) {
        if (i === 0) ctx.moveTo(pts[i].cx, pts[i].cy);
        else ctx.lineTo(pts[i].cx, pts[i].cy);
      }
      ctx.stroke();
      ctx.restore();
    }

    // Start & End markers
    const pStart = pts[0];
    const pEnd = pts[pts.length - 1];
    ctx.fillStyle = '#10b981';
    ctx.beginPath();
    ctx.arc(pStart.cx, pStart.cy, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.font = 'bold 11px monospace';
    ctx.fillText('START', pStart.cx + 8, pStart.cy - 6);

    ctx.fillStyle = '#f43f5e';
    ctx.beginPath();
    ctx.arc(pEnd.cx, pEnd.cy, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillText('END', pEnd.cx + 8, pEnd.cy + 14);
  } else {
    // Multi-track scatter preview up to curIdx
    for (let i = 0; i <= curIdx; i++) {
      const pt = pts[i];
      ctx.fillStyle = i === curIdx ? '#f59e0b' : 'rgba(56, 189, 248, 0.35)';
      ctx.beginPath();
      ctx.arc(pt.cx, pt.cy, i === curIdx ? 5 : 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Animated Moving Target Marker + Bounding Box at curIdx
  const cur = pts[curIdx];
  const bx = cur.cx - cur.w * 0.5;
  const by = cur.cy - cur.h * 0.5;

  ctx.save();
  ctx.strokeStyle = '#f59e0b';
  ctx.fillStyle = 'rgba(245, 158, 11, 0.18)';
  ctx.lineWidth = Math.max(2.5, Math.round(3 * uiScale));
  ctx.fillRect(bx, by, cur.w, cur.h);
  ctx.strokeRect(bx, by, cur.w, cur.h);

  // Center moving dot
  ctx.fillStyle = '#fbbf24';
  ctx.beginPath();
  ctx.arc(cur.cx, cur.cy, Math.max(6, Math.round(6 * uiScale)), 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2;
  ctx.stroke();

  // Larger Crisp Info tag
  const tag = `${activeTrackDisplayId.value ? `Track #${activeTrackDisplayId.value} ` : ''}${cur.class_name} | F${cur.frame_id} | ${cur.speed.toFixed(1)}km/h`;
  const fontSize = Math.round(16 * uiScale);
  ctx.font = `bold ${fontSize}px monospace`;
  const tw = ctx.measureText(tag).width;
  const pillH = Math.round(fontSize * 1.45);
  const padX = Math.round(8 * uiScale);
  const ty = Math.max(4, by - pillH - 4);
  ctx.fillStyle = 'rgba(15, 23, 42, 0.92)';
  ctx.fillRect(bx, ty, tw + padX * 2, pillH);
  ctx.fillStyle = '#fde68a';
  ctx.fillText(tag, bx + padX, ty + fontSize);
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
});
</script>
