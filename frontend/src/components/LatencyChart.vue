<template>
  <div class="card p-4 bg-slate-900/90 border border-slate-800 space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-2">
        <h3 class="text-sm font-semibold text-slate-100">&#x1F4C8; 实时流水线端到端耗时分解折线图 (ms)</h3>
        <span class="text-[11px] text-slate-400">（最近 {{ history.length }} 帧滚动窗口）</span>
      </div>

      <div class="flex flex-wrap items-center gap-3 text-xs font-mono">
        <span class="flex items-center gap-1.5 text-slate-200">
          <span class="w-3 h-0.5 bg-slate-100 inline-block"></span>
          <span>Total {{ metrics.stage_latency.total_ms.toFixed(1) }} ms</span>
        </span>
        <span class="flex items-center gap-1.5 text-rose-400">
          <span class="w-3 h-0.5 border-t border-dashed border-rose-400 inline-block"></span>
          <span>P95 {{ metrics.p95_latency_ms.toFixed(1) }} ms</span>
        </span>
        <span class="flex items-center gap-1.5 text-amber-400">
          <span class="w-3 h-0.5 bg-amber-400 inline-block"></span>
          <span>Detect {{ metrics.stage_latency.detect_ms.toFixed(1) }} ms</span>
        </span>
        <span class="flex items-center gap-1.5 text-sky-400">
          <span class="w-3 h-0.5 bg-sky-400 inline-block"></span>
          <span>Decode {{ metrics.stage_latency.decode_ms.toFixed(1) }} ms</span>
        </span>
        <span class="flex items-center gap-1.5 text-teal-400">
          <span class="w-3 h-0.5 bg-teal-400 inline-block"></span>
          <span>Track {{ metrics.stage_latency.track_ms.toFixed(1) }} ms</span>
        </span>
        <span class="flex items-center gap-1.5 text-emerald-400">
          <span class="w-3 h-0.5 bg-emerald-400 inline-block"></span>
          <span>Rules {{ metrics.stage_latency.rules_ms.toFixed(1) }} ms</span>
        </span>
      </div>
    </div>

    <div class="relative w-full h-44 bg-slate-950/90 rounded-lg border border-slate-800/80 overflow-hidden">
      <svg
        v-if="history.length >= 2"
        viewBox="0 0 800 180"
        preserveAspectRatio="none"
        class="w-full h-full block"
      >
        <g v-for="(tick, idx) in gridTicks" :key="`grid-${idx}`">
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

        <polyline
          fill="none"
          stroke="#fb7185"
          stroke-width="1.6"
          stroke-dasharray="5 4"
          :points="polylines.p95"
        />
        <polyline
          fill="none"
          stroke="#34d399"
          stroke-width="1.6"
          :points="polylines.rules"
        />
        <polyline
          fill="none"
          stroke="#2dd4bf"
          stroke-width="1.8"
          :points="polylines.track"
        />
        <polyline
          fill="none"
          stroke="#38bdf8"
          stroke-width="1.8"
          :points="polylines.decode"
        />
        <polyline
          fill="none"
          stroke="#fbbf24"
          stroke-width="2"
          :points="polylines.detect"
        />
        <polyline
          fill="none"
          stroke="#f1f5f9"
          stroke-width="2.2"
          :points="polylines.total"
        />
      </svg>
      <div
        v-else
        class="w-full h-full flex items-center justify-center text-xs text-slate-500"
      >
        <span>开启任务推流后将实时绘制各流水线阶段端到端耗时曲线...</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { LatencySample, SystemMetrics } from '../types';

const props = defineProps<{
  history: LatencySample[];
  metrics: SystemMetrics;
}>();

const svgMax = computed(() => {
  if (props.history.length === 0) return 20;
  let peak = 20;
  for (const s of props.history) {
    peak = Math.max(peak, s.total, s.p95, s.detect + s.decode + s.track + s.rules);
  }
  return Math.max(20, Math.ceil((peak * 1.1) / 10) * 10);
});

const gridTicks = computed(() => {
  const maxMs = svgMax.value;
  const topY = 16;
  const bottomY = 164;
  return [0, 0.333, 0.667, 1].map((frac) => {
    const val = Math.round(maxMs * frac);
    const y = Number((bottomY - frac * (bottomY - topY)).toFixed(1));
    return { y, label: `${val}` };
  });
});

function buildPolyline(key: keyof Omit<LatencySample, 'frameId'>): string {
  const pts = props.history;
  if (pts.length < 2) return '';
  const maxMs = Math.max(1, svgMax.value);
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

const polylines = computed(() => ({
  total: buildPolyline('total'),
  p95: buildPolyline('p95'),
  detect: buildPolyline('detect'),
  decode: buildPolyline('decode'),
  track: buildPolyline('track'),
  rules: buildPolyline('rules'),
}));
</script>
