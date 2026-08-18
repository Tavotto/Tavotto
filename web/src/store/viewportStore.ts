import { create } from 'zustand'
import { DURATION, tween } from '@/lib/motion'
import { BASE_PX_PER_MM, clamp } from '@/lib/units'

export const MIN_ZOOM = 0.25
export const MAX_ZOOM = 8

/** 世界坐标：1mm = BASE_PX_PER_MM 世界像素，缩放 1.0 即 100% */
export const mmToWorld = (mm: number) => mm * BASE_PX_PER_MM
export const worldToMm = (px: number) => px / BASE_PX_PER_MM

interface ViewportState {
  zoom: number
  panX: number
  panY: number
  /** 视口尺寸与在窗口中的位置（px），由 CanvasViewport 上报 */
  viewW: number
  viewH: number
  originX: number
  originY: number
  /** 空格键按下 = 临时平移工具 */
  spaceDown: boolean

  setViewRect: (rect: { left: number; top: number; width: number; height: number }) => void
  setSpaceDown: (v: boolean) => void
  setPan: (x: number, y: number) => void
  panBy: (dx: number, dy: number) => void
  /** 以视口内某点为锚点缩放（滚轮 / 捏合：必须跟手，永远瞬时） */
  zoomAt: (factor: number, anchorX: number, anchorY: number) => void
  /** 以视口中心缩放到指定比例；带缓动（缩放预设、⌘0 归 100% 走它） */
  setZoomCentered: (zoom: number) => void
  /**
   * 以视口中心按倍率缩放（顶栏 ± 与 ⌘±）。
   * **不要在调用方写 `setZoomCentered(zoom * 1.25)`**：`zoom` 是当前这一帧的值，
   * 连按时前一段补间还没走完，第二下就以中间值为基准，越按越缩水——用户看到的
   * 是「按键被吃了」。这里以补间终点为基准，连按几下就是几下。
   */
  zoomBy: (factor: number) => void
  /** 瞬时 fit：初始化、切画布、载入文档这类「不是用户在看着的一步」 */
  fit: (pageW: number, pageH: number, padding?: number) => void
  /** 带缓动的 fit（prefers-reduced-motion 时瞬时完成）；用户点「适应画布」时用 */
  fitAnimated: (pageW: number, pageH: number, padding?: number) => void
  /** 把一块区域挪到视口中央（放不下才缩小），带缓动；「定位到这个对象」用 */
  revealRect: (rect: { x: number; y: number; w: number; h: number }, padding?: number) => void
}

/** 视口的一个落点：补间与瞬时设置共用同一种描述 */
interface ViewTarget {
  zoom: number
  panX: number
  panY: number
}

/**
 * 在飞的视口补间。两件事都靠它：
 *
 * 1. **任何直接操纵都要先掐掉它**（滚轮缩放、平移、瞬时 fit）。不掐的话补间
 *    和用户抢着写 zoom/pan，画面会在两个目标之间抖——这是「加了动画反而更差」
 *    的典型死法。
 * 2. **连按时以补间的终点为基准算下一步**（`animTarget`），不是以当前这一帧的
 *    中间值。否则连按三下 ⌘+ 只会放大到「一下半」，用户以为按键丢了。
 */
let cancelAnim: (() => void) | null = null
let animTarget: ViewTarget | null = null

function stopAnim() {
  cancelAnim?.()
  cancelAnim = null
  animTarget = null
}

type Setter = (partial: Partial<ViewportState>) => void
type Getter = () => ViewportState

/**
 * 以视口中心为锚点缩放到 `next`。基准取补间终点（没有补间才取当前值），
 * 否则连按时锚点会以中间帧算，画面会一点点往边上飘。
 */
function zoomCenteredTo(set: Setter, get: Getter, next: number) {
  const s = get()
  const base = animTarget ?? { zoom: s.zoom, panX: s.panX, panY: s.panY }
  if (next === base.zoom) return
  const k = next / base.zoom
  const ax = s.viewW / 2
  const ay = s.viewH / 2
  animateTo(set, get, {
    zoom: next,
    panX: ax - (ax - base.panX) * k,
    panY: ay - (ay - base.panY) * k,
  })
}

/**
 * 补间到一个落点。**视口的所有缓动都必须经过这里**——reduced-motion 由
 * `tween` 内部判掉（同步落终态、一帧都不放），各处自己写 rAF 会绕过那条无障碍
 * 契约，也会绕过上面那两条「掐断 / 以终点为基准」的纪律。
 */
function animateTo(set: Setter, get: Getter, target: ViewTarget) {
  stopAnim()
  const s = get()
  if (s.zoom === target.zoom && s.panX === target.panX && s.panY === target.panY) return
  const from = { zoom: s.zoom, panX: s.panX, panY: s.panY }
  animTarget = target
  cancelAnim = tween({
    duration: DURATION.base,
    onUpdate: (e) =>
      set({
        zoom: from.zoom + (target.zoom - from.zoom) * e,
        panX: from.panX + (target.panX - from.panX) * e,
        panY: from.panY + (target.panY - from.panY) * e,
      }),
    onDone: () => {
      cancelAnim = null
      animTarget = null
    },
  })
}

export const useViewportStore = create<ViewportState>((set, get) => ({
  zoom: 1,
  panX: 0,
  panY: 0,
  viewW: 0,
  viewH: 0,
  originX: 0,
  originY: 0,
  spaceDown: false,

  setViewRect: ({ left, top, width, height }) =>
    set((s) =>
      s.viewW === width && s.viewH === height && s.originX === left && s.originY === top
        ? s
        : { viewW: width, viewH: height, originX: left, originY: top },
    ),
  setSpaceDown: (v) => set((s) => (s.spaceDown === v ? s : { spaceDown: v })),
  // 直接操纵一律先掐断在飞的补间
  setPan: (panX, panY) => {
    stopAnim()
    set({ panX, panY })
  },
  panBy: (dx, dy) => {
    stopAnim()
    set((s) => ({ panX: s.panX + dx, panY: s.panY + dy }))
  },

  zoomAt: (factor, anchorX, anchorY) => {
    stopAnim()
    const { zoom, panX, panY } = get()
    const next = clamp(zoom * factor, MIN_ZOOM, MAX_ZOOM)
    if (next === zoom) return
    const k = next / zoom
    set({
      zoom: next,
      panX: anchorX - (anchorX - panX) * k,
      panY: anchorY - (anchorY - panY) * k,
    })
  },

  setZoomCentered: (target) => {
    zoomCenteredTo(set, get, clamp(target, MIN_ZOOM, MAX_ZOOM))
  },

  zoomBy: (factor) => {
    const s = get()
    const base = animTarget?.zoom ?? s.zoom
    zoomCenteredTo(set, get, clamp(base * factor, MIN_ZOOM, MAX_ZOOM))
  },

  fit: (pageW, pageH, padding = 72) => {
    stopAnim()
    const { viewW, viewH } = get()
    if (!viewW || !viewH) return
    const wPx = mmToWorld(pageW)
    const hPx = mmToWorld(pageH)
    const zoom = clamp(
      Math.min((viewW - padding) / wPx, (viewH - padding) / hPx),
      MIN_ZOOM,
      MAX_ZOOM,
    )
    set({
      zoom,
      panX: (viewW - wPx * zoom) / 2,
      panY: (viewH - hPx * zoom) / 2,
    })
  },

  fitAnimated: (pageW, pageH, padding = 72) => {
    const s = get()
    if (!s.viewW || !s.viewH) return
    const wPx = mmToWorld(pageW)
    const hPx = mmToWorld(pageH)
    const zoom = clamp(
      Math.min((s.viewW - padding) / wPx, (s.viewH - padding) / hPx),
      MIN_ZOOM,
      MAX_ZOOM,
    )
    const target = {
      zoom,
      panX: (s.viewW - wPx * zoom) / 2,
      panY: (s.viewH - hPx * zoom) / 2,
    }
    animateTo(set, get, target)
  },

  revealRect: ({ x, y, w, h }, padding = 96) => {
    const { viewW, viewH, zoom } = get()
    if (!viewW || !viewH) return
    // 当前缩放能装下就不动它，装不下才退到刚好装下的比例
    const fitZoom = Math.min(
      (viewW - padding) / Math.max(mmToWorld(w), 1),
      (viewH - padding) / Math.max(mmToWorld(h), 1),
    )
    const next = clamp(Math.min(zoom, fitZoom), MIN_ZOOM, MAX_ZOOM)
    animateTo(set, get, {
      zoom: next,
      panX: viewW / 2 - mmToWorld(x + w / 2) * next,
      panY: viewH / 2 - mmToWorld(y + h / 2) * next,
    })
  },
}))

/* --------------------------- 坐标换算 -------------------------------------- */

export interface ViewTransform {
  zoom: number
  panX: number
  panY: number
  originX: number
  originY: number
}

export const getTransform = (): ViewTransform => {
  const { zoom, panX, panY, originX, originY } = useViewportStore.getState()
  return { zoom, panX, panY, originX, originY }
}

/** mm → 视口内 px（不含视口自身偏移） */
export const mmToViewX = (mm: number, t: ViewTransform) => t.panX + mmToWorld(mm) * t.zoom
export const mmToViewY = (mm: number, t: ViewTransform) => t.panY + mmToWorld(mm) * t.zoom
/** 长度换算：mm → 屏幕 px */
export const mmToPx = (mm: number, t: ViewTransform) => mmToWorld(mm) * t.zoom
export const pxToMm = (px: number, t: ViewTransform) => worldToMm(px / t.zoom)

/** 鼠标事件的 clientX/Y → 文档 mm */
export function clientToMm(clientX: number, clientY: number, t = getTransform()) {
  return {
    x: worldToMm((clientX - t.originX - t.panX) / t.zoom),
    y: worldToMm((clientY - t.originY - t.panY) / t.zoom),
  }
}

/** 屏幕 6px 的吸附容差换算成当前缩放下的 mm */
export const snapTolMm = (t: ViewTransform, px = 6) => pxToMm(px, t)
