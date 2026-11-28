/**
 * 配色的画布预览平面（ADR 0210 §六）：在方案弹层里 ↑↓ / 悬停时，**整张画布所有面板**一起换成那套颜色，
 * 只动 DOM，不 commit、不进历史、不发后端。
 *
 * 与 `svgPreviewStore` 的分工：那个是「一次手势、一张面板」的拖动 / 样式预览，会话、账本、等图都按一张面板设计；
 * 这里是「一次悬停、所有面板」的颜色预览。弹层开着时画布上没有手势，两者不会同时挂着。分开放，
 * 免得把一张面板的会话语义硬撑成多张。DOM 写法与还原账本用的是同一套（`lib/svgStyle`）。
 *
 * 一张面板要能预览，DOM 里得有它**这一版**的内联 SVG。图内编辑那张本来就是；其它面板平时可能挂的是位图——
 * 预览期间 `PanelView` 读 `usePalettePreviewGen(panelId)`，这一版有确切的矢量 SVG（`displayView.kind === 'exact'`）
 * 时临时改挂它；挂上后在 layout effect 里调 `reattachPalettePreview`，与绘制同帧把颜色画上去。
 * 没有确切 SVG 的（raster 档、被内存预算清掉、还没画过）**不假装**：`markPalettePreviewUnavailable` 记下，
 * 弹层说「有几处选用后才看得到」。
 *
 * 生命周期：
 *   showPalettePreview(entries) → 换一套就先还原上一套；每张面板的代号 +1，PanelView 据此重画
 *   clearPalettePreview()       → 关弹层 / Esc / 卸载：全部还原
 *   holdPalettePreview()        → 选用：commit 之后预览**继续挂着**，等各面板新一版 SVG 换上来自然退场
 *                                 （DOM 整个换掉，不用还原）；不挂着的话会先闪回旧色再变新色
 *   settleUnbackedPalettePreview(panelId, overrides) → 挂着等图期间文档又变了（撤销），
 *                                 预览代表的值已不在文档里 → 就地还原
 */
import { useSyncExternalStore } from 'react'
import {
  adapterFor,
  applyStyleEdit,
  canStyleEditApply,
  restoreStyleEdits,
  unitsPerPt,
  type StyleEdit,
} from '@/lib/svgStyle'
import { findGidNode, findPanelSvg, patchId, type PreviewPatch } from './svgPreviewStore'

/** 一条预览：哪个元素、它的 role（决定改描边还是填充）、哪个属性、什么颜色 */
export interface PalettePreviewPatch extends PreviewPatch {
  role: string
}

export interface PalettePreviewEntry {
  panelId: string
  patches: readonly PalettePreviewPatch[]
  /** 面板的图幅（mm），换算线宽用；颜色预览用不到，与样式预览共用一个上下文 */
  sizeMm?: readonly number[]
}

interface PanelHold {
  patches: readonly PalettePreviewPatch[]
  sizeMm?: readonly number[]
  /** 这一轮预览的代号：PanelView 的 layout effect 依赖它，换一套方案就重画 */
  gen: number
  /** 画在哪一版 SVG 上（null = 还没画上去） */
  renderKey: string | null
  edits: StyleEdit[]
  /** 画不出来的条数；null = 这张面板还没回话（还没挂上 SVG） */
  missed: number | null
}

const holds = new Map<string, PanelHold>()
/** 选用之后挂着等图：关弹层不再还原 */
let held = false
let gen = 0
let version = 0
const listeners = new Set<() => void>()

function bump(): void {
  version++
  for (const l of listeners) l()
}

function subscribe(l: () => void): () => void {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}

/** 在面板当前 DOM 上把这份预览画出来；返回画不出来的条数 */
function paint(panelId: string, h: PanelHold): number {
  const svg = findPanelSvg(panelId)
  if (!svg) return h.patches.length
  const ctx = { unitsPerPt: unitsPerPt(svg, h.sizeMm) }
  let missed = 0
  for (const p of h.patches) {
    const kind = adapterFor(p.role, p.prop)
    const node = kind ? findGidNode(svg, p.gid) : null
    if (!kind || !node || !canStyleEditApply(node, kind, p.value, ctx)) {
      missed++
      continue
    }
    // 每个元素的 base（整条 style 原文）只记最早那一次：还原回 matplotlib 给的那份
    for (const e of applyStyleEdit(node, kind, p.value, ctx)) {
      if (!h.edits.some((x) => x.el === e.el)) h.edits.push(e)
    }
  }
  return missed
}

function restore(h: PanelHold): void {
  restoreStyleEdits(h.edits)
  h.edits = []
}

function drop(panelId: string, restoreDom: boolean): void {
  const h = holds.get(panelId)
  if (!h) return
  if (restoreDom) restore(h)
  holds.delete(panelId)
  if (!holds.size) held = false
}

/** 预览一套颜色。`entries` 只放要改的面板；不在里面的面板保持原样 */
export function showPalettePreview(entries: readonly PalettePreviewEntry[]): void {
  for (const id of [...holds.keys()]) drop(id, true)
  held = false
  gen++
  for (const e of entries) {
    if (!e.patches.length) continue
    holds.set(e.panelId, { patches: e.patches, sizeMm: e.sizeMm, gen, renderKey: null, edits: [], missed: null })
  }
  bump()
}

/** 关弹层 / Esc / 卸载：还原。选用之后挂着等图的不动（见 holdPalettePreview） */
export function clearPalettePreview(): void {
  if (held || !holds.size) return
  for (const id of [...holds.keys()]) drop(id, true)
  bump()
}

/**
 * 选用：文档已经写好，预览挂着等各面板的新一版 SVG。还没画上去的面板没东西可挂，直接放掉
 * （它们本来就要等新图）。
 */
export function holdPalettePreview(): void {
  for (const [id, h] of [...holds]) if (h.renderKey == null) drop(id, false)
  held = holds.size > 0
  bump()
}

/**
 * PanelView 在这张面板的内联 SVG 挂进 DOM 之后调用（与 `reattachPreview` 同一类时机：layout effect，绘制之前）：
 *  - 预览中：画到这一版上（换了一套、React 重插了同一份、刚从位图换成 SVG 都走这里）
 *  - 挂着等图、换上来的是别的版本：新图已经带着正式颜色，预览退场（DOM 已换，不用还原）
 */
export function reattachPalettePreview(panelId: string, renderKey: string): void {
  const h = holds.get(panelId)
  if (!h) return
  if (held && h.renderKey !== null && h.renderKey !== renderKey) {
    drop(panelId, false)
    bump()
    return
  }
  // 同一版、节点都还在、已经画过：React 只是重跑了组件，什么都不做（重画会把预览色当成 base）
  if (h.renderKey === renderKey && h.edits.length && h.edits.every((e) => e.el.isConnected)) return
  if (h.edits.every((e) => e.el.isConnected)) restore(h)
  else h.edits = []
  h.renderKey = renderKey
  const before = h.missed
  h.missed = paint(panelId, h)
  if (h.missed !== before) bump()
}

/** PanelView：这张面板此刻没有确切的矢量 SVG，画不了——记成「选用后才看得到」 */
export function markPalettePreviewUnavailable(panelId: string): void {
  const h = holds.get(panelId)
  if (!h || h.missed === h.patches.length) return
  restore(h)
  h.renderKey = null
  h.missed = h.patches.length
  bump()
}

/**
 * 挂着等图期间文档变了：这张面板的预览代表的正式值还在不在文档里。不在（撤销了）→ 还原。
 * 与 `svgPreviewStore.settleUnbackedCommit` 同一个判据：逐条比对 gid / prop / 值。
 */
export function settleUnbackedPalettePreview(panelId: string, overrides: readonly PreviewPatch[]): void {
  if (!held) return
  const h = holds.get(panelId)
  if (!h) return
  const current = new Set(overrides.map(patchId))
  if (h.patches.every((p) => current.has(patchId(p)))) return
  drop(panelId, true)
  bump()
}

/** 这张面板的预览代号；0 = 不在预览里。PanelView 在非 0 时改挂这一版的内联 SVG */
export function palettePreviewGen(panelId: string): number {
  return holds.get(panelId)?.gen ?? 0
}

export function usePalettePreviewGen(panelId: string): number {
  return useSyncExternalStore(
    subscribe,
    () => palettePreviewGen(panelId),
    () => 0,
  )
}

/** 选用之后挂着等图：PanelView 此时允许继续挂**上一版**（`fallback`）的 SVG，新图到之前不闪回旧色 */
export function palettePreviewHeld(): boolean {
  return held
}

export function usePalettePreviewHeld(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => held,
    () => false,
  )
}

/** 预览中画不出来的条数；还在等面板回话的不算（它们一回话就会更新） */
export function palettePreviewMissed(): number {
  if (held) return 0
  let n = 0
  for (const h of holds.values()) n += h.missed ?? 0
  return n
}

export function usePalettePreviewMissed(): number {
  useSyncExternalStore(
    subscribe,
    () => version,
    () => 0,
  )
  return palettePreviewMissed()
}

/** 测试用：清掉一切，不碰 DOM */
export function resetPalettePreview(): void {
  holds.clear()
  held = false
  bump()
}
