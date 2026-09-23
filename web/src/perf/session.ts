/**
 * 性能探针的编排那一半（ADR 0075）：开始 / 结束录制、在片段边界采上下文、
 * 组装报告、交给用户保存。热路径的计时在 `perf/core.ts`（叶子），这里可以
 * 自由 import store——只有探针自己的界面 import 这个文件。
 *
 * **报告里只有数字**：没有文件名、面板 id、图内文字、脚本路径。上下文一律是
 * 计数（对象几个、SVG 节点几个）与枚举（拖动种类、预览表示法）。报告由用户
 * 自己存下来、自己决定发给谁，Tavotto 不上传（与诊断包同一条隐私线）。
 */
import { isDesktop } from '@/lib/desktop'
import { apiUrl, withProject } from '@/lib/session'
import { previewTimings } from '@/lib/previewTrace'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import {
  perfActive,
  perfCount,
  perfNow,
  perfStart,
  perfStop,
  setSegmentHook,
  type PerfRaw,
  type RenderRecord,
  type Segment,
} from './core'

export const REPORT_SCHEMA = 'tavotto-perf-probe/1'

interface Live {
  startedAt: string
  unsubs: (() => void)[]
  system: Promise<Record<string, unknown> | null>
  clientStart: Record<string, unknown>
  previewRingStart: number
}

let live: Live | null = null

/** 订阅这些 store 的通知次数：拖动期间谁在被频繁写，一眼就能看出来 */
const STORES = {
  interaction: useInteractionStore,
  selection: useSelectionStore,
  render: useRenderStore,
  ui: useUiStore,
  viewport: useViewportStore,
  workspace: useWorkspaceStore,
} as const

export function probeRunning(): boolean {
  return live !== null && perfActive()
}

export function startProbe(): boolean {
  if (live) return false
  if (!perfStart()) return false
  const unsubs = Object.entries(STORES).map(([name, store]) =>
    store.subscribe(() => perfCount(`store.${name}`)),
  )
  // documentStore 分三类记：同一个 store 里既有文档本体也有保存状态，只数通知
  // 分不清「拖动途中改了文档」与「上一次松手 1 秒后的自动保存在改 saveState」
  unsubs.push(
    useDocumentStore.subscribe((s, prev) => {
      perfCount('store.document')
      if (s.doc !== prev.doc) perfCount('store.document.doc')
      else if (s.saveState !== prev.saveState || s.dirty !== prev.dirty) perfCount('store.document.save')
      else perfCount('store.document.other')
    }),
  )
  setSegmentHook(onSegment)
  live = {
    startedAt: new Date().toISOString(),
    unsubs,
    system: fetchSystemFacts(),
    clientStart: clientFacts(),
    previewRingStart: previewTimings().length,
  }
  return true
}

/** 放弃这次录制：什么都不留 */
export function cancelProbe(): void {
  if (!live) return
  teardown()
  perfStop()
}

export interface PerfReport {
  schema: typeof REPORT_SCHEMA
  created_at: string
  started_at: string
  duration_ms: number
  client: Record<string, unknown>
  system: Record<string, unknown> | null
  idle_frame_ms: number[]
  segments: Segment[]
  authority: { frames: number; moves: number; first_frame_ms: number | null; commit_to_authority_ms: number | null }[]
  /** 每次引擎渲染的时间线（ADR 0075「松手链路」）；只有数字 */
  renders: RenderRecord[]
}

export async function finishProbe(): Promise<PerfReport | null> {
  if (!live) return null
  const l = live
  teardown()
  const raw: PerfRaw | null = perfStop()
  if (!raw) return null
  const system = await l.system
  const ring = previewTimings()
  return {
    schema: REPORT_SCHEMA,
    created_at: new Date().toISOString(),
    started_at: l.startedAt,
    duration_ms: raw.duration_ms,
    client: { ...l.clientStart, end: clientFacts() },
    system,
    idle_frame_ms: raw.idle_frame_ms,
    segments: raw.segments,
    renders: raw.renders,
    // 图内元素拖动的「松手 → 权威 SVG 换上画布」：用户眼里的第二种卡。
    // 计时环里有面板 id，这里只取数字
    authority: ring.slice(Math.min(l.previewRingStart, ring.length)).map((t) => ({
      frames: t.preview_frame_count,
      moves: t.preview_move_count,
      first_frame_ms: t.preview_first_frame,
      commit_to_authority_ms: t.commit_to_authority_ms,
    })),
  }
}

function teardown(): void {
  if (!live) return
  for (const u of live.unsubs) u()
  setSegmentHook(null)
  live = null
}

/* ----------------------------------------------------------------- 上下文 */

function onSegment(seg: Segment, phase: 'begin' | 'end'): void {
  if (phase === 'begin') {
    const t0 = perfNow()
    seg.context = {
      ...domFacts(),
      ...documentFacts(),
      workspace_mode: useWorkspaceStore.getState().mode,
      zoom: round3(useViewportStore.getState().zoom),
      selected: useSelectionStore.getState().ids.length,
      // 分析器据此知道 store.document.* 三类计数是分开记的（旧版报告没有）
      doc_split: true,
    }
    // 采上下文本身要遍历 DOM：记下它花了多久，分析端把它从第一帧里扣掉
    seg.context.probe_overhead_ms = round1(perfNow() - t0)
  } else {
    // 事务里攒了多少条补丁：`history.accumulate` 每次 pointermove 都会整份复制
    const txn = useDocumentStore.getState().txn
    seg.context.txn_patches_at_end = txn ? txn.patches.length : null
  }
}

function domFacts(): Record<string, unknown> {
  if (typeof document === 'undefined') return {}
  const svgs = Array.from(document.querySelectorAll('svg'))
  let svgNodes = 0
  let maxSvg = 0
  for (const s of svgs) {
    const n = s.getElementsByTagName('*').length
    svgNodes += n
    if (n > maxSvg) maxSvg = n
  }
  return {
    dom_nodes: document.getElementsByTagName('*').length,
    svg_roots: svgs.length,
    svg_nodes: svgNodes,
    largest_svg_nodes: maxSvg,
    inline_images: document.querySelectorAll('svg image').length,
  }
}

function documentFacts(): Record<string, unknown> {
  const doc = useDocumentStore.getState().doc
  const objects = doc.objects
  let panels = 0
  let overrides = 0
  for (const o of objects) {
    if (o.type !== 'panel') continue
    panels++
    overrides += o.overrides.length
  }
  const modes: Record<string, number> = {}
  let svgBytes = 0
  for (const r of Object.values(useRenderStore.getState().byKey)) {
    const mode = r.preview?.mode ?? 'vector'
    modes[mode] = (modes[mode] ?? 0) + 1
    svgBytes += r.svgBytes || 0
  }
  return {
    objects: objects.length,
    panels,
    overrides,
    history_depth: useDocumentStore.getState().past.length,
    preview_modes: modes,
    resident_svg_kb: Math.round(svgBytes / 1024),
  }
}

function clientFacts(): Record<string, unknown> {
  if (typeof window === 'undefined') return {}
  const nav = navigator as Navigator & { deviceMemory?: number }
  let supported: string[] = []
  try {
    supported = [...(PerformanceObserver?.supportedEntryTypes ?? [])]
  } catch {
    supported = []
  }
  return {
    user_agent: nav.userAgent,
    platform: nav.platform,
    cores: nav.hardwareConcurrency ?? null,
    device_memory_gb: nav.deviceMemory ?? null,
    dpr: window.devicePixelRatio,
    screen: { w: window.screen?.width ?? null, h: window.screen?.height ?? null },
    viewport: { w: window.innerWidth, h: window.innerHeight },
    reduced_motion: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? null,
    entry_types: supported,
  }
}

async function fetchSystemFacts(): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(apiUrl('/api/perf/system'), withProject())
    if (!res.ok) return null
    const body = (await res.json()) as unknown
    return body && typeof body === 'object' ? (body as Record<string, unknown>) : null
  } catch {
    return null
  }
}

const round1 = (v: number) => Math.round(v * 10) / 10
const round3 = (v: number) => Math.round(v * 1000) / 1000

/* ----------------------------------------------------------------- 交付 */

/** 报告里给人看的一行：帧率与掉帧比例。判断「卡在哪」是分析端的事，这里不下结论 */
export function quickSummary(report: PerfReport): { segments: number; fps: number | null; jankPct: number | null } {
  const frames = report.segments.flatMap((s) => s.frames.map((f) => f[0]))
  if (!frames.length) return { segments: report.segments.length, fps: null, jankPct: null }
  const idle = [...report.idle_frame_ms].sort((a, b) => a - b)
  const refresh = idle.length >= 10 ? idle[Math.floor(idle.length / 2)] : 1000 / 60
  const mean = frames.reduce((a, b) => a + b, 0) / frames.length
  const jank = frames.filter((dt) => dt > refresh * 1.5).length
  return {
    segments: report.segments.length,
    fps: Math.round(1000 / mean),
    jankPct: Math.round((jank / frames.length) * 100),
  }
}

export function reportFilename(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `tavotto-perf-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.json`
  )
}

export interface SavedReport {
  name: string
  /** 后端写好的目录；null = 后端没接住，只走了浏览器下载 */
  dir: string | null
}

/**
 * 保存报告。**经后端写进数据目录**（`POST /api/perf/report`）：桌面壳的
 * WKWebView 没有注册下载处理器，`<a download>` 在那里会被静默取消——报告
 * 必须落在一个壳能在访达里显示出来的地方。浏览器模式另外照常给一份下载。
 */
export async function saveReport(report: PerfReport): Promise<SavedReport> {
  const body = JSON.stringify(report)
  try {
    const res = await fetch(
      apiUrl('/api/perf/report'),
      withProject({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body }),
    )
    if (res.ok) {
      const j = (await res.json()) as { dir?: unknown; name?: unknown }
      if (typeof j.dir === 'string' && typeof j.name === 'string') {
        if (!isDesktop()) downloadText(body, j.name)
        return { name: j.name, dir: j.dir }
      }
    }
  } catch {
    /* 落到下面的浏览器下载 */
  }
  const name = reportFilename()
  downloadText(body, name)
  return { name, dir: null }
}

function downloadText(text: string, name: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
  } finally {
    URL.revokeObjectURL(url)
  }
}
