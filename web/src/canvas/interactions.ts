import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg, t, type UiMessage } from '@/i18n'
import {
  anchoredRect,
  endpointDelta,
  rectsIntersect,
  resizeRect,
  snapCandidates,
  snapEdge,
  snapMove,
  unrotateVecDeg,
  type Rect,
  type ResizeDir,
} from '@/lib/geometry'
import type { Manifest, ManifestElement } from '@/lib/api'
import { flipY, resizeGroup, round4, unionBox, type Rect4 } from '@/lib/axesLayout'
import {
  anchorOf,
  arrowEndpointsOf,
  axesCompanions,
  elementSnapCandidates,
  groupBoxes,
  groupPatches,
  isElementHidden,
  positionOf,
  type AlignEntry,
  type Group,
} from '@/lib/elementGeom'
import { newId } from '@/lib/id'
import {
  geomAreaFrac,
  geomContains,
  geomDistMm,
  geomHitTolMm,
  geomInkAreaFrac,
} from '@/lib/pathGeom'
import { clamp } from '@/lib/units'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { panelRender, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore, type Tool } from '@/store/uiStore'
import {
  clientToMm,
  getTransform,
  pxToMm,
  snapTolMm,
  useViewportStore,
} from '@/store/viewportStore'
import { setOverride, setOverrides } from '@/store/actions'
import { previewTransform } from '@/store/svgPreviewStore'
import {
  beginElementPreview,
  cancelElementPreview,
  commitElementPreview,
} from './elementPreview'
import {
  isLinear,
  lineEndpoints,
  objectRotation,
  panelAspectLocked,
  panelRotation,
  rotateVec,
  rotationSwaps,
  unrotateVec,
  type ArrowObject,
  type CanvasObject,
  type EndPoint,
  type PanelObject,
  type PanelRotation,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { expandGroups, movableTargets, moveLabel, warnBlockedGroups } from '@/store/actions'

/** 历史标签描述符（与 store/actions.ts 同一命名空间） */
const hist = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`history.${key}`, values, 'workspace')

/**
 * 画出来的那类对象叫什么——文字/箭头走对象类型名，其余走形状名。
 *
 * 两个分支各自收窄成自己的字面量联合：模板 key 的静态展开按**参数类型**走，
 * 传整个 `Tool` 进去会让提取器同时要求 `shape.text`、`objectType.rect` 这类
 * 根本不存在的条目。
 *
 * 回**描述符**而不是翻好的字符串：历史条目活得比一次渲染长，把「矩形」
 * 当场翻好塞进去，切到英文后历史面板只重翻外层模板，显示成「Add 矩形」，
 * 而且换不回来（原始 tool 已经没了）。
 */
const drawnToolMsg = (tool: Exclude<Tool, 'select'>): UiMessage =>
  tool === 'text' || tool === 'arrow'
    ? msg(`objectType.${tool}`)
    : msg(`shape.${tool}`)

/* -------------------------------------------------------------------------- */
/*  指针追踪骨架                                                               */
/* -------------------------------------------------------------------------- */

/** 一次拖动是怎么结束的——**提交与取消必须分开**，见 TrackOptions.onEnd */
export interface TrackEnd {
  /**
   * true = pointercancel / lostpointercapture：系统把这次交互作废了
   * （手势被识别成滚动、笔离开数位板、别的元素抢走了指针捕获）。
   * 这**不是**用户完成了操作：一律取消——不写 override、不进历史、不渲染、
   * 不留临时 transform。旧实现把 cancel 与 up 走同一条路，于是一次被系统
   * 打断的拖动会静默落成一条真实改动。
   */
  cancelled: boolean
}

interface TrackOptions {
  onMove: (ev: PointerEvent, dxPx: number, dyPx: number) => void
  onEnd: (moved: boolean, ev: PointerEvent, end: TrackEnd) => void
  /** 超过该像素位移才算「真的动了」 */
  threshold?: number
}

export function trackPointer(e: ReactPointerEvent, { onMove, onEnd, threshold = 2 }: TrackOptions) {
  const startX = e.clientX
  const startY = e.clientY
  let moved = false
  let done = false

  const move = (ev: PointerEvent) => {
    const dx = ev.clientX - startX
    const dy = ev.clientY - startY
    if (!moved && Math.abs(dx) + Math.abs(dy) < threshold) return
    moved = true
    onMove(ev, dx, dy)
  }
  const finish = (cancelled: boolean) => (ev: Event) => {
    if (done) return
    done = true
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    window.removeEventListener('pointercancel', cancel)
    window.removeEventListener('lostpointercapture', cancel)
    onEnd(moved, ev as PointerEvent, { cancelled })
  }
  const up = finish(false)
  const cancel = finish(true)
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
  window.addEventListener('pointercancel', cancel)
  // 指针捕获被别处抢走 = 后续 pointerup 再也不会到我们手里，按取消处理
  window.addEventListener('lostpointercapture', cancel)
}

const doc = () => useDocumentStore.getState().doc
const interaction = () => useInteractionStore.getState()

/** 当前吸附偏好；总开关关闭时返回 null，调用方据此完全跳过吸附 */
function snapPrefs() {
  const ui = useUiStore.getState()
  if (!ui.snapEnabled) return null
  return {
    objects: ui.snapToObjects,
    guides: ui.snapToGuides,
    grid: ui.snapToGrid,
    gridSize: ui.gridSize,
  }
}

const candidatesFor = (exclude: Set<string>) => {
  const prefs = snapPrefs()
  if (!prefs) return null
  const cands = snapCandidates(doc().objects, exclude, doc().page, doc().guides, prefs)
  if (prefs.objects) {
    // 图内元素的中心线也参与：拖画布标注（箭头指向图里那行字）时能吸到
    // 图内文字 / 图例 / 子图的水平、垂直中心线上，参考线照常显示
    const rs = useRenderStore.getState()
    for (const o of doc().objects) {
      if (o.type !== 'panel' || o.hidden || exclude.has(o.id)) continue
      const manifest = panelRender(rs, o)?.manifest
      if (!manifest) continue
      const extra = elementSnapCandidates(o, manifest)
      cands.xs.push(...extra.xs)
      cands.ys.push(...extra.ys)
    }
  }
  return cands
}

/**
 * shift 方向锁（Illustrator 语义）：位移投影到最近的水平 / 垂直 / 45° 方向，
 * 斜拖时也能顺着对角线走。cos(π/2) 是 6e-17 不是 0：轴向分量必须逐位归零，
 * 否则「锁垂直」仍带极小水平漂移。
 */
function axisLock(dx: number, dy: number): [number, number] {
  const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4)
  const ux = Math.abs(Math.cos(ang)) < 1e-9 ? 0 : Math.cos(ang)
  const uy = Math.abs(Math.sin(ang)) < 1e-9 ? 0 : Math.sin(ang)
  const proj = dx * ux + dy * uy
  return [ux * proj, uy * proj]
}

/**
 * 分数坐标位移的 shift 方向锁：fx / fy 分别除以内容宽高，45° 只有换算到
 * 内容像素系投影、再换算回去才是视觉上的 45°（水平 / 垂直不受比例影响）。
 */
function contentAxisLock(
  layout: { width: number; height: number },
  dfx: number,
  dfy: number,
): [number, number] {
  const [dx, dy] = axisLock(dfx * layout.width, dfy * layout.height)
  return [dx / layout.width, dy / layout.height]
}

/**
 * 拖动时排除锁定对象；成组对象整组跟着走，**组内有锁定成员则整组都不动**
 * （规则唯一出处是 actions 的 movableTargets，方向键微调走的是同一个）。
 */
function draggableSelection(): { targets: CanvasObject[]; blockedGroups: number } {
  const { objects, blockedGroups } = movableTargets(useSelectionStore.getState().ids)
  return { targets: objects.filter((o) => !o.hidden), blockedGroups }
}

/* -------------------------------------------------------------------------- */
/*  移动                                                                       */
/* -------------------------------------------------------------------------- */

export function startMoveDrag(e: ReactPointerEvent, objectId: string) {
  const store = useDocumentStore.getState()
  const { targets, blockedGroups } = draggableSelection()
  warnBlockedGroups(blockedGroups, targets.length > 0)
  if (!targets.length) return
  const primary = targets.find((o) => o.id === objectId) ?? targets[0]
  const origin = new Map(targets.map((o) => [o.id, { x: o.x, y: o.y }]))
  const primaryOrigin = origin.get(primary.id)!
  const excluded = new Set(targets.map((o) => o.id))
  const cands = candidatesFor(excluded)

  interaction().begin('move')
  store.beginTxn(moveLabel(targets.length))

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const t = getTransform()
      let dx = pxToMm(dxPx, t)
      let dy = pxToMm(dyPx, t)
      if (ev.shiftKey) {
        ;[dx, dy] = axisLock(dx, dy)
      }

      let snapX: number[] = []
      let snapY: number[] = []
      if (cands && !ev.metaKey && !ev.ctrlKey) {
        const moving: Rect = {
          x: primaryOrigin.x + dx,
          y: primaryOrigin.y + dy,
          w: primary.w,
          h: primary.h,
        }
        const snap = snapMove(moving, cands, snapTolMm(t))
        dx += snap.dx
        dy += snap.dy
        snapX = snap.guideXs
        snapY = snap.guideYs
      }
      interaction().setSnap(snapX, snapY)

      store.txnUpdate((d) => {
        for (const o of d.objects) {
          const start = origin.get(o.id)
          if (!start) continue
          o.x = start.x + dx
          o.y = start.y + dy
        }
      })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      // pointercancel = 这次交互被系统作废，一律丢弃：不留位移、不进历史
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

/* -------------------------------------------------------------------------- */
/*  缩放                                                                       */
/* -------------------------------------------------------------------------- */

/**
 * 拖八个缩放手柄。对象可能带任意角度 rotationDeg（x/y/w/h 恒为未旋转包围盒，
 * 旋转只由 ObjectView 的 CSS rotate 呈现），所以：
 * 1. 屏幕位移先反旋转回对象局部系，手柄方向才与 resizeRect 的 e/w/n/s 对得上；
 * 2. 局部系里算出的新包围盒再经 anchoredRect 落位，保证被拖手柄对面那个锚点
 *    在页面上不动（否则中心一变，图形会先整体跳一段）；
 * 3. 旋转后不再做吸附——候选线是页面 x/y 轴的，而旋转对象的可见边根本不平行于
 *    它们，硬吸只会把用户往一个看不出所以然的位置上拽。
 */
export function startResizeDrag(e: ReactPointerEvent, objectId: string, dir: ResizeDir) {
  e.stopPropagation()
  const store = useDocumentStore.getState()
  const target = doc().objects.find((o) => o.id === objectId)
  if (!target || target.locked) return
  const orig: Rect = { x: target.x, y: target.y, w: target.w, h: target.h }
  const excluded = new Set([objectId])
  const cands = candidatesFor(excluded)
  const isText = target.type === 'text'
  const rot = objectRotation(target)
  // 面板的等比与否由它自己的宽高比锁定开关决定，形状仍是默认等比
  const keepRatio = target.type === 'panel' ? panelAspectLocked(target) : !isText

  interaction().begin('resize')
  store.beginTxn(hist('resizeObjects'))

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const t = getTransform()
      const [dx, dy] = unrotateVecDeg(pxToMm(dxPx, t), pxToMm(dyPx, t), rot)
      // 角点按锁定状态等比，Alt 反转；shift 强制等比（Illustrator 语义，
      // 边柄按住 shift 也等比缩放）；文字只改宽度、不参与等比
      const corner = (dir.includes('e') || dir.includes('w')) && (dir.includes('n') || dir.includes('s'))
      const proportional =
        !isText && (corner ? ev.shiftKey || keepRatio !== ev.altKey : ev.shiftKey)
      let next = resizeRect(orig, dir, dx, dy, proportional)

      if (isText) {
        next = { ...next, y: orig.y, h: orig.h }
      }

      // 缩放时只吸附正在移动的边
      const tol = snapTolMm(t)
      const snapX: number[] = []
      const snapY: number[] = []
      if (cands && !rot && !ev.metaKey && !ev.ctrlKey && !proportional) {
        if (dir.includes('e')) {
          const hit = snapEdge(next.x + next.w, cands.xs, tol)
          if (hit != null) {
            next.w = Math.max(3, hit - next.x)
            snapX.push(hit)
          }
        } else if (dir.includes('w')) {
          const hit = snapEdge(next.x, cands.xs, tol)
          if (hit != null) {
            next.w = Math.max(3, next.x + next.w - hit)
            next.x = hit
            snapX.push(hit)
          }
        }
        if (dir.includes('s')) {
          const hit = snapEdge(next.y + next.h, cands.ys, tol)
          if (hit != null) {
            next.h = Math.max(2, hit - next.y)
            snapY.push(hit)
          }
        } else if (dir.includes('n')) {
          const hit = snapEdge(next.y, cands.ys, tol)
          if (hit != null) {
            next.h = Math.max(2, next.y + next.h - hit)
            next.y = hit
            snapY.push(hit)
          }
        }
      }
      interaction().setSnap(snapX, snapY)

      const placed = anchoredRect(orig, next, dir, rot)
      store.txnUpdate((d) => {
        const o = d.objects.find((x) => x.id === objectId)
        if (!o) return
        o.x = placed.x
        o.y = placed.y
        o.w = placed.w
        if (!isText) o.h = placed.h
      })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      // pointercancel = 这次交互被系统作废，一律丢弃：不留位移、不进历史
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

/* -------------------------------------------------------------------------- */
/*  线状对象端点（箭头 / 直线形状）                                             */
/* -------------------------------------------------------------------------- */

/**
 * 拖动端点：端点存的是包围盒内的比例坐标，拖动时先算出两端的绝对 mm
 * 位置，再由两点重新推出包围盒，保证包围盒始终贴合线段。
 * 箭头与直线形状共用这条路径（端点字段同构，见 types/document.isLinear）。
 *
 * 带 rotationDeg 时，屏幕位移经 endpointDelta 解成局部位移（不是简单反旋转：
 * 包围盒随端点变，旋转支点也跟着动，详见 geometry.endpointDelta），之后整条
 * 链路仍在局部系里跑。15° 吸附因此吸的是局部角度——旋转角按 15° 步进（属性面板
 * 的默认档）时，屏幕上看到的仍是 15° 的整数倍。页面参考线吸附与缩放同理，
 * 旋转后不再参与：候选线是页面 x/y 轴的，与旋转后的可见方向对不上。
 */
export function startEndpointDrag(e: ReactPointerEvent, objectId: string, which: 'start' | 'end') {
  e.stopPropagation()
  const store = useDocumentStore.getState()
  const target = doc().objects.find((o) => o.id === objectId)
  if (!target || !isLinear(target) || target.locked) return

  const ends = lineEndpoints(target)
  const abs = (p: EndPoint) => ({
    x: target.x + p.rx * target.w,
    y: target.y + p.ry * target.h,
  })
  const fixed = abs(which === 'start' ? ends.end : ends.start)
  const movingStart = abs(which === 'start' ? ends.start : ends.end)
  const rot = objectRotation(target)
  const cands = candidatesFor(new Set([objectId]))

  interaction().begin('endpoint')
  store.beginTxn(hist(target.type === 'arrow' ? 'arrowEndpoint' : 'lineEndpoint'))

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const t = getTransform()
      const [ldx, ldy] = endpointDelta(pxToMm(dxPx, t), pxToMm(dyPx, t), rot)
      let px = movingStart.x + ldx
      let py = movingStart.y + ldy

      if (ev.shiftKey) {
        // 15° 吸附，画示意箭头时很有用
        const dx = px - fixed.x
        const dy = py - fixed.y
        const len = Math.hypot(dx, dy)
        const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 12)) * (Math.PI / 12)
        px = fixed.x + Math.cos(ang) * len
        py = fixed.y + Math.sin(ang) * len
      }

      const snapX: number[] = []
      const snapY: number[] = []
      if (cands && !rot && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) {
        const tol = snapTolMm(t)
        const hx = snapEdge(px, cands.xs, tol)
        if (hx != null) {
          px = hx
          snapX.push(hx)
        }
        const hy = snapEdge(py, cands.ys, tol)
        if (hy != null) {
          py = hy
          snapY.push(hy)
        }
      }
      interaction().setSnap(snapX, snapY)

      const a = which === 'start' ? { x: px, y: py } : fixed
      const b = which === 'start' ? fixed : { x: px, y: py }
      const x = Math.min(a.x, b.x)
      const y = Math.min(a.y, b.y)
      const w = Math.max(Math.abs(b.x - a.x), 0.01)
      const h = Math.max(Math.abs(b.y - a.y), 0.01)

      store.txnUpdate((d) => {
        const o = d.objects.find((x) => x.id === objectId)
        if (!o || !isLinear(o)) return
        o.x = x
        o.y = y
        o.w = w
        o.h = h
        o.start = { rx: (a.x - x) / w, ry: (a.y - y) / h }
        o.end = { rx: (b.x - x) / w, ry: (b.y - y) / h }
      })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      // pointercancel = 这次交互被系统作废，一律丢弃：不留位移、不进历史
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

/* -------------------------------------------------------------------------- */
/*  框选                                                                       */
/* -------------------------------------------------------------------------- */

export function startMarquee(e: ReactPointerEvent) {
  const additive = e.shiftKey
  const base = additive ? [...useSelectionStore.getState().ids] : []
  const start = clientToMm(e.clientX, e.clientY)
  if (!additive) useSelectionStore.getState().clear()
  interaction().begin('marquee')

  trackPointer(e, {
    onMove: (ev) => {
      const cur = clientToMm(ev.clientX, ev.clientY)
      const rect: Rect = {
        x: Math.min(start.x, cur.x),
        y: Math.min(start.y, cur.y),
        w: Math.abs(cur.x - start.x),
        h: Math.abs(cur.y - start.y),
      }
      interaction().setMarquee(rect)
      const hit = doc()
        .objects.filter((o) => !o.hidden && rectsIntersect(o, rect))
        .map((o) => o.id)
      useSelectionStore.getState().set(expandGroups([...new Set([...base, ...hit])]))
    },
    onEnd: () => interaction().end(),
  })
}

/* -------------------------------------------------------------------------- */
/*  平移                                                                       */
/* -------------------------------------------------------------------------- */

export function startPan(e: ReactPointerEvent) {
  const { panX, panY } = useViewportStore.getState()
  interaction().begin('pan')
  trackPointer(e, {
    threshold: 0,
    onMove: (_ev, dx, dy) => useViewportStore.getState().setPan(panX + dx, panY + dy),
    onEnd: () => interaction().end(),
  })
}

/* -------------------------------------------------------------------------- */
/*  绘制新对象                                                                 */
/* -------------------------------------------------------------------------- */

const DEFAULT_DRAW: Record<string, { w: number; h: number }> = {
  text: { w: 40, h: 5 },
  arrow: { w: 30, h: 14 },
  rect: { w: 30, h: 20 },
  ellipse: { w: 30, h: 20 },
  line: { w: 30, h: 0.01 },
}

/**
 * 用当前工具拖出一个新对象；只是点一下则落一个默认尺寸的对象。
 * 完成后工具自动回到选择态（与 Figma / Illustrator 一致）。
 *
 * 箭头 / 直线拖动中把真实端点写进 draft：OverlaySvg 按最终样式实时预览
 * 那条线（不是包围盒虚线框），松手也用同一对端点落对象——吸附与 shift
 * 角度锁在预览里是什么样，落下来就是什么样。
 * shift（Illustrator 语义）：矩形 / 椭圆锁成正方形 / 正圆（锚在起点），
 * 箭头 / 直线锁 15° 角（与端点拖拽同一档）。
 */
export function startDraw(e: ReactPointerEvent, tool: Exclude<Tool, 'select'>) {
  const store = useDocumentStore.getState()
  const start = clientToMm(e.clientX, e.clientY)
  const cands = candidatesFor(new Set())
  const linear = tool === 'arrow' || tool === 'line'
  interaction().begin('draw')

  trackPointer(e, {
    onMove: (ev) => {
      const t = getTransform()
      let cur = clientToMm(ev.clientX, ev.clientY, t)
      // shift 角度锁优先于吸附（与端点拖拽一致：锁角时不再吸附，两者会互相拆台）
      if (ev.shiftKey && linear) {
        const dx = cur.x - start.x
        const dy = cur.y - start.y
        const len = Math.hypot(dx, dy)
        const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 12)) * (Math.PI / 12)
        cur = { x: start.x + Math.cos(ang) * len, y: start.y + Math.sin(ang) * len }
        interaction().setSnap([], [])
      } else {
        if (cands && !ev.metaKey && !ev.ctrlKey) {
          const tol = snapTolMm(t)
          const hx = snapEdge(cur.x, cands.xs, tol)
          const hy = snapEdge(cur.y, cands.ys, tol)
          cur = { x: hx ?? cur.x, y: hy ?? cur.y }
          interaction().setSnap(hx != null ? [hx] : [], hy != null ? [hy] : [])
        }
        if (ev.shiftKey && (tool === 'rect' || tool === 'ellipse')) {
          const dx = cur.x - start.x
          const dy = cur.y - start.y
          const side = Math.max(Math.abs(dx), Math.abs(dy))
          cur = {
            x: start.x + (dx < 0 ? -side : side),
            y: start.y + (dy < 0 ? -side : side),
          }
        }
      }
      interaction().setDraft({
        tool,
        x: Math.min(start.x, cur.x),
        y: Math.min(start.y, cur.y),
        w: Math.abs(cur.x - start.x),
        h: Math.abs(cur.y - start.y),
        ...(linear ? { start: { x: start.x, y: start.y }, end: cur } : {}),
      })
    },
    onEnd: (moved, ev, fin) => {
      const draft = interaction().draft
      // 优先用 draft 里的端点：吸附 / shift 锁角只作用于 onMove，
      // 直接读松手坐标会让最终对象与预览差一口气
      const end = draft?.end ?? clientToMm(ev.clientX, ev.clientY)
      interaction().end()
      // pointercancel：草稿丢掉，不落对象（工具也不该切回选择）
      if (fin.cancelled) return

      const fallback = DEFAULT_DRAW[tool]
      const rect: Rect =
        moved && draft && draft.w > 0.5
          ? draft
          : { x: start.x, y: start.y, w: fallback.w, h: fallback.h }

      const id = newId(tool[0])
      let created: CanvasObject
      if (tool === 'text') {
        const text: TextObject = {
          id,
          type: 'text',
          x: rect.x,
          y: rect.y,
          w: Math.max(rect.w, 12),
          h: 5,
          text: t('objectType.text'),
          sizePt: 10,
          bold: false,
          color: '#000000',
          align: 'left',
        }
        created = text
      } else if (tool === 'arrow' || tool === 'line') {
        // 箭头 / 直线沿实际拖动方向，而不是永远左上到右下（直线也不再永远水平）。
        // 点一下不拖时按 DEFAULT_DRAW 落一条：箭头默认斜向、直线默认水平。
        const sx = moved ? start.x : rect.x
        const sy = moved ? start.y : rect.y + (tool === 'arrow' ? rect.h : 0)
        const ex = moved ? end.x : rect.x + rect.w
        const ey = moved ? end.y : rect.y
        const x = Math.min(sx, ex)
        const y = Math.min(sy, ey)
        // 正好水平 / 竖直时另一边钳到 0.01：包围盒不能是零厚度（比例坐标要除它）
        const w = Math.max(Math.abs(ex - sx), 0.01)
        const h = Math.max(Math.abs(ey - sy), 0.01)
        const ends = {
          start: { rx: (sx - x) / w, ry: (sy - y) / h },
          end: { rx: (ex - x) / w, ry: (ey - y) / h },
        }
        created =
          tool === 'arrow'
            ? ({
                id,
                type: 'arrow',
                x,
                y,
                w,
                h,
                ...ends,
                strokePt: 1,
                color: '#1B1B18',
                head: 'end',
              } satisfies ArrowObject)
            : ({
                id,
                type: 'shape',
                shape: 'line',
                x,
                y,
                w,
                h,
                ...ends,
                strokePt: 1,
                color: '#1B1B18',
                fill: null,
              } satisfies ShapeObject)
      } else {
        const shape: ShapeObject = {
          id,
          type: 'shape',
          shape: tool,
          x: rect.x,
          y: rect.y,
          w: Math.max(rect.w, 1),
          h: Math.max(rect.h, 1),
          strokePt: 1,
          color: '#1B1B18',
          fill: null,
        }
        created = shape
      }

      store.commit(hist('addShape', { shape: drawnToolMsg(tool) }), (d) => {
        d.objects.push(created)
      })
      const ui = useUiStore.getState()
      // 在图内编辑态画标注：新对象属于画布层，必须先退出图内编辑，
      // 属性页才会跟到新对象上（否则它的属性根本改不了）
      if (ui.elementPanelId) ui.setElementPanel(null)
      useSelectionStore.getState().set([id])
      ui.setTool('select')
      if (tool === 'text') ui.setEditingText(id)
    },
  })
}

/* -------------------------------------------------------------------------- */
/*  参考线                                                                     */
/* -------------------------------------------------------------------------- */

/** 从标尺拖出新参考线；index 非空表示拖动已有参考线（拖回标尺则删除） */
export function startGuideDrag(e: ReactPointerEvent, axis: 'x' | 'y', index: number | null) {
  e.stopPropagation()
  const store = useDocumentStore.getState()
  const page = doc().page
  interaction().begin('guide')

  const posOf = (ev: PointerEvent | ReactPointerEvent) => {
    const p = clientToMm(ev.clientX, ev.clientY)
    return Math.round((axis === 'x' ? p.x : p.y) * 10) / 10
  }
  const inRange = (pos: number) => pos >= -2 && pos <= (axis === 'x' ? page.w : page.h) + 2

  if (index != null) store.beginTxn(hist('moveGuide'))

  trackPointer(e, {
    threshold: 0,
    onMove: (ev) => {
      const pos = posOf(ev)
      if (index == null) {
        interaction().setPendingGuide({ axis, pos })
      } else {
        store.txnUpdate((d) => {
          if (d.guides[index]) d.guides[index].pos = pos
        })
      }
    },
    onEnd: (moved, ev, end) => {
      const pos = posOf(ev)
      interaction().end()
      // pointercancel：参考线既不新增也不删除，正在拖的那条回到原位
      if (end.cancelled) {
        if (index != null) store.endTxn({ discard: true })
        return
      }
      if (index == null) {
        if (moved && inRange(pos)) {
          store.commit(hist('addGuide'), (d) => {
            d.guides.push({ axis, pos })
          })
        }
        return
      }
      store.endTxn({ discard: !moved })
      if (!inRange(pos)) {
        store.commit(hist('deleteGuide'), (d) => {
          d.guides.splice(index, 1)
        })
      }
    },
  })
}

/* -------------------------------------------------------------------------- */
/*  裁剪                                                                       */
/* -------------------------------------------------------------------------- */

export type CropHandle = ResizeDir

const HANDLE_CYCLE = ['n', 'e', 's', 'w'] as const

/** 屏幕方位的手柄 → 内容坐标系方位（旋转的逆）：转 90° 时屏幕右缘是内容顶缘 */
function unrotateHandle(h: CropHandle, r: PanelRotation): CropHandle {
  if (!r) return h
  const steps = (4 - r / 90) % 4
  const mapped = [...h].map(
    (ch) => HANDLE_CYCLE[(HANDLE_CYCLE.indexOf(ch as (typeof HANDLE_CYCLE)[number]) + steps) % 4],
  )
  // 拼回 'nw'/'se' 这类合法方向名：纵向字母在前
  const ns = mapped.find((c) => c === 'n' || c === 's') ?? ''
  const ew = mapped.find((c) => c === 'e' || c === 'w') ?? ''
  return (ns + ew) as CropHandle
}

/**
 * 裁剪框以归一化比例存储（内容坐标系，与旋转无关）；拖动时限制在 0–1 内并
 * 保证最小 5% 边长。旋转的面板：屏幕位移/手柄先逆旋转回内容坐标系再套同一套
 * 边缘逻辑；包围盒以「未裁剪整图的画布中心」为锚重算——内容在画布上纹丝不动，
 * 动的只是取景窗（rot=0 时与旧公式逐项等价）。
 */
export function startCropDrag(
  e: ReactPointerEvent,
  objectId: string,
  handle: CropHandle | 'move',
) {
  e.stopPropagation()
  const store = useDocumentStore.getState()
  const panel = doc().objects.find((o) => o.id === objectId)
  if (!panel || panel.type !== 'panel') return
  const crop = panel.crop ?? { x: 0, y: 0, w: 1, h: 1 }
  const rot = panelRotation(panel)
  const swap = rotationSwaps(rot)
  // 未裁剪整图的显示尺寸（内容坐标系：90/270 时与包围盒长宽互换）
  const fullW = (swap ? panel.h : panel.w) / crop.w
  const fullH = (swap ? panel.w : panel.h) / crop.h
  const cHandle = handle === 'move' ? handle : unrotateHandle(handle, rot)
  // 取景窗中心相对整图中心的偏移（内容系）旋转到画布系，得到整图的画布锚点
  const [anchorDx, anchorDy] = rotateVec(
    fullW * ((1 - crop.w) / 2 - crop.x),
    fullH * ((1 - crop.h) / 2 - crop.y),
    rot,
  )
  const anchorX = panel.x + panel.w / 2 + anchorDx
  const anchorY = panel.y + panel.h / 2 + anchorDy

  interaction().begin('crop')
  store.beginTxn(hist('adjustCrop'))

  trackPointer(e, {
    onMove: (_ev, dxPx, dyPx) => {
      const t = getTransform()
      const [dxc, dyc] = unrotateVec(pxToMm(dxPx, t), pxToMm(dyPx, t), rot)
      const du = dxc / fullW
      const dv = dyc / fullH
      let { x, y, w, h } = crop

      if (cHandle === 'move') {
        x = clamp(crop.x + du, 0, 1 - crop.w)
        y = clamp(crop.y + dv, 0, 1 - crop.h)
      } else {
        const MIN = 0.05
        if (cHandle.includes('w')) {
          const nx = clamp(crop.x + du, 0, crop.x + crop.w - MIN)
          w = crop.x + crop.w - nx
          x = nx
        } else if (cHandle.includes('e')) {
          w = clamp(crop.w + du, MIN, 1 - crop.x)
        }
        if (cHandle.includes('n')) {
          const ny = clamp(crop.y + dv, 0, crop.y + crop.h - MIN)
          h = crop.y + crop.h - ny
          y = ny
        } else if (cHandle.includes('s')) {
          h = clamp(crop.h + dv, MIN, 1 - crop.y)
        }
      }

      store.txnUpdate((d) => {
        const o = d.objects.find((x2) => x2.id === objectId)
        if (!o || o.type !== 'panel') return
        o.crop = { x, y, w, h }
        // 整图锚死在画布上，包围盒围着新取景窗重算
        const cw = fullW * w
        const ch = fullH * h
        const [offX, offY] = rotateVec(
          fullW * ((1 - w) / 2 - x),
          fullH * ((1 - h) / 2 - y),
          rot,
        )
        o.w = swap ? ch : cw
        o.h = swap ? cw : ch
        o.x = anchorX - offX - o.w / 2
        o.y = anchorY - offY - o.h / 2
      })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      // pointercancel = 这次交互被系统作废，一律丢弃：不留位移、不进历史
      store.endTxn({ discard: !moved || end.cancelled })
    },
  })
}

/* -------------------------------------------------------------------------- */
/*  图内元素编辑                                                               */
/* -------------------------------------------------------------------------- */

/**
 * 用 manifest bbox 做命中测试：面积小者优先，「图表外壳」类降权，
 * 边缘留 0.4% 容差。不依赖 SVG 内部结构，因此换 matplotlib 版本也不会失效。
 */
// isElementHidden 已随 manifest 几何一起收进 lib/elementGeom；原调用点从这里继续拿
export { isElementHidden }

/**
 * 命中评分的角色权重：重叠时谁该让路。
 *
 * 子图是容器，刻度与刻度标签是子图的外壳 —— 它们都不是「盖在别人上面」的
 * 内容，用户点到重叠区时想要的几乎总是那个内容元素。刻度标签尤其危险：
 * 它的 bbox 常常只有千分之几，纯比面积必赢，而 matplotlib 即使没把它画出来
 * （比如那条只放「CMP only」的窄条轴）也照样报一个 bbox，于是变成一块看不见
 * 的挡板，把真实文字标签的点击偷走。
 *
 * 降权而不是排除：单独摆在空白处的刻度标签仍然选得中，只是不再抢别人的。
 */
const HIT_PENALTY: Record<string, number> = {
  axes: 10,
  axes3d: 10,
  ticks: 20,
  ticklabel: 20,
}

/**
 * 沿**路径**命中的容差（mm，路径两侧各留这么宽）。图内独立箭头、曲线、
 * 填充/多边形的边线共用同一档——它们在用户眼里都是「点线本身」。
 */
export const PATH_HIT_MM = 1.5

/** 点到线段的距离，mm 系：分数坐标 x/y 分别乘以图宽图高，距离才是视觉距离 */
function arrowDistMm(
  size: [number, number],
  fx: number,
  fy: number,
  a: [number, number],
  b: [number, number],
): number {
  const px = fx * size[0]
  const py = fy * size[1]
  const ax = a[0] * size[0]
  const ay = a[1] * size[1]
  const bx = b[0] * size[0]
  const by = b[1] * size[1]
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  const t = len2 ? clamp(((px - ax) * dx + (py - ay) * dy) / len2, 0, 1) : 0
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t))
}

export function pickElement(
  manifest: Manifest | null | undefined,
  fx: number,
  fy: number,
  lockedGids?: readonly string[],
): ManifestElement | null {
  if (!manifest) return null
  const PAD = 0.004
  let best: ManifestElement | null = null
  let bestScore = Infinity
  for (const el of manifest.elements) {
    if (el.gid === 'figure') continue
    if (isElementHidden(el)) continue // 隐藏的元素不该再挡住点击
    if (lockedGids?.includes(el.gid)) continue // 锁定元素只能从元素树选中
    // 图内独立箭头按线本身命中（与画布箭头同语义，见 hitTest.test 的沿线命中）：
    // 斜箭头的 bbox 是一大块空白矩形，按矩形命中会让远离线的点击也选中箭头、
    // 还把底下元素的点击偷走。评分用线的「墨迹面积」（长 × 2×容差，换算回
    // 分数系），与其余元素的 bbox 面积同一量纲。
    if (el.arrow_endpoints && el.arrow_endpoints.length >= 2) {
      const [a, b] = el.arrow_endpoints
      const [sw, sh] = manifest.size_mm
      if (arrowDistMm(manifest.size_mm, fx, fy, a, b) > PATH_HIT_MM) continue
      const lenMm = Math.hypot((b[0] - a[0]) * sw, (b[1] - a[1]) * sh)
      const score = (lenMm * 2 * PATH_HIT_MM) / (sw * sh)
      if (score < bestScore) {
        bestScore = score
        best = el
      }
      continue
    }
    // 有真实路径的元素（曲线 / 填充 / 独立形状）按**路径**命中，不用 bbox：
    // 一条斜曲线的 bbox 是一大块空白矩形，按矩形命中会让离线很远的点击也选中
    // 它，还把底下元素的点击偷走。填充区域内部照旧算命中（点进去就是选它），
    // 空心的只在描边附近命中。裁剪框之外一律不命中（那儿本来就看不见）。
    if (el.geometry) {
      const geom = el.geometry
      // 容差要把**画出来的那条粗线**算进去：固定 1.5mm 只覆盖中心线，
      // 粗线的边缘像素会落在容差之外（geomHitTolMm 的注释里有账）
      const tol = geomHitTolMm(geom, PATH_HIT_MM)
      const inside = geomContains(geom, fx, fy)
      const dist = inside ? 0 : geomDistMm(geom, manifest.size_mm, fx, fy)
      if (!inside && dist > tol) continue
      // 评分与 bbox 面积同一量纲：填充按真实面积（小的赢），空心按墨迹面积
      // （一条线的墨迹极小，因此总能从子图容器手里把点击拿回来）
      const score = inside
        ? geomAreaFrac(geom) * (HIT_PENALTY[el.role] ?? 1)
        : geomInkAreaFrac(geom, manifest.size_mm, tol)
      if (score < bestScore) {
        bestScore = score
        best = el
      }
      continue
    }
    const [x, y, w, h] = el.bbox
    if (fx < x - PAD || fx > x + w + PAD || fy < y - PAD || fy > y + h + PAD) continue
    const area = w * h
    const score = area * (HIT_PENALTY[el.role] ?? 1)
    if (score < bestScore) {
      bestScore = score
      best = el
    }
  }
  return best ?? manifest.elements.find((e) => e.gid === 'figure') ?? null
}

/**
 * 拖动图内可拖元素：先直接平移 SVG 里对应的 <g> 做乐观预览，
 * 松手才把新锚点写成 override 触发真渲染。
 */
export function startElementDrag(
  e: ReactPointerEvent,
  panel: PanelObject,
  element: ManifestElement,
  layout: { width: number; height: number },
) {
  if (!element.anchor || !element.drag_prop) return
  e.stopPropagation()
  const dragProp = element.drag_prop
  // 基准走 anchorOf：它优先取文档里已写下的 override（见那边的说明）
  const anchor = anchorOf(panel, element) ?? element.anchor

  interaction().begin('element')
  beginElementPreview(panel)
  // 面板可能被旋转过：屏幕位移要先转回内容坐标系，图内的分数坐标才对得上
  const toContent = contentDelta(panel, layout)
  // 松手写 onMove 最后一次的位移：shift 锁向只作用于 onMove，若重读松手坐标，
  // shift 先于抬指松开时落点会与预览差一口气
  let last: [number, number] = [0, 0]

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      let [dfx, dfy] = toContent(dxPx, dyPx)
      if (ev.shiftKey) [dfx, dfy] = contentAxisLock(layout, dfx, dfy)
      last = [dfx, dfy]
      previewTransform(element.gid, dfx, dfy)
      interaction().setGidDrag({ gid: element.gid, dfx, dfy })
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      if (!moved || end.cancelled) {
        cancelElementPreview()
        return
      }
      const [dfx, dfy] = last
      setOverride(panel.id, element.gid, dragProp, [anchor[0] + dfx, anchor[1] + dfy], true)
      commitElementPreview(panel.id)
    },
  })
}

/**
 * 拖动图内独立箭头（FancyArrowPatch）：整体平移或拖单个端点。
 * 整体平移时顺带平移 SVG <g> 做乐观预览；单端拖动改变形状，SVG 预览会骗人，
 * 改在覆盖层画一条虚线（OverlaySvg 读 arrowPreview），松手写 endpoints_frac。
 *
 * shift（Illustrator 语义，与画布箭头同一套档位）：整体拖动锁水平 / 垂直 / 45°，
 * 拖单端点相对固定端锁 15° 角。端点是 figure 分数坐标（x/y 分别除以图宽图高），
 * 角度必须换算到内容像素系再算，锁出来的才是视觉角度。
 */
export function startArrowDrag(
  e: ReactPointerEvent,
  panel: PanelObject,
  element: ManifestElement,
  layout: { width: number; height: number },
  which: 'both' | 'start' | 'end',
) {
  const pts = arrowEndpointsOf(panel, element)
  if (!pts) return
  e.stopPropagation()

  interaction().begin('element')
  beginElementPreview(panel)
  const toContent = contentDelta(panel, layout)
  const W = layout.width
  const H = layout.height

  const compute = (dfx: number, dfy: number, shift: boolean) => {
    if (which === 'both') {
      if (shift) [dfx, dfy] = contentAxisLock(layout, dfx, dfy)
      return {
        dfx,
        dfy,
        a: [pts[0][0] + dfx, pts[0][1] + dfy] as [number, number],
        b: [pts[1][0] + dfx, pts[1][1] + dfy] as [number, number],
      }
    }
    const fixed = pts[which === 'start' ? 1 : 0] as [number, number]
    const moving = pts[which === 'start' ? 0 : 1]
    let mx = moving[0] + dfx
    let my = moving[1] + dfy
    if (shift) {
      // 15° 锁角（与画布箭头端点拖拽同一档），在内容像素系里取角
      const dx = (mx - fixed[0]) * W
      const dy = (my - fixed[1]) * H
      const len = Math.hypot(dx, dy)
      const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 12)) * (Math.PI / 12)
      mx = fixed[0] + (Math.cos(ang) * len) / W
      my = fixed[1] + (Math.sin(ang) * len) / H
    }
    const moved: [number, number] = [mx, my]
    return which === 'start'
      ? { dfx, dfy, a: moved, b: fixed }
      : { dfx, dfy, a: fixed, b: moved }
  }

  // 松手写 onMove 最后一次的结果：shift 锁角只作用于 onMove，重读松手坐标会
  // 让落点与预览差一口气（与 startDraw 读 draft 同一取舍）
  let last: ReturnType<typeof compute> | null = null

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const [dfx, dfy] = toContent(dxPx, dyPx)
      const r = compute(dfx, dfy, ev.shiftKey)
      last = r
      if (which === 'both') {
        previewTransform(element.gid, r.dfx, r.dfy)
        interaction().setGidDrag({ gid: element.gid, dfx: r.dfx, dfy: r.dfy })
      } else {
        // 单端拖动改的是形状，平移 SVG 会骗人：预览交给覆盖层的虚线
        interaction().setArrowPreview({ gid: element.gid, a: r.a, b: r.b })
      }
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      if (!moved || !last || end.cancelled) {
        cancelElementPreview()
        return
      }
      setOverride(
        panel.id,
        element.gid,
        'endpoints_frac',
        [last.a[0], last.a[1], last.b[0], last.b[1]].map(round4),
        true,
      )
      commitElementPreview(panel.id)
    },
  })
}

/**
 * 屏幕像素位移 → 面板内容的分数位移。
 * layout 是世界像素（未旋转的内容尺寸），换算到屏幕要乘 zoom；
 * 面板带 90° 步进旋转时先把位移反向旋转回内容坐标系。
 */
function contentDelta(panel: PanelObject, layout: { width: number; height: number }) {
  const rot = panelRotation(panel)
  return (dxPx: number, dyPx: number): [number, number] => {
    const { zoom } = useViewportStore.getState()
    const [dx, dy] = unrotateVec(dxPx, dyPx, rot)
    return [dx / (layout.width * zoom), dy / (layout.height * zoom)]
  }
}

/* -------------------------------------------------------------------------- */
/*  axes 拖动 / 缩放子图占比                                                   */
/* -------------------------------------------------------------------------- */

/**
 * 拖动整个子图或它的八个手柄，改的是 matplotlib 的 axes position（figure 占比）。
 * 移动时顺带平移 SVG 里的 <g> 做预览；缩放不做 SVG 预览——matplotlib 重排后
 * 刻度和字号并不会跟着线性缩放，假预览会骗人，只给一个覆盖层线框。
 *
 * 整体平移会带上随行元素（被手动摆过的标签、色条轴、孪生轴，见
 * `axesCompanions`）——设置里可关。缩放不带：随行元素该缩到哪里没有可信答案，
 * matplotlib 自己重排出来的才算数。
 *
 * element 必须已经过 geomTarget 解析：点位图时传进来的是它的宿主子图。
 */
export function startAxesDrag(
  e: ReactPointerEvent,
  panel: PanelObject,
  element: ManifestElement,
  layout: { width: number; height: number },
  mode: 'move' | ResizeDir,
) {
  const start = positionOf(panel, element)
  if (!start) return
  e.stopPropagation()

  const MIN = 0.05
  const manifest = panelRender(useRenderStore.getState(), panel)?.manifest
  const companions =
    mode === 'move' && manifest && useUiStore.getState().dragAxesWithCompanions
      ? axesCompanions(panel, manifest, element.gid)
      : []
  interaction().begin('element')
  beginElementPreview(panel)
  const toContent = contentDelta(panel, layout)

  const compute = (dxPx: number, dyPx: number, shift = false) => {
    let [dfx, dfy] = toContent(dxPx, dyPx)
    let [x, y, w, h] = start

    if (mode === 'move') {
      // 整体拖动支持 shift 锁向（与画布对象移动一致）；缩放手柄不参与
      if (shift) [dfx, dfy] = contentAxisLock(layout, dfx, dfy)
      x = clamp(x + dfx, 0, 1 - w)
      y = clamp(y - dfy, 0, 1 - h) // 屏幕向下 = bottom-origin 的 y 变小
      return { rect: [x, y, w, h] as Rect4, dfx, dfy }
    }
    if (mode.includes('e')) w = clamp(w + dfx, MIN, 1 - x)
    else if (mode.includes('w')) {
      const nx = clamp(x + dfx, 0, x + w - MIN)
      w = x + w - nx
      x = nx
    }
    if (mode.includes('s')) {
      const ny = clamp(y - dfy, 0, y + h - MIN)
      h = y + h - ny
      y = ny
    } else if (mode.includes('n')) {
      h = clamp(h - dfy, MIN, 1 - y)
    }
    return { rect: [x, y, w, h] as Rect4, dfx, dfy }
  }

  /**
   * 落在子图上的**净位移**（内容分数、y 向下）。
   * 取钳位后的结果反推，而不是用光标的原始位移：贴到画布边上时子图停了，
   * 随行元素要是照着原始位移继续走，一组东西就被拆散了。
   */
  const netDelta = (rect: Rect4): [number, number] => [rect[0] - start[0], start[1] - rect[1]]

  // 松手写 onMove 最后一次的结果：shift 锁向只作用于 onMove（见 startArrowDrag）
  let last: Rect4 | null = null

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      const { rect } = compute(dxPx, dyPx, ev.shiftKey)
      last = rect
      interaction().setElementPreview({ boxes: { [element.gid]: flipY(rect) } })
      // 只有纯平移的 SVG 预览是准的。缩放要 matplotlib 重排（刻度、字号、
      // 图例都不会跟着线性缩放），假装缩放只会画出一张必然被纠正的图——
      // 覆盖层线框如实表达「框会变成这么大」，成图由权威渲染说了算
      if (mode === 'move') {
        const [ndx, ndy] = netDelta(rect)
        previewTransform(element.gid, ndx, ndy)
        // 后代不单独平移：它们嵌在宿主的 <g> 里，已经跟着动了
        for (const c of companions) {
          if (c.previewsSeparately) previewTransform(c.gid, ndx, ndy)
        }
      }
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      if (!moved || !last || end.cancelled) {
        cancelElementPreview()
        return
      }
      const rect = last
      const patches = [
        { gid: element.gid, prop: 'position', value: rect.map(round4) },
        ...companions.map((c) => c.shift(...netDelta(rect))),
      ]
      // 一次 setOverrides = 一条撤销 = 一次权威渲染
      if (patches.length > 1)
        setOverrides(panel.id, hist('moveElement', { label: element.label }), patches, true)
      else setOverride(panel.id, element.gid, 'position', rect.map(round4), true)
      commitElementPreview(panel.id)
    },
  })
}

/**
 * 图内多选整组平移：拖动多选里的任一成员，全体按同一位移走。
 *
 * 每个成员写自己的那条 override —— 子图与位图（经 geom 代理）写 position、
 * 文字写 pos_frac、图例写 loc_frac —— 具体写法由 alignEntries 的 write 决定，
 * 这里只负责把「同一个分数位移」发给每个成员。松手一次 setOverrides =
 * 一条撤销 = 一次渲染。
 *
 * 不把成员钳进画布：一旦有成员贴边，钳位会让整组卡住、相对布局也被拆散，
 * 与成组缩放（resizeGroup）的取舍一致，超出部分由 matplotlib 自己裁掉。
 */
export function startElementGroupMove(
  e: ReactPointerEvent,
  panel: PanelObject,
  entries: AlignEntry[],
  layout: { width: number; height: number },
) {
  e.stopPropagation()
  interaction().begin('element')
  // 纯平移的乐观预览是准的（不像缩放会触发 matplotlib 重排），SVG 一起跟手
  beginElementPreview(panel)
  const toContent = contentDelta(panel, layout)

  const shifted = (dfx: number, dfy: number): Rect4[] =>
    entries.map((en) => [en.box[0] + dfx, en.box[1] + dfy, en.box[2], en.box[3]])

  // 松手写 onMove 最后一次的位移：shift 锁向只作用于 onMove（见 startArrowDrag）
  let last: [number, number] = [0, 0]

  trackPointer(e, {
    onMove: (ev, dxPx, dyPx) => {
      let [dfx, dfy] = toContent(dxPx, dyPx)
      if (ev.shiftKey) [dfx, dfy] = contentAxisLock(layout, dfx, dfy)
      last = [dfx, dfy]
      const boxes = shifted(dfx, dfy)
      interaction().setElementPreview({
        boxes: Object.fromEntries(entries.map((en, i) => [en.key, boxes[i]])),
        group: unionBox(boxes) ?? undefined,
      })
      for (const en of entries) previewTransform(en.key, dfx, dfy)
    },
    onEnd: (moved, _ev, end) => {
      interaction().end()
      if (!moved || end.cancelled) {
        cancelElementPreview()
        return
      }
      const boxes = shifted(last[0], last[1])
      setOverrides(
        panel.id,
        hist('moveElements', { count: entries.length }),
        entries.map((en, i) => en.write(boxes[i])),
      )
      commitElementPreview(panel.id)
    },
  })
}

/**
 * 拖组包围框的手柄，成组缩放多个子图：组框按手柄方向缩放，每个成员再线性
 * 重映射进新组框，组内相对布局因此保持不变。松手一次性提交全部 position——
 * 一条撤销、一次引擎渲染。
 */
export function startGroupResize(
  e: ReactPointerEvent,
  panel: PanelObject,
  group: Group,
  layout: { width: number; height: number },
  dir: ResizeDir,
) {
  e.stopPropagation()
  interaction().begin('element')
  beginElementPreview(panel)

  const toContent = contentDelta(panel, layout)
  const nextGroup = (dxPx: number, dyPx: number) => {
    const [dfx, dfy] = toContent(dxPx, dyPx)
    return resizeGroup(group.box, dir, dfx, dfy)
  }
  // shift 锁向不作用于成组缩放，松手重读坐标与预览一致；但仍记下最后一帧，
  // 免得 onEnd 与 onMove 各算一遍
  let last: Rect4 | null = null

  trackPointer(e, {
    onMove: (_ev, dxPx, dyPx) => {
      const box = nextGroup(dxPx, dyPx)
      last = box
      // 缩放不做 SVG 预览（见 startAxesDrag 的同一条取舍）：
      // 手柄、线框与最终 patch 是准的，像素由 matplotlib 重排后说了算
      interaction().setElementPreview({
        boxes: Object.fromEntries(groupBoxes(group, box)),
        group: box,
      })
    },
    onEnd: (moved, ev, end) => {
      interaction().end()
      if (!moved || end.cancelled) {
        cancelElementPreview()
        return
      }
      const box = last ?? nextGroup(ev.clientX - e.clientX, ev.clientY - e.clientY)
      setOverrides(panel.id, hist('resizeAxes', { count: group.entries.length }), groupPatches(group, box))
      commitElementPreview(panel.id)
    },
  })
}
