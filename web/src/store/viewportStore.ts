import { create } from 'zustand'
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
  /** 以视口内某点为锚点缩放 */
  zoomAt: (factor: number, anchorX: number, anchorY: number) => void
  setZoomCentered: (zoom: number) => void
  fit: (pageW: number, pageH: number, padding?: number) => void
  /** 带 150ms 缓动的 fit（prefers-reduced-motion 时瞬时完成）；双击空白回中用 */
  fitAnimated: (pageW: number, pageH: number, padding?: number) => void
  /** 把一块区域挪到视口中央（放不下才缩小），用于「定位到这个对象」 */
  revealRect: (rect: { x: number; y: number; w: number; h: number }, padding?: number) => void
}

let fitRaf = 0

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
  setPan: (panX, panY) => set({ panX, panY }),
  panBy: (dx, dy) => set((s) => ({ panX: s.panX + dx, panY: s.panY + dy })),

  zoomAt: (factor, anchorX, anchorY) => {
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
    const { zoom, viewW, viewH } = get()
    const next = clamp(target, MIN_ZOOM, MAX_ZOOM)
    if (next === zoom) return
    get().zoomAt(next / zoom, viewW / 2, viewH / 2)
  },

  fit: (pageW, pageH, padding = 72) => {
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
    const reduced =
      typeof matchMedia !== 'undefined' &&
      matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) {
      set(target)
      return
    }
    const from = { zoom: s.zoom, panX: s.panX, panY: s.panY }
    const t0 = performance.now()
    const DUR = 150 // 120–180ms 区间取中
    cancelAnimationFrame(fitRaf)
    const step = (now: number) => {
      const t = Math.min(1, (now - t0) / DUR)
      const e = 1 - (1 - t) ** 3 // easeOutCubic
      set({
        zoom: from.zoom + (target.zoom - from.zoom) * e,
        panX: from.panX + (target.panX - from.panX) * e,
        panY: from.panY + (target.panY - from.panY) * e,
      })
      if (t < 1) fitRaf = requestAnimationFrame(step)
    }
    fitRaf = requestAnimationFrame(step)
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
    set({
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
