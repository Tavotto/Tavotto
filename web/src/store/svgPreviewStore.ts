/**
 * 预览平面（transient preview）——与文档 / 历史平面严格分离的那一半。
 *
 * 分工（数据流唯一出处，改这里之前先读 docs 的 Phase F 一节与 CLAUDE.md）：
 *
 *   pointerdown  → beginPreview()：记下这一版 SVG 的身份与被碰元素的**原始**
 *                  transform / style，什么都还没改
 *   pointermove  → previewTransform / previewStyle：只动 DOM，rAF 合并成一帧。
 *                  **不 commit、不进历史、不发后端**
 *   pointerup    → commitPreview()：把最后一帧的值交给调用方写正式 override
 *                  （documentStore.commit → 一条历史），预览**继续挂着**
 *   权威 SVG 回来 → reattachPreview() 发现渲染键已变 → 预览连同旧 DOM 一起消失
 *   pointercancel→ cancelPreview()：DOM 还原，不写 override、不进历史、不渲染
 *
 * 两条纪律：
 *
 * 1. **临时 transform 永远从 base 现算，不做字符串累加。**
 *    旧实现直接 `setAttribute('transform', 'translate(…)')`，把 matplotlib
 *    自己的 transform 整个盖掉——`<image>` 的 `scale(1 -1) translate(…)` 就是
 *    这么被抹掉的（位图预览会上下翻）。现在写的是 `translate(…) <原始>`：
 *    平移发生在父坐标系里，原始变换原样保留。
 *
 * 2. **预览状态挂在「面板 + 这一版 SVG」上，不挂在 session 上。**
 *    连着拖两个元素时，第二次 begin 不能把第一次的预览位移当成 base
 *    （那会双倍位移，而且第一个元素的预览会在权威渲染回来之前弹回去）。
 */
import {
  adapterFor,
  applyStyleEdit,
  canStyleEditApply,
  restoreStyleEdits,
  unitsPerPt,
  type StyleEdit,
} from '@/lib/svgStyle'
import {
  newTiming,
  traceAuthority,
  traceCommit,
  traceFrame,
  type PreviewTiming,
} from '@/lib/previewTrace'

/**
 * 历史粒度。
 *   gesture ：一次连续拖动 / scrub / 输入会话 = 一条历史（默认）
 *   granular：每个有语义的控件变化各进一条历史，后端渲染仍可延迟到操作结束
 * 两种模式下**都必须经过 documentStore.commit**，区别只在事务边界。
 */
export type HistoryMode = 'gesture' | 'granular'

/**
 * 全局默认历史粒度。默认 gesture——一次拖动几百个 pointermove 各压一条撤销，
 * 撤销栈直接不可用。granular 留给「每个控件变化都要能单独撤销」的场合：
 * 它**只改事务边界，不改渲染策略**，后端渲染照样推迟到手势结束。
 * 目前没有界面开关（也不该有一个随手能拨错的开关），走这两个函数设置。
 */
let historyMode: HistoryMode = 'gesture'

export const getHistoryMode = (): HistoryMode => historyMode
export const setHistoryMode = (m: HistoryMode): void => {
  historyMode = m
}

export interface PreviewPatch {
  gid: string
  prop: string
  value: unknown
}

/** 挂在某个面板的**某一版 SVG** 上的预览；SVG 一换整份作废 */
interface PanelPreview {
  panelId: string
  renderKey: string
  /** gid → 这一版 SVG 里它原本的 transform（null = 本来就没有这个属性） */
  baseTransforms: Map<string, string | null>
  /**
   * gid → 采 base 时那个 DOM 节点本身。reattach 靠它分辨「React 真的重插了
   * SVG」还是「只是又渲染了一遍 React 组件」——认错的代价是重新采 base 时
   * 把**已经挪过的位置**当成原位，位移翻倍且再也还原不回去。
   */
  baseNodes: Map<string, Element>
  /** gid → 当前预览位移（figure 分数、y 向下） */
  transforms: Map<string, [number, number]>
  /** 样式改动的可逆账本（每个元素只记最早那次的整条 style 原文） */
  edits: StyleEdit[]
  /** `gid|prop` → 当前预览值，供重新挂载时重放 */
  styles: Map<string, PreviewPatch & { role: string }>
  sizeMm: readonly number[] | undefined
}

export interface PreviewSession {
  id: number
  panelId: string
  /** 建立 session 那一刻画布上挂的是哪一版渲染 */
  baseRenderKey: string
  baseRev: number
  historyMode: HistoryMode
  startedAt: number
  commitStartedAt: number | null
  /** 已提交、等着权威渲染追上来的那组正式 patch */
  pendingCommit: PreviewPatch[] | null
  /** 只有这个键的权威渲染回来才算「追上了」 */
  awaitKey: string | null
  cancelled: boolean
  /** 已经收尾（提交完成或已取消）；留着只是为了让 reattach 认领 */
  settled: boolean
  timing: PreviewTiming
}

let session: PreviewSession | null = null
let seq = 0
const panels = new Map<string, PanelPreview>()

/* -------------------------------------------------------------------------- */
/*  DOM 查找                                                                   */
/* -------------------------------------------------------------------------- */

/** 面板的内联 SVG。**一切 DOM 操作都必须先经过它**：预览绝不能越界到别的面板 */
export function findPanelSvg(panelId: string): SVGSVGElement | null {
  if (typeof document === 'undefined') return null
  const wrap = document.querySelector(`[data-element-svg="${cssEscape(panelId)}"]`)
  return wrap?.querySelector('svg') ?? null
}

/**
 * gid 对应的节点。**查不到不是异常**：manifest 的伪元素（误差棒 / 柱形系列 /
 * 刻度组）在 SVG 里根本没有 gid，`<image>` 的 gid 也不在 `<g>` 上——
 * 调用方据此回退到覆盖层预览 + 后端渲染，绝不抛错。
 */
export function findGidNode(svg: SVGSVGElement | null, gid: string): Element | null {
  if (!svg) return null
  return svg.querySelector(`[id="${cssEscape(gid)}"]`)
}

function cssEscape(v: string): string {
  return typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(v) : v.replace(/["\\]/g, '\\$&')
}

/* -------------------------------------------------------------------------- */
/*  session 生命周期                                                           */
/* -------------------------------------------------------------------------- */

export interface BeginOptions {
  panelId: string
  /** 现在画布上挂着的那一版渲染的键（renderKeyOf 或 latest 的退路） */
  renderKey: string
  rev?: number
  historyMode?: HistoryMode
  /** manifest 的 size_mm，linewidth 的 pt→user unit 换算要用 */
  sizeMm?: readonly number[]
}

export function beginPreview(opts: BeginOptions): number {
  // 上一轮还挂着（提交完还没等到权威渲染）：只是**交班**，绝不还原 DOM——
  // 还原等于让刚拖完的元素在权威渲染回来之前先弹回原位
  if (session && !session.settled) retire(session)

  const panel = panelStateFor(opts.panelId, opts.renderKey, opts.sizeMm)
  panel.sizeMm = opts.sizeMm ?? panel.sizeMm

  session = {
    id: ++seq,
    panelId: opts.panelId,
    baseRenderKey: opts.renderKey,
    baseRev: opts.rev ?? 0,
    historyMode: opts.historyMode ?? historyMode,
    startedAt: performance?.now?.() ?? Date.now(),
    commitStartedAt: null,
    pendingCommit: null,
    awaitKey: null,
    cancelled: false,
    settled: false,
    timing: newTiming(opts.panelId),
  }
  return session.id
}

function panelStateFor(
  panelId: string,
  renderKey: string,
  sizeMm?: readonly number[],
): PanelPreview {
  const cur = panels.get(panelId)
  // 这一版 SVG 已经换过了：旧账本对应的节点早就不在文档里，直接丢掉重开
  if (cur && cur.renderKey === renderKey) return cur
  const next: PanelPreview = {
    panelId,
    renderKey,
    baseTransforms: new Map(),
    baseNodes: new Map(),
    transforms: new Map(),
    edits: [],
    styles: new Map(),
    sizeMm,
  }
  panels.set(panelId, next)
  return next
}

/** 交班：session 收尾但 DOM 上的预览留着（等权威渲染来换） */
function retire(s: PreviewSession): void {
  s.settled = true
  if (s.pendingCommit == null) {
    // 没提交就被顶掉的（拖到一半又去拖别的）：那份预览没有正式值撑腰，
    // 留在画布上就是「看得见但撤销不了」，必须还原
    restorePanel(s.panelId)
  }
}

/**
 * 取消：DOM 还原到 base，不写 override、不进历史、不触发渲染。
 * pointercancel / lostpointercapture / 没真的移动过 都走这条。
 */
export function cancelPreview(): void {
  if (!session) return
  session.cancelled = true
  session.settled = true
  restorePanel(session.panelId)
  session = null
}

/**
 * 提交：调用方已经（或即将）用这组 patch 写正式 override。
 * 预览**继续挂在 DOM 上**，直到 awaitKey 那一版权威 SVG 换上来。
 */
export function commitPreview(patches: PreviewPatch[], awaitKey: string | null): void {
  if (!session) return
  session.pendingCommit = patches
  session.awaitKey = awaitKey
  session.commitStartedAt = performance?.now?.() ?? Date.now()
  traceCommit(session.timing)
}

/** 当前 session（只读，测试与调试用） */
export function previewSession(): Readonly<PreviewSession> | null {
  return session
}

/** 某个面板当前挂着的预览位移（覆盖层跟随用） */
export function previewTransformOf(panelId: string, gid: string): [number, number] | null {
  return panels.get(panelId)?.transforms.get(gid) ?? null
}

/** 测试与切项目用：清干净，不碰 DOM（DOM 由 React 自己收） */
export function resetPreview(): void {
  session = null
  panels.clear()
  pending.clear()
  if (rafId != null) cancelRaf(rafId)
  rafId = null
}

/* -------------------------------------------------------------------------- */
/*  还原                                                                       */
/* -------------------------------------------------------------------------- */

function restorePanel(panelId: string): void {
  const p = panels.get(panelId)
  if (!p) return
  const svg = findPanelSvg(panelId)
  for (const [gid, base] of p.baseTransforms) {
    const node = findGidNode(svg, gid)
    if (!node) continue
    if (base == null) node.removeAttribute('transform')
    else node.setAttribute('transform', base)
  }
  restoreStyleEdits(p.edits)
  panels.delete(panelId)
}

/* -------------------------------------------------------------------------- */
/*  逐帧写 DOM（rAF 合并）                                                     */
/* -------------------------------------------------------------------------- */

type PendingOp =
  | { kind: 'transform'; panelId: string; gid: string; dfx: number; dfy: number }
  | { kind: 'style'; panelId: string; gid: string; role: string; prop: string; value: unknown }

const pending = new Map<string, PendingOp>()
let rafId: number | null = null

const scheduleRaf = (fn: () => void): number =>
  typeof requestAnimationFrame === 'function' ? requestAnimationFrame(fn) : (setTimeout(fn, 0) as unknown as number)
const cancelRaf = (id: number): void => {
  if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(id)
  else clearTimeout(id as unknown as ReturnType<typeof setTimeout>)
}

function schedule(key: string, op: PendingOp): void {
  pending.set(key, op)
  if (session) session.timing.preview_move_count++
  if (rafId != null) return
  rafId = scheduleRaf(() => {
    rafId = null
    flushPreviewFrame()
  })
}

/**
 * 把攒下的预览操作写进 DOM。一次 rAF 一帧——连着几十个 pointermove
 * 只落一次 DOM 写入。测试里直接调它，不必等真实的动画帧。
 */
export function flushPreviewFrame(): void {
  if (!pending.size) return
  const ops = [...pending.values()]
  pending.clear()
  for (const op of ops) {
    if (op.kind === 'transform') writeTransform(op)
    else writeStyle(op)
  }
  if (session) traceFrame(session.timing)
}

function writeTransform(op: Extract<PendingOp, { kind: 'transform' }>): void {
  const p = panels.get(op.panelId)
  if (!p) return
  const svg = findPanelSvg(op.panelId)
  const node = findGidNode(svg, op.gid)
  p.transforms.set(op.gid, [op.dfx, op.dfy])
  if (!node) return // gid 在 SVG 里不存在：覆盖层预览接管，这里安静退出
  if (!p.baseTransforms.has(op.gid)) {
    p.baseTransforms.set(op.gid, node.getAttribute('transform'))
    p.baseNodes.set(op.gid, node)
  }
  const base = p.baseTransforms.get(op.gid) ?? null
  const vb = viewBox(svg)
  const tx = op.dfx * vb[0]
  const ty = op.dfy * vb[1]
  // **永远从 base 现算**：`translate(…) <原始>` 让平移落在父坐标系里，
  // matplotlib 自己的 scale/translate（`<image>` 上就有）原样保留
  node.setAttribute('transform', base ? `translate(${tx},${ty}) ${base}` : `translate(${tx},${ty})`)
}

function writeStyle(op: Extract<PendingOp, { kind: 'style' }>): void {
  const p = panels.get(op.panelId)
  if (!p) return
  const kind = adapterFor(op.role, op.prop)
  if (!kind) return
  const svg = findPanelSvg(op.panelId)
  const node = findGidNode(svg, op.gid)
  p.styles.set(`${op.gid}|${op.prop}`, { gid: op.gid, prop: op.prop, value: op.value, role: op.role })
  if (!node) return
  const edits = applyStyleEdit(node, kind, op.value, { unitsPerPt: unitsPerPt(svg, p.sizeMm) })
  // 每个元素的 base（整条 style 原文）只记最早那一次：同一条属性被拖着改
  // 十次，账本里仍然只有 matplotlib 给的那份原文
  for (const e of edits) {
    if (!p.edits.some((x) => x.el === e.el)) p.edits.push(e)
  }
}

function viewBox(svg: SVGSVGElement | null): [number, number] {
  const vb = (svg?.getAttribute('viewBox') ?? '').split(/[\s,]+/).map(Number)
  if (vb.length < 4 || !Number.isFinite(vb[2]) || !Number.isFinite(vb[3])) return [100, 100]
  return [vb[2], vb[3]]
}

/* -------------------------------------------------------------------------- */
/*  对外的预览入口                                                             */
/* -------------------------------------------------------------------------- */

/** 平移预览（figure 分数位移，y 向下）；无 session 时是 no-op */
export function previewTransform(gid: string, dfx: number, dfy: number): void {
  if (!session || session.settled) return
  schedule(`t:${gid}`, { kind: 'transform', panelId: session.panelId, gid, dfx, dfy })
}

/**
 * 样式预览。**返回 false = 这一次预览不会发生，调用方必须原路走后端。**
 *
 * 四种 false：不在能力表里（白名单，见 lib/svgStyle.ts）／没有进行中的会话／
 * gid 在这一版 SVG 里根本不存在／**这个 artist 上没有可改的叶子**。
 *
 * 第三种是真实存在的一大类——误差棒、柱形系列、刻度组都是 manifest 的伪元素，
 * matplotlib 给它们的成员发的是自动 id。第四种同样常见：能力表按 role+prop 发，
 * 而同一个 role 的两个 artist 在 SVG 上可以长得完全不同——`fill=False` 的
 * PathPatch 写的是 `fill: none`，改 facecolor 一个叶子都碰不到。
 *
 * 这里**必须同步算一遍**：光看能力表就回 true 的话，调用方会据此把渲染策略
 * 降成 `'none'`，于是用户拖着改颜色**整轮什么都不会发生**——比改动前
 * （每次都发后端）还糟。
 */
export function previewStyle(gid: string, role: string, prop: string, value: unknown): boolean {
  const kind = adapterFor(role, prop)
  if (!kind) return false
  if (!session || session.settled) return false
  const svg = findPanelSvg(session.panelId)
  const node = findGidNode(svg, gid)
  if (!node) return false
  const sizeMm = panels.get(session.panelId)?.sizeMm
  if (!canStyleEditApply(node, kind, value, { unitsPerPt: unitsPerPt(svg, sizeMm) })) return false
  schedule(`s:${gid}|${prop}`, { kind: 'style', panelId: session.panelId, gid, role, prop, value })
  return true
}

/* -------------------------------------------------------------------------- */
/*  与权威渲染的接合                                                           */
/* -------------------------------------------------------------------------- */

/**
 * PanelView 每次内联 SVG 变化后调用。三种情形：
 *
 *  a. 换上来的正是等的那一版（renderKey === awaitKey）→ 预览完成使命，
 *     账本作废（DOM 早就随 innerHTML 一起换掉了，没什么可还原的）；
 *  b. 还是原来那一版（renderKey === 预览挂靠的那版）→ React 重新插了一遍
 *     同一份 SVG（面板重挂、标签页切回来）：**把预览重放上去**，
 *     否则用户会看到自己刚拖完的元素凭空弹回原位；
 *  c. 换成了别的版本（别的变体、脚本重建）→ 预览失去依据，静默作废。
 */
export function reattachPreview(panelId: string, renderKey: string): void {
  const p = panels.get(panelId)
  const s = session && session.panelId === panelId ? session : null

  if (s?.awaitKey && renderKey === s.awaitKey) {
    traceAuthority(s.timing)
    panels.delete(panelId)
    session = null
    return
  }
  if (!p) return
  if (p.renderKey !== renderKey) {
    // 已经不是预览挂靠的那一版：DOM 节点全换了，账本里的引用都是野的
    panels.delete(panelId)
    if (s) session = null
    return
  }
  // 同一版 SVG：先分辨 DOM 到底换没换。节点还是原来那几个 = React 只是重跑了
  // 一遍组件，预览原封不动挂着，**什么都不做**——此时重新采 base 会把已经
  // 挪过的位置当成原位（位移翻倍，还原也还不回去）
  const svg = findPanelSvg(panelId)
  if (!svg) return
  if (domIntact(p, svg)) return
  p.baseTransforms.clear()
  p.baseNodes.clear()
  p.edits = []
  for (const [gid, [dfx, dfy]] of p.transforms) {
    writeTransform({ kind: 'transform', panelId, gid, dfx, dfy })
  }
  for (const st of p.styles.values()) {
    writeStyle({ kind: 'style', panelId, gid: st.gid, role: st.role, prop: st.prop, value: st.value })
  }
}

/** 账本里记着的那些节点是不是还在文档里、还是同一批（= DOM 没被重插） */
function domIntact(p: PanelPreview, svg: SVGSVGElement): boolean {
  if (!p.baseNodes.size && !p.edits.length) return false
  for (const [gid, node] of p.baseNodes) {
    if (findGidNode(svg, gid) !== node) return false
  }
  for (const e of p.edits) {
    if (!e.el.isConnected) return false
  }
  return true
}

/**
 * 权威渲染失败时的收尾：**预览留在画布上**。
 * 文档里已经是用户要的值了，把预览撤掉等于让画布和属性页各说各话；
 * 渲染失败本身由角标表达，用户可以继续编辑或重试。
 */
export function settleFailedAuthority(panelId: string): void {
  if (session?.panelId === panelId) {
    session.settled = true
    session = null
  }
}
