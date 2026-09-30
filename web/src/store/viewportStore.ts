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
  /**
   * 「适应」模式：视口此刻显示的是最近一次 `fit` / `fitAnimated` 算出来的落点，
   * 用户还没自己动过。**只在这个模式下**舞台尺寸变化（侧栏开合、窗口缩放）会
   * 重算一次适配——用户手动缩放 / 平移过之后，视口是他的，尺寸再变也不碰
   * （审计 B01 / B17 / B57：首次打开按侧栏都展开之后的舞台算适配；手动 167%
   * 时保留缩放与平移）。任何直接操纵都会退出这个模式。
   */
  fitted: boolean
  /**
   * 顶栏读数要显示的缩放（2026-09-15，读数改成会滚的数字之后）：补间路径（± / 预设 /
   * 适应画布 / 定位 / 还原）一起步就是**终点值**，读数只滚一次、与画布同拍；瞬时路径
   * （滚轮 / 捏合 / 切画布还原 / 瞬时 fit）就是当前值。不是几何权威——几何一律读 `zoom`。
   */
  readoutZoom: number
  /** 最近一次缩放是不是一步到位的（补间路径）——是才让读数滚，滚轮那种连续输入即时换数 */
  readoutRolls: boolean
  /**
   * 视口补间正在走（`animateTo` 起步到落定 / 被掐断）。只给观察者用：舞台把它挂成
   * `data-view-tweening`，e2e 据此等取景落定再量几何——补间途中 zoom / pan 与刚变的页面
   * 尺寸对不上，量出来的「居中」是半路上的值（#720 的 posix-e2e 偶发红）。
   */
  tweening: boolean

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
  /** 适应取景要给底部浮动工具条让出的高度（px；0 = 不让）。取景算法只有 `fitTarget` 一处，各入口都走它 */
  setFitBottomClear: (px: number) => void
  /** 此刻的让位高度 */
  fitBottomClear: number
  /** 瞬时 fit：初始化、切画布、载入文档这类「不是用户在看着的一步」 */
  fit: (pageW: number, pageH: number, padding?: number) => void
  /** 带缓动的 fit（prefers-reduced-motion 时瞬时完成）；用户点「适应画布」时用 */
  fitAnimated: (pageW: number, pageH: number, padding?: number) => void
  /**
   * 带缓动地取景文档里任意一块矩形（mm），同样进入适应模式——之后舞台尺寸变了按
   * 这块矩形重算。给「刚加的图伸出页面一点」用：取景页面 ∪ 那张图（`addFigureToLayout`）。
   */
  fitRectAnimated: (rect: { x: number; y: number; w: number; h: number }, padding?: number) => void
  /** 瞬时版的 `fitRectAnimated`：切画布还原一块非页面的取景框用（标签切换不补间） */
  fitRect: (rect: { x: number; y: number; w: number; h: number }, padding?: number) => void
  /**
   * 此刻适应模式取景的那块**非页面**矩形；不在适应模式、或取景的就是页面时回 null。
   * 画布会话离开时存它（`canvasSession.capture`）：取景「页面 ∪ 刚加的图」之后切走
   * 再切回，只记得 `fitted` 的话会按页面重新适配，把伸出页面那截裁掉（#706 评审 P2）。
   * 取景页面时不存矩形、回来按**当时**的页面算，页面尺寸由别处改过也对得上。
   */
  fitFrame: () => FitFrame | null
  /** 把一块区域挪到视口中央（放不下才缩小），带缓动；「定位到这个对象」用 */
  revealRect: (rect: { x: number; y: number; w: number; h: number }, padding?: number) => void
  /**
   * 还原到一个**记下来的**落点，带缓动。只给「把用户原来看的那一片还回去」用
   * （离开快速编辑回到画布排版，审计 T01）——现算出来的落点走 `fit` /
   * `revealRect`，它们才知道页面与视口尺寸。
   */
  restoreView: (view: ViewTarget) => void
  /**
   * 瞬时落到一个**记下来的**落点。给切画布还原会话用（标签切换是纯显隐，不补间）。
   * 与 `restoreView` 同一条纪律：退出适应模式——否则 `fitted` / `lastFit` 还是上一张
   * 画布的，下一次侧栏开合就按别的画布的取景框把还原出来的视口重算掉。
   */
  setView: (view: ViewTarget) => void
}

/** 适应模式的一块非页面取景框（mm）与留白（px） */
export interface FitFrame {
  x: number
  y: number
  w: number
  h: number
  padding: number
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
  if (animTarget) useViewportStore.setState({ tweening: false })
  animTarget = null
}

type Setter = (partial: Partial<ViewportState>) => void
type Getter = () => ViewportState

/**
 * 以视口中心为锚点缩放到 `next`。基准取补间终点（没有补间才取当前值），
 * 否则连按时锚点会以中间帧算，画面会一点点往边上飘。
 */
function zoomCenteredTo(set: Setter, get: Getter, next: number) {
  leaveFitMode(set, get)
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
  set({ readoutZoom: target.zoom, readoutRolls: true, tweening: true })
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
      set({ tweening: false })
    },
  })
}

/**
 * 最近一次要求适配的取景框；舞台尺寸变了、仍在适应模式时按它重算。`page` = 这块
 * 就是页面（`fit` / `fitAnimated`），否则是调用方给的矩形（`fitRect*`）。
 */
let lastFit: { x: number; y: number; pageW: number; pageH: number; padding: number; page: boolean } | null =
  null

/**
 * 用户自己动过视口 = 退出适应模式：之后舞台尺寸再变也不重算。
 *
 * `stopAnim()` 里做不了这件事——`fit` 自己第一句就是它。所以直接操纵各自
 * 调一次：不调的话，用户在舞台量到尺寸之前调的缩放会在下一帧被补上来的 fit
 * 覆盖掉，而那看起来就是「我的缩放被吃了」。
 */
function leaveFitMode(set: Setter, get: Getter) {
  if (get().fitted) set({ fitted: false })
}

/** 瞬时适配到一块取景框（`fit` / `fitRect` 共用） */
function fitInstant(set: Setter, get: Getter, frame: NonNullable<typeof lastFit>) {
  stopAnim()
  lastFit = frame
  const { viewW, viewH } = get()
  // 舞台还没挂载（Project Picker → 工作台的那个空档）：现在算不出缩放，
  // 进入适应模式等 `setViewRect` 第一次量到尺寸再做。丢掉的话新项目会沿用
  // 上一个项目留下的缩放（审计 T03）。
  if (!viewW || !viewH) {
    set({ fitted: true })
    return
  }
  const { x, y, pageW, pageH, padding } = frame
  const target = fitTarget(viewW, viewH, pageW, pageH, padding, x, y, get().fitBottomClear)
  set({ ...target, fitted: true, readoutZoom: target.zoom, readoutRolls: false })
}

/** 带缓动地适配到一块取景框（`fitAnimated` / `fitRectAnimated` 共用） */
function fitTweened(set: Setter, get: Getter, frame: NonNullable<typeof lastFit>) {
  lastFit = frame
  const s = get()
  if (!s.viewW || !s.viewH) {
    set({ fitted: true })
    return
  }
  set({ fitted: true })
  const { x, y, pageW, pageH, padding } = frame
  animateTo(set, get, fitTarget(s.viewW, s.viewH, pageW, pageH, padding, x, y, s.fitBottomClear))
}

/**
 * 画布底部浮动工具条（`CanvasToolbar`）的顶边离舞台底边 52px，再留 8px 的缝：显示时适应取景的
 * 下边距不小于它，取景框的底边落在工具条顶边之上（#770 评审 P2：竖版页高度受限时底部只留
 * `padding / 2` = 36px，页面底 16px 压在工具条下面、点击被截走）。工具条隐藏时为 0，回到对称留白。
 */
export const TOOLBAR_FIT_CLEARANCE = 60

/** 按取景框算落点；调用方保证视口已量到尺寸。`bottomClear`：底部要让出的高度（px），只抬高下边距 */
function fitTarget(
  viewW: number,
  viewH: number,
  pageW: number,
  pageH: number,
  padding: number,
  x = 0,
  y = 0,
  bottomClear = 0,
): ViewTarget {
  const wPx = mmToWorld(pageW)
  const hPx = mmToWorld(pageH)
  const top = padding / 2
  const bottom = Math.max(padding / 2, bottomClear)
  const zoom = clamp(Math.min((viewW - padding) / wPx, (viewH - top - bottom) / hPx), MIN_ZOOM, MAX_ZOOM)
  return {
    zoom,
    panX: (viewW - wPx * zoom) / 2 - mmToWorld(x) * zoom,
    panY: top + (viewH - top - bottom - hPx * zoom) / 2 - mmToWorld(y) * zoom,
  }
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
  fitted: false,
  fitBottomClear: 0,
  readoutZoom: 1,
  readoutRolls: false,
  tweening: false,

  setViewRect: ({ left, top, width, height }) => {
    const s = get()
    if (s.viewW === width && s.viewH === height && s.originX === left && s.originY === top) return
    const resized = s.viewW !== width || s.viewH !== height
    set({ viewW: width, viewH: height, originX: left, originY: top })
    // 舞台尺寸变了、而视口还处在适应模式：按同一个取景框重算。这一条同时
    // 覆盖两种情形——舞台第一次量到尺寸（从 Project Picker 进工作台的空档里
    // 收到的 `fit` 在这里补上），以及侧栏开合 / 窗口缩放（首次打开时侧栏是在
    // 舞台量过一次尺寸之后才展开的，不重算就会按整窗宽度算出 190% 上下的比例，
    // 空画布的起步提示被挤到可视区右缘——审计 B01）。用户动过视口就不在适应
    // 模式里，这里一个字不碰。
    if (resized && s.fitted && lastFit && width && height) {
      const { x, y, pageW, pageH, padding } = lastFit
      const target = fitTarget(width, height, pageW, pageH, padding, x, y, s.fitBottomClear)
      stopAnim()
      set({ ...target, readoutZoom: target.zoom, readoutRolls: false })
    }
  },
  setFitBottomClear: (px) => {
    if (get().fitBottomClear === px) return
    set({ fitBottomClear: px })
    // 适应模式里：让位高度变了（工具条出现 / 消失）就按同一个取景框重算
    const { fitted, viewW, viewH } = get()
    if (fitted && lastFit && viewW && viewH) {
      const { x, y, pageW, pageH, padding } = lastFit
      const target = fitTarget(viewW, viewH, pageW, pageH, padding, x, y, px)
      stopAnim()
      set({ ...target, readoutZoom: target.zoom, readoutRolls: false })
    }
  },
  setSpaceDown: (v) => set((s) => (s.spaceDown === v ? s : { spaceDown: v })),
  // 直接操纵一律先掐断在飞的补间，并退出适应模式
  setPan: (panX, panY) => {
    stopAnim()
    leaveFitMode(set, get)
    set({ panX, panY })
  },
  panBy: (dx, dy) => {
    stopAnim()
    leaveFitMode(set, get)
    set((s) => ({ panX: s.panX + dx, panY: s.panY + dy }))
  },

  zoomAt: (factor, anchorX, anchorY) => {
    stopAnim()
    leaveFitMode(set, get)
    const { zoom, panX, panY } = get()
    const next = clamp(zoom * factor, MIN_ZOOM, MAX_ZOOM)
    if (next === zoom) return
    const k = next / zoom
    set({
      zoom: next,
      panX: anchorX - (anchorX - panX) * k,
      panY: anchorY - (anchorY - panY) * k,
      readoutZoom: next,
      readoutRolls: false,
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
    fitInstant(set, get, { x: 0, y: 0, pageW, pageH, padding, page: true })
  },

  fitRect: ({ x, y, w, h }, padding = 72) => {
    fitInstant(set, get, { x, y, pageW: w, pageH: h, padding, page: false })
  },

  fitFrame: () => {
    if (!get().fitted || !lastFit || lastFit.page) return null
    const { x, y, pageW: w, pageH: h, padding } = lastFit
    return { x, y, w, h, padding }
  },

  fitAnimated: (pageW, pageH, padding = 72) => {
    fitTweened(set, get, { x: 0, y: 0, pageW, pageH, padding, page: true })
  },

  fitRectAnimated: ({ x, y, w, h }, padding = 72) => {
    fitTweened(set, get, { x, y, pageW: w, pageH: h, padding, page: false })
  },

  revealRect: ({ x, y, w, h }, padding = 96) => {
    leaveFitMode(set, get)
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

  restoreView: ({ zoom, panX, panY }) => {
    // 与其它直接落点同一条纪律：退出适应模式，否则舞台量到尺寸的下一帧
    // 会把还原出来的视口盖掉
    leaveFitMode(set, get)
    if (!get().viewW || !get().viewH) return
    animateTo(set, get, { zoom: clamp(zoom, MIN_ZOOM, MAX_ZOOM), panX, panY })
  },

  setView: ({ zoom, panX, panY }) => {
    stopAnim()
    leaveFitMode(set, get)
    const next = clamp(zoom, MIN_ZOOM, MAX_ZOOM)
    set({ zoom: next, panX, panY, readoutZoom: next, readoutRolls: false })
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
