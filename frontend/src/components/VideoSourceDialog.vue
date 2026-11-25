<template>
  <dialog ref="dialog" class="source-dialog" aria-labelledby="source-dialog-title" @cancel="handleCancel">
    <header class="source-dialog-header">
      <div>
        <h2 id="source-dialog-title">选择视频</h2>
        <p>选择服务端可访问的视频与检测模型。</p>
      </div>
      <button type="button" class="btn btn-secondary" :disabled="busy" @click="dialog?.close()">关闭</button>
    </header>

    <form class="source-form" @submit.prevent="start">
      <label class="source-field">
        <span>视频来源</span>
        <select v-model="videoSelection" class="workspace-field" required autofocus>
          <option value="" disabled>选择视频或图像序列</option>
          <option v-for="asset in videoAssets" :key="asset" :value="asset">{{ asset }}</option>
          <option :value="CUSTOM">输入视频路径、流地址或设备编号</option>
        </select>
      </label>
      <p v-if="!videoAssets.length" class="source-hint">将素材放在服务端的 data/videos 或 data/datasets，或直接输入路径。</p>
      <label v-if="videoSelection === CUSTOM" class="source-field">
        <span>视频路径或地址</span>
        <input v-model.trim="customVideo" class="workspace-field" required placeholder="data/videos/traffic.mp4" />
      </label>

      <label class="source-field">
        <span>模型权重</span>
        <select v-model="modelSelection" class="workspace-field" required>
          <option value="" disabled>选择 .pt、.onnx 或 .engine 文件</option>
          <option v-for="asset in modelAssets" :key="asset" :value="asset">{{ asset }}</option>
          <option :value="CUSTOM">输入模型路径</option>
        </select>
      </label>
      <p v-if="!modelAssets.length" class="source-hint">将权重放在服务端的 data/weights，或直接输入路径。</p>
      <label v-if="modelSelection === CUSTOM" class="source-field">
        <span>模型路径</span>
        <input v-model.trim="customModel" class="workspace-field" required placeholder="data/weights/yolov8n.pt" />
      </label>

      <p v-if="error" class="workspace-error" role="alert">{{ error }}</p>
      <div class="source-dialog-actions">
        <button type="button" class="btn btn-secondary" :disabled="busy || loadingAssets" @click="refreshAssets">
          {{ loadingAssets ? '正在扫描…' : '刷新素材列表' }}
        </button>
        <button type="submit" class="btn btn-primary" :disabled="busy || loadingAssets">
          {{ busy ? '正在启动…' : '开始跟踪' }}
        </button>
      </div>
    </form>
  </dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useMotStore } from '../stores/motStore';
import type { DetectorType } from '../types';

const emit = defineEmits<{ started: [taskId: number] }>();
const store = useMotStore();
const dialog = ref<HTMLDialogElement | null>(null);
const busy = ref(false);
const loadingAssets = ref(false);
const error = ref('');
const videoSelection = ref('');
const modelSelection = ref('');
const customVideo = ref('');
const customModel = ref('');
const CUSTOM = '__custom__';
const detectors: Record<string, DetectorType> = { pt: 'yolo', onnx: 'onnx', engine: 'tensorrt' };
const videoAssets = computed(() => [...new Set([...store.localAssets.videos, ...store.localAssets.datasets])]);
const modelAssets = computed(() => store.localAssets.weights.filter((asset) => modelType(asset)));

function modelType(asset: string) {
  return detectors[asset.split('.').pop()?.toLowerCase() || ''];
}

async function refreshAssets() {
  loadingAssets.value = true;
  try {
    const loaded = await store.fetchLocalAssets();
    error.value = loaded ? '' : '无法刷新本地素材。请检查服务连接，或手动填写路径。';
    if (!modelSelection.value && modelAssets.value.length === 1) {
      modelSelection.value = modelAssets.value[0];
    }
  } finally {
    loadingAssets.value = false;
  }
}

function open() {
  error.value = '';
  dialog.value?.showModal();
  void refreshAssets();
}

function handleCancel(event: Event) {
  if (busy.value) event.preventDefault();
}

async function start() {
  if (busy.value) return;
  error.value = '';
  const video = videoSelection.value === CUSTOM ? customVideo.value : videoSelection.value;
  const model = modelSelection.value === CUSTOM ? customModel.value : modelSelection.value;
  const detector = modelType(model);
  if (video === 'demo') {
    error.value = '模拟素材请从首页选择「试用演示」。';
    return;
  }
  if (!video || !detector) {
    error.value = '请选择视频来源，并提供 .pt、.onnx 或 .engine 模型权重。';
    return;
  }

  busy.value = true;
  try {
    const matchingTask = store.tasks.find((task) =>
      task.video_source === video && task.config.model_path === model && task.config.detector_type === detector,
    );
    const task = matchingTask ?? await store.createTask({
      name: (video.split(/[\\/]/).filter(Boolean).pop() || video).slice(0, 100),
      video_source: video,
      config: {
        video_source: video,
        model_path: model,
        detector_type: detector,
        playback_mode: 'native',
        device: detector === 'tensorrt' ? 'cuda:0' : 'cpu',
        half_precision: detector === 'tensorrt',
      },
    });

    if (task) {
      store.setActiveTask(task.id);
      if (task.status === 'running' || await store.startTask(task.id)) {
        emit('started', task.id);
        dialog.value?.close();
        return;
      }
    }
    error.value = store.taskError || '无法启动跟踪。请检查视频和模型路径。';
  } finally {
    busy.value = false;
  }
}

defineExpose({ open });
</script>
