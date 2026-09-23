/**
 * 性能探针的热路径那一半（ADR 0075）。
 *
 * **叶子模块**：不 import 任何 store / 组件。热路径（`trackPointer`、
 * `documentStore.txnUpdate`、预览平面的逐帧写入、几个画布组件的函数体）只
 * import 这一个文件；录制的编排、store 订阅、DOM 统计与报告组装在
 * `perf/session.ts`，只被探针自己的界面 import——反过来就会绕出 import 环。
 *
 * **没在录制时零成本**：每个入口的第一句都是 `if (!rec)`，不下
 * `performance.mark`、不分配对象、不挂任何监听。探针是给少数人排查用的，
 * 不许让所有人为它付钱。
 *
 * 量的主语（写判据之前先说清）：
 * - **帧**：rAF 回调时间戳之差 = 这一帧的间隔。间隔超过刷新周期 = 掉帧。
 * - **一帧里时间花在哪**：从上一次 rAF 回调到这一次之间，
 *   `input.handler`（pointermove 的业务处理，含 zustand 同步通知）+
 *   `input.react_flush`（React 在紧随其后的微任务里同步重渲染）+
 *   `raf.*`（rAF 里的脚本，如预览写 DOM）+ `render`（浏览器在主线程上的
 *   样式 / 布局 / 绘制：rAF 回调开始 → 同一帧渲染结束后第一个任务）。
 *   剩下的记作「未归因」——GC、别的任务、没插桩的代码。
 * - **输入延迟**：pointermove 的 `event.timeStamp` → 它之后第一次渲染结束。
 *
 * 这些都是主线程视角：合成器 / GPU 线程上的卡顿主线程看不见，只会表现成
 * 「间隔长、但各段都不大」，分析端把它单独当一档说出来，不假装归因。
 */

/** 插桩点的闭集。改名 = 报告格式变了，分析端 `scripts/perf_report.py` 同步改 */
export type PerfSpan =
  /** trackPointer 的 onMove 整段（含其中的 txnUpdate / 吸附 / store 写入） */
  | 'input.handler'
  /** onMove 返回后的微任务：React 对 useSyncExternalStore 订阅的同步重渲染 */
  | 'input.react_flush'
  /** documentStore.txnUpdate：immer produce + 补丁累加 + 通知订阅者 */
  | 'doc.txn_update'
  /** 画布对象拖动的吸附计算 */
  | 'snap.compute'
  /** 预览平面在 rAF 里把攒下的位移 / 样式写进 SVG DOM */
  | 'raf.preview_write'
  /**
   * 自动保存的同步那一段（buildProject + JSON.stringify + 写本机副本）。
   * 它在最后一次编辑 1 秒后触发——常常正好落在下一次拖动中间
   */
  | 'autosave.flush'

export const PERF_SPANS: readonly PerfSpan[] = [
  'input.handler',
  'input.react_flush',
  'doc.txn_update',
  'snap.compute',
  'raf.preview_write',
  'autosave.flush',
]

/** 每帧一行：[间隔, 主线程渲染, handler, react_flush, rAF 脚本, pointermove 数, 输入延迟] */
export type FrameRow = [number, number | null, number, number, number, number, number | null]

export interface SpanStat {
  count: number
  total: number
  max: number
  /** 采样（毫秒，一位小数），分析端算分位数用；超过上限后均匀抽稀 */
  samples: number[]
}

export interface Segment {
  kind: string
  source: 'user' | 'synthetic'
  /** 自动测试的哪一轮（如 `m1` = 每帧 1 个 pointermove）；用户操作为 null */
  label: string | null
  /** 相对录制开始（毫秒） */
  start: number
  end: number | null
  /** 松手之后还会继续记一小段（commit / 权威渲染 / 自动保存那一下卡顿常在这里） */
  tailUntil: number | null
  frames: FrameRow[]
  tail: FrameRow[]
  moves: number
  spans: Partial<Record<PerfSpan, SpanStat>>
  counts: Record<string, number>
  /**
   * 松手之后那 600ms 里的计数与 span，**与拖动中分开记**：松手后的悬停
   * pointermove、commit、权威 SVG 换上来都会让组件重渲染，混进 `counts`
   * 会让「每个 pointermove 引起几次渲染」的分子多出一截、分母却没变
   */
  tailSpans: Partial<Record<PerfSpan, SpanStat>>
  tailCounts: Record<string, number>
  /** 片段开始 / 结束那一刻由 session 填的上下文（DOM 规模、文档规模……） */
  context: Record<string, unknown>
}

interface FrameAcc {
  handler: number
  flush: number
  raf: number
  moves: number
  /** 这一帧里第一个 pointermove 的 timeStamp（算输入延迟） */
  firstMoveTs: number | null
}

interface Recording {
  t0: number
  idle: number[]
  segments: Segment[]
  current: Segment | null
  lastRaf: number | null
  acc: FrameAcc
  /** 上一帧的主线程渲染耗时：在渲染结束后的任务里才量得到，记进下一行 */
  pendingRender: number | null
  /**
   * 刚写下的那一行。它的 pointermove 就在这一帧里被画出来，所以输入延迟要在
   * 这一帧渲染结束时**补回这一行**——记进下一行的话，零星的真实拖动（不是每帧
   * 都有 move）会把延迟配给错的帧，甚至配给一个没有 move 的帧
   */
  lastRow: FrameRow | null
  rafHandle: number | null
  channel: MessageChannel | null
  renderStart: number
  latencyTs: number | null
  onMoveCapture: (ev: PointerEvent) => void
  listeners: Set<() => void>
  source: 'user' | 'synthetic'
  label: string | null
}

const TAIL_MS = 600
const MAX_FRAMES_PER_SEGMENT = 4000
const MAX_SEGMENTS = 120
const MAX_IDLE = 240
const MAX_SAMPLES = 2000

let rec: Recording | null = null

export const perfNow = (): number =>
  typeof performance !== 'undefined' ? performance.now() : Date.now()

export function perfActive(): boolean {
  return rec !== null
}

const round1 = (v: number) => Math.round(v * 10) / 10

function newAcc(): FrameAcc {
  return { handler: 0, flush: 0, raf: 0, moves: 0, firstMoveTs: null }
}

/** 这一刻的记录落到哪：拖动中的片段，或者刚结束那段的尾巴 */
function target(r: Recording): { seg: Segment; tail: boolean } | null {
  const s = r.current
  if (s) return { seg: s, tail: false }
  const last = r.segments.at(-1)
  if (last?.tailUntil != null && perfNow() - r.t0 <= last.tailUntil) return { seg: last, tail: true }
  return null
}

function addSpan(name: PerfSpan, ms: number): void {
  const r = rec
  if (!r) return
  if (name === 'input.handler') r.acc.handler += ms
  else if (name === 'input.react_flush') r.acc.flush += ms
  else if (name === 'raf.preview_write') r.acc.raf += ms
  const at = target(r)
  if (!at) return
  const bag = at.tail ? at.seg.tailSpans : at.seg.spans
  const st = (bag[name] ??= { count: 0, total: 0, max: 0, samples: [] })
  st.count++
  st.total += ms
  if (ms > st.max) st.max = ms
  if (st.samples.length < MAX_SAMPLES) st.samples.push(round1(ms))
  else st.samples[st.count % MAX_SAMPLES] = round1(ms)
}

/** 包一段同步代码计时。没在录制时就是直接调用 */
export function perfSpan<T>(name: PerfSpan, fn: () => T): T {
  if (!rec) return fn()
  const t0 = perfNow()
  try {
    return fn()
  } finally {
    addSpan(name, perfNow() - t0)
  }
}

/**
 * pointermove 的业务处理专用：除了量它自己，还在紧随其后的微任务里量 React
 * 的同步重渲染。useSyncExternalStore 的更新是 SyncLane，React 在 set 的当下
 * 排了一个微任务去 flush；这里排的微任务在它之后，所以两者之差就是那次 flush。
 */
export function perfInput(fn: () => void): void {
  if (!rec) {
    fn()
    return
  }
  const t0 = perfNow()
  try {
    fn()
  } finally {
    const t1 = perfNow()
    addSpan('input.handler', t1 - t0)
    queueMicrotask(() => addSpan('input.react_flush', perfNow() - t1))
  }
}

/** 计数（组件渲染次数、store 通知次数）。名字是开放的，分析端按前缀分组 */
export function perfCount(name: string): void {
  const r = rec
  if (!r) return
  const at = target(r)
  if (!at) return
  const bag = at.tail ? at.seg.tailCounts : at.seg.counts
  bag[name] = (bag[name] ?? 0) + 1
}

/* ----------------------------------------------------------------- 片段 */

export type SegmentHook = (seg: Segment, phase: 'begin' | 'end') => void
let segmentHook: SegmentHook | null = null

/** session 用它在片段边界采上下文（DOM / 文档规模）；core 自己不认识任何 store */
export function setSegmentHook(h: SegmentHook | null): void {
  segmentHook = h
}

/** 一次拖动开始（interactionStore.begin 调）。已有进行中的片段就先收掉 */
export function perfSegmentBegin(kind: string): void {
  const r = rec
  if (!r) return
  if (r.current) perfSegmentEnd()
  if (r.segments.length >= MAX_SEGMENTS) return
  const seg: Segment = {
    kind,
    source: r.source,
    label: r.label,
    start: round1(perfNow() - r.t0),
    end: null,
    tailUntil: null,
    frames: [],
    tail: [],
    moves: 0,
    spans: {},
    counts: {},
    tailSpans: {},
    tailCounts: {},
    context: {},
  }
  r.current = seg
  r.segments.push(seg)
  try {
    segmentHook?.(seg, 'begin')
  } catch {
    /* 探针绝不能把一次拖动弄挂 */
  }
  // 不在这里通知界面：拖动刚开始那一帧不该为探针自己的界面重渲染付钱
}

export function perfSegmentEnd(): void {
  const r = rec
  if (!r?.current) return
  const seg = r.current
  try {
    segmentHook?.(seg, 'end')
  } catch {
    /* 同上 */
  }
  const at = perfNow() - r.t0
  seg.end = round1(at)
  seg.tailUntil = round1(at + TAIL_MS)
  r.current = null
  notify(r)
}

/* ----------------------------------------------------------------- 帧循环 */

function onRaf(ts: number): void {
  const r = rec
  if (!r) return
  const cbStart = perfNow()
  if (r.lastRaf != null) {
    const dt = ts - r.lastRaf
    const a = r.acc
    const row: FrameRow = [
      round1(dt),
      r.pendingRender == null ? null : round1(r.pendingRender),
      round1(a.handler),
      round1(a.flush),
      round1(a.raf),
      a.moves,
      null, // 输入延迟：这一帧渲染结束时由 onAfterRender 补上
    ]
    r.lastRow = null
    const seg = r.current
    if (seg) {
      if (seg.frames.length < MAX_FRAMES_PER_SEGMENT) {
        seg.frames.push(row)
        r.lastRow = row
      }
    } else {
      const last = r.segments.at(-1)
      if (last?.tailUntil != null && cbStart - r.t0 <= last.tailUntil) {
        last.tail.push(row)
        r.lastRow = row
      } else if (r.idle.length < MAX_IDLE && r.segments.length === 0) r.idle.push(round1(dt))
    }
  }
  r.lastRaf = ts
  r.pendingRender = null
  r.latencyTs = r.acc.firstMoveTs
  r.acc = newAcc()
  r.renderStart = cbStart
  // 这一帧的渲染（其余 rAF 回调 + 样式 / 布局 / 绘制）结束后，第一个任务
  // 才会跑到这条消息；两者之差就是主线程上的渲染耗时
  r.channel?.port2.postMessage(0)
  r.rafHandle = requestAnimationFrame(onRaf)
}

function onAfterRender(): void {
  const r = rec
  if (!r) return
  const t = perfNow()
  r.pendingRender = t - r.renderStart
  if (r.latencyTs != null && r.lastRow) r.lastRow[6] = round1(t - r.latencyTs)
  r.latencyTs = null
}

/* ----------------------------------------------------------------- 录制 */

export function perfStart(): boolean {
  if (rec) return false
  if (typeof requestAnimationFrame !== 'function' || typeof window === 'undefined') return false
  const channel = typeof MessageChannel === 'function' ? new MessageChannel() : null
  const r: Recording = {
    t0: perfNow(),
    idle: [],
    segments: [],
    current: null,
    lastRaf: null,
    acc: newAcc(),
    pendingRender: null,
    lastRow: null,
    rafHandle: null,
    channel,
    renderStart: 0,
    latencyTs: null,
    listeners: new Set(),
    source: 'user',
    label: null,
    onMoveCapture: (ev: PointerEvent) => {
      const cur = rec
      if (!cur) return
      cur.acc.moves++
      if (cur.acc.firstMoveTs == null) cur.acc.firstMoveTs = ev.timeStamp
      if (cur.current) cur.current.moves++
    },
  }
  if (channel) channel.port1.onmessage = onAfterRender
  // 捕获阶段、最先注册：pointermove 的计数与时间戳不受业务监听器影响
  window.addEventListener('pointermove', r.onMoveCapture, { capture: true, passive: true })
  rec = r
  r.rafHandle = requestAnimationFrame(onRaf)
  return true
}

export interface PerfRaw {
  duration_ms: number
  idle_frame_ms: number[]
  segments: Segment[]
}

/** 停止录制并交出原始数据；没在录制返回 null */
export function perfStop(): PerfRaw | null {
  const r = rec
  if (!r) return null
  if (r.current) perfSegmentEnd()
  if (r.rafHandle != null) cancelAnimationFrame(r.rafHandle)
  window.removeEventListener('pointermove', r.onMoveCapture, { capture: true })
  if (r.channel) {
    r.channel.port1.onmessage = null
    r.channel.port1.close()
  }
  rec = null
  const out = { duration_ms: round1(perfNow() - r.t0), idle_frame_ms: r.idle, segments: r.segments }
  for (const l of r.listeners) l()
  return out
}

/** 接下来的片段记成「自动测试」还是「用户操作」 */
export function perfSetSource(source: 'user' | 'synthetic', label: string | null = null): void {
  if (!rec) return
  rec.source = source
  rec.label = label
}

/** 片段数变化时通知探针界面（只在片段边界触发，不在每帧触发） */
export function perfSubscribe(fn: () => void): () => void {
  const r = rec
  if (!r) return () => {}
  r.listeners.add(fn)
  return () => r.listeners.delete(fn)
}

function notify(r: Recording): void {
  for (const l of r.listeners) {
    try {
      l()
    } catch {
      /* 同上 */
    }
  }
}

export function perfSegmentCount(): number {
  return rec?.segments.length ?? 0
}

export function perfCurrentSegment(): Segment | null {
  return rec?.current ?? null
}
