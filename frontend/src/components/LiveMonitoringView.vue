<template>
  <div class="monitor-page">
    <header class="workspace-heading monitor-heading">
      <div>
        <h1>实时监控</h1>
        <p>选择视频，在画面上查看目标、轨迹和区域异常。</p>
      </div>
      <div class="workspace-actions">
        <button type="button" class="btn btn-secondary" :disabled="taskActionBusy || savingConfig" @click="startDemo">
          试用违章演示
        </button>
        <button type="button" class="btn btn-primary" :disabled="taskActionBusy || savingConfig" @click="sourceDialog?.open()">
          选择视频
        </button>
      </div>
    </header>

    <VideoSourceDialog ref="sourceDialog" @started="focusTask" />

    <p v-if="store.taskError" class="workspace-error" role="alert">{{ store.taskError }}</p>

    <div class="monitor-grid">
      <section class="monitor-stage" aria-labelledby="video-heading">
        <header class="monitor-toolbar">
          <div class="min-w-0">
            <h2 id="video-heading">视频预览</h2>
            <p class="monitor-source" :title="store.activeTask?.video_source">{{ sourceLabel }}</p>
          </div>
          <div class="monitor-toolbar-actions">
            <label class="monitor-task-picker">
              <span class="sr-only">选择已有任务</span>
              <select class="workspace-field" :title="store.activeTask?.name" :value="store.activeTaskId ?? ''" :disabled="taskActionBusy || savingConfig" @change="handleTaskChange">
                <option value="" disabled>选择已有任务</option>
                <option v-for="task in store.tasks" :key="task.id" :value="task.id">{{ task.name }}</option>
              </select>
            </label>
            <button
              v-if="store.activeTask"
              type="button"
              class="btn"
              :class="isRunning ? 'btn-secondary' : 'btn-primary'"
              :disabled="taskActionBusy || savingConfig"
              @click="toggleTracking"
            >
              {{ taskActionBusy ? '处理中…' : isRunning ? '停止跟踪' : '开始跟踪' }}
            </button>
          </div>
        </header>

        <!-- Multi-Pane Video Grid -->
        <div
          class="monitor-video-grid grid gap-2"
          :class="layoutMode === '1x1' ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'"
        >
          <div
            v-for="(paneTaskId, paneIdx) in visiblePaneTaskIds"
            :key="`pane-${paneIdx}`"
            class="rounded-lg border border-slate-800 bg-slate-950 overflow-hidden flex flex-col"
          >
            <!-- Per-Pane Toolbar -->
            <div
              v-if="layoutMode !== '1x1'"
              class="flex flex-wrap items-center justify-between gap-2 px-2.5 py-1.5 bg-slate-900 border-b border-slate-800 text-xs"
            >
              <div class="flex items-center gap-2">
                <span class="text-[11px] font-mono text-slate-400">#{{ paneIdx + 1 }}</span>
                <select
                  :value="paneTaskId ?? ''"
                  @change="(e) => handlePaneTaskChange(paneIdx, e)"
                  :aria-label="`分屏 ${paneIdx + 1} 的任务`"
                  class="bg-slate-800 border border-slate-700 rounded px-2 py-0.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 max-w-[200px]"
                >
                  <option value="">-- 选择监控任务 --</option>
                  <option v-for="t in store.tasks" :key="t.id" :value="t.id">
                    {{ t.name }} ({{ formatTaskStatus(t.status) }})
                  </option>
                </select>
              </div>

              <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
                <span
                  v-if="paneTaskId"
                  class="badge py-0.5 px-1.5"
                  :class="isPaneDemo(paneTaskId) ? 'badge-amber' : 'badge-green'"
                >
                  {{ isPaneDemo(paneTaskId) ? '仿真Mock' : '真实推理' }}
                </span>
                <span
                  v-if="paneTaskId"
                  class="badge py-0.5 px-1.5"
                  :class="getPaneFrame(paneTaskId)?.is_keyframe === false ? 'badge-blue' : 'badge-gray'"
                >
                  {{ getPaneFrame(paneTaskId)?.is_keyframe === false ? '卡尔曼补帧' : '关键帧' }}
                  ({{ (((getPaneFrame(paneTaskId)?.metrics?.detect_skip_ratio ?? 0) * 100)).toFixed(0) }}%)
                </span>
                <span v-if="getPaneFrame(paneTaskId)" class="text-teal-300 font-mono">
                  处理均速 {{ (getPaneFrame(paneTaskId)?.metrics?.fps ?? 0).toFixed(1) }} 帧/秒
                </span>
              </div>
            </div>

            <!-- MJPEG Underlay + Transparent Vector Canvas Overlay -->
            <div class="monitor-viewport">
              <template v-if="paneTaskId">
                <!-- Real MJPEG Underlay -->
                <img
                  v-if="shouldShowVideoFeed(paneTaskId)"
                  :src="videoFeedUrl(paneTaskId, paneIdx)"
                  class="w-full h-full object-contain block select-none"
                  alt="正在跟踪的视频画面"
                  @load="onImgLoad(paneTaskId)"
                  @error="onImgError(paneTaskId)"
                />

                <div
                  v-if="!shouldShowVideoFeed(paneTaskId) || mjpegErrored[paneTaskId] || !mjpegReady[paneTaskId]"
                  class="monitor-placeholder"
                >
                  <span class="monitor-placeholder-icon" aria-hidden="true"></span>
                  <strong>{{ previewTitle(paneTaskId) }}</strong>
                  <span>{{ previewHint(paneTaskId) }}</span>
                </div>

                <!-- Transparent Vector Canvas Overlay -->
                <canvas
                  :ref="(el) => setCanvasRef(paneIdx, el)"
                  :width="getFrameWidth(paneTaskId)"
                  :height="getFrameHeight(paneTaskId)"
                  class="absolute inset-0 w-full h-full object-contain pointer-events-none block"
                ></canvas>

                <!-- Active Violation Alert Toast on Primary / Single View -->
                <transition-group
                  v-if="paneIdx === 0"
                  name="fade"
                  tag="div"
                  class="absolute top-3 right-3 flex flex-col gap-2 max-w-sm pointer-events-none"
                >
                  <div
                    v-for="v in getPaneViolations(paneTaskId)"
                    :key="v.id ?? `${v.track_id}-${v.frame_id}-${v.violation_type}`"
                    class="bg-rose-950/90 border border-rose-500 text-rose-200 text-xs px-3 py-2 rounded-lg shadow-lg backdrop-blur alert-pulse pointer-events-auto flex items-start gap-2"
                  >
                    <span class="text-base">&#x1F6A8;</span>
                    <div class="flex-1">
                      <div class="font-bold flex items-center justify-between gap-2">
                        <span>{{ formatViolationTitle(v.violation_type) }}</span>
                        <span class="text-[10px] text-rose-400">Track #{{ v.track_id }}</span>
                      </div>
                      <p class="text-[11px] text-rose-300/90 mt-0.5">
                        {{ v.details?.note || '检测到异常轨迹行为' }}
                      </p>
                    </div>
                  </div>
                </transition-group>

                <!-- Compact Violation Pill on Secondary Multi-View Panes -->
                <div
                  v-else-if="getPaneViolations(paneTaskId).length > 0"
                  class="absolute top-2 right-2 bg-rose-950/90 border border-rose-500 text-rose-200 text-[10px] px-2 py-1 rounded shadow pointer-events-none"
                >
                  <span>&#x1F6A8; 违章报警 &times;{{ getPaneViolations(paneTaskId).length }}</span>
                </div>
              </template>

              <div v-else class="monitor-placeholder">
                <span class="monitor-placeholder-icon" aria-hidden="true"></span>
                <strong>{{ layoutMode === '1x1' ? '还没有视频' : '请选择分屏任务' }}</strong>
                <span>{{ layoutMode === '1x1' ? '请选择视频，或启动模拟演示。' : '从此分屏上方的列表选择任务。' }}</span>
              </div>
            </div>
          </div>
        </div>

        <footer class="monitor-stage-footer">
          <div class="monitor-signal" :class="{ 'is-active': store.wsConnected && isRunning }">
            <span aria-hidden="true"></span>{{ connectionLabel }}
          </div>
          <div class="workspace-actions">
            <label class="monitor-layout">
              <span>分屏</span>
              <select v-model="layoutMode" class="workspace-field">
                <option v-for="option in layoutOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>
            <button v-if="isRunning" type="button" class="btn btn-secondary" @click="handleReconnect">重新连接</button>
          </div>
        </footer>
      </section>

      <aside class="monitor-summary" aria-label="当前任务概况">
        <section class="monitor-summary-panel">
          <div class="monitor-summary-header">
            <h2>任务概况</h2>
            <span class="monitor-status" :class="{ 'is-active': isRunning }">{{ statusLabel }}</span>
          </div>
          <div class="monitor-task-name" :title="store.activeTask?.name">{{ store.activeTask?.name || '尚未选择任务' }}</div>
          <p class="monitor-summary-muted" v-if="store.activeTask">
            {{ isPaneDemo(store.activeTask.id) ? '模拟视频 · Mock 检测' : '真实视频 · ' + detectorLabel }}
          </p>

          <dl class="monitor-stat-grid">
            <div class="monitor-stat">
              <dt>跟踪目标</dt>
              <dd>{{ activeFrame ? activeFrame.tracks.length : '—' }}</dd>
            </div>
            <div class="monitor-stat">
              <dt>待确认异常</dt>
              <dd>{{ store.activeTaskId ? pendingViolations : '—' }}</dd>
            </div>
          </dl>

          <div class="monitor-summary-meta">
            <span>画面尺寸</span>
            <span>{{ activeFrame?.frame_width && activeFrame?.frame_height ? activeFrame.frame_width + ' × ' + activeFrame.frame_height : '—' }}</span>
          </div>
          <div class="monitor-summary-meta">
            <span>区域规则</span>
            <span>{{ store.activeTask ? (isRoiRulesEnabled ? '已启用' : '已关闭') : '—' }}</span>
          </div>
          <div class="monitor-summary-meta">
            <span>后台处理均速</span>
            <span>{{ activeFrame ? primaryMetrics.fps.toFixed(1) + ' 帧/秒' : '—' }}</span>
          </div>
        </section>

        <section class="monitor-summary-panel monitor-shortcuts">
          <h2>目标与事件</h2>
          <button type="button" class="monitor-shortcut" @click="store.activeTab = 'tracks'">
            <span>查询历史轨迹</span><span aria-hidden="true">↗</span>
          </button>
          <button type="button" class="monitor-shortcut" @click="store.activeTab = 'violations'">
            <span>查看异常事件</span><span aria-hidden="true">↗</span>
          </button>
        </section>
      </aside>
    </div>

    <details class="monitor-disclosure">
      <summary>高级设置</summary>

      <div class="monitor-disclosure-body">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <label class="flex items-center gap-2 text-sm text-slate-300">
            <input v-model="store.singleStreamExclusive" type="checkbox" class="accent-teal-400" />
            启动新任务时停止其他任务
          </label>
          <span v-if="savingConfig" class="text-sm text-teal-300" role="status">正在应用设置…</span>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <!-- Column 1: ROI Detection (Default OFF) & Display Layers -->
          <div class="bg-slate-950/60 border border-slate-800 rounded-lg p-3 space-y-2.5">
            <div class="font-semibold text-slate-200">区域规则与画面图层</div>
            <label class="flex items-center justify-between gap-2 cursor-pointer bg-slate-900 px-2.5 py-2 rounded border border-slate-800">
              <div>
                <div class="text-slate-100 font-medium">开启 ROI 规则检测与区域显示</div>
                <div class="text-[11px] text-slate-400">未校准实际街景/航拍视角建议保持关闭</div>
              </div>
              <input
                type="checkbox"
                :checked="isRoiRulesEnabled"
                :disabled="!store.activeTask || savingConfig"
                @change="handleToggleRoiRules"
                class="accent-indigo-500 w-4 h-4"
              />
            </label>

            <div class="flex flex-wrap gap-x-4 gap-y-2 pt-1 text-slate-300">
              <label class="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" v-model="store.displaySettings.showBBoxes" class="accent-indigo-500 rounded" />
                <span>检测框</span>
              </label>
              <label class="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" v-model="store.displaySettings.showLabels" class="accent-indigo-500 rounded" />
                <span>轨迹标签</span>
              </label>
              <label class="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" v-model="store.displaySettings.showTrails" class="accent-indigo-500 rounded" />
                <span>历史轨迹</span>
              </label>
              <label class="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" v-model="store.displaySettings.showVectors" class="accent-indigo-500 rounded" />
                <span>速度矢量</span>
              </label>
              <label class="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" v-model="store.displaySettings.showZones" class="accent-indigo-500 rounded" />
                <span>仅叠加ROI框</span>
              </label>
            </div>
          </div>

          <!-- Column 2: TensorRT / CUDA Hardware, FP16 & Batching -->
          <div class="bg-slate-950/60 border border-slate-800 rounded-lg p-3 space-y-2.5">
            <div class="font-semibold text-slate-200">模型与推理设备</div>
            <div class="grid grid-cols-3 gap-2">
              <label class="space-y-1">
                <span class="text-slate-400 block">推理后端</span>
                <select
                  :value="store.activeTask?.config?.detector_type || 'yolo'"
                  :disabled="!store.activeTask || savingConfig || isPrimarySimulation"
                  @change="handleDetectorTypeChange"
                  class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-100"
                >
                  <option value="tensorrt">TensorRT (.engine)</option>
                  <option value="yolo">PyTorch YOLO (.pt)</option>
                  <option value="onnx">ONNX Runtime</option>
                  <option v-if="isPrimarySimulation" value="mock">Mock 演示</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-slate-400 block">推理设备</span>
                <select
                  :value="store.activeTask?.config?.device || 'cuda:0'"
                  :disabled="!store.activeTask || savingConfig || isPrimarySimulation"
                  @change="(e) => applyActiveConfig({ device: (e.target as HTMLSelectElement).value })"
                  class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-100"
                >
                  <option value="cuda:0">CUDA GPU</option>
                  <option value="cpu">CPU</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-slate-400 block">微批 Batch</span>
                <select
                  :value="store.activeTask?.config?.batch_size ?? 8"
                  :disabled="!store.activeTask || savingConfig"
                  @change="(e) => applyActiveConfig({ batch_size: Number((e.target as HTMLSelectElement).value) })"
                  class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-100"
                >
                  <option :value="1">Batch = 1</option>
                  <option :value="4">Batch = 4</option>
                  <option :value="8">Batch = 8</option>
                  <option :value="16">Batch = 16</option>
                </select>
              </label>
            </div>
            <label class="flex items-center justify-between gap-2 cursor-pointer bg-slate-900 px-2.5 py-1.5 rounded border border-slate-800">
              <span class="text-slate-200">开启 CUDA FP16 半精度推理 (Tensor Core)</span>
              <input
                type="checkbox"
                :checked="store.activeTask?.config?.half_precision !== false"
                :disabled="!store.activeTask || savingConfig || isPrimarySimulation"
                @change="(e) => applyActiveConfig({ half_precision: (e.target as HTMLInputElement).checked })"
                class="accent-emerald-500 w-4 h-4"
              />
            </label>
          </div>

          <!-- Column 3: Playback Speed Mode, FPS Cap & Adaptive Keyframe Skip -->
          <div class="bg-slate-950/60 border border-slate-800 rounded-lg p-3 space-y-2.5">
            <div class="font-semibold text-slate-200">播放与检测频率</div>
            <div class="grid grid-cols-3 gap-2">
              <label class="space-y-1">
                <span class="text-slate-400 block">播放节奏</span>
                <select
                  :value="store.activeTask?.config?.playback_mode || 'native'"
                  :disabled="!store.activeTask || savingConfig"
                  @change="(e) => applyActiveConfig({ playback_mode: (e.target as HTMLSelectElement).value as 'native' | 'uncapped' })"
                  class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-100"
                >
                  <option value="native">按源帧率读取</option>
                  <option value="uncapped">全速压测 (吞吐优先)</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-slate-400 block">压测限速</span>
                <select
                  :value="store.activeTask?.config?.fps ?? 0"
                  :disabled="!store.activeTask || savingConfig || (store.activeTask?.config?.playback_mode || 'native') === 'native'"
                  @change="(e) => applyActiveConfig({ fps: Number((e.target as HTMLSelectElement).value) })"
                  class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-100 disabled:opacity-50"
                >
                  <option :value="0">不限速</option>
                  <option :value="30">30 FPS</option>
                  <option :value="60">60 FPS</option>
                  <option :value="120">120 FPS</option>
                  <option :value="300">300 FPS</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-slate-400 block">跳帧步长</span>
                <select
                  :value="store.activeTask?.config?.max_skip_stride ?? 2"
                  :disabled="!store.activeTask || savingConfig"
                  @change="(e) => applyActiveConfig({ max_skip_stride: Number((e.target as HTMLSelectElement).value) })"
                  class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-100"
                >
                  <option :value="1">1 (逐帧)</option>
                  <option :value="2">2 (推荐)</option>
                  <option :value="3">3</option>
                  <option :value="4">4</option>
                </select>
              </label>
            </div>
            <label class="flex items-center justify-between gap-2 cursor-pointer bg-slate-900 px-2.5 py-1.5 rounded border border-slate-800">
              <span class="text-slate-200">开启自适应关键帧跳帧 (非关键帧走卡尔曼外推)</span>
              <input
                type="checkbox"
                :checked="Boolean(store.activeTask?.config?.adaptive_skip)"
                :disabled="!store.activeTask || savingConfig"
                @change="(e) => applyActiveConfig({ adaptive_skip: (e.target as HTMLInputElement).checked })"
                class="accent-cyan-500 w-4 h-4"
              />
            </label>
          </div>
        </div>
      </div>
    </details>

    <details class="monitor-disclosure">
      <summary>性能详情</summary>
      <div class="monitor-disclosure-body">
        <p class="monitor-summary-muted">
          后台处理均速按任务启动后的处理帧数计算，包含只做跟踪预测的帧。画面刷新率暂未测量。
        </p>
        <div v-if="activeFrame" class="monitor-performance">
          <span>后台处理均速 <strong>{{ primaryMetrics.fps.toFixed(1) }} 帧/秒</strong></span>
          <span>读帧缓冲丢帧 <strong>{{ primaryMetrics.drop_count }}</strong></span>
          <span>检测跳过率 <strong>{{ ((primaryMetrics.detect_skip_ratio ?? 0) * 100).toFixed(1) }}%</strong></span>
          <span>平均处理耗时 <strong>{{ primaryMetrics.avg_latency_ms.toFixed(1) }} ms</strong></span>
        </div>

        <!-- Real-Time End-to-End Pipeline Latency Breakdown Line Chart -->
        <div class="monitor-chart">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center gap-2">
              <h3 class="text-sm font-semibold text-slate-100">处理阶段耗时</h3>
              <span class="text-[11px] text-slate-400">最近 {{ latencyHistory.length }} 次采样</span>
            </div>

            <div v-if="activeFrame" class="flex flex-wrap items-center gap-3 text-xs">
              <span class="flex items-center gap-1.5 text-slate-200">
                <span class="w-3 h-0.5 bg-slate-100 inline-block"></span>
                <span>总耗时 {{ primaryMetrics.stage_latency.total_ms.toFixed(1) }} ms</span>
              </span>
              <span class="flex items-center gap-1.5 text-rose-400">
                <span class="w-3 h-0.5 border-t border-dashed border-rose-400 inline-block"></span>
                <span>P95 {{ primaryMetrics.p95_latency_ms.toFixed(1) }} ms</span>
              </span>
              <span class="flex items-center gap-1.5 text-amber-400">
                <span class="w-3 h-0.5 bg-amber-400 inline-block"></span>
                <span>检测 {{ primaryMetrics.stage_latency.detect_ms.toFixed(1) }} ms</span>
              </span>
              <span class="flex items-center gap-1.5 text-sky-400">
                <span class="w-3 h-0.5 bg-sky-400 inline-block"></span>
                <span>解码 {{ primaryMetrics.stage_latency.decode_ms.toFixed(1) }} ms</span>
              </span>
              <span class="flex items-center gap-1.5 text-teal-400">
                <span class="w-3 h-0.5 bg-teal-400 inline-block"></span>
                <span>跟踪 {{ primaryMetrics.stage_latency.track_ms.toFixed(1) }} ms</span>
              </span>
              <span class="flex items-center gap-1.5 text-emerald-400">
                <span class="w-3 h-0.5 bg-emerald-400 inline-block"></span>
                <span>规则 {{ primaryMetrics.stage_latency.rules_ms.toFixed(1) }} ms</span>
              </span>
            </div>
          </div>

          <div class="relative w-full h-44 bg-slate-950/90 rounded-lg border border-slate-800/80 overflow-hidden">
            <svg
              v-if="activeFrame && latencyHistory.length >= 2"
              viewBox="0 0 800 180"
              preserveAspectRatio="none"
              class="w-full h-full block"
            >
              <!-- Horizontal gridlines & ms labels -->
              <g v-for="(tick, idx) in latencyGridTicks" :key="`grid-${idx}`">
                <line
                  x1="44"
                  :y1="tick.y"
                  x2="788"
                  :y2="tick.y"
                  stroke="rgba(51, 65, 85, 0.55)"
                  stroke-dasharray="3 3"
                  stroke-width="1"
                />
                <text
                  x="38"
                  :y="tick.y + 3"
                  text-anchor="end"
                  fill="#64748b"
                  font-size="10"
                  font-family="monospace"
                >
                  {{ tick.label }}
                </text>
              </g>

              <!-- P95 reference curve -->
              <polyline
                fill="none"
                stroke="#fb7185"
                stroke-width="1.6"
                stroke-dasharray="5 4"
                :points="latencyPolylines.p95"
              />
              <!-- Stage series -->
              <polyline
                fill="none"
                stroke="#34d399"
                stroke-width="1.6"
                :points="latencyPolylines.rules"
              />
              <polyline
                fill="none"
                stroke="#2dd4bf"
                stroke-width="1.8"
                :points="latencyPolylines.track"
              />
              <polyline
                fill="none"
                stroke="#38bdf8"
                stroke-width="1.8"
                :points="latencyPolylines.decode"
              />
              <polyline
                fill="none"
                stroke="#fbbf24"
                stroke-width="2"
                :points="latencyPolylines.detect"
              />
              <polyline
                fill="none"
                stroke="#f1f5f9"
                stroke-width="2.2"
                :points="latencyPolylines.total"
              />
            </svg>
            <div
              v-else
              class="w-full h-full flex items-center justify-center text-xs text-slate-500"
            >
              <span>任务运行后显示处理耗时</span>
            </div>
          </div>
        </div>
      </div>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, type ComponentPublicInstance } from 'vue';
import VideoSourceDialog from './VideoSourceDialog.vue';
import { useMotStore } from '../stores/motStore';
import type {
  DetectorType,
  StreamConfig,
  TrackFrame,
  TrackItem,
  ViolationEvent,
  Zone,
} from '../types';

type LayoutMode = '1x1' | '2x1' | '2x2';

interface LatencySample {
  frameId: number;
  total: number;
  p95: number;
  decode: number;
  detect: number;
  track: number;
  rules: number;
}

const MAX_LATENCY_POINTS = 60;

const store = useMotStore();
const layoutMode = ref<LayoutMode>('1x1');
const sourceDialog = ref<InstanceType<typeof VideoSourceDialog> | null>(null);
const taskActionBusy = ref(false);
const isRunning = computed(() => store.activeTask?.status === 'running');
const savingConfig = ref(false);
const paneSelections = ref<(number | null)[]>([null, null, null, null]);
const canvasRefs = ref<Record<number, HTMLCanvasElement>>({});
const mjpegEpochs = ref<Record<number, number>>({});
const mjpegErrored = ref<Record<number, boolean>>({});
const mjpegReady = ref<Record<number, boolean>>({});
const latencyHistory = ref<LatencySample[]>([]);
let animationId = 0;

watch(
  () => store.activeTaskId,
  () => {
    latencyHistory.value = [];
  }
);

watch(
  () => store.currentFrame,
  (frame) => {
    if (!frame || frame.task_id !== store.activeTaskId || !isRunning.value || !frame.metrics?.stage_latency) {
      return;
    }
    const st = frame.metrics.stage_latency;
    const total =
      st.total_ms > 0
        ? st.total_ms
        : frame.metrics.avg_latency_ms || st.decode_ms + st.detect_ms + st.track_ms + st.rules_ms;
    const next = [
      ...latencyHistory.value,
      {
        frameId: frame.frame_id,
        total,
        p95: frame.metrics.p95_latency_ms || total,
        decode: st.decode_ms || 0,
        detect: st.detect_ms || 0,
        track: st.track_ms || 0,
        rules: st.rules_ms || 0,
      },
    ];
    if (next.length > MAX_LATENCY_POINTS) {
      next.splice(0, next.length - MAX_LATENCY_POINTS);
    }
    latencyHistory.value = next;
  }
);

const latencySvgMax = computed(() => {
  if (latencyHistory.value.length === 0) return 20;
  let peak = 20;
  for (const s of latencyHistory.value) {
    peak = Math.max(peak, s.total, s.p95, s.detect + s.decode + s.track + s.rules);
  }
  return Math.max(20, Math.ceil((peak * 1.1) / 10) * 10);
});

const latencyGridTicks = computed(() => {
  const maxMs = latencySvgMax.value;
  const topY = 16;
  const bottomY = 164;
  return [0, 0.333, 0.667, 1].map((frac) => {
    const val = Math.round(maxMs * frac);
    const y = Number((bottomY - frac * (bottomY - topY)).toFixed(1));
    return { y, label: `${val}` };
  });
});

function buildLatencyPolyline(key: keyof Omit<LatencySample, 'frameId'>): string {
  const pts = latencyHistory.value;
  if (pts.length < 2) return '';
  const maxMs = Math.max(1, latencySvgMax.value);
  const leftX = 48;
  const rightX = 786;
  const topY = 16;
  const bottomY = 164;
  const stepX = (rightX - leftX) / Math.max(1, pts.length - 1);
  return pts
    .map((sample, idx) => {
      const x = (leftX + idx * stepX).toFixed(1);
      const clamped = Math.max(0, Math.min(maxMs, sample[key]));
      const y = (bottomY - (clamped / maxMs) * (bottomY - topY)).toFixed(1);
      return `${x},${y}`;
    })
    .join(' ');
}

const latencyPolylines = computed(() => ({
  total: buildLatencyPolyline('total'),
  p95: buildLatencyPolyline('p95'),
  detect: buildLatencyPolyline('detect'),
  decode: buildLatencyPolyline('decode'),
  track: buildLatencyPolyline('track'),
  rules: buildLatencyPolyline('rules'),
}));

const layoutOptions: { label: string; value: LayoutMode }[] = [
  { label: '单路详览 (1\u00D71)', value: '1x1' },
  { label: '双路分屏 (2\u00D71)', value: '2x1' },
  { label: '四路分屏 (2\u00D72)', value: '2x2' },
];

function setCanvasRef(idx: number, el: Element | ComponentPublicInstance | null) {
  if (el instanceof HTMLCanvasElement) {
    canvasRefs.value[idx] = el;
  } else if (!el) {
    delete canvasRefs.value[idx];
  }
}

watch(
  () => [store.tasks, store.activeTaskId] as const,
  ([tasks, activeId]) => {
    const next = [...paneSelections.value];
    if (tasks.length === 0) {
      paneSelections.value = [null, null, null, null];
      return;
    }
    if (!next[0] || !tasks.some((t) => t.id === next[0])) {
      next[0] = activeId || tasks[0].id;
    }
    for (let i = 1; i < 4; i++) {
      if (!next[i] || !tasks.some((t) => t.id === next[i])) {
        next[i] = tasks[i % tasks.length].id;
      }
    }
    paneSelections.value = next;
  },
  { immediate: true, deep: true }
);

const visiblePaneTaskIds = computed<(number | null)[]>(() => {
  if (layoutMode.value === '1x1') {
    return [store.activeTaskId];
  }
  const count = layoutMode.value === '2x1' ? 2 : 4;
  return paneSelections.value.slice(0, count);
});

watch(
  [layoutMode, visiblePaneTaskIds],
  ([mode, ids]) => {
    if (mode === '1x1') {
      store.disconnectMultiStreams();
      if (store.activeTaskId) {
        store.fetchZonesForTask(store.activeTaskId);
      }
    } else {
      const validIds = ids.filter((id): id is number => typeof id === 'number' && id > 0);
      store.setMultiStreamTasks(validIds);
    }
  },
  { immediate: true }
);

function handlePaneTaskChange(paneIdx: number, e: Event) {
  const target = e.target as HTMLSelectElement;
  const val = target.value ? parseInt(target.value, 10) : null;
  const next = [...paneSelections.value];
  next[paneIdx] = val !== null && !Number.isNaN(val) ? val : null;
  paneSelections.value = next;
}

function getPaneFrame(taskId: number | null): TrackFrame | null {
  if (!taskId || !shouldShowVideoFeed(taskId)) return null;
  if (taskId === store.activeTaskId) return activeFrame.value;
  return store.multiWsStatus[taskId] ? store.multiFrames[taskId] ?? null : null;
}

function getPaneZones(taskId: number | null): Zone[] {
  if (!taskId) return [];
  if (store.multiZones[taskId]) return store.multiZones[taskId];
  if (taskId === store.activeTaskId) return store.zones;
  return [];
}

function getPaneViolations(taskId: number | null): ViolationEvent[] {
  return taskId && getPaneFrame(taskId) ? store.recentViolationsByTask[taskId] ?? [] : [];
}

function getTaskStatus(taskId: number): string {
  return store.tasks.find((t) => t.id === taskId)?.status || 'idle';
}

function shouldShowVideoFeed(taskId: number): boolean {
  return getTaskStatus(taskId) === 'running';
}

function videoFeedUrl(taskId: number, paneIdx = 0): string {
  const epoch = mjpegEpochs.value[taskId] || 0;
  return `/api/tasks/${taskId}/video_feed?t=${epoch}&pane=${paneIdx}`;
}

function onImgLoad(taskId: number) {
  if (!mjpegReady.value[taskId]) mjpegReady.value = { ...mjpegReady.value, [taskId]: true };
  if (mjpegErrored.value[taskId]) {
    mjpegErrored.value = { ...mjpegErrored.value, [taskId]: false };
  }
}

function onImgError(taskId: number) {
  mjpegErrored.value = { ...mjpegErrored.value, [taskId]: true };
}

function getFrameWidth(taskId: number | null): number {
  return getPaneFrame(taskId)?.frame_width || 1280;
}

function getFrameHeight(taskId: number | null): number {
  return getPaneFrame(taskId)?.frame_height || 720;
}

function isPaneDemo(taskId: number | null): boolean {
  if (!taskId) return true;
  const frame = getPaneFrame(taskId);
  if (frame?.is_demo_simulation !== undefined) return frame.is_demo_simulation;
  if (frame?.metrics?.is_demo_simulation !== undefined) return frame.metrics.is_demo_simulation;
  const task = store.tasks.find((t) => t.id === taskId);
  if (!task) return true;
  return task.video_source === 'demo' || task.config?.detector_type === 'mock';
}

const activeFrame = computed(() => {
  const frame = store.currentFrame;
  return isRunning.value && store.wsConnected && frame?.task_id === store.activeTaskId ? frame : null;
});
const primaryMetrics = computed(() => activeFrame.value?.metrics ?? store.systemMetrics);
const pendingViolations = computed(() =>
  store.unacknowledgedViolations.filter((event) => event.task_id === store.activeTaskId).length,
);
const detectorLabel = computed(() => {
  const detector = store.activeTask?.config.detector_type;
  if (detector === 'tensorrt') return 'TensorRT';
  if (detector === 'onnx') return 'ONNX';
  return 'YOLO';
});
const isPrimarySimulation = computed(() => store.activeTask?.video_source === 'demo' || store.activeTask?.config.detector_type === 'mock');
const sourceLabel = computed(() => {
  const source = store.activeTask?.video_source;
  if (source === 'demo:violations') return '内置模拟视频 · 逆行、越界和滞留';
  if (source === 'demo') return '内置模拟视频';
  return source || '尚未选择视频源';
});
const statusLabel = computed(() => formatTaskStatus(store.activeTask?.status));
const connectionLabel = computed(() => {
  if (!isRunning.value) return '跟踪未启动';
  if (store.wsConnected) return '跟踪数据已连接';
  if (store.wsError) return '跟踪数据连接异常';
  return '等待跟踪数据';
});

function formatTaskStatus(status?: string) {
  switch (status) {
    case 'running': return '运行中';
    case 'paused': return '已暂停';
    case 'stopped': return '已停止';
    case 'error': return '任务异常';
    case 'idle': return '待启动';
    default: return '未选择';
  }
}

function previewTitle(taskId: number) {
  if (mjpegErrored.value[taskId]) return '视频预览连接异常';
  if (getTaskStatus(taskId) === 'error') return '任务运行异常';
  return shouldShowVideoFeed(taskId) ? '正在等待视频画面' : '视频尚未开始';
}

function previewHint(taskId: number) {
  if (mjpegErrored.value[taskId]) return '可重新连接视频预览。';
  if (getTaskStatus(taskId) === 'error') return '请检查视频来源和检测模型。';
  if (shouldShowVideoFeed(taskId)) return '首帧到达后将在此显示。';
  return taskId === store.activeTaskId ? '选择上方的「开始跟踪」启动任务。' : '可在任务管理中启动此任务。';
}

const isRoiRulesEnabled = computed(() => {
  return Boolean(store.activeTask?.config?.enable_roi_rules);
});

async function applyActiveConfig(patch: Partial<StreamConfig>) {
  if (!store.activeTaskId || savingConfig.value || taskActionBusy.value) return;
  savingConfig.value = true;
  try {
    const tid = store.activeTaskId;
    const updated = await store.updateTaskConfig(tid, patch);
    if (updated) {
      latencyHistory.value = [];
      resetPreview(tid);
    }
  } finally {
    savingConfig.value = false;
  }
}

async function handleToggleRoiRules(e: Event) {
  const checked = (e.target as HTMLInputElement).checked;
  store.displaySettings.showZones = checked;
  await applyActiveConfig({ enable_roi_rules: checked });
}

function formatViolationTitle(type: string): string {
  switch (type) {
    case 'wrong_way':
    case 'wrong-way':
      return '逆行违章 (Wrong-Way)';
    case 'intrusion':
      return '区域越界侵入 (Intrusion)';
    case 'loitering':
      return '禁停超时滞留 (Loitering)';
    default:
      return '交通违章预警';
  }
}

function resetPreview(taskId: number) {
  mjpegErrored.value = { ...mjpegErrored.value, [taskId]: false };
  mjpegReady.value = { ...mjpegReady.value, [taskId]: false };
  mjpegEpochs.value = { ...mjpegEpochs.value, [taskId]: (mjpegEpochs.value[taskId] ?? 0) + 1 };
}

function focusTask(id: number) {
  layoutMode.value = '1x1';
  store.setActiveTask(id);
  latencyHistory.value = [];
  resetPreview(id);
  void store.fetchViolations();
}

function handleTaskChange(event: Event) {
  const id = Number((event.target as HTMLSelectElement).value);
  if (!id) return;
  store.taskError = null;
  focusTask(id);
}

async function toggleTracking() {
  const id = store.activeTaskId;
  if (!id || taskActionBusy.value) return;
  taskActionBusy.value = true;
  try {
    if (isRunning.value) {
      if (await store.stopTask(id)) {
        latencyHistory.value = [];
        resetPreview(id);
      }
    } else {
      latencyHistory.value = [];
      resetPreview(id);
      if (await store.startTask(id)) void store.fetchViolations();
    }
  } finally {
    taskActionBusy.value = false;
  }
}

async function startDemo() {
  if (taskActionBusy.value || savingConfig.value) return;
  taskActionBusy.value = true;
  store.taskError = null;
  try {
    const task = await store.prepareViolationDemo();
    if (task) {
      focusTask(task.id);
      if (!task.config.enable_roi_rules) {
        const updated = await store.updateTaskConfig(task.id, { enable_roi_rules: true });
        if (!updated) return;
      }
      store.displaySettings.showZones = true;
      if (task.status !== 'running' && await store.startTask(task.id)) void store.fetchViolations();
    }
  } finally {
    taskActionBusy.value = false;
  }
}

async function handleDetectorTypeChange(e: Event) {
  const newType = (e.target as HTMLSelectElement).value as DetectorType;
  const cfg = store.activeTask?.config;
  if (!cfg) return;

  const extMap: Record<string, string> = {
    tensorrt: '.engine',
    yolo: '.pt',
    onnx: '.onnx',
  };
  const targetExt = extMap[newType];
  let nextModelPath = cfg.model_path || 'data/weights/yolov8s.pt';

  if (targetExt) {
    const curNorm = nextModelPath.replace(/\\/g, '/');
    const candidate = curNorm.replace(/\.(pt|onnx|engine)$/i, targetExt);
    const localWeights = (store.localAssets.weights || []).map((w) => w.replace(/\\/g, '/'));
    if (localWeights.includes(candidate)) {
      nextModelPath = candidate;
    } else {
      const sameStem = localWeights.find((w) => w.toLowerCase().endsWith(targetExt));
      nextModelPath = sameStem || `data/weights/yolov8s${targetExt}`;
    }
  }

  await applyActiveConfig({
    detector_type: newType,
    model_path: nextModelPath,
  });
}

function handleReconnect() {
  if (!store.activeTaskId) return;
  const tid = store.activeTaskId;
  resetPreview(tid);
  store.connectWebSocket(tid);
  if (layoutMode.value !== '1x1') {
    for (const id of visiblePaneTaskIds.value) {
      if (id) {
        if (id !== tid) resetPreview(id);
        store.connectTaskWs(id);
      }
    }
  }
}

function getTrackColor(t: TrackItem, isViolator: boolean): { stroke: string; fill: string } {
  if (isViolator) {
    return { stroke: '#f43f5e', fill: 'rgba(244, 63, 94, 0.22)' };
  }
  const cls = (t.class_name || '').toLowerCase();
  if (cls === 'vehicle' || cls === 'car' || cls === 'truck' || cls === 'bus') {
    return { stroke: '#38bdf8', fill: 'rgba(56, 189, 248, 0.10)' };
  }
  if (cls === 'pedestrian' || cls === 'person') {
    return { stroke: '#34d399', fill: 'rgba(52, 211, 153, 0.10)' };
  }
  if (cls === 'motorcycle' || cls === 'bicycle') {
    return { stroke: '#fbbf24', fill: 'rgba(251, 191, 36, 0.12)' };
  }
  return { stroke: '#a78bfa', fill: 'rgba(167, 139, 250, 0.10)' };
}

function drawPaneOverlay(paneIdx: number, taskId: number | null) {
  const canvas = canvasRefs.value[paneIdx];
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  if (!taskId || getTaskStatus(taskId) !== 'running') return;

  const frame = getPaneFrame(taskId);
  const zones = getPaneZones(taskId);
  const uiScale = Math.max(1.0, w / 960);

  if (store.displaySettings.showZones) {
    for (const zone of zones) {
      drawZone(ctx, zone);
    }
  }

  const tracks = frame?.tracks || [];
  const activeViolations = getPaneViolations(taskId);
  const activeViolatorIds = new Set(activeViolations.map((v) => v.track_id));

  for (const t of tracks) {
    const isViolator = activeViolatorIds.has(t.track_id);
    const colors = getTrackColor(t, isViolator);

    if (store.displaySettings.showTrails && t.trail && t.trail.length > 1) {
      ctx.beginPath();
      ctx.strokeStyle = isViolator ? 'rgba(244, 63, 94, 0.85)' : colors.stroke;
      ctx.lineWidth = Math.max(2, Math.round((isViolator ? 3 : 2.2) * uiScale));
      for (let i = 0; i < t.trail.length; i++) {
        const pt = t.trail[i];
        if (i === 0) ctx.moveTo(pt[0], pt[1]);
        else ctx.lineTo(pt[0], pt[1]);
      }
      ctx.stroke();
    }

    const [x1, y1, x2, y2] = t.bbox;
    const bw = Math.max(1, x2 - x1);
    const bh = Math.max(1, y2 - y1);

    if (store.displaySettings.showBBoxes) {
      ctx.strokeStyle = colors.stroke;
      ctx.fillStyle = colors.fill;
      ctx.lineWidth = Math.max(2, Math.round((isViolator ? 3.5 : 2.5) * uiScale));
      ctx.fillRect(x1, y1, bw, bh);
      ctx.strokeRect(x1, y1, bw, bh);
    }

    if (store.displaySettings.showVectors && t.direction) {
      const cx = x1 + bw * 0.5;
      const cy = y1 + bh * 0.5;
      const rawDx = t.direction[0];
      const rawDy = t.direction[1];
      const mag = Math.hypot(rawDx, rawDy);
      if (mag > 1e-3) {
        const scale = mag <= 1.5 ? (42 * uiScale) / mag : Math.min(0.45, (64 * uiScale) / mag);
        const endX = cx + rawDx * scale;
        const endY = cy + rawDy * scale;

        ctx.strokeStyle = isViolator ? '#f43f5e' : '#c084fc';
        ctx.fillStyle = isViolator ? '#f43f5e' : '#c084fc';
        ctx.lineWidth = Math.max(2, Math.round(2 * uiScale));
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(endX, endY);
        ctx.stroke();

        const angle = Math.atan2(endY - cy, endX - cx);
        const headLen = Math.round(7 * uiScale);
        ctx.beginPath();
        ctx.moveTo(endX, endY);
        ctx.lineTo(endX - headLen * Math.cos(angle - Math.PI / 6), endY - headLen * Math.sin(angle - Math.PI / 6));
        ctx.lineTo(endX - headLen * Math.cos(angle + Math.PI / 6), endY - headLen * Math.sin(angle + Math.PI / 6));
        ctx.closePath();
        ctx.fill();
      }
    }

    if (store.displaySettings.showLabels && store.displaySettings.showBBoxes) {
      const fontSize = Math.round(16 * uiScale);
      const padX = Math.round(8 * uiScale);
      const bannerH = Math.round(fontSize * 1.45);
      const label = `ID:${t.track_id} ${t.class_name} ${(t.conf * 100).toFixed(0)}% ${t.speed.toFixed(1)}km/h`;
      ctx.font = `bold ${fontSize}px monospace`;
      const textWidth = ctx.measureText(label).width;
      const bannerW = textWidth + padX * 2;
      const bannerX = Math.max(0, Math.min(x1, w - bannerW));
      const bannerY = Math.max(0, y1 - bannerH);

      ctx.fillStyle = isViolator ? 'rgba(225, 29, 72, 0.94)' : 'rgba(15, 23, 42, 0.88)';
      ctx.fillRect(bannerX, bannerY, bannerW, bannerH);

      ctx.fillStyle = '#ffffff';
      ctx.fillText(label, bannerX + padX, bannerY + Math.round(fontSize * 1.08));

      if (isViolator) {
        const alertFont = Math.round(14 * uiScale);
        ctx.fillStyle = '#fb7185';
        ctx.font = `bold ${alertFont}px sans-serif`;
        ctx.fillText('\u{1F6A8} VIOLATION!', bannerX, Math.max(alertFont + 2, bannerY - Math.round(4 * uiScale)));
      }
    }
  }

  for (const v of activeViolations) {
    if (!v.bbox || v.bbox.length < 4) continue;
    const [vx1, vy1, vx2, vy2] = v.bbox;
    ctx.save();
    ctx.strokeStyle = 'rgba(244, 63, 94, 0.95)';
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    ctx.strokeRect(vx1 - 3, vy1 - 3, vx2 - vx1 + 6, vy2 - vy1 + 6);
    ctx.restore();
  }
}

function drawZone(ctx: CanvasRenderingContext2D, zone: Zone) {
  if (!zone.polygon || zone.polygon.length < 3) return;

  ctx.save();
  ctx.beginPath();
  zone.polygon.forEach((pt, idx) => {
    if (idx === 0) ctx.moveTo(pt[0], pt[1]);
    else ctx.lineTo(pt[0], pt[1]);
  });
  ctx.closePath();

  let strokeColor = 'rgba(245, 158, 11, 0.8)';
  let fillColor = 'rgba(245, 158, 11, 0.10)';
  let labelColor = '#fbbf24';

  if (zone.zone_type === 'intrusion') {
    strokeColor = 'rgba(239, 68, 68, 0.85)';
    fillColor = 'rgba(239, 68, 68, 0.14)';
    labelColor = '#f87171';
    ctx.setLineDash([8, 6]);
  } else if (zone.zone_type === 'lane') {
    strokeColor = 'rgba(16, 185, 129, 0.75)';
    fillColor = 'rgba(16, 185, 129, 0.08)';
    labelColor = '#34d399';
    ctx.setLineDash([5, 4]);
  } else {
    ctx.setLineDash([6, 4]);
  }

  ctx.strokeStyle = strokeColor;
  ctx.fillStyle = fillColor;
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
  ctx.setLineDash([]);

  const anchor = zone.polygon[0];
  ctx.font = 'bold 12px sans-serif';
  ctx.fillStyle = 'rgba(15, 23, 42, 0.72)';
  const titleW = ctx.measureText(zone.name).width;
  ctx.fillRect(anchor[0] + 4, anchor[1] + 4, titleW + 10, 18);
  ctx.fillStyle = labelColor;
  ctx.fillText(zone.name, anchor[0] + 9, anchor[1] + 17);

  if (zone.zone_type === 'lane' && zone.direction_vector && zone.direction_vector.length >= 2) {
    const cx = zone.polygon.reduce((acc, p) => acc + p[0], 0) / zone.polygon.length;
    const cy = zone.polygon.reduce((acc, p) => acc + p[1], 0) / zone.polygon.length;
    const [dx, dy] = zone.direction_vector;
    const mag = Math.hypot(dx, dy) || 1;
    const ux = (dx / mag) * 36;
    const uy = (dy / mag) * 36;
    ctx.strokeStyle = 'rgba(52, 211, 153, 0.85)';
    ctx.fillStyle = 'rgba(52, 211, 153, 0.85)';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(cx - ux * 0.5, cy - uy * 0.5);
    ctx.lineTo(cx + ux * 0.5, cy + uy * 0.5);
    ctx.stroke();

    const angle = Math.atan2(uy, ux);
    ctx.beginPath();
    ctx.moveTo(cx + ux * 0.5, cy + uy * 0.5);
    ctx.lineTo(cx + ux * 0.5 - 8 * Math.cos(angle - Math.PI / 6), cy + uy * 0.5 - 8 * Math.sin(angle - Math.PI / 6));
    ctx.lineTo(cx + ux * 0.5 - 8 * Math.cos(angle + Math.PI / 6), cy + uy * 0.5 - 8 * Math.sin(angle + Math.PI / 6));
    ctx.closePath();
    ctx.fill();
  }

  ctx.restore();
}

function renderLoop() {
  const panes = visiblePaneTaskIds.value;
  for (let i = 0; i < panes.length; i++) {
    drawPaneOverlay(i, panes[i]);
  }
  animationId = requestAnimationFrame(renderLoop);
}

onMounted(() => {
  const now = Date.now();
  const nextEpochs: Record<number, number> = { ...mjpegEpochs.value };
  const nextErrored: Record<number, boolean> = { ...mjpegErrored.value };
  for (const t of store.tasks) {
    if (t.status === 'running') {
      nextEpochs[t.id] = now;
      nextErrored[t.id] = false;
    }
  }
  mjpegEpochs.value = nextEpochs;
  mjpegErrored.value = nextErrored;
  if (store.activeTaskId && store.activeTask?.status === 'running' && !store.wsConnected) {
    store.connectWebSocket(store.activeTaskId);
  }
  animationId = requestAnimationFrame(renderLoop);
});

onUnmounted(() => {
  if (animationId) {
    cancelAnimationFrame(animationId);
  }
  store.disconnectMultiStreams();
});
</script>
