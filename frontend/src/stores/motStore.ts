import { defineStore } from 'pinia';
import type {
  AblationResult,
  ActiveTab,
  BenchmarksResponse,
  LocalAssetsResponse,
  StreamConfig,
  StreamPresetItem,
  StreamPresetsResponse,
  SystemMetrics,
  Task,
  TrackFrame,
  TrackPoint,
  TrackQueryResponse,
  TrackSummary,
  VideoUploadResponse,
  ViolationEvent,
  Zone,
} from '../types';

export interface FetchTracksParams {
  task_id: number;
  track_id?: number | null;
  class_name?: string;
  start_ts?: number;
  end_ts?: number;
  limit?: number;
}

function emptySystemMetrics(activeStreams = 0): SystemMetrics {
  return {
    fps: 0,
    p95_latency_ms: 0,
    avg_latency_ms: 0,
    drop_count: 0,
    queue_size: 0,
    detect_skip_ratio: 0,
    is_demo_simulation: false,
    active_streams: activeStreams,
    gmc_shift_px: 0,
    stage_latency: {
      decode_ms: 0,
      detect_ms: 0,
      track_ms: 0,
      rules_ms: 0,
      total_ms: 0,
    },
  };
}

export const useMotStore = defineStore('mot', {
  state: () => ({
    tasks: [] as Task[],
    activeTaskId: null as number | null,
    violations: [] as ViolationEvent[],
    systemMetrics: emptySystemMetrics(),
    ablationResults: [] as AblationResult[],
    ablationLoading: false,
    wsError: null as string | null,
    taskError: null as string | null,
    activeTab: 'monitoring' as ActiveTab,

    // Single-source-of-truth per-task stream & metadata maps
    multiTaskIds: [] as number[],
    framesByTask: {} as Record<number, TrackFrame>,
    zonesByTask: {} as Record<number, Zone[]>,
    wsConnectedByTask: {} as Record<number, boolean>,

    // Trajectory retrieval & replay state
    trackSummaries: [] as TrackSummary[],
    trackPoints: [] as TrackPoint[],
    tracksLoading: false,
    tracksTaskId: null as number | null,

    // Local assets, stream presets & benchmark results
    localAssets: {
      videos: [],
      datasets: [],
      weights: [],
    } as LocalAssetsResponse,
    streamPresets: [] as StreamPresetItem[],
    lockedBenchmarks: {
      available: false,
      experiments: {},
    } as BenchmarksResponse,

    // Canvas rendering toggles (ROI zones disabled by default)
    displaySettings: {
      showBBoxes: true,
      showLabels: true,
      showTrails: true,
      showZones: false,
      showVectors: true,
    },

    // Prevent multiple demos fighting for GPU unless multi-stream concurrency is explicitly desired
    singleStreamExclusive: true,

    // Single unified WebSocket map keyed by taskId
    _wsByTask: {} as Record<number, WebSocket>,
  }),

  getters: {
    activeTask(state): Task | undefined {
      return state.tasks.find((t) => t.id === state.activeTaskId);
    },
    currentFrame(state): TrackFrame | null {
      return state.activeTaskId ? state.framesByTask[state.activeTaskId] || null : null;
    },
    wsConnected(state): boolean {
      return state.activeTaskId ? Boolean(state.wsConnectedByTask[state.activeTaskId]) : false;
    },
    zones(state): Zone[] {
      return state.activeTaskId ? state.zonesByTask[state.activeTaskId] || [] : [];
    },
    multiFrames(state): Record<number, TrackFrame> {
      return state.framesByTask;
    },
    multiZones(state): Record<number, Zone[]> {
      return state.zonesByTask;
    },
    multiWsStatus(state): Record<number, boolean> {
      return state.wsConnectedByTask;
    },
    unacknowledgedViolations(state): ViolationEvent[] {
      return state.violations.filter((v) => !v.acknowledged);
    },
    activeTracksCount(state): number {
      return state.activeTaskId ? state.framesByTask[state.activeTaskId]?.tracks?.length || 0 : 0;
    },
    defaultWeightPath(state): string {
      const weights = state.localAssets.weights || [];
      if (weights.includes('weights/yolov8n.pt')) return 'weights/yolov8n.pt';
      const firstPt = weights.find((w) => w.toLowerCase().endsWith('.pt'));
      return firstPt || weights[0] || 'weights/yolov8n.pt';
    },
  },

  actions: {
    _ingestFrameViolations(frame: TrackFrame) {
      if (frame.active_violations && frame.active_violations.length > 0) {
        for (const v of frame.active_violations) {
          const exists = this.violations.some(
            (item) =>
              item.task_id === v.task_id &&
              item.track_id === v.track_id &&
              item.frame_id === v.frame_id &&
              item.violation_type === v.violation_type
          );
          if (!exists) {
            this.violations.unshift(v);
          }
        }
      }
    },

    _closeTaskWs(taskId: number) {
      const ws = this._wsByTask[taskId];
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        try {
          ws.close();
        } catch (_) {}
        delete this._wsByTask[taskId];
      }
      if (taskId in this.wsConnectedByTask) {
        const nextStatus = { ...this.wsConnectedByTask };
        delete nextStatus[taskId];
        this.wsConnectedByTask = nextStatus;
      }
    },

    _clearTaskFrame(taskId: number) {
      if (taskId in this.framesByTask) {
        const nextFrames = { ...this.framesByTask };
        delete nextFrames[taskId];
        this.framesByTask = nextFrames;
      }
    },

    async fetchTasks() {
      try {
        const res = await fetch('/api/tasks');
        if (res.ok) {
          this.tasks = await res.json();
          if (this.tasks.length > 0 && !this.activeTaskId) {
            this.setActiveTask(this.tasks[0].id);
          }
        }
      } catch (err) {
        console.error('Failed to fetch tasks:', err);
      }
    },

    async createTask(payload: { name: string; video_source: string; config?: Partial<StreamConfig> }): Promise<Task | null> {
      this.taskError = null;
      try {
        const res = await fetch('/api/tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          const newTask: Task = await res.json();
          this.tasks.unshift(newTask);
          this.setActiveTask(newTask.id);
          return newTask;
        }
        const errBody = await res.json().catch(() => null);
        this.taskError = errBody?.detail || `创建任务失败 (HTTP ${res.status})`;
      } catch (err) {
        console.error('Failed to create task:', err);
        this.taskError = String(err);
      }
      return null;
    },

    async uploadVideo(file: File): Promise<VideoUploadResponse | null> {
      this.taskError = null;
      try {
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch('/api/videos/upload', {
          method: 'POST',
          body: formData,
        });
        if (res.ok) {
          const data: VideoUploadResponse = await res.json();
          await this.fetchLocalAssets();
          return data;
        }
        const errBody = await res.json().catch(() => null);
        this.taskError = errBody?.detail || `视频上传失败 (HTTP ${res.status})`;
      } catch (err) {
        console.error('Failed to upload video:', err);
        this.taskError = String(err);
      }
      return null;
    },

    async deleteTask(id: number) {
      try {
        const res = await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
        if (res.ok) {
          this.tasks = this.tasks.filter((t) => t.id !== id);
          this._closeTaskWs(id);
          this._clearTaskFrame(id);
          const nextZones = { ...this.zonesByTask };
          delete nextZones[id];
          this.zonesByTask = nextZones;
          this.multiTaskIds = this.multiTaskIds.filter((tid) => tid !== id);

          if (this.activeTaskId === id) {
            this.activeTaskId = this.tasks[0]?.id || null;
            if (this.activeTaskId) {
              this.fetchZonesForTask(this.activeTaskId);
              if (this.tasks[0]?.status === 'running') {
                this.connectTaskWs(this.activeTaskId);
              }
            }
          }
        }
      } catch (err) {
        console.error('Failed to delete task:', err);
      }
    },

    async startTask(id: number, exclusive?: boolean) {
      this.taskError = null;
      const useExclusive = exclusive !== undefined ? exclusive : this.singleStreamExclusive;
      try {
        if (useExclusive) {
          const othersRunning = this.tasks.filter((t) => t.id !== id && t.status === 'running');
          for (const other of othersRunning) {
            await this.stopTask(other.id);
          }
        }
        const res = await fetch(`/api/tasks/${id}/start`, { method: 'POST' });
        if (res.ok) {
          const task = this.tasks.find((t) => t.id === id);
          if (task) task.status = 'running';
          if (this.activeTaskId !== id && (useExclusive || !this.activeTaskId)) {
            this.activeTaskId = id;
            this.displaySettings.showZones = Boolean(task?.config?.enable_roi_rules);
            this.fetchZonesForTask(id);
          }
          if (this.activeTaskId === id || this.multiTaskIds.includes(id)) {
            this.connectTaskWs(id);
          }
        } else {
          const errBody = await res.json().catch(() => null);
          this.taskError = errBody?.detail || `启动任务失败 (HTTP ${res.status})`;
          await this.fetchTasks();
        }
      } catch (err) {
        console.error('Failed to start task:', err);
        this.taskError = String(err);
      }
    },

    async stopTask(id: number) {
      try {
        const res = await fetch(`/api/tasks/${id}/stop`, { method: 'POST' });
        if (res.ok) {
          const task = this.tasks.find((t) => t.id === id);
          if (task) task.status = 'stopped';

          this._closeTaskWs(id);
          this._clearTaskFrame(id);

          const remainingRunning = this.tasks.filter((t) => t.status === 'running').length;
          if (remainingRunning === 0 || this.activeTaskId === id) {
            this.systemMetrics = emptySystemMetrics(remainingRunning);
          }
        }
      } catch (err) {
        console.error('Failed to stop task:', err);
      }
    },

    setActiveTask(id: number) {
      if (this.activeTaskId === id) return;
      const prevId = this.activeTaskId;
      this.activeTaskId = id;

      if (prevId && !this.multiTaskIds.includes(prevId)) {
        this._closeTaskWs(prevId);
      }

      const target = this.tasks.find((t) => t.id === id);
      if (target) {
        this.displaySettings.showZones = Boolean(target.config?.enable_roi_rules);
      }
      this.fetchZonesForTask(id);
      if (target?.status === 'running') {
        this.connectTaskWs(id);
      } else {
        this._clearTaskFrame(id);
        const remainingRunning = this.tasks.filter((t) => t.status === 'running').length;
        this.systemMetrics = emptySystemMetrics(remainingRunning);
      }
    },

    async updateTaskConfig(taskId: number, patch: Partial<StreamConfig>) {
      this.taskError = null;
      const task = this.tasks.find((t) => t.id === taskId);
      if (!task) return null;
      const nextConfig: StreamConfig = {
        ...task.config,
        ...patch,
      };
      try {
        const res = await fetch(`/api/tasks/${taskId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config: nextConfig }),
        });
        if (res.ok) {
          const updated: Task = await res.json();
          const idx = this.tasks.findIndex((t) => t.id === taskId);
          if (idx !== -1) {
            this.tasks[idx] = updated;
          }
          if (taskId === this.activeTaskId && patch.enable_roi_rules !== undefined) {
            this.displaySettings.showZones = Boolean(patch.enable_roi_rules);
          }
          if (updated.status === 'running' && (taskId === this.activeTaskId || this.multiTaskIds.includes(taskId))) {
            this.connectTaskWs(taskId);
          }
          return updated;
        }
        const errBody = await res.json().catch(() => null);
        this.taskError = errBody?.detail || `更新配置失败 (HTTP ${res.status})`;
      } catch (err) {
        console.error('Failed to update task config:', err);
        this.taskError = String(err);
      }
      return null;
    },

    async fetchZones(taskId?: number) {
      const targetId = taskId ?? this.activeTaskId;
      if (targetId) {
        await this.fetchZonesForTask(targetId);
      }
    },

    async fetchZonesForTask(taskId: number) {
      try {
        const res = await fetch(`/api/zones?task_id=${taskId}`);
        if (res.ok) {
          const data: Zone[] = await res.json();
          this.zonesByTask = { ...this.zonesByTask, [taskId]: data };
        }
      } catch (err) {
        console.error(`Failed to fetch zones for task ${taskId}:`, err);
      }
    },

    async fetchViolations() {
      try {
        const url = this.activeTaskId
          ? `/api/violations?task_id=${this.activeTaskId}&limit=100`
          : '/api/violations?limit=100';
        const res = await fetch(url);
        if (res.ok) {
          this.violations = await res.json();
        }
      } catch (err) {
        console.error('Failed to fetch violations:', err);
      }
    },

    async acknowledgeViolation(id: number) {
      try {
        const res = await fetch(`/api/violations/${id}/acknowledge`, { method: 'POST' });
        if (res.ok) {
          const updated = await res.json();
          const idx = this.violations.findIndex((v) => v.id === id);
          if (idx !== -1) {
            this.violations[idx] = updated;
          }
        }
      } catch (err) {
        console.error('Failed to acknowledge violation:', err);
      }
    },

    async deleteViolation(id: number) {
      try {
        const res = await fetch(`/api/violations/${id}`, { method: 'DELETE' });
        if (res.ok) {
          this.violations = this.violations.filter((v) => v.id !== id);
        }
      } catch (err) {
        console.error('Failed to delete violation:', err);
      }
    },

    async fetchMetrics() {
      try {
        const url = this.activeTaskId ? `/api/metrics?task_id=${this.activeTaskId}` : '/api/metrics';
        const res = await fetch(url);
        if (res.ok) {
          this.systemMetrics = await res.json();
        }
      } catch (err) {
        console.error('Failed to fetch metrics:', err);
      }
    },

    async fetchAblation() {
      this.ablationLoading = true;
      try {
        const res = await fetch('/api/ablation');
        if (res.ok) {
          this.ablationResults = await res.json();
        }
      } catch (err) {
        console.error('Failed to fetch ablation:', err);
      } finally {
        this.ablationLoading = false;
      }
    },

    async fetchTracks(params: FetchTracksParams) {
      this.tracksLoading = true;
      try {
        const qs = new URLSearchParams();
        qs.set('task_id', String(params.task_id));
        if (params.track_id !== undefined && params.track_id !== null && !Number.isNaN(params.track_id)) {
          qs.set('track_id', String(params.track_id));
        }
        if (params.class_name && params.class_name !== 'all') {
          qs.set('class_name', params.class_name);
        }
        if (params.start_ts !== undefined) {
          qs.set('start_ts', String(params.start_ts));
        }
        if (params.end_ts !== undefined) {
          qs.set('end_ts', String(params.end_ts));
        }
        if (params.limit !== undefined) {
          qs.set('limit', String(params.limit));
        }
        const res = await fetch(`/api/tracks?${qs.toString()}`);
        if (res.ok) {
          const data: TrackQueryResponse = await res.json();
          this.tracksTaskId = data.task_id ?? params.task_id;
          this.trackSummaries = data.summaries || [];
          this.trackPoints = data.points || [];
        }
      } catch (err) {
        console.error('Failed to fetch tracks:', err);
      } finally {
        this.tracksLoading = false;
      }
    },

    async fetchLocalAssets() {
      try {
        const res = await fetch('/api/local-assets');
        if (res.ok) {
          const data: LocalAssetsResponse = await res.json();
          this.localAssets = {
            videos: data.videos || [],
            datasets: data.datasets || [],
            weights: data.weights || [],
          };
        }
      } catch (err) {
        console.error('Failed to fetch local assets:', err);
      }
    },

    async fetchStreamPresets() {
      try {
        const res = await fetch('/api/stream_presets');
        if (res.ok) {
          const data: StreamPresetsResponse = await res.json();
          this.streamPresets = data.presets || [];
          this.localAssets = {
            videos: data.videos || this.localAssets.videos,
            datasets: data.datasets || this.localAssets.datasets,
            weights: data.weights || this.localAssets.weights,
          };
        }
      } catch (err) {
        console.error('Failed to fetch stream presets:', err);
      }
    },

    async fetchLockedBenchmarks() {
      try {
        const res = await fetch('/api/benchmarks');
        if (res.ok) {
          const data: BenchmarksResponse = await res.json();
          this.lockedBenchmarks = {
            available: Boolean(data.available),
            experiments: data.experiments || {},
          };
        }
      } catch (err) {
        console.error('Failed to fetch locked benchmarks:', err);
      }
    },

    connectWebSocket(taskId: number) {
      this.connectTaskWs(taskId);
    },

    disconnectWebSocket() {
      if (this.activeTaskId && !this.multiTaskIds.includes(this.activeTaskId)) {
        this._closeTaskWs(this.activeTaskId);
      }
    },

    connectTaskWs(taskId: number) {
      this._closeTaskWs(taskId);

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/stream/${taskId}`;

      try {
        const ws = new WebSocket(wsUrl);
        this._wsByTask[taskId] = ws;

        ws.onopen = () => {
          if (this._wsByTask[taskId] !== ws) return;
          this.wsConnectedByTask = { ...this.wsConnectedByTask, [taskId]: true };
          if (taskId === this.activeTaskId) {
            this.wsError = null;
          }
        };

        ws.onmessage = (event) => {
          if (this._wsByTask[taskId] !== ws || event.data === 'pong') return;
          try {
            const frame: TrackFrame = JSON.parse(event.data);
            if ((frame as any).error) {
              if (taskId === this.activeTaskId) {
                this.wsError = String((frame as any).error);
              }
              return;
            }
            const task = this.tasks.find((t) => t.id === taskId);
            if (task && task.status !== 'running') {
              return;
            }
            this.framesByTask = { ...this.framesByTask, [taskId]: frame };
            if (taskId === this.activeTaskId && frame.metrics) {
              this.systemMetrics = frame.metrics;
            }
            this._ingestFrameViolations(frame);
          } catch (e) {
            console.error(`Error parsing WS frame for task ${taskId}:`, e);
          }
        };

        ws.onerror = () => {
          if (this._wsByTask[taskId] !== ws) return;
          this.wsConnectedByTask = { ...this.wsConnectedByTask, [taskId]: false };
          if (taskId === this.activeTaskId) {
            this.wsError = 'WebSocket connection error';
          }
        };

        ws.onclose = () => {
          if (this._wsByTask[taskId] === ws) {
            delete this._wsByTask[taskId];
            this.wsConnectedByTask = { ...this.wsConnectedByTask, [taskId]: false };
          }
        };
      } catch (err) {
        console.error(`Failed to connect WS for task ${taskId}:`, err);
        this.wsConnectedByTask = { ...this.wsConnectedByTask, [taskId]: false };
        if (taskId === this.activeTaskId) {
          this.wsError = String(err);
        }
      }
    },

    setMultiStreamTasks(ids: number[]) {
      const uniqueIds = Array.from(new Set(ids.filter((id) => Number.isFinite(id) && id > 0)));
      this.multiTaskIds = uniqueIds;

      const wanted = new Set<number>(uniqueIds);
      if (this.activeTaskId && this.activeTask?.status === 'running') {
        wanted.add(this.activeTaskId);
      }

      // Close sockets no longer needed
      for (const key of Object.keys(this._wsByTask)) {
        const tid = Number(key);
        if (!wanted.has(tid)) {
          this._closeTaskWs(tid);
        }
      }

      // Ensure running panes have active WS & cached zones
      for (const tid of uniqueIds) {
        const task = this.tasks.find((t) => t.id === tid);
        const existing = this._wsByTask[tid];
        if (
          task?.status === 'running' &&
          (!existing || (existing.readyState !== WebSocket.OPEN && existing.readyState !== WebSocket.CONNECTING))
        ) {
          this.connectTaskWs(tid);
        }
        if (!this.zonesByTask[tid]) {
          this.fetchZonesForTask(tid);
        }
      }
    },

    disconnectMultiStreams() {
      this.multiTaskIds = [];
      for (const key of Object.keys(this._wsByTask)) {
        const tid = Number(key);
        if (tid !== this.activeTaskId || this.activeTask?.status !== 'running') {
          this._closeTaskWs(tid);
        }
      }
    },
  },
});
