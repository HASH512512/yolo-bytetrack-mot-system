import { defineStore } from 'pinia';
import type {
  AblationResult,
  ActiveTab,
  BenchmarksResponse,
  LocalAssetsResponse,
  StreamConfig,
  SystemMetrics,
  Task,
  TrackFrame,
  TrackPoint,
  TrackQueryResponse,
  TrackSummary,
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

export const useMotStore = defineStore('mot', {
  state: () => ({
    tasks: [] as Task[],
    activeTaskId: null as number | null,
    zones: [] as Zone[],
    violations: [] as ViolationEvent[],
    currentFrame: null as TrackFrame | null,
    systemMetrics: {
      fps: 0,
      p95_latency_ms: 0,
      avg_latency_ms: 0,
      drop_count: 0,
      queue_size: 0,
      detect_skip_ratio: 0,
      is_demo_simulation: false,
      active_streams: 0,
      stage_latency: {
        decode_ms: 0,
        detect_ms: 0,
        track_ms: 0,
        rules_ms: 0,
        total_ms: 0,
      },
    } as SystemMetrics,
    ablationResults: [] as AblationResult[],
    ablationLoading: false,
    wsConnected: false,
    wsError: null as string | null,
    taskError: null as string | null,
    activeTab: 'monitoring' as ActiveTab,

    // Multi-stream concurrency state
    multiTaskIds: [] as number[],
    multiFrames: {} as Record<number, TrackFrame>,
    multiZones: {} as Record<number, Zone[]>,
    multiWsStatus: {} as Record<number, boolean>,

    // Trajectory retrieval & replay state
    trackSummaries: [] as TrackSummary[],
    trackPoints: [] as TrackPoint[],
    tracksLoading: false,
    tracksTaskId: null as number | null,

    // Local assets & benchmark results
    localAssets: {
      videos: [],
      datasets: [],
      weights: [],
    } as LocalAssetsResponse,
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

    // Internal WS references
    _ws: null as WebSocket | null,
    _multiWs: {} as Record<number, WebSocket>,
  }),

  getters: {
    activeTask(state): Task | undefined {
      return state.tasks.find((t) => t.id === state.activeTaskId);
    },
    unacknowledgedViolations(state): ViolationEvent[] {
      return state.violations.filter((v) => !v.acknowledged);
    },
    activeTracksCount(state): number {
      return state.currentFrame?.tracks?.length || 0;
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

    async createTask(payload: { name: string; video_source: string; config?: any }) {
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

    async deleteTask(id: number) {
      try {
        const res = await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
        if (res.ok) {
          this.tasks = this.tasks.filter((t) => t.id !== id);
          if (this._multiWs[id]) {
            try {
              this._multiWs[id].close();
            } catch (_) {}
            delete this._multiWs[id];
            delete this.multiWsStatus[id];
            delete this.multiFrames[id];
          }
          this.multiTaskIds = this.multiTaskIds.filter((tid) => tid !== id);

          if (this.activeTaskId === id) {
            this.disconnectWebSocket();
            this.currentFrame = null;
            this.activeTaskId = this.tasks[0]?.id || null;
            if (this.activeTaskId) {
              this.fetchZones(this.activeTaskId);
              if (this.tasks[0]?.status === 'running') {
                this.connectWebSocket(this.activeTaskId);
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
          if (this.activeTaskId !== id && useExclusive) {
            this.activeTaskId = id;
            this.currentFrame = null;
            this.fetchZones(id);
          }
          if (this.activeTaskId === id) {
            this.connectWebSocket(id);
          }
          if (this.multiTaskIds.includes(id)) {
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

          if (this.activeTaskId === id) {
            this.disconnectWebSocket();
            this.currentFrame = null;
          }
          if (this._multiWs[id]) {
            try {
              this._multiWs[id].close();
            } catch (_) {}
            delete this._multiWs[id];
          }
          const nextStatus = { ...this.multiWsStatus };
          delete nextStatus[id];
          this.multiWsStatus = nextStatus;

          const nextFrames = { ...this.multiFrames };
          delete nextFrames[id];
          this.multiFrames = nextFrames;

          const anyRunning = this.tasks.some((t) => t.status === 'running');
          if (!anyRunning || this.activeTaskId === id) {
            this.systemMetrics = {
              fps: 0,
              p95_latency_ms: 0,
              avg_latency_ms: 0,
              drop_count: 0,
              queue_size: 0,
              detect_skip_ratio: 0,
              is_demo_simulation: false,
              active_streams: this.tasks.filter((t) => t.status === 'running').length,
              stage_latency: {
                decode_ms: 0,
                detect_ms: 0,
                track_ms: 0,
                rules_ms: 0,
                total_ms: 0,
              },
            };
          }
        }
      } catch (err) {
        console.error('Failed to stop task:', err);
      }
    },

    setActiveTask(id: number) {
      if (this.activeTaskId === id) return;
      this.activeTaskId = id;
      this.currentFrame = null;
      this.disconnectWebSocket();
      const target = this.tasks.find((t) => t.id === id);
      if (target) {
        this.displaySettings.showZones = Boolean(target.config?.enable_roi_rules);
      }
      this.fetchZones(id);
      if (target?.status === 'running') {
        this.connectWebSocket(id);
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
          if (updated.status === 'running') {
            if (taskId === this.activeTaskId) {
              this.connectWebSocket(taskId);
            }
            if (this.multiTaskIds.includes(taskId)) {
              this.connectTaskWs(taskId);
            }
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
      try {
        const url = taskId ? `/api/zones?task_id=${taskId}` : '/api/zones';
        const res = await fetch(url);
        if (res.ok) {
          const data: Zone[] = await res.json();
          this.zones = data;
          if (taskId) {
            this.multiZones = { ...this.multiZones, [taskId]: data };
          }
        }
      } catch (err) {
        console.error('Failed to fetch zones:', err);
      }
    },

    async fetchZonesForTask(taskId: number) {
      try {
        const res = await fetch(`/api/zones?task_id=${taskId}`);
        if (res.ok) {
          const data: Zone[] = await res.json();
          this.multiZones = { ...this.multiZones, [taskId]: data };
          if (taskId === this.activeTaskId) {
            this.zones = data;
          }
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
      this.disconnectWebSocket();

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/stream/${taskId}`;

      try {
        const ws = new WebSocket(wsUrl);
        this._ws = ws;

        ws.onopen = () => {
          this.wsConnected = true;
          this.wsError = null;
        };

        ws.onmessage = (event) => {
          if (event.data === 'pong') return;
          try {
            const frame: TrackFrame = JSON.parse(event.data);
            if ((frame as any).error) {
              this.wsError = String((frame as any).error);
              return;
            }
            this.currentFrame = frame;
            this.multiFrames = { ...this.multiFrames, [taskId]: frame };
            if (frame.metrics) {
              this.systemMetrics = frame.metrics;
            }
            this._ingestFrameViolations(frame);
          } catch (e) {
            console.error('Error parsing WS frame:', e);
          }
        };

        ws.onerror = () => {
          this.wsError = 'WebSocket connection error';
          this.wsConnected = false;
        };

        ws.onclose = () => {
          if (this._ws === ws) {
            this.wsConnected = false;
          }
        };
      } catch (err) {
        this.wsError = String(err);
        this.wsConnected = false;
      }
    },

    disconnectWebSocket() {
      if (this._ws) {
        try {
          this._ws.close();
        } catch (_) {}
        this._ws = null;
      }
      this.wsConnected = false;
    },

    connectTaskWs(taskId: number) {
      if (this._multiWs[taskId]) {
        try {
          this._multiWs[taskId].close();
        } catch (_) {}
        delete this._multiWs[taskId];
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/stream/${taskId}`;

      try {
        const ws = new WebSocket(wsUrl);
        this._multiWs[taskId] = ws;

        ws.onopen = () => {
          this.multiWsStatus = { ...this.multiWsStatus, [taskId]: true };
        };

        ws.onmessage = (event) => {
          if (event.data === 'pong') return;
          try {
            const frame: TrackFrame = JSON.parse(event.data);
            if ((frame as any).error) return;
            this.multiFrames = { ...this.multiFrames, [taskId]: frame };
            if (taskId === this.activeTaskId) {
              this.currentFrame = frame;
              if (frame.metrics) {
                this.systemMetrics = frame.metrics;
              }
            }
            this._ingestFrameViolations(frame);
          } catch (e) {
            console.error(`Error parsing multi-WS frame for task ${taskId}:`, e);
          }
        };

        ws.onerror = () => {
          this.multiWsStatus = { ...this.multiWsStatus, [taskId]: false };
        };

        ws.onclose = () => {
          if (this._multiWs[taskId] === ws) {
            this.multiWsStatus = { ...this.multiWsStatus, [taskId]: false };
            delete this._multiWs[taskId];
          }
        };
      } catch (err) {
        console.error(`Failed to connect multi-WS for task ${taskId}:`, err);
        this.multiWsStatus = { ...this.multiWsStatus, [taskId]: false };
      }
    },

    setMultiStreamTasks(ids: number[]) {
      const uniqueIds = Array.from(new Set(ids.filter((id) => Number.isFinite(id) && id > 0)));
      this.multiTaskIds = uniqueIds;

      // Close sockets no longer in active layout
      for (const key of Object.keys(this._multiWs)) {
        const tid = Number(key);
        if (!uniqueIds.includes(tid)) {
          try {
            this._multiWs[tid].close();
          } catch (_) {}
          delete this._multiWs[tid];
          const nextStatus = { ...this.multiWsStatus };
          delete nextStatus[tid];
          this.multiWsStatus = nextStatus;
        }
      }

      // Open sockets only for already running panes & fetch zones
      for (const tid of uniqueIds) {
        const task = this.tasks.find((t) => t.id === tid);
        const existing = this._multiWs[tid];
        if (
          task?.status === 'running' &&
          (!existing || (existing.readyState !== WebSocket.OPEN && existing.readyState !== WebSocket.CONNECTING))
        ) {
          this.connectTaskWs(tid);
        }
        if (!this.multiZones[tid]) {
          this.fetchZonesForTask(tid);
        }
      }
    },

    disconnectMultiStreams() {
      for (const key of Object.keys(this._multiWs)) {
        const tid = Number(key);
        try {
          this._multiWs[tid].close();
        } catch (_) {}
      }
      this._multiWs = {};
      this.multiWsStatus = {};
    },
  },
});
