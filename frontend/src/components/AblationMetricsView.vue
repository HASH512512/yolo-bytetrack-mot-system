<template>
  <div class="space-y-4">
    <!-- Top Summary Banner -->
    <div class="card bg-gradient-to-r from-slate-900 via-indigo-950/40 to-slate-900 border-indigo-900/40">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div class="space-y-1.5">
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="text-lg font-bold text-slate-100 flex items-center gap-2">
              <span>&#x1F3AF;</span>
              <span>ByteTrack 算法消融实验与流水线性能评测</span>
            </h2>

            <!-- Prominent data_kind Isolation Badge -->
            <span
              v-if="isRealBenchmarkData"
              class="badge badge-green text-xs gap-1"
            >
              <span>&#x2705;</span>
              <span>
                真实实测数据 ({{ primaryDataKind === 'public_benchmark' ? '公开数据集实测' : '自采真实视频实测' }})
              </span>
            </span>
            <span
              v-else
              class="badge badge-amber text-xs gap-1"
            >
              <span>&#x26A0;&#xFE0F;</span>
              <span>合成基准数据 (synthetic) — 用于算法模块功能验证</span>
            </span>
          </div>

          <p class="text-xs text-slate-400">
            <span>
              以保留完整恢复池的标准 ByteTrack (official_baseline) 为对照组，依次增量评测 Class-Aware、NSA-Kalman、DIoU+VDC/ORU 与 Lazy HSV 外观门控。
            </span>
          </p>
        </div>

        <button
          @click="handleRefreshAll"
          :disabled="store.ablationLoading"
          class="btn btn-primary text-xs py-1.5 px-3 self-start md:self-auto shrink-0"
        >
          <span>{{ store.ablationLoading ? '评测中...' : '\u{1F504} 重新加载消融与压测数据' }}</span>
        </button>
      </div>
    </div>

    <!-- Comparative Table -->
    <div class="card space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <span class="text-sm font-bold text-slate-100">算法消融实验指标对比表 (MOTA / IDF1 / IDSW / FPS)</span>
        <span v-if="primaryExpId" class="badge badge-blue text-[11px]">
          <span>实验锁 ID: {{ primaryExpId }}</span>
        </span>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-xs text-left">
          <thead class="text-slate-400 bg-slate-800/80 border-b border-slate-700">
            <tr>
              <th class="py-2.5 px-3 font-semibold">算法配置 (Variant)</th>
              <th class="py-2.5 px-3 font-semibold text-center">数据来源 (data_kind)</th>
              <th class="py-2.5 px-3 font-semibold text-center">MOTA &uarr; (%)</th>
              <th class="py-2.5 px-3 font-semibold text-center">IDF1 &uarr; (%)</th>
              <th class="py-2.5 px-3 font-semibold text-center">ID Switches &darr;</th>
              <th class="py-2.5 px-3 font-semibold text-center">跟踪 FPS &uarr;</th>
              <th class="py-2.5 px-3 font-semibold text-center">P95 延迟 (ms)</th>
              <th class="py-2.5 px-3 font-semibold text-center">主要跟踪 (MT %)</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800">
            <tr
              v-for="item in store.ablationResults"
              :key="item.method"
              class="hover:bg-slate-800/50 transition-colors"
              :class="item.method.includes('Full') ? 'bg-indigo-950/20 font-medium' : ''"
            >
              <td class="py-2.5 px-3">
                <div class="flex items-center gap-2">
                  <span
                    class="w-2 h-2 rounded-full shrink-0"
                    :class="item.method.includes('Full') ? 'bg-emerald-400' : 'bg-indigo-400'"
                  ></span>
                  <span class="text-slate-200">{{ item.method }}</span>
                  <span
                    v-if="item.method.includes('Full')"
                    class="badge badge-green text-[10px] ml-1"
                  >
                    <span>本设计完整方案</span>
                  </span>
                </div>
              </td>
              <td class="py-2.5 px-3 text-center">
                <span
                  class="badge text-[10px]"
                  :class="isRealKind(item.data_kind) ? 'badge-green' : 'badge-amber'"
                >
                  {{ formatDataKind(item.data_kind) }}
                </span>
              </td>
              <td class="py-2.5 px-3 text-center text-cyan-400 font-semibold">{{ item.mota.toFixed(2) }}%</td>
              <td class="py-2.5 px-3 text-center text-indigo-400 font-semibold">{{ item.idf1.toFixed(2) }}%</td>
              <td class="py-2.5 px-3 text-center">
                <span
                  class="badge text-[11px]"
                  :class="item.id_switches === 0 ? 'badge-green' : 'badge-amber'"
                >
                  <span>{{ item.id_switches }} 次</span>
                </span>
              </td>
              <td class="py-2.5 px-3 text-center text-slate-300">{{ item.fps.toFixed(0) }} Hz</td>
              <td class="py-2.5 px-3 text-center text-slate-300">{{ item.p95_latency_ms.toFixed(2) }} ms</td>
              <td class="py-2.5 px-3 text-center text-emerald-400">{{ item.mostly_tracked.toFixed(1) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Visual Chart Comparisons -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Chart 1: ID Switches & IDF1 Improvement -->
      <div class="card space-y-3">
        <span class="text-sm font-bold text-slate-100">IDF1 保持度与 ID Switches 改善柱状对比</span>
        <div class="space-y-3 pt-2">
          <div v-for="item in store.ablationResults" :key="`chart-${item.method}`" class="space-y-1">
            <div class="flex justify-between text-xs">
              <span class="text-slate-300">{{ item.method }}</span>
              <div class="flex gap-3">
                <span class="text-indigo-400 font-medium">IDF1: {{ item.idf1.toFixed(1) }}%</span>
                <span class="text-rose-400 font-medium">IDSW: {{ item.id_switches }}</span>
              </div>
            </div>
            <div class="w-full bg-slate-800 h-2.5 rounded-full overflow-hidden flex">
              <div
                class="bg-gradient-to-r from-indigo-500 to-cyan-400 h-full rounded-full transition-all duration-500"
                :style="{ width: `${Math.min(100, Math.max(0, item.idf1))}%` }"
              ></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Chart 2: Pipeline Stage Latency Breakdown -->
      <div class="card space-y-3">
        <span class="text-sm font-bold text-slate-100">实时流水线端到端耗时分解 (BatchInferenceScheduler)</span>
        <div class="space-y-3 pt-2">
          <div class="space-y-1">
            <div class="flex justify-between text-xs text-slate-400">
              <span>阶段分布占比</span>
              <span class="text-emerald-400 font-medium">总计: {{ stageTotalMs.toFixed(1) }} ms</span>
            </div>
            <div class="w-full h-4 bg-slate-800 rounded-lg overflow-hidden flex">
              <div
                class="bg-blue-500 h-full transition-all duration-300"
                :style="{ width: `${(store.systemMetrics.stage_latency.decode_ms / stageTotalMs) * 100}%` }"
                title="解码/预处理 (Decode)"
              ></div>
              <div
                class="bg-amber-500 h-full transition-all duration-300"
                :style="{ width: `${(store.systemMetrics.stage_latency.detect_ms / stageTotalMs) * 100}%` }"
                title="检测推理 (Detect)"
              ></div>
              <div
                class="bg-cyan-500 h-full transition-all duration-300"
                :style="{ width: `${(store.systemMetrics.stage_latency.track_ms / stageTotalMs) * 100}%` }"
                title="ByteTrack 关联 (Track)"
              ></div>
              <div
                class="bg-emerald-500 h-full transition-all duration-300"
                :style="{ width: `${(store.systemMetrics.stage_latency.rules_ms / stageTotalMs) * 100}%` }"
                title="规则引擎判定 (Rules)"
              ></div>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2 text-xs pt-2">
            <div class="flex items-center gap-2 p-2 rounded bg-slate-800/40">
              <span class="w-2.5 h-2.5 rounded bg-blue-500"></span>
              <div class="flex-1 flex justify-between">
                <span class="text-slate-400">视频解码/缩放</span>
                <span class="font-mono text-slate-200">{{ store.systemMetrics.stage_latency.decode_ms.toFixed(1) }}ms</span>
              </div>
            </div>
            <div class="flex items-center gap-2 p-2 rounded bg-slate-800/40">
              <span class="w-2.5 h-2.5 rounded bg-amber-500"></span>
              <div class="flex-1 flex justify-between">
                <span class="text-slate-400">检测推理</span>
                <span class="font-mono text-amber-300">{{ store.systemMetrics.stage_latency.detect_ms.toFixed(1) }}ms</span>
              </div>
            </div>
            <div class="flex items-center gap-2 p-2 rounded bg-slate-800/40">
              <span class="w-2.5 h-2.5 rounded bg-cyan-500"></span>
              <div class="flex-1 flex justify-between">
                <span class="text-slate-400">ByteTrack 关联</span>
                <span class="font-mono text-cyan-300">{{ store.systemMetrics.stage_latency.track_ms.toFixed(1) }}ms</span>
              </div>
            </div>
            <div class="flex items-center gap-2 p-2 rounded bg-slate-800/40">
              <span class="w-2.5 h-2.5 rounded bg-emerald-500"></span>
              <div class="flex-1 flex justify-between">
                <span class="text-slate-400">规则引擎判定</span>
                <span class="font-mono text-emerald-300">{{ store.systemMetrics.stage_latency.rules_ms.toFixed(1) }}ms</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- System Engineering Benchmarks Section -->
    <div class="card space-y-4 border-emerald-900/40 bg-slate-900/90">
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
        <div>
          <h3 class="text-sm md:text-base font-bold text-slate-100 flex items-center gap-2">
            <span>&#x1F512;</span>
            <span>系统工程压测数据</span>
            <span
              v-if="store.lockedBenchmarks.available && systemBenchmarkExp"
              class="badge badge-green text-[10px]"
            >
              <span>已加载</span>
            </span>
            <span v-else class="badge badge-gray text-[10px]">
              <span>暂无压测数据</span>
            </span>
          </h3>
          <p v-if="systemBenchmarkExp" class="text-[11px] text-slate-400 mt-1">
            <span>数据文件: {{ systemBenchmarkExp.lock_file || 'benchmarks/experiments/system_benchmark.json' }}</span>
            <span v-if="systemBenchmarkExp.created_at" class="ml-3">
              <span>生成时间: {{ systemBenchmarkExp.created_at }}</span>
            </span>
            <span v-if="systemBenchmarkExp.git_commit" class="ml-3 font-mono">
              <span>Commit: {{ systemBenchmarkExp.git_commit }}</span>
            </span>
          </p>
        </div>

        <button
          @click="store.fetchLockedBenchmarks()"
          class="btn btn-secondary text-xs py-1 px-3"
        >
          <span>&#x1F504; 刷新压测数据</span>
        </button>
      </div>

      <!-- Empty / Unavailable Placeholder -->
      <div
        v-if="!store.lockedBenchmarks.available || Object.keys(store.lockedBenchmarks.experiments).length === 0"
        class="p-6 rounded-lg border border-dashed border-slate-700 bg-slate-950/50 text-center space-y-2 text-xs text-slate-400"
      >
        <div class="text-base font-semibold text-slate-300">尚未读取到系统工程压测数据</div>
        <p>
          <span>可将本地压测 JSON 放入 benchmarks/experiments/ 并生成 benchmarks/manifest.json 后刷新展示。</span>
        </p>
      </div>

      <!-- System Benchmark Tables -->
      <div v-else class="space-y-4">
        <div v-if="systemBenchmarkExp" class="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <!-- 1. sqlite_wal_perf -->
          <div class="p-3 rounded-lg bg-slate-950/70 border border-slate-800 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-cyan-300">sqlite_wal_perf (SQLite WAL 落库与复合索引查询)</span>
              <span class="badge badge-blue text-[10px]">system_benchmark</span>
            </div>
            <div v-if="sqlitePerfTable && sqlitePerfTable.rows.length > 0" class="overflow-x-auto">
              <table class="w-full text-[11px] text-left">
                <thead class="text-slate-400 bg-slate-800/80 border-b border-slate-700">
                  <tr>
                    <th v-for="h in sqlitePerfTable.headers" :key="h" class="py-1.5 px-2 font-semibold">{{ h }}</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/80">
                  <tr v-for="(row, rIdx) in sqlitePerfTable.rows" :key="`sql-${rIdx}`" class="hover:bg-slate-800/40">
                    <td v-for="(cell, cIdx) in row" :key="`sql-${rIdx}-${cIdx}`" class="py-1.5 px-2 font-mono text-slate-200">
                      {{ formatMetricCell(cell) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="text-xs text-slate-500 py-4 text-center">
              <span>暂无 sqlite_wal_perf 压测明细</span>
            </div>
          </div>

          <!-- 2. scheduler_overhead -->
          <div class="p-3 rounded-lg bg-slate-950/70 border border-slate-800 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-indigo-300">scheduler_overhead (批处理推理调度器纯后处理开销)</span>
              <span class="badge badge-blue text-[10px]">system_benchmark</span>
            </div>
            <div v-if="schedulerOverheadTable && schedulerOverheadTable.rows.length > 0" class="overflow-x-auto">
              <table class="w-full text-[11px] text-left">
                <thead class="text-slate-400 bg-slate-800/80 border-b border-slate-700">
                  <tr>
                    <th v-for="h in schedulerOverheadTable.headers" :key="h" class="py-1.5 px-2 font-semibold">{{ h }}</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/80">
                  <tr v-for="(row, rIdx) in schedulerOverheadTable.rows" :key="`sch-${rIdx}`" class="hover:bg-slate-800/40">
                    <td v-for="(cell, cIdx) in row" :key="`sch-${rIdx}-${cIdx}`" class="py-1.5 px-2 font-mono text-slate-200">
                      {{ formatMetricCell(cell) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="text-xs text-slate-500 py-4 text-center">
              <span>暂无 scheduler_overhead 压测明细</span>
            </div>
          </div>

          <!-- 3. multistream_concurrency -->
          <div class="p-3 rounded-lg bg-slate-950/70 border border-slate-800 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-emerald-300">multistream_concurrency (多路并发流跨流合批压测)</span>
              <span class="badge badge-green text-[10px]">MultiStream</span>
            </div>
            <div v-if="multistreamTable && multistreamTable.rows.length > 0" class="overflow-x-auto">
              <table class="w-full text-[11px] text-left">
                <thead class="text-slate-400 bg-slate-800/80 border-b border-slate-700">
                  <tr>
                    <th v-for="h in multistreamTable.headers" :key="h" class="py-1.5 px-2 font-semibold">{{ h }}</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/80">
                  <tr v-for="(row, rIdx) in multistreamTable.rows" :key="`ms-${rIdx}`" class="hover:bg-slate-800/40">
                    <td v-for="(cell, cIdx) in row" :key="`ms-${rIdx}-${cIdx}`" class="py-1.5 px-2 font-mono text-slate-200">
                      {{ formatMetricCell(cell) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="text-xs text-slate-500 py-4 text-center border border-dashed border-slate-800 rounded">
              <span>尚未读取到 multistream_concurrency 压测表。</span>
            </div>
          </div>

          <!-- 4. adaptive_skip_overhead -->
          <div class="p-3 rounded-lg bg-slate-950/70 border border-slate-800 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-amber-300">adaptive_skip_overhead (自适应关键帧跳帧调度开销)</span>
              <span class="badge badge-amber text-[10px]">Adaptive Skip</span>
            </div>
            <div v-if="adaptiveSkipTable && adaptiveSkipTable.rows.length > 0" class="overflow-x-auto">
              <table class="w-full text-[11px] text-left">
                <thead class="text-slate-400 bg-slate-800/80 border-b border-slate-700">
                  <tr>
                    <th v-for="h in adaptiveSkipTable.headers" :key="h" class="py-1.5 px-2 font-semibold">{{ h }}</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/80">
                  <tr v-for="(row, rIdx) in adaptiveSkipTable.rows" :key="`askip-${rIdx}`" class="hover:bg-slate-800/40">
                    <td v-for="(cell, cIdx) in row" :key="`askip-${rIdx}-${cIdx}`" class="py-1.5 px-2 font-mono text-slate-200">
                      {{ formatMetricCell(cell) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="text-xs text-slate-500 py-4 text-center border border-dashed border-slate-800 rounded">
              <span>尚未读取到 adaptive_skip_overhead 压测表。</span>
            </div>
          </div>
        </div>

        <!-- Other Benchmark Experiments -->
        <div v-if="otherLockedExperiments.length > 0" class="space-y-2 pt-2">
          <div class="text-xs font-bold text-slate-300">其他真实实验记录 (manifest.json)</div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div
              v-for="item in otherLockedExperiments"
              :key="item.expId"
              class="p-3 rounded-lg bg-slate-950/60 border border-slate-800 text-xs space-y-1.5"
            >
              <div class="flex items-center justify-between">
                <span class="font-mono font-bold text-cyan-300">{{ item.expId }}</span>
                <span class="badge text-[10px]" :class="isRealKind(item.exp.data_kind) ? 'badge-green' : 'badge-blue'">
                  {{ formatDataKind(item.exp.data_kind) }}
                </span>
              </div>
              <div class="text-[11px] text-slate-400">
                <span>数据文件: {{ item.exp.lock_file || '-' }}</span>
                <span v-if="item.exp.created_at" class="ml-2">| {{ item.exp.created_at }}</span>
              </div>
              <div v-if="item.exp.table_ids && item.exp.table_ids.length > 0" class="flex flex-wrap gap-1">
                <span v-for="tid in item.exp.table_ids" :key="tid" class="badge badge-gray text-[10px]">
                  {{ tid }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Architecture & Module Design Notes -->
    <div class="card space-y-3 bg-slate-900/80 border-slate-800">
      <span class="text-sm font-bold text-slate-200">核心模块改进与工程实现说明</span>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
        <div class="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40 space-y-1">
          <span class="font-semibold text-indigo-400">1. True ByteTrack Baseline</span>
          <p class="text-slate-400 text-[11px] leading-relaxed">
            <span>遵循 ByteTrack 两阶段高低置信度匹配与 30 帧丢失恢复池 (official_baseline)，保证各模块消融对比基准一致。</span>
          </p>
        </div>
        <div class="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40 space-y-1">
          <span class="font-semibold text-cyan-400">2. Class-Aware + VDC / ORU</span>
          <p class="text-slate-400 text-[11px] leading-relaxed">
            <span>类别感知代价抑制行人/车辆跨类误配；VDC 速度方向一致性约束转弯交会；ORU 以观测为中心的虚拟插值显著缓解长遮挡后的卡尔曼漂移。</span>
          </p>
        </div>
        <div class="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40 space-y-1">
          <span class="font-semibold text-emerald-400">3. Lazy HSV 惰性外观门控</span>
          <p class="text-slate-400 text-[11px] leading-relaxed">
            <span>不引入重型 ReID 推理网络，仅在目标处于 LOST 重连或代价矩阵存在模糊竞争行时按需提取上下半区 HSV 直方图余弦距离，兼顾精度与速度。</span>
          </p>
        </div>
        <div class="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40 space-y-1">
          <span class="font-semibold text-amber-400">4. 自适应跳帧 + ONNX 后端</span>
          <p class="text-slate-400 text-[11px] leading-relaxed">
            <span>结合运动加速度与 ROI 边界守卫动态触发关键帧检测或卡尔曼 predict_only 补帧；ONNXDetectorAdapter 零 PyTorch 依赖实现检测后端无缝热插拔。</span>
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useMotStore } from '../stores/motStore';
import type { BenchmarkExperiment, BenchmarkTable } from '../types';

const store = useMotStore();

const METRIC_HEADER_MAP: Record<string, string> = {
  rows: '轨迹行数',
  write_elapsed_ms: '写入耗时 (ms)',
  write_rows_per_sec: '写入吞吐 (rows/s)',
  db_size_mb: '体积 (MB)',
  query_p50_ms: '查询 P50 (ms)',
  query_p95_ms: '查询 P95 (ms)',
  summary_p50_ms: '摘要 P50 (ms)',
  summary_p95_ms: '摘要 P95 (ms)',
  trajectory_p50_ms: '检索 P50 (ms)',
  trajectory_p95_ms: '检索 P95 (ms)',
  batch_size: 'Batch Size',
  frames: '帧数',
  avg_track_ms: '平均跟踪 (ms)',
  avg_rules_ms: '平均规则 (ms)',
  p95_track_plus_rules_ms: '跟踪+规则 P95 (ms)',
  pipeline_non_detect_fps: '非检测吞吐 (FPS)',
  stream_count: '并发流数',
  streams: '并发流数',
  num_streams: '并发流数',
  aggregate_fps: '聚合 FPS',
  per_stream_fps: '单流平均 FPS',
  p95_latency_ms: 'P95 延迟 (ms)',
  dropped_frames: '丢帧数',
  drop_count: '丢帧数',
  avg_batch_size: '平均合批大小',
  stride: '最大步长',
  max_skip_stride: '最大步长',
  detect_skip_ratio: '检测跳过率',
  effective_fps: '有效吞吐 (FPS)',
  e2e_fps: '端到端 FPS',
};

function isRealKind(kind?: string): boolean {
  return kind === 'public_benchmark' || kind === 'self_collected';
}

function formatDataKind(kind?: string): string {
  if (kind === 'public_benchmark') return 'public_benchmark (真实公开库)';
  if (kind === 'self_collected') return 'self_collected (自采实测)';
  if (kind === 'system_benchmark') return 'system_benchmark (工程实测)';
  return 'synthetic (合成诊断)';
}

const primaryDataKind = computed(() => {
  const first = store.ablationResults[0];
  return first?.data_kind || 'synthetic';
});

const primaryExpId = computed(() => {
  return store.ablationResults.find((r) => r.exp_id)?.exp_id || null;
});

const isRealBenchmarkData = computed(() => isRealKind(primaryDataKind.value));

const stageTotalMs = computed(() => {
  const s = store.systemMetrics.stage_latency;
  const sum = (s.decode_ms || 0) + (s.detect_ms || 0) + (s.track_ms || 0) + (s.rules_ms || 0);
  return Math.max(0.1, sum);
});

const systemBenchmarkExp = computed<BenchmarkExperiment | null>(() => {
  return store.lockedBenchmarks.experiments?.system_benchmark || null;
});

function buildTableFromFlatMetrics(
  flatMetrics: Record<string, any> | undefined,
  prefixes: string[],
  title: string
): BenchmarkTable | null {
  if (!flatMetrics) return null;
  const rowMap: Record<string, Record<string, any>> = {};
  const colOrder: string[] = [];

  for (const [fullKey, val] of Object.entries(flatMetrics)) {
    const matchedPrefix = prefixes.find((p) => fullKey.startsWith(p));
    if (!matchedPrefix) continue;
    const rest = fullKey.slice(matchedPrefix.length);
    const dotIdx = rest.indexOf('.');
    if (dotIdx === -1) continue;
    const rowKey = rest.slice(0, dotIdx);
    const colKey = rest.slice(dotIdx + 1);
    if (!rowMap[rowKey]) rowMap[rowKey] = {};
    rowMap[rowKey][colKey] = val;
    if (!colOrder.includes(colKey)) {
      colOrder.push(colKey);
    }
  }

  const rowKeys = Object.keys(rowMap);
  if (rowKeys.length === 0 || colOrder.length === 0) return null;

  const headers = colOrder.map((c) => METRIC_HEADER_MAP[c] || c);
  const rows = rowKeys.map((rk) => colOrder.map((ck) => rowMap[rk][ck] ?? '-'));
  return { title, headers, rows };
}

const sqlitePerfTable = computed<BenchmarkTable | null>(() => {
  const exp = systemBenchmarkExp.value;
  if (!exp) return null;
  if (exp.tables?.sqlite_wal_perf) return exp.tables.sqlite_wal_perf;
  return buildTableFromFlatMetrics(
    exp.flat_metrics,
    ['system_benchmark.sqlite_bench.', 'system_benchmark.sqlite_wal_perf.'],
    'SQLite WAL 轨迹批量落库与索引查询实测'
  );
});

const schedulerOverheadTable = computed<BenchmarkTable | null>(() => {
  const exp = systemBenchmarkExp.value;
  if (!exp) return null;
  if (exp.tables?.scheduler_overhead) return exp.tables.scheduler_overhead;
  return buildTableFromFlatMetrics(
    exp.flat_metrics,
    ['system_benchmark.scheduler_bench.', 'system_benchmark.scheduler_overhead.'],
    'BatchInferenceScheduler 批处理调度开销实测'
  );
});

const multistreamTable = computed<BenchmarkTable | null>(() => {
  const exp = systemBenchmarkExp.value;
  if (!exp) return null;
  if (exp.tables?.multistream_concurrency) return exp.tables.multistream_concurrency;
  return buildTableFromFlatMetrics(
    exp.flat_metrics,
    [
      'system_benchmark.multistream_concurrency.',
      'system_benchmark.multistream_bench.',
      'system_benchmark.multistream.',
    ],
    'MultiStreamBatchCoordinator 多路并发压测'
  );
});

const adaptiveSkipTable = computed<BenchmarkTable | null>(() => {
  const exp = systemBenchmarkExp.value;
  if (!exp) return null;
  if (exp.tables?.adaptive_skip_overhead) return exp.tables.adaptive_skip_overhead;
  return buildTableFromFlatMetrics(
    exp.flat_metrics,
    [
      'system_benchmark.adaptive_skip_overhead.',
      'system_benchmark.adaptive_skip_bench.',
      'system_benchmark.adaptive_skip.',
    ],
    'AdaptiveKeyframeController 自适应跳帧开销压测'
  );
});

const otherLockedExperiments = computed(() => {
  const exps = store.lockedBenchmarks.experiments || {};
  return Object.entries(exps)
    .filter(([k]) => k !== 'system_benchmark')
    .map(([expId, exp]) => ({ expId, exp }));
});

function formatMetricCell(val: string | number): string {
  if (typeof val === 'number') {
    if (Number.isInteger(val)) return String(val);
    return val.toFixed(val < 1 ? 4 : 2);
  }
  return String(val ?? '-');
}

async function handleRefreshAll() {
  await Promise.all([store.fetchAblation(), store.fetchLockedBenchmarks()]);
}
</script>
