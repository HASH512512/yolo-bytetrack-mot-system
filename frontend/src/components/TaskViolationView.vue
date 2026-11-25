<template>
  <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
    <!-- Left Column: Task CRUD Management (5 cols) -->
    <div class="lg:col-span-5 space-y-4">
      <div class="card space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="text-base font-bold text-slate-100">监控任务管理</span>
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
            class="p-3 rounded-lg border transition-all cursor-pointer"
            :class="
              store.activeTaskId === task.id
                ? 'bg-slate-800/90 border-indigo-500 shadow-md shadow-indigo-500/10'
                : 'bg-slate-800/40 border-slate-700/60 hover:bg-slate-800/70'
            "
            @click="store.setActiveTask(task.id)"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-medium text-sm text-slate-100 truncate">{{ task.name }}</span>
                  <span
                    v-if="store.activeTaskId === task.id"
                    class="badge badge-indigo text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/30"
                  >
                    <span>当前激活</span>
                  </span>
                </div>
                <div class="text-xs text-slate-400 mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  <span class="truncate max-w-[220px]" :title="task.video_source">源: {{ task.video_source }}</span>
                  <span class="uppercase text-cyan-300">{{ task.config.detector_type || 'yolo' }}</span>
                  <span>{{ task.config.fps > 0 ? `${task.config.fps} FPS` : '自适应FPS' }}</span>
                  <span v-if="task.config.adaptive_skip" class="text-emerald-300">
                    <span>跳帧&le;{{ task.config.max_skip_stride || 2 }}</span>
                  </span>
                </div>
                <div v-if="task.config.model_path" class="text-[11px] text-slate-500 mt-0.5 truncate">
                  <span>权重: {{ task.config.model_path }}</span>
                </div>
                <div class="flex flex-wrap gap-1 mt-1.5 text-[10px]">
                  <span
                    v-if="task.config.use_gmc && task.config.gmc_method !== 'none'"
                    class="badge badge-amber py-0 px-1.5"
                  >
                    <span>GMC:{{ task.config.gmc_method || 'sparse_optflow' }}</span>
                  </span>
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
                  <span class="badge py-0 px-1.5" :class="task.config.use_lazy_appearance ? 'badge-blue' : 'badge-gray'">
                    <span>LazyHSV</span>
                  </span>
                </div>
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
            <div class="flex items-center justify-end gap-2 mt-3 pt-2 border-t border-slate-700/40">
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
    <div class="lg:col-span-7 space-y-4">
      <div class="card space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <span class="text-base font-bold text-slate-100">违章事件预警看板</span>
            <span
              v-if="store.unacknowledgedViolations.length > 0"
              class="badge badge-red text-xs animate-pulse"
            >
              <span>{{ store.unacknowledgedViolations.length }} 条待确认</span>
            </span>
          </div>

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
    <div
      v-if="showCreateModal"
      class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
    >
      <div class="card w-full max-w-2xl bg-slate-900 border-slate-700 space-y-4 shadow-2xl max-h-[92vh] overflow-y-auto">
        <div class="flex items-center justify-between border-b border-slate-800 pb-3">
          <h3 class="text-base font-bold text-slate-100">新建跟踪监测任务</h3>
          <button @click="showCreateModal = false" class="text-slate-400 hover:text-slate-200">&#x2715;</button>
        </div>

        <form @submit.prevent="handleCreateTask" class="space-y-3.5 text-xs">
          <div>
            <label class="block text-slate-300 mb-1 font-medium">任务名称</label>
            <input
              v-model="newTaskForm.name"
              type="text"
              required
              placeholder="例如: 校园东门主干道监控 / 桌面实时测试"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <!-- Source Type Tabs -->
          <div class="space-y-2.5 p-3 rounded-xl bg-slate-950/70 border border-slate-800">
            <label class="block text-slate-300 font-medium">视频输入源类型</label>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="tab in sourceTypeTabs"
                :key="tab.value"
                type="button"
                @click="selectSourceType(tab.value)"
                class="px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-colors"
                :class="
                  sourceType === tab.value
                    ? 'bg-indigo-600 text-white border-indigo-500'
                    : 'bg-slate-900 text-slate-400 border-slate-700 hover:text-slate-200'
                "
              >
                {{ tab.label }}
              </button>
            </div>

            <!-- Tab 1: Upload MP4 -->
            <div v-if="sourceType === 'upload'" class="space-y-2 pt-1">
              <div class="flex flex-wrap items-center gap-2">
                <input
                  ref="modalFileInputRef"
                  type="file"
                  accept=".mp4,.avi,.mov,.mkv,.webm"
                  class="hidden"
                  @change="handleModalFileUpload"
                />
                <button
                  type="button"
                  :disabled="uploadingModalFile"
                  @click="modalFileInputRef?.click()"
                  class="btn btn-primary text-xs py-1.5 px-3"
                >
                  <span>{{ uploadingModalFile ? '上传中...' : '\u{1F4E4} 选择并上传视频文件' }}</span>
                </button>
                <span v-if="uploadedFileLabel" class="text-emerald-300 font-mono">
                  <span>已上传: {{ uploadedFileLabel }}</span>
                </span>
                <span v-else class="text-slate-400">支持 .mp4 / .avi / .mov / .mkv / .webm</span>
              </div>
            </div>

            <!-- Tab 2: Local Webcam -->
            <div v-else-if="sourceType === 'camera'" class="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
              <div>
                <label class="block text-slate-400 mb-1">摄像头设备编号</label>
                <select
                  v-model.number="cameraIndex"
                  @change="syncCameraUri"
                  class="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-slate-100"
                >
                  <option :value="0">默认摄像头 (camera://0)</option>
                  <option :value="1">外接摄像头 1 (camera://1)</option>
                  <option :value="2">外接摄像头 2 (camera://2)</option>
                </select>
              </div>
              <div class="flex items-end text-slate-400 text-[11px] pb-1">
                <span>Windows 下自动优先使用 DirectShow 低延迟采集。</span>
              </div>
            </div>

            <!-- Tab 3: PC Screen Capture -->
            <div v-else-if="sourceType === 'screen'" class="space-y-2 pt-1">
              <div class="flex flex-wrap gap-2">
                <button
                  type="button"
                  @click="applyScreenPreset('full')"
                  class="px-2.5 py-1 rounded border text-[11px]"
                  :class="screenMode === 'full' ? 'bg-cyan-600/30 border-cyan-500 text-cyan-200' : 'bg-slate-900 border-slate-700 text-slate-300'"
                >
                  <span>全屏采集 (screen://0)</span>
                </button>
                <button
                  type="button"
                  @click="applyScreenPreset('center')"
                  class="px-2.5 py-1 rounded border text-[11px]"
                  :class="screenMode === 'center' ? 'bg-cyan-600/30 border-cyan-500 text-cyan-200' : 'bg-slate-900 border-slate-700 text-slate-300'"
                >
                  <span>中心窗口 1280&times;720 (游戏/视频窗)</span>
                </button>
                <button
                  type="button"
                  @click="screenMode = 'region'"
                  class="px-2.5 py-1 rounded border text-[11px]"
                  :class="screenMode === 'region' ? 'bg-cyan-600/30 border-cyan-500 text-cyan-200' : 'bg-slate-900 border-slate-700 text-slate-300'"
                >
                  <span>自定义屏幕区域</span>
                </button>
              </div>

              <div v-if="screenMode === 'region'" class="grid grid-cols-5 gap-2">
                <label>
                  <span class="text-slate-400 block mb-0.5">Monitor</span>
                  <input v-model.number="screenRegion.monitor" @input="syncScreenUri" type="number" min="0" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100" />
                </label>
                <label>
                  <span class="text-slate-400 block mb-0.5">X</span>
                  <input v-model.number="screenRegion.x" @input="syncScreenUri" type="number" min="0" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100" />
                </label>
                <label>
                  <span class="text-slate-400 block mb-0.5">Y</span>
                  <input v-model.number="screenRegion.y" @input="syncScreenUri" type="number" min="0" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100" />
                </label>
                <label>
                  <span class="text-slate-400 block mb-0.5">W</span>
                  <input v-model.number="screenRegion.w" @input="syncScreenUri" type="number" min="64" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100" />
                </label>
                <label>
                  <span class="text-slate-400 block mb-0.5">H</span>
                  <input v-model.number="screenRegion.h" @input="syncScreenUri" type="number" min="64" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-100" />
                </label>
              </div>
            </div>

            <!-- Tab 4: RTSP Stream -->
            <div v-else-if="sourceType === 'rtsp'" class="pt-1">
              <span class="text-[11px] text-slate-400">支持 RTSP / RTMP / HTTP-FLV 等网络摄像机地址（断流自动重连）。</span>
            </div>

            <!-- Tab 5: Local Asset Path / Demo -->
            <div v-else class="pt-1">
              <select
                v-model="localAssetPreset"
                @change="onLocalAssetPresetChange"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                <option value="demo">仿真演示流 (demo - Campus Simulator)</option>
                <optgroup v-if="store.localAssets.videos.length > 0" label="本地视频 (data/videos & data/uploads)">
                  <option v-for="v in store.localAssets.videos" :key="`vid-${v}`" :value="v">
                    {{ v }}
                  </option>
                </optgroup>
                <optgroup v-if="store.localAssets.datasets.length > 0" label="本地图像序列 (data/datasets)">
                  <option v-for="d in store.localAssets.datasets" :key="`ds-${d}`" :value="d">
                    {{ d }}
                  </option>
                </optgroup>
                <option value="__custom__">手动输入本地路径...</option>
              </select>
            </div>

            <!-- Editable Resolved Video Source URI -->
            <div>
              <label class="block text-slate-400 mb-1">最终输入源 URI / 路径</label>
              <input
                v-model="newTaskForm.video_source"
                type="text"
                required
                placeholder="输入 mp4 路径、camera://0、screen://0 或 rtsp://..."
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <!-- Detector Backend & Built-in Weight Picker -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div>
              <label class="block text-slate-300 mb-1 font-medium">检测器推理后端</label>
              <select
                v-model="newTaskForm.detector_type"
                @change="onDetectorBackendChange"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                <option value="yolo">Ultralytics YOLO (.pt)</option>
                <option value="onnx">ONNX Runtime 引擎 (.onnx)</option>
                <option value="tensorrt">TensorRT 引擎 (.engine)</option>
                <option value="mock">Mock 仿真检测器 (无需权重)</option>
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
              <span>模型权重路径 (默认内置 weights/yolov8n.pt)</span>
            </label>
            <select
              v-model="weightPreset"
              @change="onWeightPresetChange"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              <option value="weights/yolov8n.pt">weights/yolov8n.pt (内置 YOLOv8n PyTorch)</option>
              <option value="weights/yolov8n.onnx">weights/yolov8n.onnx (内置 YOLOv8n ONNX)</option>
              <option
                v-for="w in extraLocalWeights"
                :key="`wt-${w}`"
                :value="w"
              >
                {{ w }}
              </option>
              <option value="__custom__">自定义权重文件路径...</option>
            </select>
            <input
              v-model="newTaskForm.model_path"
              type="text"
              placeholder="例如: weights/yolov8n.pt 或 weights/yolov8n.onnx"
              class="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
            />
          </div>

          <!-- GMC Camera Motion Compensation Section -->
          <div class="p-3 rounded-lg bg-amber-950/20 border border-amber-800/40 space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="newTaskForm.use_gmc" class="accent-amber-500 rounded" />
                <span class="text-amber-200 font-medium">启用全局相机运动补偿 (GMC)</span>
              </label>
              <select
                v-model="newTaskForm.gmc_method"
                :disabled="!newTaskForm.use_gmc"
                class="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 text-slate-100 disabled:opacity-50"
              >
                <option value="sparse_optflow">航拍光流 (sparse_optflow)</option>
                <option value="phase">极速相位相关 (phase)</option>
                <option value="hybrid">混合防甩镜模式 (hybrid)</option>
                <option value="none">关闭 (none)</option>
              </select>
            </div>
            <p class="text-[11px] text-slate-400">
              <span>针对无人机航拍、游戏录屏甩镜或手持移动拍摄场景，通过仿射变换修正卡尔曼状态防止 ID 跳变。</span>
            </p>
          </div>

          <!-- Playback, FPS & Adaptive Keyframe Stride -->
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label class="block text-slate-300 mb-1 font-medium">播放节奏</label>
              <select
                v-model="newTaskForm.playback_mode"
                class="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-2 text-slate-100"
              >
                <option value="native">固定原速 (native)</option>
                <option value="uncapped">全速压测 (uncapped)</option>
              </select>
            </div>
            <div>
              <label class="block text-slate-300 mb-1 font-medium">目标帧率 (0=不限)</label>
              <input
                v-model.number="newTaskForm.fps"
                type="number"
                min="0"
                max="300"
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
                <option :value="2">2 (推荐)</option>
                <option :value="3">3</option>
                <option :value="4">4</option>
              </select>
            </div>
          </div>

          <!-- Adaptive Keyframe & ROI Rules Toggles -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <label class="flex items-center gap-2 cursor-pointer p-2.5 rounded-lg bg-slate-800/60 border border-slate-700/60">
              <input type="checkbox" v-model="newTaskForm.adaptive_skip" class="accent-indigo-500 rounded" />
              <span class="text-slate-200 font-medium">启用自适应关键帧跳帧</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer p-2.5 rounded-lg bg-slate-800/60 border border-slate-700/60">
              <input type="checkbox" v-model="newTaskForm.enable_roi_rules" class="accent-indigo-500 rounded" />
              <span class="text-slate-200 font-medium">启用 ROI 违章规则检测</span>
            </label>
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
              @click="showCreateModal = false"
              class="btn btn-secondary text-xs py-1.5 px-3"
            >
              <span>取消</span>
            </button>
            <button type="submit" class="btn btn-primary text-xs py-1.5 px-4">
              <span>创建任务</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useMotStore } from '../stores/motStore';
import type { GmcMethod } from '../types';

type SourceTab = 'upload' | 'camera' | 'screen' | 'rtsp' | 'local';

const store = useMotStore();
const showCreateModal = ref(false);
const activeFilter = ref('all');
const createError = ref<string | null>(null);

const sourceType = ref<SourceTab>('upload');
const modalFileInputRef = ref<HTMLInputElement | null>(null);
const uploadingModalFile = ref(false);
const uploadedFileLabel = ref<string | null>(null);
const cameraIndex = ref(0);
const screenMode = ref<'full' | 'center' | 'region'>('full');
const screenRegion = ref({ monitor: 0, x: 320, y: 180, w: 1280, h: 720 });
const localAssetPreset = ref('demo');
const weightPreset = ref('weights/yolov8n.pt');

const sourceTypeTabs: { label: string; value: SourceTab }[] = [
  { label: '本地上传 MP4', value: 'upload' },
  { label: '本机摄像头', value: 'camera' },
  { label: 'PC 桌面屏幕采集', value: 'screen' },
  { label: 'RTSP 网络流', value: 'rtsp' },
  { label: '本地路径 / 演示', value: 'local' },
];

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
  detector_type: 'yolo',
  model_path: 'weights/yolov8n.pt',
  imgsz: 640,
  conf_threshold: 0.35,
  iou_threshold: 0.5,
  fps: 0,
  playback_mode: 'native',
  downsample_fps: 5.0,
  enable_roi_rules: false,
  use_gmc: false,
  gmc_method: 'sparse_optflow' as GmcMethod,
  adaptive_skip: true,
  max_skip_stride: 2,
  use_class_aware: true,
  use_nsa: true,
  use_diou: true,
  use_vdc: true,
  use_oru: true,
  use_lazy_appearance: false,
  use_recovery_pool: true,
});

const extraLocalWeights = computed(() => {
  return (store.localAssets.weights || []).filter(
    (w) => w !== 'weights/yolov8n.pt' && w !== 'weights/yolov8n.onnx'
  );
});

function isWrongWay(type: string): boolean {
  return type === 'wrong_way' || type === 'wrong-way';
}

const filteredViolations = computed(() => {
  const list = store.violations;
  if (activeFilter.value === 'all') return list;
  if (activeFilter.value === 'unack') return list.filter((v) => !v.acknowledged);
  if (activeFilter.value === 'wrong_way') {
    return list.filter((v) => isWrongWay(v.violation_type));
  }
  return list.filter((v) => v.violation_type === activeFilter.value);
});

function openCreateModal() {
  createError.value = null;
  uploadedFileLabel.value = null;
  store.fetchLocalAssets();
  const defW = store.defaultWeightPath || 'weights/yolov8n.pt';
  newTaskForm.value.model_path = defW;
  weightPreset.value = defW;
  showCreateModal.value = true;
}

function selectSourceType(tab: SourceTab) {
  sourceType.value = tab;
  if (tab === 'camera') {
    syncCameraUri();
  } else if (tab === 'screen') {
    applyScreenPreset(screenMode.value);
    newTaskForm.value.use_gmc = true;
    if (newTaskForm.value.gmc_method === 'none') {
      newTaskForm.value.gmc_method = 'phase';
    }
  } else if (tab === 'rtsp') {
    if (!newTaskForm.value.video_source.startsWith('rtsp://') && !newTaskForm.value.video_source.startsWith('http://')) {
      newTaskForm.value.video_source = 'rtsp://127.0.0.1:8554/live';
    }
  } else if (tab === 'local') {
    if (localAssetPreset.value !== '__custom__') {
      newTaskForm.value.video_source = localAssetPreset.value;
    }
  }
}

async function handleModalFileUpload(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  uploadingModalFile.value = true;
  createError.value = null;
  try {
    const uploaded = await store.uploadVideo(file);
    if (uploaded) {
      newTaskForm.value.video_source = uploaded.path;
      uploadedFileLabel.value = `${uploaded.filename} (${(uploaded.size_bytes / (1024 * 1024)).toFixed(1)} MB)`;
      if (!newTaskForm.value.name.trim()) {
        newTaskForm.value.name = `上传视频: ${uploaded.filename}`;
      }
    } else {
      createError.value = store.taskError || '视频上传失败';
    }
  } finally {
    uploadingModalFile.value = false;
    input.value = '';
  }
}

function syncCameraUri() {
  newTaskForm.value.video_source = `camera://${Math.max(0, cameraIndex.value)}`;
}

function applyScreenPreset(mode: 'full' | 'center' | 'region') {
  screenMode.value = mode;
  if (mode === 'full') {
    newTaskForm.value.video_source = 'screen://0';
  } else if (mode === 'center') {
    screenRegion.value = { monitor: 0, x: 320, y: 180, w: 1280, h: 720 };
    syncScreenUri();
  } else {
    syncScreenUri();
  }
}

function syncScreenUri() {
  const r = screenRegion.value;
  newTaskForm.value.video_source = `screen://${Math.max(0, r.monitor)}?x=${Math.max(0, r.x)}&y=${Math.max(0, r.y)}&w=${Math.max(64, r.w)}&h=${Math.max(64, r.h)}`;
}

function onLocalAssetPresetChange() {
  if (localAssetPreset.value !== '__custom__') {
    newTaskForm.value.video_source = localAssetPreset.value;
  }
}

function onDetectorBackendChange() {
  const dt = newTaskForm.value.detector_type;
  if (dt === 'onnx' && (!newTaskForm.value.model_path || newTaskForm.value.model_path.endsWith('.pt'))) {
    newTaskForm.value.model_path = 'weights/yolov8n.onnx';
    weightPreset.value = 'weights/yolov8n.onnx';
  } else if (dt === 'yolo' && (!newTaskForm.value.model_path || newTaskForm.value.model_path.endsWith('.onnx'))) {
    newTaskForm.value.model_path = 'weights/yolov8n.pt';
    weightPreset.value = 'weights/yolov8n.pt';
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
  newTaskForm.value.use_lazy_appearance = false;
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
  if (!newTaskForm.value.name.trim()) return;
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
      playback_mode: f.playback_mode,
      downsample_fps: f.downsample_fps,
      enable_roi_rules: f.enable_roi_rules,
      use_gmc: f.use_gmc,
      gmc_method: f.use_gmc ? f.gmc_method : 'none',
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
  if (created) {
    showCreateModal.value = false;
    newTaskForm.value.name = '';
  } else {
    createError.value = store.taskError || '创建任务失败，请检查输入参数或本地路径';
  }
}
</script>
