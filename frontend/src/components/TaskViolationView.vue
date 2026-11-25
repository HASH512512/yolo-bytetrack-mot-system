<template>
  <div class="min-w-0">
    <!-- Left Column: Task CRUD Management (5 cols) -->
    <div v-if="view === 'tasks'" class="space-y-4">
      <div class="card space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <h2 class="text-base font-semibold text-slate-100">已保存任务</h2>
            <span class="badge badge-blue text-xs">{{ store.tasks.length }} 个任务</span>
          </div>
          <button @click="openCreateModal" class="btn btn-primary text-xs py-1.5 px-3">
            <span>+ 新建任务</span>
          </button>
        </div>

        <div
          v-if="store.taskError"
          class="p-2.5 rounded-lg bg-rose-950/60 border border-rose-700/70 text-rose-200 text-xs flex items-center justify-between gap-2"
        >
          <span>{{ store.taskError }}</span>
          <button @click="store.taskError = null" class="text-rose-400 hover:text-rose-200">&#x2715;</button>
        </div>

        <!-- Task List -->
        <div class="space-y-2 max-h-[640px] overflow-y-auto pr-1">
          <div
            v-for="task in store.tasks"
            :key="task.id"
            class="p-3 rounded-lg border transition-colors"
            :class="
              store.activeTaskId === task.id
                ? 'bg-slate-800/90 border-teal-800'
                : 'bg-slate-800/40 border-slate-700/60 hover:bg-slate-800/70'
            "
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-medium text-sm text-slate-100 truncate">{{ task.name }}</span>
                  <span
                    v-if="store.activeTaskId === task.id"
                    class="badge badge-green text-[10px]"
                  >
                    <span>当前监控</span>
                  </span>
                </div>
                <div class="text-xs text-slate-400 mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  <span class="truncate max-w-[220px]" :title="task.video_source">源: {{ task.video_source }}</span>
                  <span class="uppercase text-cyan-300">{{ task.config.detector_type || 'mock' }}</span>
                  <span v-if="task.config.adaptive_skip" class="text-emerald-300">
                    <span>跳帧&le;{{ task.config.max_skip_stride || 2 }}</span>
                  </span>
                </div>
                <div v-if="task.config.model_path" class="text-[11px] text-slate-500 mt-0.5 truncate">
                  <span>权重: {{ task.config.model_path }}</span>
                </div>
                <details class="mt-2 text-xs text-slate-400">
                  <summary class="cursor-pointer">跟踪算法配置</summary>
                  <div class="flex flex-wrap gap-1 mt-1.5">
                    <span class="badge py-0 px-1.5" :class="task.config.use_class_aware !== false ? 'badge-blue' : 'badge-gray'">
                      <span>ClassAware</span>
                    </span>
                    <span class="badge py-0 px-1.5" :class="task.config.use_nsa ? 'badge-blue' : 'badge-gray'">
                      <span>NSA</span>
                    </span>
                    <span class="badge py-0 px-1.5" :class="task.config.use_diou ? 'badge-blue' : 'badge-gray'">
                      <span>DIoU</span>
                    </span>
                    <span class="badge py-0 px-1.5" :class="task.config.use_vdc !== false ? 'badge-blue' : 'badge-gray'">
                      <span>VDC</span>
                    </span>
                    <span class="badge py-0 px-1.5" :class="task.config.use_oru !== false ? 'badge-blue' : 'badge-gray'">
                      <span>ORU</span>
                    </span>
                    <span class="badge py-0 px-1.5" :class="task.config.use_lazy_appearance !== false ? 'badge-blue' : 'badge-gray'">
                      <span>LazyHSV</span>
                    </span>
                  </div>
                </details>
              </div>

              <!-- Status Badge -->
              <span
                class="badge text-[10px] shrink-0"
                :class="
                  task.status === 'running'
                    ? 'badge-green'
                    : task.status === 'paused'
                    ? 'badge-amber'
                    : task.status === 'error'
                    ? 'badge-red'
                    : 'badge-gray'
                "
              >
                {{ formatStatus(task.status) }}
              </span>
            </div>

            <!-- Task Action Buttons -->
            <div class="flex flex-wrap items-center justify-end gap-2 mt-3 pt-2 border-t border-slate-700/40">
              <button type="button" class="btn btn-secondary text-xs" @click="openMonitor(task.id)">打开监控</button>
              <button
                v-if="task.status !== 'running'"
                @click.stop="store.startTask(task.id)"
                class="btn btn-success text-xs py-1 px-2.5"
              >
                <span>启动</span>
              </button>
              <button
                v-else
                @click.stop="store.stopTask(task.id)"
                class="btn btn-secondary text-xs py-1 px-2.5"
              >
                <span>停止</span>
              </button>
              <button
                @click.stop="store.deleteTask(task.id)"
                class="btn btn-danger text-xs py-1 px-2.5"
              >
                <span>删除</span>
              </button>
            </div>
          </div>

          <div v-if="store.tasks.length === 0" class="text-center py-8 text-slate-500 text-xs">
            <span>暂无任务，请点击上方按钮创建</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Right Column: Violations Alert & Audit Log (7 cols) -->
    <div v-else class="space-y-4">
      <div class="card space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <h2 class="text-base font-semibold text-slate-100">事件记录</h2>
            <span
              v-if="store.unacknowledgedViolations.length > 0"
              class="badge badge-red text-xs animate-pulse"
            >
              <span>{{ store.unacknowledgedViolations.length }} 条待确认</span>
            </span>
          </div>

          <label class="flex flex-wrap items-center gap-2 text-sm text-slate-300">
            任务
            <select class="workspace-field" :value="store.activeTaskId ?? ''" @change="selectEventTask">
              <option value="" disabled>请选择任务</option>
              <option v-for="task in store.tasks" :key="task.id" :value="task.id">{{ task.name }}</option>
            </select>
          </label>
        </div>
        <div class="flex flex-wrap gap-2">
          <!-- Filter Tabs -->
          <div class="flex flex-wrap items-center gap-1 bg-slate-800 p-1 rounded-lg text-xs">
            <button
              v-for="f in filterOptions"
              :key="f.value"
              @click="activeFilter = f.value"
              class="px-2.5 py-1 rounded transition-colors"
              :class="
                activeFilter === f.value
                  ? 'bg-indigo-600 text-white font-medium'
                  : 'text-slate-400 hover:text-slate-200'
              "
            >
              {{ f.label }}
            </button>
          </div>
        </div>

        <!-- Violations List -->
        <div class="space-y-2 max-h-[640px] overflow-y-auto pr-1">
          <div
            v-for="v in filteredViolations"
            :key="v.id || `${v.track_id}-${v.frame_id}-${v.violation_type}`"
            class="p-3 rounded-lg border flex flex-col md:flex-row md:items-center justify-between gap-3 transition-colors"
            :class="
              v.acknowledged
                ? 'bg-slate-800/30 border-slate-700/50 opacity-75'
                : 'bg-rose-950/20 border-rose-900/60 shadow-sm'
            "
          >
            <div class="flex items-start gap-3">
              <span class="text-xl mt-0.5">
                {{ getViolationIcon(v.violation_type) }}
              </span>
              <div>
                <div class="flex flex-wrap items-center gap-2">
                  <span
                    class="badge text-[11px]"
                    :class="getViolationBadgeClass(v.violation_type)"
                  >
                    {{ formatViolationType(v.violation_type) }}
                  </span>
                  <span class="text-xs font-mono text-slate-300">Track #{{ v.track_id }}</span>
                  <span class="text-[11px] text-slate-500">Frame {{ v.frame_id }}</span>
                </div>
                <p class="text-xs text-slate-300 mt-1">
                  <span>{{ v.details?.note || '轨迹触发规则引擎违章判定阈值' }}</span>
                  <span v-if="v.details?.speed" class="text-amber-400 ml-1">
                    ({{ v.details.speed }} km/h)
                  </span>
                </p>
                <div class="text-[11px] text-slate-500 mt-0.5">
                  时间: {{ formatTime(v.timestamp) }}
                </div>
              </div>
            </div>

            <!-- Action buttons -->
            <div class="flex items-center gap-2 self-end md:self-center">
              <button
                v-if="!v.acknowledged && v.id"
                @click="store.acknowledgeViolation(v.id)"
                class="btn btn-primary text-xs py-1 px-2.5"
              >
                <span>&#x2713; 确认已处置</span>
              </button>
              <span v-else class="text-xs text-emerald-400 font-medium px-2">
                <span>已归档确认</span>
              </span>
              <button
                v-if="v.id"
                @click="store.deleteViolation(v.id)"
                class="text-slate-500 hover:text-rose-400 p-1 rounded text-xs transition-colors"
                title="删除记录 (Delete)"
              >
                <span>&#x2715;</span>
              </button>
            </div>
          </div>

          <div
            v-if="filteredViolations.length === 0"
            class="text-center py-10 text-slate-500 text-xs"
          >
            <span>当前筛选条件下无违章事件</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Task Modal -->
    <dialog v-if="view === 'tasks'" ref="createDialog" class="source-dialog" aria-labelledby="task-dialog-title" @cancel="cancelCreate">
      <div class="space-y-4">
        <div class="flex items-center justify-between border-b border-slate-800 pb-3">
          <h2 id="task-dialog-title" class="text-base font-semibold text-slate-100">新建跟踪任务</h2>
          <button type="button" class="btn btn-secondary" :disabled="creatingTask" @click="createDialog?.close()">关闭</button>
        </div>

        <form @submit.prevent="handleCreateTask" class="space-y-3 text-xs">
          <div>
            <label for="task-name" class="block text-slate-300 mb-1 font-medium">任务名称</label>
            <input
              id="task-name"
              v-model="newTaskForm.name"
              type="text"
              required
              autofocus
              maxlength="100"
              placeholder="例如: 校园东门主干道监控 (CAM-02)"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <!-- Local Video / Dataset / Demo Picker -->
          <div class="space-y-1.5">
            <label class="block text-slate-300 font-medium">视频或图像序列输入源</label>
            <select
              v-model="videoSourcePreset"
              @change="onVideoPresetChange"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              <optgroup label="演示与实时设备 (Demo / Device)">
                <option value="demo">仿真演示流 (demo - Campus Simulator)</option>
                <option value="0">本地摄像头 (Device 0)</option>
                <option value="rtsp://127.0.0.1:8554/live">RTSP 实时视频流模板</option>
              </optgroup>
              <optgroup v-if="store.localAssets.videos.length > 0" label="本地视频文件 (data/videos)">
                <option v-for="v in store.localAssets.videos" :key="`vid-${v}`" :value="v">
                  {{ v }}
                </option>
              </optgroup>
              <optgroup v-if="store.localAssets.datasets.length > 0" label="本地图像序列集 (data/datasets)">
                <option v-for="d in store.localAssets.datasets" :key="`ds-${d}`" :value="d">
                  {{ d }}
                </option>
              </optgroup>
              <option value="__custom__">自定义本地路径 / RTSP URL...</option>
            </select>

            <input
              v-model="newTaskForm.video_source"
              type="text"
              required
              placeholder="输入 demo、本地视频路径、图像序列目录或 rtsp://..."
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
            />
          </div>

          <!-- Detector Backend & Local Weight Picker -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div>
              <label class="block text-slate-300 mb-1 font-medium">检测器推理后端</label>
              <select
                v-model="newTaskForm.detector_type"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                <option value="mock">Mock 仿真检测器 (无需权重)</option>
                <option value="tensorrt">TensorRT 引擎 (.engine · GPU 零拷贝)</option>
                <option value="yolo">Ultralytics YOLO (.pt)</option>
                <option value="onnx">ONNX Runtime 引擎 (.onnx)</option>
              </select>
            </div>

            <div>
              <label class="block text-slate-300 mb-1 font-medium">输入尺寸 (imgsz)</label>
              <select
                v-model.number="newTaskForm.imgsz"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                <option :value="640">640 &times; 640 (标准实时)</option>
                <option :value="960">960 &times; 960 (远距小目标)</option>
                <option :value="1280">1280 &times; 1280 (高清密集)</option>
              </select>
            </div>
          </div>

          <div v-if="newTaskForm.detector_type !== 'mock'" class="space-y-1.5">
            <label class="block text-slate-300 font-medium">
              <span>本地模型权重路径 (仅加载本地已有文件，禁止自动下载)</span>
            </label>
            <select
              v-model="weightPreset"
              @change="onWeightPresetChange"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              <option value="">
                {{
                  store.localAssets.weights.length > 0
                    ? '-- 从 data/weights 选择本地权重 --'
                    : '-- data/weights 暂无扫描到权重，请在下方手动输入路径 --'
                }}
              </option>
              <option v-for="w in store.localAssets.weights" :key="`wt-${w}`" :value="w">
                {{ w }}
              </option>
              <option value="__custom__">自定义权重文件路径...</option>
            </select>
            <input
              v-model="newTaskForm.model_path"
              type="text"
              placeholder="例如: data/weights/yolov8n.pt 或 data/weights/yolov8n.onnx"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
            />
          </div>

          <!-- FPS & Adaptive Keyframe Stride -->
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label class="block text-slate-300 mb-1 font-medium">读取限速 (FPS)</label>
              <input
                v-model.number="newTaskForm.fps"
                type="number"
                min="0"
                max="1000"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label for="track-sample-rate" class="block text-slate-300 mb-1 font-medium">轨迹记录频率 (Hz)</label>
              <input
                id="track-sample-rate"
                v-model.number="newTaskForm.downsample_fps"
                type="number"
                min="1"
                max="25"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label class="block text-slate-300 mb-1 font-medium">置信度阈值</label>
              <input
                v-model.number="newTaskForm.conf_threshold"
                type="number"
                step="0.05"
                min="0.05"
                max="0.95"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label class="block text-slate-300 mb-1 font-medium">最大跳帧步长</label>
              <select
                v-model.number="newTaskForm.max_skip_stride"
                :disabled="!newTaskForm.adaptive_skip"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
              >
                <option :value="1">1 (不跳过)</option>
                <option :value="2">2 (最多跳 1 帧)</option>
                <option :value="3">3 (最多跳 2 帧)</option>
                <option :value="4">4 (最多跳 3 帧)</option>
              </select>
            </div>
          </div>

          <!-- Adaptive Keyframe Detection Toggle -->
          <div class="p-2.5 rounded-lg bg-slate-800/60 border border-slate-700/60">
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" v-model="newTaskForm.adaptive_skip" class="accent-indigo-500 rounded" />
              <span class="text-slate-200 font-medium">启用自适应关键帧跳帧检测 (Adaptive Keyframe + 卡尔曼补帧)</span>
            </label>
            <p class="text-[11px] text-slate-400 mt-1 pl-5">
              <span>在目标平稳运动且远离 ROI 边界时跳过检测器前向，使用 Kalman predict_only 保持连续跟踪并统计跳过率。</span>
            </p>
          </div>

          <!-- ByteTrack Algorithm Ablation Switches -->
          <div class="border-t border-slate-800 pt-3 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-slate-300 font-medium">ByteTrack 增强模块配置</span>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  @click="applyPreset('full')"
                  class="text-[11px] text-indigo-400 hover:text-indigo-300 underline"
                >
                  <span>全增强 (Full)</span>
                </button>
                <button
                  type="button"
                  @click="applyPreset('baseline')"
                  class="text-[11px] text-slate-400 hover:text-slate-200 underline"
                >
                  <span>标准基线 (Baseline)</span>
                </button>
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="newTaskForm.use_class_aware" class="accent-indigo-500 rounded" />
                <span class="text-slate-300">Class-Aware 类别感知匹配</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="newTaskForm.use_nsa" class="accent-indigo-500 rounded" />
                <span class="text-slate-300">NSA-Kalman 噪声自适应</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="newTaskForm.use_diou" class="accent-indigo-500 rounded" />
                <span class="text-slate-300">DIoU 中心距代价矩阵</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="newTaskForm.use_vdc" class="accent-indigo-500 rounded" />
                <span class="text-slate-300">VDC 速度方向一致性</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="newTaskForm.use_oru" class="accent-indigo-500 rounded" />
                <span class="text-slate-300">ORU 观测为中心遮挡恢复</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="newTaskForm.use_lazy_appearance" class="accent-indigo-500 rounded" />
                <span class="text-slate-300">Lazy HSV 惰性外观门控</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer sm:col-span-2">
                <input type="checkbox" v-model="newTaskForm.use_recovery_pool" class="accent-indigo-500 rounded" />
                <span class="text-slate-300">标准 ByteTrack 丢失恢复池 (30帧轨迹维持)</span>
              </label>
            </div>
          </div>

          <div
            v-if="createError"
            class="p-2 rounded bg-rose-950/60 border border-rose-700 text-rose-200 text-xs"
          >
            {{ createError }}
          </div>

          <div class="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
            <button
              type="button"
              :disabled="creatingTask"
              @click="createDialog?.close()"
              class="btn btn-secondary text-xs py-1.5 px-3"
            >
              <span>取消</span>
            </button>
            <button type="submit" class="btn btn-primary text-xs py-1.5 px-4" :disabled="creatingTask">
              {{ creatingTask ? '正在创建…' : '创建任务' }}
            </button>
          </div>
        </form>
      </div>
    </dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useMotStore } from '../stores/motStore';

defineProps<{ view: 'tasks' | 'violations' }>();

const store = useMotStore();
const createDialog = ref<HTMLDialogElement | null>(null);
const creatingTask = ref(false);
const activeFilter = ref('all');
const createError = ref<string | null>(null);
const videoSourcePreset = ref('demo');
const weightPreset = ref('');

const filterOptions = [
  { label: '全部违章', value: 'all' },
  { label: '逆行', value: 'wrong_way' },
  { label: '越界', value: 'intrusion' },
  { label: '滞留', value: 'loitering' },
  { label: '待确认', value: 'unack' },
];

const newTaskForm = ref({
  name: '',
  video_source: 'demo',
  detector_type: 'mock',
  model_path: '',
  imgsz: 640,
  conf_threshold: 0.35,
  iou_threshold: 0.5,
  fps: 25,
  downsample_fps: 5.0,
  adaptive_skip: false,
  max_skip_stride: 2,
  use_class_aware: true,
  use_nsa: true,
  use_diou: true,
  use_vdc: true,
  use_oru: true,
  use_lazy_appearance: true,
  use_recovery_pool: true,
});

function isWrongWay(type: string): boolean {
  return type === 'wrong_way' || type === 'wrong-way';
}

const filteredViolations = computed(() => {
  const list = store.violations.filter((event) => event.task_id === store.activeTaskId);
  if (activeFilter.value === 'all') return list;
  if (activeFilter.value === 'unack') return list.filter((v) => !v.acknowledged);
  if (activeFilter.value === 'wrong_way') {
    return list.filter((v) => isWrongWay(v.violation_type));
  }
  return list.filter((v) => v.violation_type === activeFilter.value);
});

function openMonitor(id: number) {
  store.taskError = null;
  store.setActiveTask(id);
  void store.fetchViolations();
  store.activeTab = 'monitoring';
}

function selectEventTask(event: Event) {
  const id = Number((event.target as HTMLSelectElement).value);
  if (!id) return;
  store.setActiveTask(id);
  void store.fetchViolations();
}

function openCreateModal() {
  createError.value = null;
  void store.fetchLocalAssets();
  createDialog.value?.showModal();
}

function cancelCreate(event: Event) {
  if (creatingTask.value) event.preventDefault();
}

function onVideoPresetChange() {
  if (videoSourcePreset.value !== '__custom__') {
    newTaskForm.value.video_source = videoSourcePreset.value;
  }
}

function onWeightPresetChange() {
  if (weightPreset.value && weightPreset.value !== '__custom__') {
    newTaskForm.value.model_path = weightPreset.value;
  }
}

function applyPreset(preset: 'full' | 'baseline') {
  const enabled = preset === 'full';
  newTaskForm.value.use_class_aware = enabled;
  newTaskForm.value.use_nsa = enabled;
  newTaskForm.value.use_diou = enabled;
  newTaskForm.value.use_vdc = enabled;
  newTaskForm.value.use_oru = enabled;
  newTaskForm.value.use_lazy_appearance = enabled;
  newTaskForm.value.use_recovery_pool = true;
}

function formatStatus(status: string): string {
  switch (status) {
    case 'running':
      return '运行中';
    case 'paused':
      return '已暂停';
    case 'stopped':
      return '已停止';
    case 'error':
      return '异常';
    default:
      return '就绪';
  }
}

function getViolationIcon(type: string): string {
  if (isWrongWay(type)) return '\u26D4';
  if (type === 'intrusion') return '\u{1F6B7}';
  return '\u{1F6D1}';
}

function getViolationBadgeClass(type: string): string {
  if (isWrongWay(type)) return 'badge-red';
  if (type === 'intrusion') return 'badge-amber';
  return 'badge-blue';
}

function formatViolationType(type: string): string {
  if (isWrongWay(type)) return '车辆逆行';
  switch (type) {
    case 'intrusion':
      return '违规越界';
    case 'loitering':
      return '超时滞留';
    default:
      return '交通违规';
  }
}

function formatTime(ts: number): string {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

async function handleCreateTask() {
  createError.value = null;
  if (!newTaskForm.value.name.trim() || creatingTask.value) return;
  creatingTask.value = true;
  const f = newTaskForm.value;
  const created = await store.createTask({
    name: f.name.trim(),
    video_source: f.video_source.trim() || 'demo',
    config: {
      video_source: f.video_source.trim() || 'demo',
      detector_type: f.detector_type,
      model_path: f.detector_type === 'mock' ? null : (f.model_path.trim() || null),
      imgsz: f.imgsz,
      conf_threshold: f.conf_threshold,
      iou_threshold: f.iou_threshold,
      fps: f.fps,
      downsample_fps: f.downsample_fps,
      adaptive_skip: f.adaptive_skip,
      max_skip_stride: f.adaptive_skip ? f.max_skip_stride : 1,
      use_class_aware: f.use_class_aware,
      use_nsa: f.use_nsa,
      use_diou: f.use_diou,
      use_vdc: f.use_vdc,
      use_oru: f.use_oru,
      use_lazy_appearance: f.use_lazy_appearance,
      use_recovery_pool: f.use_recovery_pool,
    },
  });
  creatingTask.value = false;
  if (created) {
    createDialog.value?.close();
    newTaskForm.value.name = '';
  } else {
    createError.value = store.taskError || '创建任务失败，请检查输入参数或本地路径';
  }
}
</script>
